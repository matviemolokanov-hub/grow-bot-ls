import requests
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import os

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8808377767:AAECq8Cy4QXWjUYqg1K384IH1J87v0V3ItY")
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"
DATA_FILE = "user_settings.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def load_settings():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_settings(settings):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

user_settings = load_settings()
last_stock_data = None
all_items = {}

# ================= ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ =================

def get_all_items_from_api(data):
    items = {}
    for shop_type, category in [("SeedShop_Normal", "Семена"), 
                                  ("CrateShop", "Ящики"), 
                                  ("GearShop", "Снаряжение")]:
        for item in data.get("shops", {}).get(shop_type, []):
            name = item.get('name')
            if name:
                items[name] = {
                    'name': name,
                    'rarity': item.get('rarity', 'Common'),
                    'category': category,
                }
    return items

def get_stock_signature(data):
    signature = {}
    for shop_type in ["SeedShop_Normal", "CrateShop", "GearShop"]:
        for item in data.get("shops", {}).get(shop_type, []):
            signature[item.get('name')] = item.get('stock', 0)
    return signature

def get_changes(old, new):
    added = {n: s for n, s in new.items() if n not in old}
    removed = {n: s for n, s in old.items() if n not in new}
    return added, removed

def format_stock_message(data):
    msg = f"📦 <b>ВЕСЬ СТОК Grow a Garden 2</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
    for shop_type, shop_name in [("SeedShop_Normal", "🌾 Семена"), 
                                   ("CrateShop", "📦 Ящики"), 
                                   ("GearShop", "⚙️ Снаряжение")]:
        msg += f"{shop_name}:\n"
        has = False
        for item in data.get("shops", {}).get(shop_type, []):
            if item.get("stock", 0) > 0:
                msg += f"• {item['name']} — {item['stock']} шт. ({item.get('rarity', 'Common')})\n"
                has = True
        if not has:
            msg += "Нет в наличии\n"
        msg += "\n"
    return msg

# ================= КНОПКИ И МЕНЮ =================

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🌾 Семена", callback_data="category_Семена")],
        [InlineKeyboardButton("📦 Ящики", callback_data="category_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="category_Снаряжение")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_items_menu(category, page=0):
    items_per_page = 10
    
    items_list = [name for name, info in all_items.items() if info['category'] == category]
    items_list.sort()
    
    total_pages = (len(items_list) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_items = items_list[start:end]
    
    keyboard = []
    for item_name in current_items:
        rarity_emoji = {'Common': '🟢', 'Rare': '🔵', 'Epic': '🟣', 'Legendary': '🟡', 'Mythic': '🔴', 'Super': '⭐'}.get(all_items[item_name]['rarity'], '⚪')
        keyboard.append([InlineKeyboardButton(f"{rarity_emoji} {item_name}", callback_data=f"item_{item_name}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"page_{category}_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"page_{category}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

# ================= ОБНОВЛЕНИЕ ПРЕДМЕТОВ =================

async def update_all_items():
    global all_items
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code == 200:
            all_items = get_all_items_from_api(resp.json())
            logger.info(f"Загружено предметов: {len(all_items)}")
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

# ================= КОМАНДЫ И КНОПКИ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_settings(user_settings)
    await update_all_items()
    await update.message.reply_text(
        "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
        "Выбери категорию, затем нажми на предмет,\n"
        "чтобы получать уведомления, когда он появится в стоке.\n\n"
        f"📦 <b>Всего предметов:</b> {len(all_items)}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    if query.data == "ignore":
        await query.answer()
        return
    
    await query.answer()
    
    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_settings(user_settings)
    
    if not all_items:
        await update_all_items()
    
    data = query.data
    
    if data == "back_to_menu":
        await query.edit_message_text(
            "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
            "Выбери категорию, затем нажми на предмет,\n"
            "чтобы получать уведомления, когда он появится в стоке.\n\n"
            f"📦 <b>Всего предметов:</b> {len(all_items)}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu())
    
    elif data.startswith("category_"):
        category = data.replace("category_", "")
        await query.edit_message_text(
            f"📂 <b>{category}</b>\n\nНажми на предмет, чтобы подписаться на уведомления:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_items_menu(category, 0))
        context.user_data['current_category'] = category
    
    elif data.startswith("item_"):
        item_name = data.replace("item_", "")
        subscriptions = user_settings[user_id].get("subscriptions", [])
        
        if item_name in subscriptions:
            subscriptions.remove(item_name)
            await query.answer(f"❌ {item_name} удалён из уведомлений")
        else:
            subscriptions.append(item_name)
            await query.answer(f"✅ {item_name} добавлен в уведомления")
        
        user_settings[user_id]["subscriptions"] = subscriptions
        save_settings(user_settings)
        
        category = context.user_data.get('current_category', 'Семена')
        await query.edit_message_reply_markup(reply_markup=get_items_menu(category, 0))
    
    elif data.startswith("page_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        await query.edit_message_reply_markup(reply_markup=get_items_menu(category, page))

# ================= ФОНОВАЯ ПРОВЕРКА =================

async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code != 200:
            return
        data = resp.json()
        new_stock_sig = get_stock_signature(data)
        
        if last_stock_data is not None:
            added, removed = get_changes(last_stock_data, new_stock_sig)
            
            if added:
                for user_id, settings in user_settings.items():
                    subscriptions = settings.get("subscriptions", [])
                    if not subscriptions:
                        continue
                    
                    user_added = {n: s for n, s in added.items() if n in subscriptions}
                    
                    if user_added:
                        msg = f"📢 <b>Появились предметы из твоих подписок!</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                        for name, stock in user_added.items():
                            rarity = all_items.get(name, {}).get('rarity', 'Common')
                            rarity_emoji = {'Common': '🟢', 'Rare': '🔵', 'Epic': '🟣', 'Legendary': '🟡', 'Mythic': '🔴', 'Super': '⭐'}.get(rarity, '⚪')
                            msg += f"{rarity_emoji} <b>{name}</b> — {stock} шт.\n"
                        
                        try:
                            await context.bot.send_message(int(user_id), msg, parse_mode=ParseMode.HTML)
                            logger.info(f"Уведомление отправлено {user_id}")
                        except Exception as e:
                            logger.error(f"Не отправлено {user_id}: {e}")
        
        last_stock_data = new_stock_sig
            
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")

# ================= ЗАПУСК =================

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(check_and_notify, interval=60, first=5)
    logger.info("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
