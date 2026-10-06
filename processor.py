import asyncio
import logging
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import database
import keyboards
from browser import browser_manager
from config import MAX_TABS_PER_USER, WEBSITE_TIMEOUT_S
import idle_worker

logger = logging.getLogger(__name__)

# Per-user asyncio Task objects
active_tasks: dict[int, asyncio.Task] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_url(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash from path for dedup purposes."""
    try:
        p = urlparse(url.strip())
        normalised = f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/') or '/'}"
        if p.query:
            normalised += f"?{p.query}"
        return normalised
    except Exception:
        return url.strip().lower()


def _find_duplicates(websites: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Return (deduped_list, duplicate_urls).

    Duplicates are URLs that normalise to the same string.
    The first occurrence is kept; subsequent ones are reported and dropped.
    """
    seen: dict[str, str] = {}   # normalised → original url
    deduped: list[dict] = []
    duplicates: list[str] = []

    for w in websites:
        key = _normalise_url(w["url"])
        if key in seen:
            duplicates.append(w["url"])
        else:
            seen[key] = w["url"]
            deduped.append(w)

    return deduped, duplicates


# ── Prompt / confirm ──────────────────────────────────────────────────────────

async def start_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    websites = await database.get_websites(user_id)

    if not websites:
        await query.edit_message_text(
            "You have no websites added yet.",
            reply_markup=keyboards.get_back_home_keyboard("menu_websites"),
        )
        return

    profile = await database.get_user_profile(user_id)
    msg_line = profile.get("message") or "Not configured"

    # Run dedup check and show notice if needed
    deduped, duplicates = _find_duplicates(websites)
    dedup_notice = ""
    if duplicates:
        dedup_notice = (
            f"\n\n⚠️ {len(duplicates)} duplicate URL(s) found and will be skipped:\n"
            + "\n".join(f"  • {u}" for u in duplicates[:5])
            + ("\n  …and more" if len(duplicates) > 5 else "")
        )

    text = (
        f"🚀 Ready\n\n"
        f"Websites: {len(deduped)} (unique){dedup_notice}\n"
        f"Message: {msg_line[:60] if profile.get('message') else 'Not configured'}\n\n"
        f"The bot will inspect each website and fill the available "
        f"contact fields on each form."
    )
    await query.edit_message_text(text, reply_markup=keyboards.get_start_confirm_keyboard())


# ── Start processing ──────────────────────────────────────────────────────────

async def process_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    # Guard: one active job per user
    if user_id in active_tasks and not active_tasks[user_id].done():
        await query.edit_message_text(
            "⚠️ A processing task is already running for your account.",
            reply_markup=keyboards.get_processing_keyboard(),
        )
        return

    websites = await database.get_websites(user_id)
    profile  = await database.get_user_profile(user_id)

    # Deduplicate before processing — only work on unique URLs
    websites, _ = _find_duplicates(websites)

    msg = await query.edit_message_text(
        "🚀 Processing starting...",
        reply_markup=keyboards.get_processing_keyboard(),
    )

    idle_worker.mark_job_started(user_id)
    task = asyncio.create_task(
        _run_processing(user_id, websites, profile, context, msg.chat_id, msg.message_id)
    )
    active_tasks[user_id] = task


# ── Core processing loop ──────────────────────────────────────────────────────

async def _run_processing(user_id: int, websites: list, profile: dict,
                          context, chat_id: int, message_id: int):
    """
    Process all websites for one user in parallel batches.

    Each website gets a hard outer timeout of WEBSITE_TIMEOUT_S seconds.
    If it exceeds this, it is recorded as SKIPPED_SLOW and auto-enqueued
    for retry — the tab slot is freed immediately.
    """
    total     = len(websites)
    processed = 0
    success   = 0
    failed    = 0
    review    = 0
    skipped   = 0
    dead      = 0
    lock      = asyncio.Lock()
    pending: list = []

    async def _process_one(w: dict):
        nonlocal processed, success, failed, review, skipped, dead

        try:
            # Hard outer timeout — if the site hangs beyond this, skip it
            result = await asyncio.wait_for(
                browser_manager.process_website(w["url"], profile),
                timeout=WEBSITE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            result = {
                "status": "SKIPPED_SLOW",
                "reason": f"Site exceeded {WEBSITE_TIMEOUT_S}s timeout — queued for retry",
            }

        await database.add_result(user_id, w["url"], result["status"], result["reason"])

        # Auto-enqueue retryable statuses only — never re-queue dead domains
        if result["status"] in database.RETRYABLE_STATUSES:
            await database.enqueue_retry(user_id, w["url"], attempt=1)

        async with lock:
            processed += 1
            status = result["status"]
            if status == "SUCCESS":
                success += 1
            elif status == "SKIPPED_SLOW":
                skipped += 1
            elif status in database.DEAD_STATUSES:
                dead += 1
            elif status in {"FAILED", "ERROR", "TIMEOUT", "BLOCKED"}:
                failed += 1
            else:
                review += 1

    async def _update_progress():
        lines = [
            f"🚀 Processing...\n",
            f"Total: {total}",
            f"Processed: {processed}\n",
            f"✅ Success: {success}",
            f"❌ Failed: {failed}",
            f"⚠️ Review: {review}",
        ]
        if skipped:
            lines.append(f"⏩ Too slow (re-queued): {skipped}")
        if dead:
            lines.append(f"🪦 Permanently dead: {dead}")
        try:
            await context.bot.edit_message_text(
                "\n".join(lines),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboards.get_processing_keyboard(),
            )
        except Exception:
            pass   # Telegram edit-too-fast errors; safe to ignore

    try:
        semaphore = asyncio.Semaphore(MAX_TABS_PER_USER)

        async def _guarded(w):
            async with semaphore:
                await _process_one(w)

        pending   = [asyncio.ensure_future(_guarded(w)) for w in websites]
        completed = 0
        for coro in asyncio.as_completed(pending):
            await coro
            completed += 1
            if completed % 5 == 0 or completed == total:
                await _update_progress()

        # Final summary
        lines = [
            f"✅ Processing Complete\n",
            f"Total: {total}",
            f"Processed: {processed}\n",
            f"✅ Success: {success}",
            f"❌ Failed: {failed}",
            f"⚠️ Review: {review}",
        ]
        if skipped:
            lines.append(f"⏩ Too slow (re-queued for retry): {skipped}")
        if dead:
            lines.append(f"🪦 Permanently dead (no retry): {dead}")

        await context.bot.edit_message_text(
            "\n".join(lines),
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboards.get_back_home_keyboard(),
        )

    except asyncio.CancelledError:
        for f in pending:
            f.cancel()
        stop_text = (
            f"🛑 Processing stopped.\n\n"
            f"Processed: {processed}\n"
            f"Remaining: {total - processed}"
        )
        try:
            await context.bot.edit_message_text(
                stop_text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboards.get_back_home_keyboard(),
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"Unexpected error in processing for user {user_id}: {e}")
        try:
            await context.bot.edit_message_text(
                f"❌ Unexpected error: {str(e)[:200]}",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboards.get_back_home_keyboard(),
            )
        except Exception:
            pass

    finally:
        idle_worker.mark_job_finished(user_id)
        active_tasks.pop(user_id, None)


# ── Stop ──────────────────────────────────────────────────────────────────────

async def process_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    task    = active_tasks.get(user_id)

    if task and not task.done():
        task.cancel()
        # The CancelledError handler inside _run_processing will edit the message.
    else:
        await query.edit_message_text(
            "No active processing task found.",
            reply_markup=keyboards.get_back_home_keyboard(),
        )
