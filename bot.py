import requests
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"
PREDICT_URL = "https://grow-a-garden-2-tracker.onrender.com/api/predictions"

DATA_DIR = "/app/data" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "user_settings.json")
GROUP_SETTINGS_FILE = os.path.join(DATA_DIR, "group_settings.json")
ITEMS_CACHE_FILE = os.path.join(DATA_DIR, "items_cache.json")
PREDICT_MESSAGES_FILE = os.path.join(DATA_DIR, "predict_messages.json")
MULTIPLIERS_CACHE_FILE = os.path.join(DATA_DIR, "multipliers_cache.json")
MULTIPLIER_MESSAGES_FILE = os.path.join(DATA_DIR, "multiplier_messages.json")

CACHE_TTL = 300
ADMIN_IDS = [7632708290]

# ================= ВСЕ ТИПЫ ПОГОДЫ =================
WEATHER_TYPES = {
    "Clear": {"emoji": "☀️", "name": "Обычная"},
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
    handlers=[logging.FileHandler(os.path.join(DATA_DIR, 'bot.log'), encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

def format_timestamp(ts):
    if not ts or ts == 0:
        return "—"
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime('%H:%M:%S')

def load_json(filename, default=None):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        return default if default is not None else {}
    except Exception as e:
        logger.error(f"Ошибка загрузки {filename}: {e}")
        return default if default is not None else {}

def save_json(filename, data):
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")
        return False

async def safe_edit(query, text=None, reply_markup=None):
    try:
        if text is not None:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Ошибка safe_edit: {e}")
    except Exception as e:
        logger.error(f"Критическая ошибка safe_edit: {e}")

# ================= ДАННЫЕ И КЕШ =================
user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
predict_messages = load_json(PREDICT_MESSAGES_FILE, {})
multiplier_messages = load_json(MULTIPLIER_MESSAGES_FILE, {})
all_items = {}
last_stock_data = None
last_weather_data = None
_items_cache_time = 0

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
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            new_items = {}
            mapping = {"SeedShop_Normal": "Семена", "CrateShop": "Ящики", "GearShop": "Снаряжение"}
            for shop_key, cat_name in mapping.items():
                for item in data.get("shops", {}).get(shop_key, []):
                    name = item.get('name')
                    if name:
                        new_items[name] = {
                            'name': name,
                            'rarity': item.get('rarity', 'Common'),
                            'category': cat_name
                        }
            all_items = new_items
            _items_cache_time = time.time()
            save_json(ITEMS_CACHE_FILE, {'items': all_items, 'timestamp': _items_cache_time})
            logger.info(f"Загружено предметов: {len(all_items)}")
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
    return all_items

def get_multipliers():
    try:
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code == 200:
            mults = resp.json().get('fruitMultipliers', [])
            mults.sort(key=lambda x: x.get('multiplier', 0), reverse=True)
            return mults
    except:
        pass
    return []

def get_predictions_data():
    try:
        resp = requests.get(PREDICT_URL, timeout=10)
        if resp.status_code == 200 and resp.text:
            return resp.json()
    except Exception as e:
        logger.error(f"Ошибка получения предсказаний: {e}")
    return None

# ================= ФОРМАТИРОВАНИЕ СООБЩЕНИЙ =================
def format_predict_msg(data):
    if not data:
        return "❌ Ошибка данных"
    now = time.time()
    msk_time = get_msk_time().strftime('%H:%M:%S')
    msg = f"🔮 <b>ПРЕДСКАЗАНИЯ</b>\n🔄 <i>Обновлено: {msk_time} МСК</i>\n"
    msg += "═" * 30 + "\n\n"

    # Лунные фазы
    weathers = sorted([w for w in data.get('weathers', []) if w.get('timestamp', 0) > now], key=lambda x: x['timestamp'])[:10]
    if weathers:
        msg += "🌙 <b>ЛУННЫЕ ФАЗЫ</b>\n"
        for w in weathers:
            name = w.get('name', 'Неизвестно')
            ts = w.get('timestamp', 0)
            info = WEATHER_TYPES.get(name, {"emoji": "🌙", "name": name})
            time_str = format_timestamp(ts)
            minutes_left = int((ts - now) / 60)
            if minutes_left > 0:
                msg += f"  {info['emoji']} {info['name']} — {time_str} (через {minutes_left} мин.)\n"
            else:
                msg += f"  {info['emoji']} {info['name']} — {time_str}\n"
        msg += "\n"

    # Редкий сток
    important = ["Dragon's Breath", "Moon Bloom", "Venom Spitter", "Sunflower",
                 "Legendary Sprinkler", "Super Sprinkler", "Super Watering Can"]
    all_p = data.get('seeds', []) + data.get('gears', []) + data.get('props', [])

    stock_now = [i for i in all_p if i.get('relativeText') == "*Currently on Stock*" and i.get('name') in important]
    upcoming = sorted([i for i in all_p if i.get('name') in important and i.get('timestamp', 0) > now],
                      key=lambda x: x['timestamp'])[:15]
    past = sorted([i for i in all_p if i.get('name') in important and i.get('timestamp', 0) < now and i.get('relativeText') != "*Currently on Stock*"],
                  key=lambda x: x['timestamp'], reverse=True)[:10]

    msg += "📦 <b>РЕДКИЙ СТОК</b>\n"

    if stock_now:
        msg += "  🟢 <b>В НАЛИЧИИ СЕЙЧАС:</b>\n"
        for i in stock_now[:10]:
            name = i.get('name', 'Неизвестно')
            mult = i.get('multiplier', '')
            msg += f"    • {name}" + (f" (x{mult})" if mult else "") + "\n"
        msg += "\n"
    else:
        msg += "  🟢 <b>В НАЛИЧИИ СЕЙЧАС:</b> Нет\n\n"

    if upcoming:
        msg += "  ⏳ <b>ОЖИДАЙТЕ В БЛИЖАЙШЕЕ ВРЕМЯ:</b>\n"
        for i in upcoming:
            name = i.get('name', 'Неизвестно')
            ts = i.get('timestamp', 0)
            time_str = format_timestamp(ts)
            minutes_left = int((ts - now) / 60)
            msg += f"    • {name} — {time_str}" + (f" (через {minutes_left} мин.)" if minutes_left > 0 else "") + "\n"
        msg += "\n"
    else:
        msg += "  ⏳ <b>ОЖИДАЙТЕ В БЛИЖАЙШЕЕ ВРЕМЯ:</b> Нет\n\n"

    if past:
        msg += "  🕐 <b>БЫЛИ В СТОКЕ:</b>\n"
        for i in past:
            name = i.get('name', 'Неизвестно')
            ts = i.get('timestamp', 0)
            msg += f"    • {name} — {format_timestamp(ts)}\n"
    else:
        msg += "  🕐 <b>БЫЛИ В СТОКЕ:</b> Нет\n"

    msg += "\n" + "═" * 30 + "\n"
    msg += "🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_multipliers_message():
    mults = get_multipliers()
    if not mults:
        return "❌ Нет данных"
    msg = f"📊 <b>МУЛЬТИПЛИКАТОРЫ ПРЕДМЕТОВ</b>\n🔄 <i>Обновлено: {get_msk_time().strftime('%H:%M:%S')} МСК</i>\n"
    msg += "═" * 30 + "\n\n"

    high = []
    low = []
    for item in mults:
        mult = item.get('multiplier', 0)
        name = item.get('name', 'Неизвестно')
        if mult >= 1.0:
            high.append(f"    • {name} — <b>x{mult:.2f}</b>")
        else:
            low.append(f"    • {name} — <b>x{mult:.2f}</b>")

    if high:
        msg += "  🟢 <b>ВЫСОКИЕ МУЛЬТИПЛИКАТОРЫ (1.0+):</b>\n"
        msg += "\n".join(high) + "\n\n"
    if low:
        msg += "  🔴 <b>НИЗКИЕ МУЛЬТИПЛИКАТОРЫ (&lt;1.0):</b>\n"
        msg += "\n".join(low) + "\n"

    msg += "\n" + "═" * 30 + "\n"
    msg += "💡 <i>Чем выше множитель, тем дороже предмет при продаже</i>\n"
    msg += "🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_weather_message(weather_key):
    msk_time = get_msk_time()
    weather_info = WEATHER_TYPES.get(weather_key, {"emoji": "☀️", "name": "Обычная"})
    if weather_key == "Clear":
        msg = f"🌤️ <b>ПОГОДА ИЗМЕНИЛАСЬ!</b>\n☀️ <b>Обычная погода</b>\n"
    else:
        msg = f"🌤️ <b>ПОГОДА ИЗМЕНИЛАСЬ!</b>\n{weather_info['emoji']} <b>{weather_info['name']}</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_full_stock_message(data):
    msk_time = get_msk_time()
    msg = f"📦 <b>ТЕКУЩИЙ СТОК Grow a Garden 2</b>\n🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n"
    for shop_type, shop_name in [("SeedShop_Normal", "🌾 Семена"), ("CrateShop", "📦 Ящики"), ("GearShop", "⚙️ Снаряжение")]:
        msg += f"{shop_name}:\n"
        has = False
        items_sorted = data.get("shops", {}).get(shop_type, [])
        for item in items_sorted:
            if item.get("stock", 0) > 0:
                msg += f"• {item['name']} — {item['stock']} шт.\n"
                has = True
        if not has:
            msg += "Нет в наличии\n"
        msg += "\n"
    return msg

def get_weather_type(data):
    weather = data.get('weather', {})
    weathers = weather.get('weathers', {})
    for key in WEATHER_TYPES.keys():
        if key == "Clear":
            continue
        val = weathers.get(key)
        if val is True or val == "true":
            return key
        if isinstance(val, dict) and val.get("playing") is True:
            return key
    phase = weather.get('phase', '')
    if phase in WEATHER_TYPES:
        return phase
    return "Clear"

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

def format_stock_update_message(added, changed, removed):
    """
    Форматирует единое сообщение об обновлении стока.
    added: dict {name: stock}
    changed: dict {name: {'old': old_stock, 'new': new_stock}}
    removed: список имён удалённых предметов
    """
    msk_time = get_msk_time()
    cat_emojis = {"Семена": "🌾", "Ящики": "📦", "Снаряжение": "⚙️"}
    categories = {}

    # Собираем все изменения по категориям
    for name, stock in added.items():
        info = all_items.get(name, {})
        cat = info.get('category', 'Неизвестно')
        if cat not in categories:
            categories[cat] = {'added': [], 'changed': [], 'removed': []}
        categories[cat]['added'].append((name, stock, info.get('rarity', 'Common')))

    for name, change in changed.items():
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

    if not categories:
        return None

    msg = f"📢 <b>ОБНОВЛЕНИЕ СТОКА!</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n"
    msg += "─" * 25 + "\n\n"

    for category, items in categories.items():
        cat_emoji = cat_emojis.get(category, "📌")
        msg += f"{cat_emoji} <b>{category}</b>\n"
        if items['added']:
            for name, stock, rarity in items['added']:
                msg += f"  • {name} — <b>{stock} шт.</b> ({rarity})\n"
        if items['changed']:
            for name, stock, rarity in items['changed']:
                msg += f"  • {name} — <b>{stock} шт.</b> ({rarity})\n"
        if items['removed']:
            for name, rarity in items['removed']:
                msg += f"  • {name} ({rarity})\n"
        msg += "\n"

    msg += "🤖 Наш бот: @growagardenstock235_bot"
    return msg

# ================= КЛАВИАТУРЫ =================
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Семена", callback_data="cat_Семена"), InlineKeyboardButton("📦 Ящики", callback_data="cat_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="cat_Снаряжение")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")],
        [InlineKeyboardButton("📦 Весь сток", callback_data="full_stock"), InlineKeyboardButton("📊 Множители", callback_data="show_mults")]
    ])

