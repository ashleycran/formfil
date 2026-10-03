from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards

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
        text += f"{status_icon} {r['url']}\n"
        if r['reason']:
            text += f"   {r['reason']}\n"
            
    await query.edit_message_text(text, reply_markup=keyboards.get_back_home_keyboard("menu_results"), disable_web_page_preview=True)
