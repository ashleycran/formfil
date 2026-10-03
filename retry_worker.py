"""
retry_worker.py

Background loop that checks for due retries every 5 minutes.

When processor.py records a TIMEOUT / ERROR / FAILED result for a URL,
it also calls enqueue_retry(). This worker picks those up once the
cooldown has passed and re-processes them silently.

Retry behaviour:
  Attempt 1 → retry after 30 min
  Attempt 2 → retry after 60 min
  Attempt 3 → retry after 90 min  (final attempt, then removed from queue)
"""

import asyncio
import logging
import database
from browser import browser_manager
import idle_worker

logger = logging.getLogger(__name__)

RETRY_CHECK_INTERVAL = 300   # check every 5 minutes


async def retry_worker_loop(bot):
    logger.info("[retry_worker] Started.")

    while True:
        try:
            if not idle_worker.is_busy():
                await _process_due_retries(bot)
        except asyncio.CancelledError:
            logger.info("[retry_worker] Shutting down.")
            return
        except Exception as e:
            logger.warning(f"[retry_worker] Error: {e}")

        await asyncio.sleep(RETRY_CHECK_INTERVAL)


async def _process_due_retries(bot):
    # Get all users who have at least one due retry
    async with database._pool.acquire() as conn:
        user_ids = await conn.fetch(
            "SELECT DISTINCT telegram_id FROM retry_queue WHERE retry_after <= NOW()"
        )

    for row in user_ids:
        uid = row["telegram_id"]

        # Don't retry if user is actively processing
        if uid in idle_worker._active_jobs:
            continue

        due = await database.get_due_retries(uid)
        if not due:
            continue

        profile = await database.get_user_profile(uid)

        try:
            notify_msg = await bot.send_message(
                chat_id=uid,
                text=(
                    f"🔄 Retrying {len(due)} previously failed website(s)...\n\n"
                    f"I'll let you know when done."
                ),
            )
        except Exception:
            notify_msg = None

        results_summary = {"success": 0, "failed": 0}

        for item in due:
            url     = item["url"]
            attempt = item["attempt"]

            result = await browser_manager.process_website(url, profile)
            await database.add_result(uid, url, result["status"], result["reason"])

            if result["status"] == "SUCCESS":
                await database.remove_retry(uid, url)
                results_summary["success"] += 1
            elif attempt < database.MAX_RETRY_ATTEMPTS:
                # Schedule another retry with increased cooldown
                await database.enqueue_retry(uid, url, attempt + 1)
                results_summary["failed"] += 1
            else:
                # Max attempts reached — give up
                await database.remove_retry(uid, url)
                results_summary["failed"] += 1

        # Notify user of retry results
        if notify_msg:
            try:
                await bot.edit_message_text(
                    chat_id=notify_msg.chat_id,
                    message_id=notify_msg.message_id,
                    text=(
                        f"🔄 Retry Complete\n\n"
                        f"✅ Success: {results_summary['success']}\n"
                        f"❌ Still failing: {results_summary['failed']}"
                    ),
                )
            except Exception:
                pass
