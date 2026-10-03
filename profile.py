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

async def profile_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the user to confirm before wiping the profile."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ Clear Profile\n\nThis will erase all your profile fields (name, email, phone, etc.).\n\nAre you sure?",
        reply_markup=keyboards.get_confirm_keyboard("profile_clear_do", "menu_profile"),
    )

async def profile_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually clear the profile after confirmation."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await database.clear_user_profile(user_id)
    
    await query.edit_message_text("✅ Profile cleared.", reply_markup=keyboards.get_back_home_keyboard("menu_profile"))

async def profile_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['state'] = 'PROFILE_EDIT_FULL_NAME'
    context.user_data['profile_cancel_target'] = 'menu_profile'
    
    await query.edit_message_text(
        "✏️ Edit Profile (1/7)\n\nWhat's your full name?\n\n(Send a value, /skip to leave unchanged, or cancel below)",
        reply_markup=keyboards.get_cancel_keyboard("cancel_profile_edit"),
    )

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
    context.user_data['profile_cancel_target'] = 'menu_message'
    
    await query.edit_message_text(
        "✏️ Edit Message\n\nSend the message you want submitted in contact forms.\n\n(/skip to leave unchanged, or cancel below)",
        reply_markup=keyboards.get_cancel_keyboard("cancel_message_edit"),
    )

async def message_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the user to confirm before wiping the message."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ Clear Message\n\nThis will erase your saved contact-form message.\n\nAre you sure?",
        reply_markup=keyboards.get_confirm_keyboard("message_clear_do", "menu_message"),
    )

async def message_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually clear the message after confirmation."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await database.update_user_profile(user_id, message=None)
    
    await query.edit_message_text("✅ Message cleared.", reply_markup=keyboards.get_back_home_keyboard("menu_message"))


# Step labels for the progress indicator shown to the user
PROFILE_FIELDS = [
    ('PROFILE_EDIT_FULL_NAME',  'full_name', "What's your first name?",             'PROFILE_EDIT_FIRST_NAME',  2, 7),
    ('PROFILE_EDIT_FIRST_NAME', 'first_name', "What's your last name?",             'PROFILE_EDIT_LAST_NAME',   3, 7),
    ('PROFILE_EDIT_LAST_NAME',  'last_name',  "What's your email?",                 'PROFILE_EDIT_EMAIL',       4, 7),
    ('PROFILE_EDIT_EMAIL',      'email',      "What's your phone number?",          'PROFILE_EDIT_PHONE',       5, 7),
    ('PROFILE_EDIT_PHONE',      'phone',      "What's your company name?",          'PROFILE_EDIT_COMPANY',     6, 7),
    ('PROFILE_EDIT_COMPANY',    'company',    "What's your website URL?",           'PROFILE_EDIT_WEBSITE',     7, 7),
    ('PROFILE_EDIT_WEBSITE',    'website',    "What's the subject of your inquiry?", 'PROFILE_EDIT_SUBJECT',    None, None),
    ('PROFILE_EDIT_SUBJECT',    'subject',    "Profile edit complete!",             None,                       None, None),
]

async def handle_profile_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if not state or not state.startswith('PROFILE_EDIT_'):
        return False
        
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # ── Message field ──────────────────────────────────────────────────────────
    if state == 'PROFILE_EDIT_MESSAGE':
        if text.lower() != '/skip':
            await database.update_user_profile(user_id, message=text)
        context.user_data['state'] = None
        await update.message.reply_text(
            "✅ Message saved.",
            reply_markup=keyboards.get_back_home_keyboard("menu_message"),
        )
        return True

    # ── Profile fields ─────────────────────────────────────────────────────────
    for (current_state, db_field, next_prompt, next_state, step_num, total_steps) in PROFILE_FIELDS:
        if state != current_state:
            continue

        if text.lower() != '/skip':
            await database.update_user_profile(user_id, **{db_field: text})

        if next_state:
            context.user_data['state'] = next_state
            step_label = f" ({step_num}/{total_steps})" if step_num else ""
            await update.message.reply_text(
                f"✏️ Edit Profile{step_label}\n\n{next_prompt}\n\n(Send a value, /skip to leave unchanged, or cancel below)",
                reply_markup=keyboards.get_cancel_keyboard("cancel_profile_edit"),
            )
        else:
            context.user_data['state'] = None
            await update.message.reply_text(
                "✅ Profile update complete.",
                reply_markup=keyboards.get_back_home_keyboard("menu_profile"),
            )
        return True

    return False
