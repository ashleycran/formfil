import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")

if ADMIN_TELEGRAM_ID:
    try:
        ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)
    except ValueError:
        pass

DATABASE_URL = os.getenv("DATABASE_URL")

# Fail fast with a clear message if critical env vars are missing
_missing = [k for k, v in {"BOT_TOKEN": BOT_TOKEN, "DATABASE_URL": DATABASE_URL}.items() if not v]
if _missing:
    raise EnvironmentError(f"Missing required environment variable(s): {', '.join(_missing)}")

# --- Concurrency tuning ---
# Render FREE  (512MB RAM):  MAX_GLOBAL_CONCURRENT_TABS=3, MAX_TABS_PER_USER=1
# Render Starter ($7, 512MB): keep 3-5 / 1-2
# Render Standard ($25, 2GB): 30 / 5
# Render Pro ($85, 8GB):      80 / 10
MAX_GLOBAL_CONCURRENT_TABS = int(os.getenv("MAX_GLOBAL_CONCURRENT_TABS", 3))
MAX_TABS_PER_USER          = int(os.getenv("MAX_TABS_PER_USER", 1))

# Per-website processing timeout in seconds (hard outer limit per site).
# If a site exceeds this, it is skipped and auto-enqueued for retry.
WEBSITE_TIMEOUT_S = int(os.getenv("WEBSITE_TIMEOUT_S", 45))

# Navigation timeout per page (ms) — Playwright-level inner timeout
PAGE_TIMEOUT_MS = int(os.getenv("PAGE_TIMEOUT_MS", 25000))

# How long to wait after clicking submit before checking result (ms)
POST_SUBMIT_WAIT_MS = int(os.getenv("POST_SUBMIT_WAIT_MS", 3000))
