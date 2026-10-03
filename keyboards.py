from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import math

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("👤 My Profile",  callback_data="menu_profile"),
         InlineKeyboardButton("🌐 My Websites", callback_data="menu_websites")],
        [InlineKeyboardButton("💬 My Message",  callback_data="menu_message"),
         InlineKeyboardButton("⏰ Schedule",    callback_data="menu_schedule")],
        [InlineKeyboardButton("🚀 Start",       callback_data="menu_start"),
         InlineKeyboardButton("📊 Dashboard",   callback_data="menu_dashboard")],
        [InlineKeyboardButton("📋 Results",     callback_data="menu_results"),
         InlineKeyboardButton("⚙️ Settings",   callback_data="menu_settings")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("➕ Add User", callback_data="admin_add_user"),
         InlineKeyboardButton("➖ Remove User", callback_data="admin_remove_user")],
        [InlineKeyboardButton("📋 Authorized Users", callback_data="admin_users")],
        [InlineKeyboardButton("🏠 User Bot", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_profile_keyboard():
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Profile", callback_data="profile_edit"),
         InlineKeyboardButton("🗑️ Clear Profile", callback_data="profile_clear")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_websites_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Add Website", callback_data="websites_add"),
         InlineKeyboardButton("📄 Upload TXT", callback_data="websites_upload")],
        [InlineKeyboardButton("📋 View Websites", callback_data="websites_view"),
         InlineKeyboardButton("🗑️ Remove Website", callback_data="websites_remove")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_message_keyboard():
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Message", callback_data="message_edit"),
         InlineKeyboardButton("🗑️ Clear Message", callback_data="message_clear")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_start_confirm_keyboard():
    keyboard = [
        [InlineKeyboardButton("▶️ Start", callback_data="process_start")],
        [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_processing_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛑 Stop", callback_data="process_stop")],
        [InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_results_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Successful", callback_data="results_success"),
         InlineKeyboardButton("❌ Failed", callback_data="results_failed")],
        [InlineKeyboardButton("⚠️ Problems", callback_data="results_problems"),
         InlineKeyboardButton("📜 History", callback_data="results_history")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔔 Notifications", callback_data="settings_notifications")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_home_keyboard(back_callback="menu_main"):
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
         InlineKeyboardButton("🏠 Home", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_pagination_keyboard(items, page, total_pages, callback_prefix):
    keyboard = []
    
    # Add items (e.g., users or websites)
    for item in items:
        keyboard.append([InlineKeyboardButton(item['text'], callback_data=f"{callback_prefix}_{item['id']}")])
    
    # Add pagination controls
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{callback_prefix}page_{page-1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{callback_prefix}page_{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)
