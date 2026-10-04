"""
scheduler.py

Runs a background loop that fires every 60 seconds, checks all active
user schedules, and launches processing for any user whose scheduled
hour:minute has just arrived (UTC).

Users manage their schedule via buttons — no commands needed.
"""

import asyncio
import logging
import datetime
import database
from processor import _run_processing
import idle_worker

logger = logging.getLogger(__name__)

# ── Scheduler context shim ────────────────────────────────────────────────────

class _SchedulerContext:
    """
    Minimal context object passed to _run_processing when triggered by the
    scheduler (no real telegram.ext.CallbackContext is available).

    Exposes the attributes _run_processing actually uses:
      - bot          : the Telegram Bot instance
      - user_data    : empty dict (no per-user state needed for scheduled runs)
      - chat_data    : empty dict

    If _run_processing ever needs more context attributes, add them here
    rather than silently failing with AttributeError at runtime.
    """

    def __init__(self, bot):
        self.bot       = bot
        self.user_data: dict = {}
        self.chat_data: dict = {}

# Tracks which users had their schedule fired this minute to prevent
# double-firing if the loop ticks twice in the same minute.
_fired_this_minute: set[int] = set()


async def scheduler_loop(bot):
    """
    Runs forever. Checks schedules every 60 seconds.
    bot: the telegram Bot instance passed from main.py post_init.
    """
    logger.info("[scheduler] Started.")

    while True:
        try:
            await _check_schedules(bot)
        except asyncio.CancelledError:
            logger.info("[scheduler] Shutting down.")
            return
        except Exception as e:
            logger.warning(f"[scheduler] Unexpected error: {e}")

        await asyncio.sleep(60)


async def _check_schedules(bot):
    now_utc = datetime.datetime.utcnow()
    current_hour   = now_utc.hour
    current_minute = now_utc.minute

    # Reset the dedup set at the start of each new minute
    if not hasattr(_check_schedules, "_last_minute"):
        _check_schedules._last_minute = -1

    if current_minute != _check_schedules._last_minute:
        _fired_this_minute.clear()
        _check_schedules._last_minute = current_minute

    schedules = await database.get_all_active_schedules()

    for sched in schedules:
        uid = sched["telegram_id"]

        if uid in _fired_this_minute:
            continue

        if sched["hour"] != current_hour or sched["minute"] != current_minute:
            continue

        # Prevent double-fire
        _fired_this_minute.add(uid)

        # Don't launch if user already has an active job
        if idle_worker.is_busy() and uid in idle_worker._active_jobs:
            logger.info(f"[scheduler] User {uid} already processing — skipping scheduled run.")
            continue

        logger.info(f"[scheduler] Triggering scheduled run for user {uid}")

        try:
            # Notify the user
            msg = await bot.send_message(
                chat_id=uid,
                text=(
                    "⏰ Scheduled run starting!\n\n"
                    "Your bot is now processing your websites automatically."
                ),
            )

            websites = await database.get_websites(uid)
            profile  = await database.get_user_profile(uid)

            if not websites:
                await bot.send_message(
                    chat_id=uid,
                    text="⚠️ Scheduled run skipped — no websites in your list.",
                )
                continue

            ctx = _SchedulerContext(bot)
            idle_worker.mark_job_started(uid)
            asyncio.create_task(
                _run_processing(uid, websites, profile, ctx, msg.chat_id, msg.message_id),
                name=f"scheduled_{uid}",
            )

        except Exception as e:
            logger.error(f"[scheduler] Failed to trigger run for user {uid}: {e}")
