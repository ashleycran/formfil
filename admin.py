from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards
from config import ADMIN_TELEGRAM_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    if user_id != ADMIN_TELEGRAM_ID:
        if query:
            await query.edit_message_text("🔒 Access Denied.")
        else:
            await update.message.reply_text("🔒 Access Denied.")
        return

    text = "🛠️ Admin Panel\n\nWelcome, Admin."
    reply_markup = keyboards.get_admin_menu_keyboard()
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    users = await database.get_authorized_users()
    text = f"👥 Authorized Users\n\nTotal: {len(users)}"
    
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(f"[{user['telegram_id']}]", callback_data=f"admin_view_user_{user['telegram_id']}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_admin")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_view_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.split('_')[3])
    
    text = f"👤 User\n\nTelegram ID:\n{target_id}\n\nStatus:\n🟢 Authorized\n"
    
    keyboard = [
        [InlineKeyboardButton("➖ Remove Access", callback_data=f"admin_confirm_remove_{target_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_users"),
         InlineKeyboardButton("🏠 Admin Panel", callback_data="menu_admin")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_user_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = "👤 Add User\n\nPlease send the Telegram ID\nof the person you want to authorize."
    
    context.user_data['state'] = 'WAITING_FOR_ADMIN_ADD_USER'
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_confirm_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.split('_')[3])
    
    text = f"⚠️ Remove Access?\n\nTelegram ID:\n{target_id}"
    
    keyboard = [
        [InlineKeyboardButton("✅ Remove Access", callback_data=f"admin_do_remove_{target_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_view_user_{target_id}")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_do_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.split('_')[3])
    await database.unauthorize_user(target_id)
    
    text = f"✅ Access Removed\n\nTelegram ID:\n{target_id}"
    keyboard = [[InlineKeyboardButton("🏠 Admin Panel", callback_data="menu_admin")]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_purge_dead_retries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin action: remove retry queue entries for permanently dead URLs."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    if user_id != ADMIN_TELEGRAM_ID:
        await query.edit_message_text("🔒 Access Denied.")
        return

    count = await database.purge_dead_retries()
    text = (
        f"🧹 Retry Queue Cleaned\n\n"
        f"Removed {count} stale entr{'y' if count == 1 else 'ies'} for permanently "
        f"dead URLs (CAPTCHA, NO_FORM, DEAD, BLOCKED, MISSING_INFORMATION).\n\n"
        f"These will no longer waste processing time."
    )
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    if user_id != ADMIN_TELEGRAM_ID:
        return False
        
    if state == 'WAITING_FOR_ADMIN_ADD_USER':
        try:
            target_id = int(update.message.text.strip())
            await database.authorize_user(target_id)
            
            text = f"✅ User Authorized\n\nTelegram ID:\n{target_id}"
            keyboard = [[InlineKeyboardButton("🏠 Admin Panel", callback_data="menu_admin")]]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        except ValueError:
            await update.message.reply_text("Invalid Telegram ID. It must be numeric.")
        
        context.user_data['state'] = None
        return True
    
    return False
