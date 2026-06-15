import requests
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

TOKEN = "8808377767:AAECq8Cy4QXWjUYqg1K384IH1J87v0V3ItY"
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"

DATA_FILE = "user_data.json"

try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        user_data = json.load(f)
except:
    user_data = {}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

last_stock = None

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(user_data, f, ensure_ascii=False, indent=2)

def format_stock_message(data):
    msg = f"🌱 <b>Новый сток Grow a Garden 2</b> — {datetime.now().strftime('%H:%M:%S')}\n\n"
    msg += "🌾 <b>Семена:</b>\n"
    for item in data.get("shops", {}).get("SeedShop_Normal", []):
        if item.get("stock", 0) > 0:
            msg += f"• {item.get('name')} — {item.get('stock')} шт.\n"
    msg += "\n📦 <b>Ящики:</b>\n"
    for item in data.get("shops", {}).get("CrateShop", []):
        if item.get("stock", 0) > 0:
            msg += f"• {item.get('name')} — {item.get('stock')} шт.\n"
    msg += "\n⚙️ <b>Снаряжение:</b>\n"
    for item in data.get("shops", {}).get("GearShop", []):
        if item.get("stock", 0) > 0:
            msg += f"• {item.get('name')} — {item.get('stock')} шт.\n"
    return msg

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_data:
        user_data[user_id] = {"stock": True}
        save_data()

    keyboard = [
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_stock")],
        [InlineKeyboardButton("🔄 Проверить сток сейчас", callback_data="check_now")]
    ]
    status = "✅ ВКЛ" if user_data[user_id]["stock"] else "❌ ВЫКЛ"
    await update.message.reply_text(
        f"🌱 Бот стока Grow a Garden 2\nУведомления: {status}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    if user_id not in user_data:
        user_data[user_id] = {"stock": True}

    if query.data == "toggle_stock":
        user_data[user_id]["stock"] = not user_data[user_id]["stock"]
        save_data()
        status = "✅ ВКЛ" if user_data[user_id]["stock"] else "❌ ВЫКЛ"
        await query.edit_message_text(
            f"Уведомления: {status}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Вкл/Выкл", callback_data="toggle_stock")],
                [InlineKeyboardButton("🔄 Проверить сток", callback_data="check_now")]
            ])
        )

    elif query.data == "check_now":
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code == 200:
            await query.edit_message_text(format_stock_message(resp.json()))
        else:
            await query.edit_message_text("Ошибка API")

async def check_stock(context: ContextTypes.DEFAULT_TYPE):
    global last_stock
    resp = requests.get(API_URL, timeout=10)
    if resp.status_code != 200:
        return
    data = resp.json()
    if last_stock is not None and data != last_stock:
        for uid in user_data:
            if user_data[uid].get("stock", True):
                try:
                    await context.bot.send_message(int(uid), format_stock_message(data), parse_mode=ParseMode.HTML)
                except:
                    pass
    last_stock = data

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(check_stock, interval=60, first=5)
    print("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()