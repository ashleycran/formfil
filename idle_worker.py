"""
idle_worker.py

A background coroutine that keeps the bot "warm" when no user jobs
are running.

Behaviour:
- When 0 users are actively processing → runs a lightweight DB heartbeat
  (SELECT 1) every IDLE_HEARTBEAT_INTERVAL seconds.
- The moment any user starts a processing job → the worker sleeps
  immediately and silently.
- When the last job finishes → the worker resumes automatically.

This keeps the asyncio event loop ticking, the Neon database connection
pool warm, and gives Render something to measure even during quiet periods.

The idle check interval is intentionally short (20 s) so the loop stays
responsive. The DB ping is a single cheap query.
"""

import asyncio
import logging
import os
import database

logger = logging.getLogger(__name__)

# How often (seconds) to ping the DB when idle
IDLE_HEARTBEAT_INTERVAL = int(os.getenv("IDLE_HEARTBEAT_INTERVAL", 20))

# Shared reference — processor.py imports this to signal busy / free
_active_jobs: set[int] = set()   # holds telegram_id of running jobs


def mark_job_started(telegram_id: int):
    """Call when a user processing job begins."""
    _active_jobs.add(telegram_id)


def mark_job_finished(telegram_id: int):
    """Call when a user processing job ends (success, error, or cancelled)."""
    _active_jobs.discard(telegram_id)


def is_busy() -> bool:
    return len(_active_jobs) > 0


async def idle_worker_loop():
    """
    Background loop. Starts automatically from main.py post_init.
    Runs forever; the task is cancelled only when the bot shuts down.
    """
    logger.info("[idle_worker] Started.")
    idle_ticks = 0

    while True:
        try:
            if is_busy():
                # One or more users are processing — back off completely
                idle_ticks = 0
                await asyncio.sleep(5)
                continue

            # No active jobs — do a lightweight heartbeat
            await _db_heartbeat()
            idle_ticks += 1

            if idle_ticks % 30 == 0:          # log every ~10 minutes
                logger.info(f"[idle_worker] Heartbeat #{idle_ticks} — bot is idle and healthy.")

        except asyncio.CancelledError:
            logger.info("[idle_worker] Shutting down.")
            return
        except Exception as e:
            logger.warning(f"[idle_worker] Heartbeat error: {e}")

        await asyncio.sleep(IDLE_HEARTBEAT_INTERVAL)


async def _db_heartbeat():
    """
    Cheapest possible query — just checks the DB connection is alive.
    Uses the existing asyncpg pool from database.py.
    """
    try:
        async with database._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        logger.warning(f"[idle_worker] DB heartbeat failed: {e}")
