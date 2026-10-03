"""
keep_alive.py

Two mechanisms working together to guarantee the Render free instance
never reaches the 15-minute inactivity threshold:

1. HTTP server  — serves GET /health so external pingers (UptimeRobot etc.)
                  have a live endpoint to hit.

2. Self-pinger  — the bot pings its OWN /health endpoint every 4 minutes
                  from inside the process. Even if UptimeRobot misses a beat
                  this keeps the inactivity timer from ever expiring.

Both run as asyncio background tasks and never block the Telegram loop.
"""

import asyncio
import os
import logging
import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

PORT     = int(os.getenv("PORT", 10000))
APP_URL  = os.getenv("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")
# Ping every 4 minutes  (Render's sleep threshold is 15 min)
PING_INTERVAL = int(os.getenv("KEEP_ALIVE_PING_INTERVAL", 240))

_runner: web.AppRunner | None = None


# ── HTTP health server ────────────────────────────────────────────────────────

async def _health(request: web.Request) -> web.Response:
    return web.Response(text="OK", content_type="text/plain")


async def _start_http_server():
    global _runner
    app = web.Application()
    app.router.add_get("/",       _health)
    app.router.add_get("/health", _health)

    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"[keep_alive] HTTP server live on port {PORT}")


# ── Self-pinger ───────────────────────────────────────────────────────────────

async def _self_ping_loop():
    """
    Continuously ping our own /health endpoint so Render's inactivity
    clock never reaches 15 minutes, regardless of external pingers.
    """
    # Give the HTTP server a moment to be ready
    await asyncio.sleep(10)

    health_url = f"{APP_URL.rstrip('/')}/health"
    logger.info(f"[keep_alive] Self-pinger started → {health_url} every {PING_INTERVAL}s")

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    logger.debug(f"[keep_alive] Self-ping {resp.status}")
        except Exception as e:
            logger.warning(f"[keep_alive] Self-ping failed: {e}")

        await asyncio.sleep(PING_INTERVAL)


# ── Public entry point ────────────────────────────────────────────────────────

async def start_keep_alive():
    await _start_http_server()
    asyncio.create_task(_self_ping_loop(), name="keep_alive_pinger")
