from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
import database
import keyboards
from config import ADMIN_TELEGRAM_ID
import admin
import profile
import websites
import results
import processor
import schedule_handler
import dashboard

async def _build_home_text(user_id: int, first_name: str) -> str:
    """Generate the home screen text from live DB stats."""
    stats = await database.get_dashboard_stats(user_id)

    td          = stats["today"]
    td_total    = sum(td.values())
    td_success  = td.get("SUCCESS", 0)

    at          = stats["all_time"]
    at_total    = sum(at.values())
    at_success  = at.get("SUCCESS", 0)
    success_pct = round((at_success / at_total) * 100) if at_total else 0

    sched = stats["schedule"]
    if sched and sched.get("active"):
        sched_str = f"🟢 {sched['hour']:02d}:{sched['minute']:02d} UTC"
    else:
        sched_str = "⚫ Not set"

    retry = stats["retry_pending"]
    retry_str = f"🔄 {retry} pending" if retry else "✅ None"

    last_run = stats["last_run"]
    last_run_str = last_run.strftime("%d %b  %H:%M UTC") if last_run else "Never"

    return (
        f"👋 Welcome back, {first_name}!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 Websites loaded : {stats['website_count']}\n"
        f"⏰ Auto-schedule   : {sched_str}\n"
        f"🔄 Retry queue     : {retry_str}\n"
        f"🕐 Last run        : {last_run_str}\n\n"
        f"📅 Today\n"
        f"   Processed : {td_total}   ✅ {td_success}\n\n"
        f"📈 All Time\n"
        f"   Total : {at_total}   Success rate : {success_pct}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose an option below:"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    first_name = update.effective_user.first_name or "there"

    if not await database.is_user_authorized(user_id, ADMIN_TELEGRAM_ID):
        await update.message.reply_text(
            "🔒 Access Denied\n\nYou are not authorized to use this bot.\n\nPlease contact the administrator."
        )
        return

    if user_id == ADMIN_TELEGRAM_ID:
        await update.message.reply_text(
            "🛠️ Admin Panel\n\nWelcome, Admin.",
            reply_markup=keyboards.get_admin_menu_keyboard(),
        )
    else:
        text = await _build_home_text(user_id, first_name)
        await update.message.reply_text(text, reply_markup=keyboards.get_main_menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await database.is_user_authorized(user_id, ADMIN_TELEGRAM_ID):
        await query.answer("Access Denied", show_alert=True)
        return
        
    data = query.data
    
    # Main menu / Home
    if data == "menu_main":
        await query.answer()
        context.user_data["state"] = None
        if user_id == ADMIN_TELEGRAM_ID:
            await query.edit_message_text(
                "🛠️ Admin Panel\n\nWelcome, Admin.",
                reply_markup=keyboards.get_admin_menu_keyboard(),
            )
        else:
            first_name = update.effective_user.first_name or "there"
            text = await _build_home_text(user_id, first_name)
            await query.edit_message_text(text, reply_markup=keyboards.get_main_menu_keyboard())

    # Admin menu
    elif data == "menu_admin":
        await admin.admin_panel(update, context)
    elif data == "admin_users":
        await admin.admin_users_list(update, context)
    elif data == "admin_remove_user":
        await admin.admin_users_list(update, context)   # lists users; each has a Remove Access button
    elif data.startswith("admin_view_user_"):
        await admin.admin_view_user(update, context)
    elif data == "admin_add_user":
        await admin.admin_add_user_prompt(update, context)
    elif data.startswith("admin_confirm_remove_"):
        await admin.admin_confirm_remove(update, context)
    elif data == "admin_purge_retries":
        await admin.admin_purge_dead_retries(update, context)
        
    # Profile
    elif data == "menu_profile":
        await profile.profile_menu(update, context)
    elif data == "profile_clear":
        await profile.profile_clear_confirm(update, context)
    elif data == "profile_clear_do":
        await profile.profile_clear(update, context)
    elif data == "profile_edit":
        await profile.profile_edit_start(update, context)
    elif data == "cancel_profile_edit":
        await query.answer()
        context.user_data["state"] = None
        await profile.profile_menu(update, context)

    # Message
    elif data == "menu_message":
        await profile.message_menu(update, context)
    elif data == "message_clear":
        await profile.message_clear_confirm(update, context)
    elif data == "message_clear_do":
        await profile.message_clear(update, context)
    elif data == "message_edit":
        await profile.message_edit_start(update, context)
    elif data == "cancel_message_edit":
        await query.answer()
        context.user_data["state"] = None
        await profile.message_menu(update, context)

    # Websites
    elif data == "menu_websites":
        await websites.websites_menu(update, context)
    elif data == "websites_add":
        await websites.websites_add_prompt(update, context)
    elif data == "websites_upload":
        await websites.websites_upload_prompt(update, context)
    elif data == "websites_view":
        await websites.websites_view(update, context)
    elif data == "websites_remove":
        await websites.websites_remove_list(update, context)
    elif data.startswith("websites_delprompt_"):
        await websites.websites_delprompt(update, context)
    elif data.startswith("websites_dodelete_"):
        await websites.websites_dodelete(update, context)
    elif data == "cancel_websites_add":
        await query.answer()
        context.user_data["state"] = None
        await websites.websites_menu(update, context)
    elif data == "cancel_websites_upload":
        await query.answer()
        context.user_data["state"] = None
        await websites.websites_menu(update, context)
        
    # Processing
    elif data == "menu_start":
        await processor.start_prompt(update, context)
    elif data == "process_start":
        await processor.process_start(update, context)
    elif data == "process_stop":
        await processor.process_stop(update, context)
        
    # Results
    elif data == "menu_results":
        await results.results_menu(update, context)
    elif data == "results_history":
        await results.results_history(update, context)
    elif data in ["results_success", "results_failed", "results_problems"]:
        # Simplified for now, just show history
        await results.results_history(update, context)
    elif data == "results_export_failed":
        await results.results_export_failed(update, context)
    elif data == "results_export_success":
        await results.results_export_success(update, context)
        
    # Dashboard
    elif data in ("menu_dashboard", "dashboard_refresh"):
        await dashboard.show_dashboard(update, context)

    # Schedule
    elif data == "menu_schedule":
        await schedule_handler.schedule_menu(update, context)
    elif data == "schedule_set":
        await schedule_handler.schedule_set_prompt(update, context)
    elif data == "schedule_remove":
        await schedule_handler.schedule_remove(update, context)
    elif data == "cancel_schedule_set":
        await query.answer()
        context.user_data["state"] = None
        context.user_data.pop("schedule_hour", None)
        await schedule_handler.schedule_menu(update, context)

    # Settings
    elif data == "menu_settings":
        await query.answer()
        await query.edit_message_text(
            "⚙️ Settings\n\n"
            "No additional settings are available yet.\n"
            "Features like notification preferences will appear here when ready.",
            reply_markup=keyboards.get_back_home_keyboard(),
        )
        
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await database.is_user_authorized(user_id, ADMIN_TELEGRAM_ID):
        return

    if await admin.handle_admin_text(update, context):
        return
    if await schedule_handler.handle_schedule_text(update, context):
        return
    if await profile.handle_profile_text(update, context):
        return
    if await websites.handle_websites_text(update, context):
        return

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await database.is_user_authorized(user_id, ADMIN_TELEGRAM_ID):
        return
        
    await websites.handle_websites_document(update, context)
