import requests
import json
import logging
from datetime import datetime
from telegram.ext import Application, ContextTypes
from telegram.constants import ParseMode
import os

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8808377767:AAECq8Cy4QXWjUYqg1K384IH1J87v0V3ItY")
CHAT_ID = -1004363948715          # ID чата
MESSAGE_THREAD_ID = 4            # ID темы (замените)
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

last_stock_data = None

def get_stock_signature(data):
    signature = {}
    for shop_type in ["SeedShop_Normal", "CrateShop", "GearShop"]:
        for item in data.get("shops", {}).get(shop_type, []):
            signature[item.get('name')] = item.get('stock', 0)
    return signature

def get_changes(old, new):
    added = {name: stock for name, stock in new.items() if name not in old}
    removed = {name: stock for name, stock in old.items() if name not in new}
    changed = {name: {'old': old[name], 'new': new[name]} for name in new if name in old and old[name] != new[name]}
    return added, removed, changed

async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code != 200:
            return
        data = resp.json()
        new_stock_sig = get_stock_signature(data)
        
        if last_stock_data is not None and new_stock_sig != last_stock_data:
            added, removed, changed = get_changes(last_stock_data, new_stock_sig)
            
            msg = f"🌱 <b>Изменения в стоке!</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
            
            if added:
                msg += "🟢 <b>Появились:</b>\n" + "\n".join([f"• {n} — {s} шт." for n, s in added.items()]) + "\n\n"
            if changed:
                msg += "🟡 <b>Изменилось количество:</b>\n" + "\n".join([f"• {n}: {c['old']} → {c['new']} шт." for n, c in changed.items()]) + "\n\n"
            if removed:
                msg += "🔴 <b>Пропали:</b>\n" + "\n".join([f"• {n}" for n in removed]) + "\n"
            
            await context.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=MESSAGE_THREAD_ID,
                text=msg,
                parse_mode=ParseMode.HTML
            )
            print(f"✅ Отправлено в тему")
        
        last_stock_data = new_stock_sig
            
    except Exception as e:
        print(f"❌ {e}")

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(check_and_notify, interval=60, first=5)
    print("✅ Бот запущен! Сток отправляется в тему.")
    app.run_polling()

if __name__ == "__main__":
    main()