def get_items_menu(user_id, category, page=0, is_admin=False, chat_id=None):
    items = load_items()
    subs = []
    if is_admin:
        subs = group_settings.get(str(chat_id), {}).get("subscriptions", [])
    else:
        subs = user_settings.get(str(user_id), {}).get("subscriptions", [])

    filtered = sorted([name for name, info in items.items() if info.get('category') == category])
    per_page = 10
    total_pages = max(1, (len(filtered) - 1) // per_page + 1) if filtered else 1
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    current_items = filtered[start:start + per_page]

    keyboard = []
    prefix = "aitm" if is_admin else "itm"
    for name in current_items:
        status = "✅" if name in subs else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {name}", callback_data=f"{prefix}_{category}_{page}_{name}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"pg_{prefix}_{category}_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"pg_{prefix}_{category}_{page+1}"))
    if nav:
        keyboard.append(nav)

    back_data = "admin_main" if is_admin else "menu"
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=back_data)])
    return InlineKeyboardMarkup(keyboard)

def get_admin_menu(chat_id):
    s = group_settings.get(str(chat_id), {"subscriptions": [], "weather": False})
    w_stat = "✅" if s.get("weather") else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Семена", callback_data="acat_Семена"), InlineKeyboardButton("📦 Ящики", callback_data="acat_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="acat_Снаряжение")],
        [InlineKeyboardButton(f"{w_stat} Уведомления о погоде", callback_data="adm_tgl_w")],
        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="adm_clear")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="adm_close")]
    ])

