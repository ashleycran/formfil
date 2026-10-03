"""
dashboard.py

Renders a rich, emoji-driven stats dashboard inside Telegram.
Uses a single DB call per render (get_dashboard_stats).
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import datetime


def _progress_bar(value: int, total: int, width: int = 10) -> str:
    """Render a simple block progress bar, e.g.  ████████░░  80%"""
    if total == 0:
        return "░" * width + "  0%"
    filled = round((value / total) * width)
    bar    = "█" * filled + "░" * (width - filled)
    pct    = round((value / total) * 100)
    return f"{bar}  {pct}%"


def _fmt_time(dt: datetime.datetime | None) -> str:
    if dt is None:
        return "Never"
    return dt.strftime("%d %b %Y  %H:%M UTC")


def _status_line(label: str, count: int, total: int, emoji: str) -> str:
    bar = _progress_bar(count, total)
    return f"{emoji} {label}: {count}\n   {bar}\n"


def _build_dashboard_text(stats: dict) -> str:
    at   = stats["all_time"]
    td   = stats["today"]

    # ── All-time totals ──────────────────────────────────────────────────────
    at_success = at.get("SUCCESS", 0)
    at_failed  = at.get("FAILED",  0)  + at.get("ERROR", 0) + at.get("BLOCKED", 0)
    at_timeout = at.get("TIMEOUT", 0)
    at_captcha = at.get("CAPTCHA", 0)
    at_noform  = at.get("NO_FORM", 0)
    at_missing = at.get("MISSING_INFORMATION", 0)
    at_total   = sum(at.values())

    # ── Today totals ─────────────────────────────────────────────────────────
    td_success = td.get("SUCCESS", 0)
    td_failed  = td.get("FAILED",  0)  + td.get("ERROR", 0)
    td_total   = sum(td.values())

    # ── Schedule ─────────────────────────────────────────────────────────────
    sched = stats["schedule"]
    if sched and sched.get("active"):
        sched_str = f"🟢 Daily at {sched['hour']:02d}:{sched['minute']:02d} UTC"
    else:
        sched_str = "⚫ Not set"

    # ── Success rate ─────────────────────────────────────────────────────────
    rate_bar = _progress_bar(at_success, at_total)

    lines = [
        "📊 Your Dashboard",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🗂  Websites in list:",
        f"   {stats['website_count']}",
        "",
        "📅 Today",
        f"   Processed : {td_total}",
        f"   ✅ Success : {td_success}",
        f"   ❌ Failed  : {td_failed}",
        "",
        "📈 All Time",
        f"   Total processed : {at_total}",
        "",
        _status_line("Success",  at_success, at_total, "✅"),
        _status_line("Failed",   at_failed,  at_total, "❌"),
        _status_line("CAPTCHA",  at_captcha, at_total, "🔒"),
        _status_line("Timeout",  at_timeout, at_total, "⏱"),
        _status_line("No Form",  at_noform,  at_total, "🔍"),
        _status_line("Missing",  at_missing, at_total, "⚠️"),
        "📊 Overall success rate:",
        f"   {rate_bar}",
        "",
        "🔄 Retry queue pending:",
        f"   {stats['retry_pending']} URL(s)",
        "",
        "⏰ Auto-schedule:",
        f"   {sched_str}",
        "",
        "🕐 Last run:",
        f"   {_fmt_time(stats['last_run'])}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(lines)


def _dashboard_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh",      callback_data="dashboard_refresh"),
         InlineKeyboardButton("📋 Full History", callback_data="results_history")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home",  callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    stats   = await database.get_dashboard_stats(user_id)
    text    = _build_dashboard_text(stats)

    await query.edit_message_text(
        text,
        reply_markup=_dashboard_keyboard(),
    )
