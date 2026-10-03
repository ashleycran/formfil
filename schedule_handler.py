"""
schedule_handler.py

Button-based UI for managing a user's automatic daily schedule.

Flow:
  ⏰ Schedule  (button in main menu)
       ↓
  Show current schedule (or "not set")
       ↓
  [Set Schedule] → ask for hour → ask for minute → save → confirm
  [Remove Schedule]
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards


async def schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    sched   = await database.get_schedule(user_id)

    if sched:
        time_str = f"{sched['hour']:02d}:{sched['minute']:02d} UTC"
        text = (
            f"⏰ Auto-Schedule\n\n"
            f"Status: 🟢 Active\n"
            f"Runs daily at: {time_str}\n\n"
            f"The bot will automatically process all your websites at this time every day."
        )
    else:
        text = (
            "⏰ Auto-Schedule\n\n"
            "Status: ⚫ Not set\n\n"
            "Set a daily time and the bot will run your websites automatically."
        )

    keyboard = [
        [InlineKeyboardButton("🕐 Set Schedule", callback_data="schedule_set")],
    ]
    if sched:
        keyboard.append([InlineKeyboardButton("🗑️ Remove Schedule", callback_data="schedule_remove")])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="menu_main"),
        InlineKeyboardButton("🏠 Home",  callback_data="menu_main"),
    ])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def schedule_set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "SCHEDULE_WAITING_HOUR"
    text = (
        "⏰ Set Schedule\n\n"
        "What hour should the bot run? (0–23, UTC)\n\n"
        "Example: type `9` for 9:00 AM UTC"
    )
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="menu_schedule")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def schedule_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    await database.delete_schedule(user_id)

    await query.edit_message_text(
        "✅ Schedule removed. The bot will no longer run automatically.",
        reply_markup=keyboards.get_back_home_keyboard("menu_schedule"),
    )


async def handle_schedule_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state", "")

    if state == "SCHEDULE_WAITING_HOUR":
        try:
            hour = int(update.message.text.strip())
            if not 0 <= hour <= 23:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Please send a number between 0 and 23.",
                reply_markup=keyboards.get_cancel_keyboard("cancel_schedule_set"),
            )
            return True

        context.user_data["schedule_hour"] = hour
        context.user_data["state"] = "SCHEDULE_WAITING_MINUTE"

        await update.message.reply_text(
            f"⏰ Set Schedule\n\n"
            f"Hour set to {hour:02d}.\n\n"
            "What minute? (0–59)\n\nExample: `0` for on the hour, `30` for half past.",
            reply_markup=keyboards.get_cancel_keyboard("cancel_schedule_set"),
        )
        return True

    if state == "SCHEDULE_WAITING_MINUTE":
        try:
            minute = int(update.message.text.strip())
            if not 0 <= minute <= 59:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Please send a number between 0 and 59.",
                reply_markup=keyboards.get_cancel_keyboard("cancel_schedule_set"),
            )
            return True

        hour    = context.user_data.pop("schedule_hour", 9)
        user_id = update.effective_user.id
        await database.set_schedule(user_id, hour, minute)

        context.user_data["state"] = None

        await update.message.reply_text(
            f"✅ Schedule saved!\n\n"
            f"The bot will run automatically every day at {hour:02d}:{minute:02d} UTC.",
            reply_markup=keyboards.get_back_home_keyboard("menu_schedule"),
        )
        return True

    return False
