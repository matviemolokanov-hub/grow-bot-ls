import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [7632708290]  # Ваш ID

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= КОМАНДА СПАМА =================
async def spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Спамит в группу указанное количество сообщений"""
    user_id = update.effective_user.id
    
    # Проверка, что команду использует админ
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав на использование этой команды!")
        return
    
    # Проверка, что команда вызвана в группе
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return
    
    # Получаем аргументы: /spam текст количество
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ Формат: /spam [текст] [количество]\n"
            "Пример: /spam Привет 10"
        )
        return
    
    # Собираем текст (все аргументы кроме последнего)
    text = " ".join(args[:-1])
    try:
        count = int(args[-1])
    except ValueError:
        await update.message.reply_text("❌ Количество должно быть числом!")
        return
    
    # Ограничения
    if count > 50:
        await update.message.reply_text("❌ Максимум 50 сообщений за раз!")
        return
    if count < 1:
        await update.message.reply_text("❌ Минимум 1 сообщение!")
        return
    
    chat_id = update.effective_chat.id
    
    # Подтверждение
    await update.message.reply_text(f"✅ Начинаю спам: {count} сообщений")
    
    # Сам спам
    for i in range(count):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔄 {i+1}/{count} | {text}",
                parse_mode=None
            )
            await asyncio.sleep(0.5)  # Пауза 0.5 секунды между сообщениями
        except Exception as e:
            logger.error(f"Ошибка спама: {e}")
            break
    
    await update.message.reply_text(f"✅ Спам завершён! Отправлено {count} сообщений.")

# ================= ЗАПУСК =================
def main():
    if not TOKEN:
        logger.error("Токен не найден!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("spam", spam))
    
    logger.info("✅ Бот для спама запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
