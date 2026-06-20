import requests
import json
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import os
import time

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"
DATA_FILE = "user_settings.json"
GROUP_SETTINGS_FILE = "group_settings.json"
ITEMS_CACHE_FILE = "items_cache.json"
CACHE_TTL = 300

# ================= АДМИНЫ =================
ADMIN_IDS = [7632708290]

# ================= ЧТО ОТПРАВЛЯЕТСЯ =================
RARE_RARITIES = ["Legendary", "Mythic", "Super"]
FORCED_ITEMS = [
    "Mushroom",
    "Moon Bloom",
    "Legendary Sprinkler",
    "Super Watering Can",
    "Super Sprinkler"
]

# ================= ВСЕ ТИПЫ ПОГОДЫ =================
WEATHER_TYPES = {
    "Rain": {"emoji": "🌧️", "name": "Дождь"},
    "Snowfall": {"emoji": "❄️", "name": "Снегопад"},
    "Thunderstorm": {"emoji": "⛈️", "name": "Гроза"},
    "Blood Moon": {"emoji": "🌕", "name": "Кровавая Луна"},
    "Starfall": {"emoji": "⭐", "name": "Звездопад"},
    "Midas": {"emoji": "✨", "name": "Золотая ночь"},
    "Goldmoon": {"emoji": "🌙", "name": "Золотая Луна"},
    "Rainbow": {"emoji": "🌈", "name": "Радуга"},
    "Rainbow Moon": {"emoji": "🌙🌈", "name": "Радужная Луна"},
    "Aurora": {"emoji": "🌌", "name": "Северное сияние"},
    "Fog": {"emoji": "🌫️", "name": "Туман"},
    "Wind": {"emoji": "💨", "name": "Ветер"},
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= ВРЕМЯ МСК =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

# ================= СОХРАНЕНИЕ =================
def load_json(filename, default=None):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")
        return False

user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
all_items = {}
last_stock_data = None
last_weather_data = None
_items_cache_time = 0

# ================= КЕШИРОВАНИЕ =================
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

def load_items():
    global all_items, _items_cache_time
    if time.time() - _items_cache_time < CACHE_TTL and all_items:
        return all_items

    cached = load_json(ITEMS_CACHE_FILE)
    if cached.get('items') and time.time() - cached.get('timestamp', 0) < CACHE_TTL:
        all_items = cached['items']
        _items_cache_time = cached['timestamp']
        return all_items

    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code == 200:
            all_items = get_all_items_from_api(resp.json())
            _items_cache_time = time.time()
            save_json(ITEMS_CACHE_FILE, {'items': all_items, 'timestamp': _items_cache_time})
            logger.info(f"Загружено предметов: {len(all_items)}")
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

    return all_items

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

# ================= ОПРЕДЕЛЕНИЕ ПОГОДЫ (ИСПРАВЛЕНО) =================
def get_weather_type(data):
    weather = data.get('weather', {})
    weathers = weather.get('weathers', {})
    
    # 1. Проверяем weathers (активные эффекты)
    for key in WEATHER_TYPES.keys():
        if weathers.get(key) is True or weathers.get(key) == "true":
            return key
        if isinstance(weathers.get(key), dict) and weathers.get(key, {}).get("playing") is True:
            return key
    
    # 2. Проверяем phase (лунные фазы)
    phase = weather.get('phase', '')
    phase_map = {
        "Goldmoon": "Goldmoon",
        "Blood Moon": "Blood Moon",
        "Midas": "Midas",
        "Starfall": "Starfall",
        "Rainbow Moon": "Rainbow Moon",
        "Aurora": "Aurora",
    }
    if phase in phase_map:
        return phase_map[phase]
    
    return None

# ================= ФОРМАТИРОВАНИЕ =================

def format_weather_message(weather_key):
    msk_time = get_msk_time()
    weather_info = WEATHER_TYPES.get(weather_key, {"emoji": "☀️", "name": "Обычная"})
    msg = f"🌤️ <b>ПОГОДА ИЗМЕНИЛАСЬ!</b>\n"
    msg += f"{weather_info['emoji']} <b>{weather_info['name']}</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_group_stock_message(added, changed, removed):
    msk_time = get_msk_time()
    cat_emojis = {"Семена": "🌾", "Ящики": "📦", "Снаряжение": "⚙️"}
    categories = {}

    for name, stock in added.items():
        info = all_items.get(name, {})
        cat = info.get('category', 'Неизвестно')
        if cat not in categories:
            categories[cat] = {'added': [], 'changed': [], 'removed': []}
        categories[cat]['added'].append((name, stock, info.get('rarity', 'Common')))
    
    for name, change in changed.items():
        if change['new'] > 0:
            info = all_items.get(name, {})
            cat = info.get('category', 'Неизвестно')
            if cat not in categories:
                categories[cat] = {'added': [], 'changed': [], 'removed': []}
            categories[cat]['changed'].append((name, change['new'], info.get('rarity', 'Common')))
    
    for name in removed:
        info = all_items.get(name, {})
        cat = info.get('category', 'Неизвестно')
        if cat not in categories:
            categories[cat] = {'added': [], 'changed': [], 'removed': []}
        categories[cat]['removed'].append((name, info.get('rarity', 'Common')))
    
    msg = "📢 <b>ОБНОВЛЕНИЕ СТОКА!</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n"
    msg += "─" * 25 + "\n\n"
    has_changes = False
    
    for category, items in categories.items():
        cat_emoji = cat_emojis.get(category, "📌")
        msg += f"{cat_emoji} <b>{category}</b>\n"
        if items['added']:
            for name, stock, rarity in items['added']:
                msg += f"  • {name} — <b>{stock} шт.</b> ({rarity})\n"
            has_changes = True
        if items['changed']:
            for name, stock, rarity in items['changed']:
                msg += f"  • {name} — <b>{stock} шт.</b> ({rarity})\n"
            has_changes = True
        if items['removed']:
            for name, rarity in items['removed']:
                msg += f"  • {name} ({rarity})\n"
            has_changes = True
        msg += "\n"
    
    if not has_changes:
        msg += "✅ Изменений нет\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_full_stock_message(data):
    msk_time = get_msk_time()
    msg = f"📦 <b>ТЕКУЩИЙ СТОК Grow a Garden 2</b>\n🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n"
    for shop_type, shop_name in [("SeedShop_Normal", "🌾 Семена"), ("CrateShop", "📦 Ящики"), ("GearShop", "⚙️ Снаряжение")]:
        msg += f"{shop_name}:\n"
        has = False
        for item in data.get("shops", {}).get(shop_type, []):
            if item.get("stock", 0) > 0:
                msg += f"• {item['name']} — {item['stock']} шт.\n"
                has = True
        if not has:
            msg += "Нет в наличии\n"
        msg += "\n"
    return msg

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущую погоду"""
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            weather_type = get_weather_type(data)
            weather = data.get('weather', {})
            weathers = weather.get('weathers', {})
            
            if weather_type:
                weather_info = WEATHER_TYPES.get(weather_type, {"emoji": "☀️", "name": weather_type})
                msg = f"🌤️ <b>ТЕКУЩАЯ ПОГОДА</b>\n\n"
                msg += f"{weather_info['emoji']} <b>{weather_info['name']}</b>\n"
                if isinstance(weathers.get(weather_type), dict):
                    end_time = weathers[weather_type].get('endTime')
                    if end_time:
                        msg += f"⏱️ Длится до: {datetime.fromtimestamp(end_time).strftime('%H:%M:%S')} МСК\n"
            else:
                msg = "☀️ <b>Сейчас обычная погода</b>\n(нет активных эффектов)"
            
            msg += f"\n🕐 {get_msk_time().strftime('%H:%M:%S')} МСК"
            msg += "\n\n🤖 Наш бот: @growagardenstock235_bot"
            
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("❌ Не удалось получить данные о погоде")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ================= МЕНЮ =================

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🌾 Семена", callback_data="category_Семена")],
        [InlineKeyboardButton("📦 Ящики", callback_data="category_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="category_Снаряжение")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="view_subscriptions")],
        [InlineKeyboardButton("📦 Весь сток", callback_data="show_full_stock")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_menu(chat_id):
    settings = group_settings.get(str(chat_id), {"subscriptions": [], "weather": False})
    subs_count = len(settings.get("subscriptions", []))
    weather_status = "✅" if settings.get("weather", False) else "❌"
    keyboard = [
        [InlineKeyboardButton(f"📋 Настройки группы ({subs_count})", callback_data="admin_view_subs")],
        [InlineKeyboardButton("➕ Добавить предметы", callback_data="admin_add_items")],
        [InlineKeyboardButton("➖ Удалить предметы", callback_data="admin_remove_items")],
        [InlineKeyboardButton(f"{weather_status} Погода", callback_data="admin_toggle_weather")],
        [InlineKeyboardButton("🗑️ Очистить все", callback_data="admin_clear_all")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="admin_close")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_category_menu(action):
    keyboard = [
        [InlineKeyboardButton("🌾 Семена", callback_data=f"admin_{action}_seed")],
        [InlineKeyboardButton("📦 Ящики", callback_data=f"admin_{action}_crate")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data=f"admin_{action}_gear")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_items_menu(chat_id, category, action, page=0):
    items_per_page = 10
    items = load_items()
    subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])

    items_list = [name for name, info in items.items() if info.get('category') == category]
    items_list.sort()

    total_pages = (len(items_list) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_items = items_list[start:end]

    keyboard = []
    for item_name in current_items:
        is_selected = item_name in subscriptions
        button_text = f"{'✅' if is_selected else '❌'} {item_name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"admin_{action}_{category}_{page}_{item_name}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"admin_page_{action}_{category}_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"admin_page_{action}_{category}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back_to_categories")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_subscriptions_menu(chat_id, page=0):
    subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
    items_per_page = 10
    total_pages = (len(subscriptions) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_subs = subscriptions[start:end]

    keyboard = []
    for sub in current_subs:
        keyboard.append([InlineKeyboardButton(f"❌ {sub}", callback_data=f"admin_unsub_{sub}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"admin_subs_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"admin_subs_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)

def get_items_menu(user_id, category, page=0):
    items_per_page = 10
    items = load_items()
    subscriptions = user_settings.get(str(user_id), {}).get("subscriptions", [])

    items_list = [name for name, info in items.items() if info.get('category') == category]
    items_list.sort()

    total_pages = (len(items_list) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_items = items_list[start:end]

    keyboard = []
    for item_name in current_items:
        is_selected = item_name in subscriptions
        button_text = f"{'✅' if is_selected else '❌'} {item_name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"item_{category}_{page}_{item_name}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"page_{category}_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"page_{category}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_subscriptions_menu(user_id, page=0):
    subscriptions = user_settings.get(str(user_id), {}).get("subscriptions", [])
    items_per_page = 10
    total_pages = (len(subscriptions) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_subs = subscriptions[start:end]

    keyboard = []
    for sub in current_subs:
        keyboard.append([InlineKeyboardButton(f"❌ {sub}", callback_data=f"unsub_{sub}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"sub_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"sub_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

# ================= ОБРАБОТЧИКИ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_json(DATA_FILE, user_settings)
        logger.info(f"Создан новый пользователь: {user_id}")

    items = load_items()
    await update.message.reply_text(
        "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
        "Выбери категорию, затем нажми на предмет.\n"
        "✅ — получать уведомления\n"
        "❌ — не получать\n\n"
        f"📦 <b>Всего предметов:</b> {len(items)}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав на использование админ-панели!")
        return

    if str(chat_id) not in group_settings:
        group_settings[str(chat_id)] = {"subscriptions": [], "weather": False}
        save_json(GROUP_SETTINGS_FILE, group_settings)
        logger.info(f"Созданы настройки для группы: {chat_id}")

    await update.message.reply_text(
        "👑 <b>Админ-панель</b>\n\n"
        "Здесь ты можешь настроить, какие предметы будут автоматически отправляться в этот чат при появлении в стоке.\n\n"
        "🌤️ Погода — включает уведомления о смене погоды\n\n"
        "✅ — предмет уже в списке\n"
        "❌ — не в списке",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu(chat_id)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id

    if query.data == "ignore":
        await query.answer()
        return

    await query.answer()

    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_json(DATA_FILE, user_settings)

    data = query.data

    if data == "back_to_menu":
        items = load_items()
        await query.edit_message_text(
            "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
            f"📦 <b>Всего предметов:</b> {len(items)}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu()
        )
        return

    elif data == "show_full_stock":
        try:
            resp = requests.get(API_URL, timeout=15)
            if resp.status_code == 200:
                await query.edit_message_text(format_full_stock_message(resp.json()), parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
            else:
                await query.edit_message_text("❌ Ошибка", reply_markup=get_main_menu())
        except Exception as e:
            await query.edit_message_text(f"❌ {e}", reply_markup=get_main_menu())
        return

    elif data == "view_subscriptions":
        subscriptions = user_settings[user_id].get("subscriptions", [])
        if not subscriptions:
            await query.edit_message_text("📋 <b>Нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
        else:
            await query.edit_message_text("📋 <b>Твои подписки</b>", parse_mode=ParseMode.HTML, reply_markup=get_subscriptions_menu(user_id))
        return

    elif data.startswith("sub_page_"):
        page = int(data.split("_")[2])
        await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id, page))
        return

    elif data.startswith("unsub_"):
        item_name = data.replace("unsub_", "")
        subscriptions = user_settings[user_id].get("subscriptions", [])
        subscriptions = [s for s in subscriptions if s != item_name]
        user_settings[user_id]["subscriptions"] = subscriptions
        
        if save_json(DATA_FILE, user_settings):
            logger.info(f"Сохранены настройки пользователя {user_id}")
        
        if subscriptions:
            await query.edit_message_text(
                "📋 <b>Твои подписки</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_subscriptions_menu(user_id)
            )
        else:
            await query.edit_message_text(
                "📋 <b>Нет подписок</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu()
            )
        return

    elif data.startswith("category_"):
        category = data.replace("category_", "")
        await query.edit_message_text(f"📂 <b>{category}</b>", parse_mode=ParseMode.HTML, reply_markup=get_items_menu(user_id, category, 0))
        context.user_data['current_category'] = category
        return

    elif data.startswith("item_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        item_name = "_".join(parts[3:])
        
        subscriptions = user_settings[user_id].get("subscriptions", [])
        if item_name in subscriptions:
            subscriptions.remove(item_name)
            await query.answer(f"❌ {item_name} удалён")
        else:
            subscriptions.append(item_name)
            await query.answer(f"✅ {item_name} добавлен")
        
        user_settings[user_id]["subscriptions"] = subscriptions
        save_json(DATA_FILE, user_settings)
        
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, page))
        return

    elif data.startswith("page_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, page))
        return

    elif data.startswith("admin_"):
        if int(user_id) not in ADMIN_IDS:
            await query.edit_message_text("❌ Нет прав!")
            return
        
        if data == "admin_close":
            await query.edit_message_text("👑 Админ-панель закрыта", reply_markup=None)
            return
        
        elif data == "admin_back":
            await query.edit_message_text("👑 <b>Админ-панель</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            return
        
        elif data == "admin_back_to_categories":
            await query.edit_message_text("📂 <b>Выбери категорию:</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_category_menu(context.user_data.get('admin_action', 'add')))
            return
        
        elif data == "admin_view_subs":
            subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
            if not subscriptions:
                await query.edit_message_text("📋 <b>Нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            else:
                await query.edit_message_text("📋 <b>Подписки группы</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_subscriptions_menu(chat_id))
            return
        
        elif data == "admin_toggle_weather":
            settings = group_settings.get(str(chat_id), {"subscriptions": [], "weather": False})
            settings["weather"] = not settings.get("weather", False)
            group_settings[str(chat_id)] = settings
            save_json(GROUP_SETTINGS_FILE, group_settings)
            status = "включена" if settings["weather"] else "выключена"
            await query.answer(f"🌤️ Погода {status}")
            await query.edit_message_reply_markup(reply_markup=get_admin_menu(chat_id))
            return
        
        elif data == "admin_add_items":
            context.user_data['admin_action'] = 'add'
            await query.edit_message_text("📂 <b>Выбери категорию:</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_category_menu('add'))
            return
        
        elif data == "admin_remove_items":
            context.user_data['admin_action'] = 'remove'
            await query.edit_message_text("📂 <b>Выбери категорию:</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_category_menu('remove'))
            return
        
        elif data == "admin_clear_all":
            group_settings[str(chat_id)] = {"subscriptions": [], "weather": group_settings.get(str(chat_id), {}).get("weather", False)}
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.answer("🗑️ Все подписки очищены")
            await query.edit_message_text("👑 <b>Админ-панель</b>\n\nВсе подписки очищены!", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            return
        
        elif data.startswith("admin_add_") or data.startswith("admin_remove_"):
            parts = data.split("_")
            action = parts[1]
            category = parts[2] if len(parts) > 2 else ""
            
            if len(parts) == 3:
                category_name = {"seed": "Семена", "crate": "Ящики", "gear": "Снаряжение"}.get(category, category)
                await query.edit_message_text(f"📂 <b>{category_name}</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_items_menu(chat_id, category_name, action, 0))
                context.user_data['admin_action'] = action
                context.user_data['admin_category'] = category_name
                return
            
            elif len(parts) >= 5:
                category = parts[2]
                page = int(parts[3])
                item_name = "_".join(parts[4:])
                
                subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
                if action == "add":
                    if item_name not in subscriptions:
                        subscriptions.append(item_name)
                        await query.answer(f"✅ {item_name} добавлен")
                else:
                    if item_name in subscriptions:
                        subscriptions.remove(item_name)
                        await query.answer(f"❌ {item_name} удалён")
                    else:
                        await query.answer(f"⚠️ {item_name} не в списке")
                        return
                
                group_settings[str(chat_id)] = {"subscriptions": subscriptions, "weather": group_settings.get(str(chat_id), {}).get("weather", False)}
                save_json(GROUP_SETTINGS_FILE, group_settings)
                
                await query.edit_message_reply_markup(reply_markup=get_admin_items_menu(chat_id, category, action, page))
                return
        
        elif data.startswith("admin_page_"):
            parts = data.split("_")
            action = parts[2]
            category = parts[3]
            page = int(parts[4])
            await query.edit_message_reply_markup(reply_markup=get_admin_items_menu(chat_id, category, action, page))
            return
        
        elif data.startswith("admin_subs_page_"):
            page = int(data.split("_")[3])
            await query.edit_message_reply_markup(reply_markup=get_admin_subscriptions_menu(chat_id, page))
            return
        
        elif data.startswith("admin_unsub_"):
            item_name = data.replace("admin_unsub_", "")
            subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
            subscriptions = [s for s in subscriptions if s != item_name]
            group_settings[str(chat_id)] = {"subscriptions": subscriptions, "weather": group_settings.get(str(chat_id), {}).get("weather", False)}
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.answer(f"❌ {item_name} удалён")
            
            if subscriptions:
                await query.edit_message_text(
                    "📋 <b>Подписки группы</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_admin_subscriptions_menu(chat_id)
                )
            else:
                await query.edit_message_text(
                    "📋 <b>Нет подписок</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_admin_menu(chat_id)
                )
            return

# ================= ФОНОВАЯ ПРОВЕРКА =================
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data, last_weather_data
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"API вернул код {resp.status_code}")
            return

        data = resp.json()
        new_stock_sig = get_stock_signature(data)
        new_weather = get_weather_type(data)

        # === ДИАГНОСТИКА ===
        logger.info(f"🌤️ Текущая погода: {new_weather}")
        logger.info(f"📊 Предыдущая погода: {last_weather_data}")

        # === ПОГОДА ===
        if last_weather_data is not None and new_weather != last_weather_data:
            if new_weather:
                weather_msg = format_weather_message(new_weather)
                
                # Отправляем во все группы, где включена погода
                for chat_id_str, settings in group_settings.items():
                    if settings.get("weather", False):
                        try:
                            await context.bot.send_message(
                                chat_id=int(chat_id_str),
                                text=weather_msg,
                                parse_mode=ParseMode.HTML
                            )
                            logger.info(f"✅ Погода отправлена в группу {chat_id_str}: {new_weather}")
                        except Exception as e:
                            logger.error(f"❌ Не отправлено в группу {chat_id_str}: {e}")
        
        last_weather_data = new_weather

        if last_stock_data is None:
            last_stock_data = new_stock_sig
            return

        added, removed, changed = get_changes(last_stock_data, new_stock_sig)

        if added or removed or changed:
            logger.info(f"Изменения: +{len(added)} -{len(removed)} ~{len(changed)}")

            # === ОТПРАВКА В ГРУППЫ ПО АДМИН-ПАНЕЛИ ===
            for chat_id_str, settings in group_settings.items():
                subscriptions = settings.get("subscriptions", [])
                if not subscriptions:
                    continue

                group_added = {n: s for n, s in added.items() if n in subscriptions}
                group_changed = {n: c for n, c in changed.items() if n in subscriptions}
                group_removed = {n for n in removed if n in subscriptions}

                if group_added or group_changed or group_removed:
                    msg = format_group_stock_message(group_added, group_changed, group_removed)
                    try:
                        await context.bot.send_message(
                            chat_id=int(chat_id_str),
                            text=msg,
                            parse_mode=ParseMode.HTML
                        )
                        logger.info(f"✅ Уведомление отправлено в чат {chat_id_str}")
                    except Exception as e:
                        logger.error(f"❌ Не отправлено в чат {chat_id_str}: {e}")
                else:
                    logger.info(f"⏸ Нет изменений по подпискам для группы {chat_id_str}")

            # === ЛС ===
            for uid, settings in user_settings.items():
                subs = settings.get("subscriptions", [])
                if not subs:
                    continue

                u_added = {n: s for n, s in added.items() if n in subs}
                u_changed = {n: c for n, c in changed.items() if n in subs}
                u_removed = {n for n in removed if n in subs}

                if u_added or u_changed or u_removed:
                    msg = f"📢 <b>Изменения в стоке!</b>\n🕐 {get_msk_time().strftime('%H:%M:%S')} МСК\n\n"
                    if u_added:
                        msg += "🟢 Появились:\n" + "\n".join([f"• {n} — {s} шт." for n, s in u_added.items()]) + "\n\n"
                    if u_changed:
                        msg += "🟡 Изменилось:\n" + "\n".join([f"• {n}: {c['old']} → {c['new']} шт." for n, c in u_changed.items()]) + "\n\n"
                    if u_removed:
                        msg += "🔴 Пропали:\n" + "\n".join([f"• {n}" for n in u_removed]) + "\n"

                    try:
                        await context.bot.send_message(
                            chat_id=int(uid),
                            text=msg,
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"❌ Не отправлено в ЛС {uid}: {e}")

        last_stock_data = new_stock_sig

    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")

# ================= ЗАПУСК =================
def main():
    if not TOKEN:
        logger.error("Токен не найден!")
        return

    load_items()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.job_queue.run_repeating(check_and_notify, interval=10, first=5)

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
