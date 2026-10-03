from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards

async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    profile = await database.get_user_profile(user_id)
    
    text = "👤 My Profile\n\n"
    text += f"Full Name: {profile.get('full_name') or 'Not set'}\n"
    text += f"First Name: {profile.get('first_name') or 'Not set'}\n"
    text += f"Last Name: {profile.get('last_name') or 'Not set'}\n"
    text += f"Email: {profile.get('email') or 'Not set'}\n"
    text += f"Phone: {profile.get('phone') or 'Not set'}\n"
    text += f"Company: {profile.get('company') or 'Not set'}\n"
    text += f"Website: {profile.get('website') or 'Not set'}\n"
    text += f"Subject: {profile.get('subject') or 'Not set'}\n"
    
    await query.edit_message_text(text, reply_markup=keyboards.get_profile_keyboard())

async def profile_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await database.clear_user_profile(user_id)
    
    await query.edit_message_text("✅ Profile cleared.", reply_markup=keyboards.get_back_home_keyboard("menu_profile"))

async def profile_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['state'] = 'PROFILE_EDIT_FULL_NAME'
    
    text = "What's your full name?\n\n(Send the name, or type /skip to skip this field)"
    await query.edit_message_text(text)

async def message_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    profile = await database.get_user_profile(user_id)
    msg = profile.get('message')
    
    if msg:
        text = f"💬 Current Message\n\n{msg}"
    else:
        text = "💬 Current Message\n\nNot set."
        
    await query.edit_message_text(text, reply_markup=keyboards.get_message_keyboard())

async def message_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['state'] = 'PROFILE_EDIT_MESSAGE'
    
    text = "Please send the message you want to submit in contact forms.\n\n(Send the message, or type /skip to skip)"
    await query.edit_message_text(text)

async def message_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await database.update_user_profile(user_id, message=None)
    
    await query.edit_message_text("✅ Message cleared.", reply_markup=keyboards.get_back_home_keyboard("menu_message"))


PROFILE_FIELDS = [
    ('PROFILE_EDIT_FULL_NAME', 'full_name', "What's your first name?", 'PROFILE_EDIT_FIRST_NAME'),
    ('PROFILE_EDIT_FIRST_NAME', 'first_name', "What's your last name?", 'PROFILE_EDIT_LAST_NAME'),
    ('PROFILE_EDIT_LAST_NAME', 'last_name', "What's your email?", 'PROFILE_EDIT_EMAIL'),
    ('PROFILE_EDIT_EMAIL', 'email', "What's your phone number?", 'PROFILE_EDIT_PHONE'),
    ('PROFILE_EDIT_PHONE', 'phone', "What's your company name?", 'PROFILE_EDIT_COMPANY'),
    ('PROFILE_EDIT_COMPANY', 'company', "What's your website URL?", 'PROFILE_EDIT_WEBSITE'),
    ('PROFILE_EDIT_WEBSITE', 'website', "What's the subject of your inquiry?", 'PROFILE_EDIT_SUBJECT'),
    ('PROFILE_EDIT_SUBJECT', 'subject', "Profile edit complete!", None),
]

async def handle_profile_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if not state or not state.startswith('PROFILE_EDIT_'):
        return False
        
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if state == 'PROFILE_EDIT_MESSAGE':
        if text.lower() != '/skip':
            await database.update_user_profile(user_id, message=text)
        context.user_data['state'] = None
        await update.message.reply_text("✅ Message saved.", reply_markup=keyboards.get_back_home_keyboard("menu_message"))
        return True
        
    for i, (current_state, db_field, next_prompt, next_state) in enumerate(PROFILE_FIELDS):
        if state == current_state:
            if text.lower() != '/skip':
                await database.update_user_profile(user_id, **{db_field: text})
                
            if next_state:
                context.user_data['state'] = next_state
                await update.message.reply_text(f"{next_prompt}\n\n(Send response, or type /skip)")
            else:
                context.user_data['state'] = None
                await update.message.reply_text("✅ Profile update complete.", reply_markup=keyboards.get_back_home_keyboard("menu_profile"))
            return True
            
    return False
