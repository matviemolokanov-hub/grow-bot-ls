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
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
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
    for shop_type, category in [("SeedShop_Normal", "🌾 Семена"), 
                                  ("CrateShop", "📦 Ящики"), 
                                  ("GearShop", "⚙️ Снаряжение")]:
        for item in data.get("shops", {}).get(shop_type, []):
            name = item.get('name')
            if name:
                items[name] = {
                    'name': name,
                    'rarity': item.get('rarity', 'Common'),
                    'category': category,
                    'stock': item.get('stock', 0)
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
    changed = {n: {'old': old[n], 'new': new[n]} for n in new if n in old and old[n] != new[n]}
    return added, removed, changed

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

def get_main_menu(user_id):
    settings = user_settings.get(str(user_id), {"subscriptions": []})
    subs_count = len(settings.get("subscriptions", []))
    keyboard = [
        [InlineKeyboardButton(f"📋 Мои подписки ({subs_count})", callback_data="view_subscriptions")],
        [InlineKeyboardButton("➕ Добавить предметы", callback_data="choose_items")],
        [InlineKeyboardButton("📦 Весь сток", callback_data="show_stock")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_category_menu():
    keyboard = [
        [InlineKeyboardButton("🌾 Семена", callback_data="category_seed")],
        [InlineKeyboardButton("📦 Ящики", callback_data="category_crate")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="category_gear")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_items_menu(user_id, category, page=0):
    items_per_page = 12
    subscriptions = user_settings.get(str(user_id), {}).get("subscriptions", [])
    sub_names = [s['name'] for s in subscriptions]
    
    items_list = [name for name, info in all_items.items() if info['category'].lower().startswith(category[0])]
    items_list.sort()
    
    total_pages = (len(items_list) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_items = items_list[start:end]
    
    keyboard = []
    for item_name in current_items:
        is_subscribed = item_name in sub_names
        rarity_emoji = {'Common': '🟢', 'Rare': '🔵', 'Epic': '🟣', 'Legendary': '🟡', 'Mythic': '🔴', 'Super': '⭐'}.get(all_items[item_name]['rarity'], '⚪')
        button_text = f"{'✅' if is_subscribed else '➕'} {rarity_emoji} {item_name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"toggle_{item_name}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"page_{category}_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"page_{category}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")])
    return InlineKeyboardMarkup(keyboard)

def get_subscriptions_menu(user_id, page=0):
    settings = user_settings.get(str(user_id), {"subscriptions": []})
    subscriptions = settings.get("subscriptions", [])
    items_per_page = 10
    total_pages = (len(subscriptions) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_subs = subscriptions[start:end]
    
    keyboard = []
    for sub in current_subs:
        rarity_emoji = {'Common': '🟢', 'Rare': '🔵', 'Epic': '🟣', 'Legendary': '🟡', 'Mythic': '🔴', 'Super': '⭐'}.get(sub.get('rarity', 'Common'), '⚪')
        keyboard.append([InlineKeyboardButton(f"❌ {rarity_emoji} {sub['name']}", callback_data=f"unsub_{sub['name']}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"sub_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"sub_page_{page+1}"))
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
        f"📦 <b>Доступно предметов:</b> {len(all_items)}\n\n"
        "👇 Настрой подписки на предметы:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu(user_id)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_settings(user_settings)
    
    if not all_items:
        await update_all_items()
    
    data = query.data
    
    if data == "back_to_menu":
        await query.edit_message_text(
            "🌱 <b>Grow a Garden 2 Tracker</b>\n\n" + f"📦 <b>Доступно предметов:</b> {len(all_items)}\n\n👇 Настрой подписки:",
            parse_mode=ParseMode.HTML, reply_markup=get_main_menu(user_id))
    
    elif data == "choose_items":
        await query.edit_message_text("📂 <b>Выбери категорию:</b>", parse_mode=ParseMode.HTML, reply_markup=get_category_menu())
    
    elif data == "back_to_categories":
        await query.edit_message_text("📂 <b>Выбери категорию:</b>", parse_mode=ParseMode.HTML, reply_markup=get_category_menu())
    
    elif data.startswith("category_"):
        category = data.replace("category_", "")
        category_name = {"seed": "🌾 Семена", "crate": "📦 Ящики", "gear": "⚙️ Снаряжение"}.get(category, category)
        await query.edit_message_text(
            f"📂 <b>{category_name}</b>\n\n➕ Нажми на предмет, чтобы добавить/удалить\n✅ - в подписках\n",
            parse_mode=ParseMode.HTML, 
            reply_markup=get_items_menu(user_id, category, 0))
        context.user_data['current_category'] = category
    
    elif data.startswith("toggle_"):
        item_name = data.replace("toggle_", "")
        subscriptions = user_settings[user_id].get("subscriptions", [])
        sub_names = [s['name'] for s in subscriptions]
        
        if item_name in sub_names:
            subscriptions = [s for s in subscriptions if s['name'] != item_name]
        else:
            item_info = all_items.get(item_name, {})
            subscriptions.append({'name': item_name, 'category': item_info.get('category', ''), 'rarity': item_info.get('rarity', 'Common')})
        
        user_settings[user_id]["subscriptions"] = subscriptions
        save_settings(user_settings)
        
        category = context.user_data.get('current_category', 'seed')
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, 0))
    
    elif data.startswith("page_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, page))
    
    elif data == "view_subscriptions":
        subscriptions = user_settings[user_id].get("subscriptions", [])
        if not subscriptions:
            await query.edit_message_text("📋 <b>У тебя пока нет подписок</b>\n\nДобавь предметы через '➕ Добавить предметы'", parse_mode=ParseMode.HTML, reply_markup=get_main_menu(user_id))
        else:
            await query.edit_message_text("📋 <b>Твои подписки</b>\n\nНажми на предмет, чтобы отписаться:", parse_mode=ParseMode.HTML, reply_markup=get_subscriptions_menu(user_id))
    
    elif data.startswith("sub_page_"):
        page = int(data.split("_")[2])
        await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id, page))
    
    elif data.startswith("unsub_"):
        item_name = data.replace("unsub_", "")
        subscriptions = user_settings[user_id].get("subscriptions", [])
        subscriptions = [s for s in subscriptions if s['name'] != item_name]
        user_settings[user_id]["subscriptions"] = subscriptions
        save_settings(user_settings)
        if subscriptions:
            await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id))
        else:
            await query.edit_message_text("📋 <b>У тебя пока нет подписок</b>\n\nДобавь предметы через '➕ Добавить предметы'", parse_mode=ParseMode.HTML, reply_markup=get_main_menu(user_id))
    
    elif data == "show_stock":
        try:
            resp = requests.get(API_URL, timeout=15)
            if resp.status_code == 200:
                await query.edit_message_text(format_stock_message(resp.json()), parse_mode=ParseMode.HTML, reply_markup=get_main_menu(user_id))
            else:
                await query.edit_message_text("❌ Ошибка получения стока", reply_markup=get_main_menu(user_id))
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_main_menu(user_id))

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
            added, removed, changed = get_changes(last_stock_data, new_stock_sig)
            
            if added or removed or changed:
                for user_id, settings in user_settings.items():
                    subscriptions = settings.get("subscriptions", [])
                    if not subscriptions:
                        continue
                    
                    sub_names = [s['name'] for s in subscriptions]
                    user_added = {n: s for n, s in added.items() if n in sub_names}
                    user_removed = {n: s for n, s in removed.items() if n in sub_names}
                    user_changed = {n: c for n, c in changed.items() if n in sub_names}
                    
                    if user_added or user_removed or user_changed:
                        msg = f"📢 <b>Изменения в стоке!</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                        if user_added:
                            msg += "🟢 <b>Появились:</b>\n" + "\n".join([f"• {n} — {s} шт." for n, s in user_added.items()]) + "\n\n"
                        if user_changed:
                            msg += "🟡 <b>Изменилось количество:</b>\n" + "\n".join([f"• {n}: {c['old']} → {c['new']} шт." for n, c in user_changed.items()]) + "\n\n"
                        if user_removed:
                            msg += "🔴 <b>Пропали:</b>\n" + "\n".join([f"• {n}" for n in user_removed]) + "\n"
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
