import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [7632708290]  # Ваш ID

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= КОМАНДА СПАМА =================
async def spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Спамит в группу указанное количество сообщений"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет прав")
        return
    
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда только в группах")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /spam текст количество")
        return
    
    text = " ".join(args[:-1])
    try:
        count = int(args[-1])
    except ValueError:
        await update.message.reply_text("Количество должно быть числом")
        return
    
    if count > 50:
        await update.message.reply_text("Максимум 50")
        return
    if count < 1:
        await update.message.reply_text("Минимум 1")
        return
    
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(f"Начинаю спам: {count} сообщений")
    
    for i in range(count):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text
            )
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            break
    
    await update.message.reply_text(f"Спам завершён! {count} сообщений")

# ================= ЗАПУСК =================
def main():
    if not TOKEN:
        logger.error("Токен не найден")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("spam", spam))
    
    logger.info("Бот для спама запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
