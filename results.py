import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards

# Statuses considered "failed" for export purposes
FAILED_STATUSES = ["FAILED", "ERROR", "TIMEOUT", "CAPTCHA", "NO_FORM", "MISSING_INFORMATION", "BLOCKED"]

async def results_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    summary = await database.get_results_summary(user_id)
    total = sum(summary.values())
    
    success = summary.get('SUCCESS', 0)
    failed = summary.get('FAILED', 0)
    captcha = summary.get('CAPTCHA', 0)
    no_form = summary.get('NO_FORM', 0)
    
    text = f"📊 Latest Results\n\nTotal: {total}\n\n"
    text += f"✅ Success: {success}\n"
    text += f"❌ Failed: {failed}\n"
    if captcha > 0:
        text += f"⚠️ CAPTCHA: {captcha}\n"
    if no_form > 0:
        text += f"⚠️ No Form: {no_form}\n"
        
    await query.edit_message_text(text, reply_markup=keyboards.get_results_keyboard())

async def results_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    results = await database.get_recent_results(user_id, limit=20)
    
    if not results:
        await query.edit_message_text("No results yet.", reply_markup=keyboards.get_back_home_keyboard("menu_results"))
        return
        
    text = "📜 Recent History\n\n"
    for r in results:
        status_icon = "✅" if r['status'] == 'SUCCESS' else "❌" if r['status'] == 'FAILED' else "⚠️"
        line = f"{status_icon} {r['url']}\n"
        if r['reason']:
            line += f"   {r['reason']}\n"
        if len(text) + len(line) > 3800:
            text += "…(truncated — too many results to display)"
            break
        text += line
            
    await query.edit_message_text(text, reply_markup=keyboards.get_back_home_keyboard("menu_results"), disable_web_page_preview=True)

async def results_export_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a .txt file containing all failed URLs with their reason."""
    query = update.callback_query
    await query.answer("Preparing export…")

    user_id = update.effective_user.id
    rows = await database.get_results_by_status(user_id, FAILED_STATUSES)

    if not rows:
        await query.edit_message_text(
            "No failed results to export.",
            reply_markup=keyboards.get_back_home_keyboard("menu_results"),
        )
        return

    # Build file content
    lines = [f"Failed Results Export — {len(rows)} entries\n{'='*40}\n"]
    for r in rows:
        lines.append(f"{r['status']}: {r['url']}")
        if r['reason']:
            lines.append(f"  Reason: {r['reason']}")
        lines.append("")

    content = "\n".join(lines).encode("utf-8")
    file_obj = io.BytesIO(content)
    file_obj.name = "failed_results.txt"

    await query.message.reply_document(
        document=file_obj,
        filename="failed_results.txt",
        caption=f"📄 {len(rows)} failed URL(s) exported.",
    )


async def results_export_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a .txt file containing all successful URLs."""
    query = update.callback_query
    await query.answer("Preparing export…")

    user_id = update.effective_user.id
    rows = await database.get_results_by_status(user_id, ["SUCCESS"])

    if not rows:
        await query.edit_message_text(
            "No successful results to export.",
            reply_markup=keyboards.get_back_home_keyboard("menu_results"),
        )
        return

    lines = [f"Successful Results Export — {len(rows)} entries\n{'='*40}\n"]
    for r in rows:
        lines.append(r['url'])

    content = "\n".join(lines).encode("utf-8")
    file_obj = io.BytesIO(content)
    file_obj.name = "successful_results.txt"

    await query.message.reply_document(
        document=file_obj,
        filename="successful_results.txt",
        caption=f"✅ {len(rows)} successful URL(s) exported.",
    )
