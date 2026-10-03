import asyncio
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import BOT_TOKEN
import handlers
import database
import logging
from browser import browser_manager
from keep_alive import start_keep_alive

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


async def error_handler(update: object, context) -> None:
    if isinstance(context.error, Conflict):
        # Two instances polling at the same time — happens briefly during deploys
        logger.warning("[bot] Conflict: another instance is polling. Will retry automatically.")
        return
    if isinstance(context.error, NetworkError):
        logger.warning(f"[bot] Network error (will retry): {context.error}")
        return
    logger.exception(f"[bot] Unhandled exception: {context.error}", exc_info=context.error)

async def post_init(application: Application):
    from browser import init_semaphore
    from idle_worker import idle_worker_loop
    from scheduler import scheduler_loop
    from retry_worker import retry_worker_loop
    await database.init_db()
    init_semaphore()
    await start_keep_alive()
    bot = application.bot
    asyncio.create_task(idle_worker_loop(),        name="idle_worker")
    asyncio.create_task(scheduler_loop(bot),       name="scheduler")
    asyncio.create_task(retry_worker_loop(bot),    name="retry_worker")

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set in .env")
        return

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CallbackQueryHandler(handlers.button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handlers.document_handler))
    application.add_error_handler(error_handler)

    print("Bot is starting...")
    application.run_polling()

    
if __name__ == '__main__':
    main()