def get_subscriptions_menu(user_id, page=0):
    subscriptions = user_settings.get(str(user_id), {}).get("subscriptions", [])
    items_per_page = 10
    total_pages = max(1, (len(subscriptions) + items_per_page - 1) // items_per_page) if subscriptions else 1
    page = max(0, min(page, total_pages - 1))
    start = page * items_per_page
    current_subs = subscriptions[start:start + items_per_page]

    keyboard = []
    for sub in current_subs:
        keyboard.append([InlineKeyboardButton(f"❌ {sub}", callback_data=f"unsub_{sub}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"sub_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"sub_page_{page+1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(keyboard)

# ================= КОМАНДЫ =================
async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = get_predictions_data()
    if not data:
        await update.message.reply_text("❌ Не удалось получить данные предсказаний")
        return
    msg = format_predict_msg(data)
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    predict_messages[chat_id] = sent.message_id
    save_json(PREDICT_MESSAGES_FILE, predict_messages)

async def multipliers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = format_multipliers_message()
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    chat_id = str(update.effective_chat.id)
    multiplier_messages[chat_id] = sent.message_id
    save_json(MULTIPLIER_MESSAGES_FILE, multiplier_messages)

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            weather_type = get_weather_type(data)
            weather = data.get('weather', {})
            weathers = weather.get('weathers', {})
            weather_info = WEATHER_TYPES.get(weather_type, {"emoji": "☀️", "name": "Обычная"})
            msg = f"🌤️ <b>ТЕКУЩАЯ ПОГОДА</b>\n\n{weather_info['emoji']} <b>{weather_info['name']}</b>\n"
            if weather_type != "Clear" and isinstance(weathers.get(weather_type), dict):
                end_time = weathers[weather_type].get('endTime')
                if end_time:
                    msg += f"⏱️ Длится до: {datetime.fromtimestamp(end_time).strftime('%H:%M:%S')} МСК\n"
            msg += f"\n🕐 {get_msk_time().strftime('%H:%M:%S')} МСК\n\n🤖 Наш бот: @growagardenstock235_bot"
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply
