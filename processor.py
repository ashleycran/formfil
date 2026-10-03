import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import database
import keyboards
from browser import browser_manager
from config import MAX_TABS_PER_USER
import idle_worker

logger = logging.getLogger(__name__)

# Per-user asyncio Task objects
active_tasks: dict[int, asyncio.Task] = {}

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

    text = (
        f"🚀 Ready\n\n"
        f"Websites: {len(websites)}\n"
        f"Message: {msg_line[:60] if profile.get('message') else 'Not configured'}\n\n"
        f"The bot will inspect each website and fill the available "
        f"contact fields on each form."
    )
    await query.edit_message_text(text, reply_markup=keyboards.get_start_confirm_keyboard())


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

    msg = await query.edit_message_text(
        "🚀 Processing starting...",
        reply_markup=keyboards.get_processing_keyboard(),
    )

    idle_worker.mark_job_started(user_id)   # pause idle heartbeat
    task = asyncio.create_task(
        _run_processing(user_id, websites, profile, context, msg.chat_id, msg.message_id)
    )
    active_tasks[user_id] = task


async def _run_processing(user_id: int, websites: list, profile: dict,
                          context, chat_id: int, message_id: int):
    """
    Process all websites for one user in parallel batches.
    Each batch has at most MAX_TABS_PER_USER concurrent requests.
    The global semaphore in browser.py caps total tabs across all users.
    """
    total     = len(websites)
    processed = 0
    success   = 0
    failed    = 0
    review    = 0
    lock      = asyncio.Lock()   # protects the counters above

    async def _process_one(w: dict):
        nonlocal processed, success, failed, review
        result = await browser_manager.process_website(w["url"], profile)
        await database.add_result(user_id, w["url"], result["status"], result["reason"])

        # Auto-enqueue for retry if applicable
        if result["status"] in database.RETRYABLE_STATUSES:
            await database.enqueue_retry(user_id, w["url"], attempt=1)

        async with lock:
            processed += 1
            if result["status"] == "SUCCESS":
                success += 1
            elif result["status"] in {"FAILED", "ERROR", "TIMEOUT", "BLOCKED"}:
                failed += 1
            else:
                review += 1

    async def _update_progress():
        text = (
            f"🚀 Processing...\n\n"
            f"Total: {total}\n"
            f"Processed: {processed}\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"⚠️ Review: {review}"
        )
        try:
            await context.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id,
                reply_markup=keyboards.get_processing_keyboard(),
            )
        except Exception:
            pass   # Telegram edit-too-fast errors; safe to ignore

    try:
        # ── Batch-parallel execution ──────────────────────────────────────
        semaphore = asyncio.Semaphore(MAX_TABS_PER_USER)

        async def _guarded(w):
            async with semaphore:
                await _process_one(w)

        pending   = [asyncio.ensure_future(_guarded(w)) for w in websites]
        completed = 0

        for coro in asyncio.as_completed(pending):
            await coro
            completed += 1
            # Update Telegram every 5 completions or on the last one
            if completed % 5 == 0 or completed == total:
                await _update_progress()

        # ── Done ─────────────────────────────────────────────────────────
        final_text = (
            f"✅ Processing Complete\n\n"
            f"Total: {total}\n"
            f"Processed: {processed}\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"⚠️ Review: {review}"
        )
        await context.bot.edit_message_text(
            final_text, chat_id=chat_id, message_id=message_id,
            reply_markup=keyboards.get_back_home_keyboard(),
        )

    except asyncio.CancelledError:
        # Cancel all still-running futures
        for f in pending:
            f.cancel()
        stop_text = (
            f"🛑 Processing stopped.\n\n"
            f"Processed: {processed}\n"
            f"Remaining: {total - processed}"
        )
        try:
            await context.bot.edit_message_text(
                stop_text, chat_id=chat_id, message_id=message_id,
                reply_markup=keyboards.get_back_home_keyboard(),
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"Unexpected error in processing for user {user_id}: {e}")
        try:
            await context.bot.edit_message_text(
                f"❌ Unexpected error: {str(e)[:200]}",
                chat_id=chat_id, message_id=message_id,
                reply_markup=keyboards.get_back_home_keyboard(),
            )
        except Exception:
            pass

    finally:
        idle_worker.mark_job_finished(user_id)  # resume idle heartbeat
        active_tasks.pop(user_id, None)


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
