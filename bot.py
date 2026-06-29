import requests
import json
import logging
import os
import time
import traceback
import shutil
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не задан!")

API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"
PREDICT_URL = "https://grow-a-garden-2-tracker.onrender.com/api/predictions"

DATA_DIR = "/app/data" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "user_settings.json")
GROUP_SETTINGS_FILE = os.path.join(DATA_DIR, "group_settings.json")
ITEMS_CACHE_FILE = os.path.join(DATA_DIR, "items_cache.json")
PREDICT_MESSAGES_FILE = os.path.join(DATA_DIR, "predict_messages.json")
MULTIPLIER_MESSAGES_FILE = os.path.join(DATA_DIR, "multiplier_messages.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")

CACHE_TTL = 300
ADMIN_IDS = [7632708290]
INACTIVE_DAYS = 30

# ================= ЛОГГЕР =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(DATA_DIR, 'bot.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= ВСЕ ТИПЫ ПОГОДЫ =================
WEATHER_TYPES = {
    "Clear": {"emoji": "☀️", "name": "Обычная"},
    "Rain": {"emoji": "🌧️", "name": "Дождь"},
    "Snowfall": {"emoji": "❄️", "name": "Снегопад"},
    "Thunderstorm": {"emoji": "⛈️", "name": "Гроза"},
    "Blood Moon": {"emoji": "🌕", "name": "Кровавая Луна"},
    "Gold Moon": {"emoji": "🌙", "name": "Золотая Луна"},
    "Rainbow Moon": {"emoji": "🌙🌈", "name": "Радужная Луна"},
    "Mega Moon": {"emoji": "🌕", "name": "Мега Луна"},
    "Chained Moon": {"emoji": "🌕⛓️", "name": "Прикованная Луна"},
    "Pizza Moon": {"emoji": "🍕🌙", "name": "Пицца Луна"},
    "Solar Eclipse": {"emoji": "🌑", "name": "Солнечное затмение"},
    "Starfall": {"emoji": "⭐", "name": "Звездопад"},
    "Rainbow": {"emoji": "🌈", "name": "Радуга"},
    "Aurora": {"emoji": "🌌", "name": "Северное сияние"},
    "Sunburst": {"emoji": "☀️✨", "name": "Солнечная вспышка"},
    "Midas": {"emoji": "✨", "name": "Золотая ночь"},
    "Fog": {"emoji": "🌫️", "name": "Туман"},
    "Wind": {"emoji": "💨", "name": "Ветер"},
}

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

def format_timestamp(ts):
    if not ts or ts == 0:
        return "—"
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime('%H:%M:%S')

def format_timestamp_full(ts):
    """
    Форматирует время с датой:
    - если сегодня: "сегодня в 21:58"
    - если завтра: "завтра в 21:58"
    - если в этом году: "24.06 в 21:58"
    - если в другом году: "24.06.2024 в 21:58"
    """
    if not ts or ts == 0:
        return "—"
    dt = datetime.fromtimestamp(ts, timezone(timedelta(hours=3)))
    now = get_msk_time()
    
    delta = (dt.date() - now.date()).days
    
    if delta == 0:
        day_str = "сегодня"
    elif delta == 1:
        day_str = "завтра"
    elif delta == -1:
        day_str = "вчера"
    else:
        if dt.year == now.year:
            day_str = dt.strftime('%d.%m')
        else:
            day_str = dt.strftime('%d.%m.%Y')
    
    return f"{day_str} в {dt.strftime('%H:%M')}"

def format_timestamp_with_date(ts):
    if not ts or ts == 0:
        return "—"
    dt = datetime.fromtimestamp(ts, timezone(timedelta(hours=3)))
    now = get_msk_time()
    if dt.date() == now.date():
        return f"сегодня в {dt.strftime('%H:%M:%S')}"
    else:
        return dt.strftime('%d.%m %H:%M:%S')

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

def fetch_with_retry(url, max_retries=3, timeout=10):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"⚠️ API вернул {resp.status_code}, попытка {attempt+1}/{max_retries}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка запроса: {e}, попытка {attempt+1}/{max_retries}")
            time.sleep(2 ** attempt)
    return None

def backup_data():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        date = get_msk_time().strftime('%Y-%m-%d')
        files = [DATA_FILE, GROUP_SETTINGS_FILE]
        for f in files:
            if os.path.exists(f):
                backup_file = os.path.join(BACKUP_DIR, f"{os.path.basename(f)}_{date}.json")
                shutil.copy(f, backup_file)
        logger.info(f"💾 Создана резервная копия от {date}")
        return True
    except Exception as e:
        logger.error(f"Ошибка создания резервной копии: {e}")
        return False

def clean_inactive_users():
    try:
        removed = 0
        for uid, s in list(user_settings.items()):
            if 'last_active' in s:
                if time.time() - s['last_active'] > INACTIVE_DAYS * 24 * 3600:
                    del user_settings[uid]
                    removed += 1
            else:
                user_settings[uid]['last_active'] = time.time()
        if removed > 0:
            save_json(DATA_FILE, user_settings)
            logger.info(f"🧹 Удалено неактивных пользователей: {removed}")
        return removed
    except Exception as e:
        logger.error(f"Ошибка очистки пользователей: {e}")
        return 0

def save_history(item, old_stock, new_stock):
    try:
        history = load_json(HISTORY_FILE, {})
        if item not in history:
            history[item] = []
        history[item].append({
            'time': get_msk_time().isoformat(),
            'old': old_stock,
            'new': new_stock
        })
        history[item] = history[item][-20:]
        save_json(HISTORY_FILE, history)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения истории для {item}: {e}")
        return False

# ================= ЧЁРНЫЙ СПИСОК =================
blacklist = load_json(BLACKLIST_FILE, [])

def save_blacklist():
    save_json(BLACKLIST_FILE, blacklist)

def is_group_blocked(chat_id):
    return str(chat_id) in blacklist

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

# ================= ЗАГРУЗКА ДАННЫХ =================
user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
predict_messages = load_json(PREDICT_MESSAGES_FILE, {})
multiplier_messages = load_json(MULTIPLIER_MESSAGES_FILE, {})
all_items = {}
last_stock_data = None
last_weather_data = None
_items_cache_time = 0
_multipliers_cache_time = 0
multipliers_cache = {}
_processing = False

def load_items():
    global all_items, _items_cache_time
    if time.time() - _items_cache_time < CACHE_TTL and all_items:
        return all_items

    cached = load_json(ITEMS_CACHE_FILE)
    if cached.get('items') and time.time() - cached.get('timestamp', 0) < CACHE_TTL:
        all_items = cached['items']
        _items_cache_time = cached['timestamp']
        return all_items

    data = fetch_with_retry(API_URL)
    if data:
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
    else:
        logger.warning("⚠️ Не удалось загрузить предметы, используется кеш")
    return all_items

def get_multipliers():
    global multipliers_cache, _multipliers_cache_time
    if time.time() - _multipliers_cache_time < CACHE_TTL and multipliers_cache:
        return multipliers_cache

    data = fetch_with_retry(API_URL)
    if data:
        mults = data.get('fruitMultipliers', [])
        mults.sort(key=lambda x: x.get('multiplier', 0), reverse=True)
        multipliers_cache = mults
        _multipliers_cache_time = time.time()
        logger.info(f"Мультипликаторы обновлены: {len(mults)}")
        return mults
    else:
        logger.warning("⚠️ Не удалось загрузить мультипликаторы")
    return multipliers_cache

def get_predictions_data():
    return fetch_with_retry(PREDICT_URL, max_retries=3, timeout=10)

# ================= ФОРМАТИРОВАНИЕ СООБЩЕНИЙ =================
def format_predict_msg(data):
    if not data:
        return "❌ Ошибка данных"
    now = time.time()
    msk_time = get_msk_time().strftime('%H:%M:%S')
    msg = f"🔮 <b>ПРЕДСКАЗАНИЯ</b>\n🔄 <i>Обновлено: {msk_time} МСК</i>\n"
    msg += "═" * 30 + "\n\n"

    weathers = sorted([w for w in data.get('weathers', []) if w.get('timestamp', 0) > now], key=lambda x: x['timestamp'])[:10]
    if weathers:
        msg += "🌙 <b>ЛУННЫЕ ФАЗЫ</b>\n"
        for w in weathers:
            name = w.get('name', 'Неизвестно')
            ts = w.get('timestamp', 0)
            info = WEATHER_TYPES.get(name, {"emoji": "🌙", "name": name})
            time_str = format_timestamp_full(ts)
            msg += f"  {info['emoji']} {info['name']} — {time_str}\n"
        msg += "\n"

    important = ["Dragon's Breath", "Moon Bloom", "Venom Spitter", "Sunflower",
                 "Legendary Sprinkler", "Super Sprinkler", "Super Watering Can",
                 "Hypno Bloom"]
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
            time_str = format_timestamp_full(ts)
            msg += f"    • {name} — {time_str}\n"
        msg += "\n"
    else:
        msg += "  ⏳ <b>ОЖИДАЙТЕ В БЛИЖАЙШЕЕ ВРЕМЯ:</b> Нет\n\n"

    if past:
        msg += "  🕐 <b>БЫЛИ В СТОКЕ:</b>\n"
        for i in past:
            name = i.get('name', 'Неизвестно')
            ts = i.get('timestamp', 0)
            time_str = format_timestamp_full(ts)
            msg += f"    • {name} — {time_str}\n"
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
    phase = weather.get('phase', '')
    
    # Проверяем активные эффекты в weathers
    for key in weathers.keys():
        val = weathers.get(key)
        if key == "night" or key == "day" or key == "sunset" or key == "moon":
            continue
        if val is True or val == "true":
            return key
        if isinstance(val, dict) and val.get("playing") is True:
            return key
    
    # Проверяем фазу
    if phase:
        for key in WEATHER_TYPES:
            if key == "Clear":
                continue
            if key.lower() == phase.lower():
                return key
    
    return "Clear"

def get_stock_signature(data):
    signature = {}
    for shop_type in ["SeedShop_Normal", "CrateShop", "GearShop"]:
        for item in data.get("shops", {}).get(shop_type, []):
            stock = item.get('stock', 0)
            if stock > 0:
                signature[item.get('name')] = stock
    return signature

def get_changes(old, new):
    added = {n: s for n, s in new.items() if n not in old}
    removed = {n: s for n, s in old.items() if n not in new}
    changed = {n: {'old': old[n], 'new': new[n]} for n in new if n in old and old[n] != new[n]}
    return added, removed, changed

def format_stock_update_message(added, changed, removed):
    msk_time = get_msk_time()
    cat_emojis = {"Семена": "🌾", "Ящики": "📦", "Снаряжение": "⚙️"}
    categories = {}

    if not all_items:
        logger.warning("⚠️ all_items пуст, принудительная загрузка...")
        load_items()

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
        logger.warning("⚠️ format_stock_update_message: нет категорий для отображения")
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

def get_admin_menu(chat_id):
    s = group_settings.get(str(chat_id), {"subscriptions": [], "weather": False})
    weather_status = "✅" if s.get("weather", False) else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Семена", callback_data="acat_Семена")],
        [InlineKeyboardButton("📦 Ящики", callback_data="acat_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="acat_Снаряжение")],
        [InlineKeyboardButton("🌤️ Погода", callback_data="acat_Погода")],
        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="adm_clear")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="adm_close")]
    ])

def get_weather_menu(chat_id, page=0):
    weather_list = [
        "Rain", "Snowfall", "Thunderstorm",
        "Blood Moon", "Gold Moon", "Rainbow Moon", "Mega Moon",
        "Chained Moon", "Pizza Moon", "Solar Eclipse",
        "Starfall", "Rainbow", "Aurora", "Sunburst", "Fog", "Wind"
    ]
    
    s = group_settings.get(str(chat_id), {})
    selected = s.get("selected_weather", [])
    
    per_page = 10
    total_pages = max(1, (len(weather_list) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    
    start = page * per_page
    current_items = weather_list[start:start + per_page]
    
    keyboard = []
    for weather in current_items:
        emoji = WEATHER_TYPES.get(weather, {}).get("emoji", "🌤️")
        name = WEATHER_TYPES.get(weather, {}).get("name", weather)
        status = "✅" if weather in selected else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {emoji} {name}", 
                                             callback_data=f"wthr_{weather}")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"wpage_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"wpage_{page+1}"))
    if nav:
        keyboard.append(nav)
    
    count = len(selected)
    keyboard.append([InlineKeyboardButton(f"📊 Выбрано: {count} из {len(weather_list)}", callback_data="ignore")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_main")])
    
    return InlineKeyboardMarkup(keyboard)

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
async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_id = chat.id
    chat_type = chat.type
    title = chat.title or "Без названия"
    
    msg = f"🆔 <b>ИНФОРМАЦИЯ О ЧАТЕ</b>\n\n"
    msg += f"📌 Название: <b>{title}</b>\n"
    msg += f"🆔 ID: <code>{chat_id}</code>\n"
    msg += f"📂 Тип: <b>{chat_type}</b>"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if is_group_blocked(chat_id):
        return
    logger.info(f"🔮 /predict от {chat_id}")
    data = get_predictions_data()
    if not data:
        await update.message.reply_text("❌ Не удалось получить данные предсказаний")
        return
    msg = format_predict_msg(data)
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    predict_messages[chat_id] = sent.message_id
    save_json(PREDICT_MESSAGES_FILE, predict_messages)

async def multipliers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if is_group_blocked(chat_id):
        return
    logger.info(f"📊 /multipliers от {chat_id}")
    msg = format_multipliers_message()
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    multiplier_messages[chat_id] = sent.message_id
    save_json(MULTIPLIER_MESSAGES_FILE, multiplier_messages)

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if is_group_blocked(chat_id):
        return
    logger.info(f"🌤️ /weather от {update.effective_user.id}")
    try:
        data = fetch_with_retry(API_URL)
        if data:
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
            await update.message.reply_text("❌ Не удалось получить данные о погоде")
    except Exception as e:
        logger.error(f"Ошибка /weather: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if is_group_blocked(chat_id):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Эта команда работает только в личных сообщениях с ботом!")
        return
    uid = str(update.effective_user.id)
    logger.info(f"🚀 /start от {uid}")
    if uid not in user_settings:
        user_settings[uid] = {"subscriptions": []}
    user_settings[uid]['last_active'] = time.time()
    save_json(DATA_FILE, user_settings)
    load_items()
    await update.message.reply_text(
        "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
        "Выбери категорию, затем нажми на предмет.\n"
        "✅ — получать уведомления\n"
        "❌ — не получать\n\n"
        f"📦 <b>Всего предметов:</b> {len(all_items)}\n\n"
        "🔮 /predict — предсказания лун и стока\n"
        "🌤️ /weather — текущая погода\n"
        "📊 /multipliers — мультипликаторы предметов\n"
        "🆔 /getid — узнать ID этого чата",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if is_group_blocked(str(chat_id)):
        return
    logger.info(f"👑 /admin от {user_id} в чате {chat_id}")

    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав на использование админ-панели!")
        return

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Вы не являетесь администратором этой группы!")
            return
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        await update.message.reply_text("❌ Не удалось проверить ваши права. Убедитесь, что бот является администратором группы.")
        return

    cid_s = str(chat_id)
    if cid_s not in group_settings:
        group_settings[cid_s] = {"subscriptions": [], "weather": False, "selected_weather": []}
        save_json(GROUP_SETTINGS_FILE, group_settings)

    await update.message.reply_text(
        "👑 <b>Админ-панель группы</b>\n\n"
        "Настройте уведомления для этой группы:\n\n"
        "✅ — уведомления включены для этого раздела\n"
        "❌ — уведомления выключены",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu(chat_id)
    )

# ================= КОМАНДЫ ДЛЯ ТОПИКОВ =================
async def start_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_group_blocked(str(chat_id)):
        return
    user_id = update.effective_user.id
    thread_id = update.effective_message.message_thread_id
    
    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах с топиками!")
        return
    
    if not thread_id:
        await update.message.reply_text("❌ Эта команда должна быть вызвана в топике!")
        return
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Только администраторы группы могут настраивать уведомления в топиках!")
            return
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        await update.message.reply_text("❌ Не удалось проверить ваши права!")
        return
    
    cid_s = str(chat_id)
    if cid_s not in group_settings:
        group_settings[cid_s] = {"subscriptions": [], "weather": False, "selected_weather": []}
    
    if "stock_topics" not in group_settings[cid_s]:
        group_settings[cid_s]["stock_topics"] = []
    
    if thread_id not in group_settings[cid_s]["stock_topics"]:
        group_settings[cid_s]["stock_topics"].append(thread_id)
        save_json(GROUP_SETTINGS_FILE, group_settings)
        await update.message.reply_text(
            f"✅ Этот топик добавлен для уведомлений о СТОКЕ!\n\n"
            f"📌 Теперь все уведомления о стоке будут приходить сюда.\n"
            f"🆔 ID топика: `{thread_id}`",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("ℹ️ Этот топик уже добавлен для уведомлений о стоке!")

async def stop_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_group_blocked(str(chat_id)):
        return
    user_id = update.effective_user.id
    thread_id = update.effective_message.message_thread_id
    
    if chat_id > 0 or not thread_id:
        await update.message.reply_text("❌ Эта команда работает только в топиках групп!")
        return
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Только администраторы могут управлять этим!")
            return
    except:
        pass
    
    cid_s = str(chat_id)
    if cid_s in group_settings and "stock_topics" in group_settings[cid_s]:
        if thread_id in group_settings[cid_s]["stock_topics"]:
            group_settings[cid_s]["stock_topics"].remove(thread_id)
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await update.message.reply_text("✅ Этот топик удалён из уведомлений о стоке!")
        else:
            await update.message.reply_text("ℹ️ Этот топик не был добавлен для уведомлений о стоке")
    else:
        await update.message.reply_text("ℹ️ Нет настроек для этого топика")

async def start_weather_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_group_blocked(str(chat_id)):
        return
    user_id = update.effective_user.id
    thread_id = update.effective_message.message_thread_id
    
    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах с топиками!")
        return
    
    if not thread_id:
        await update.message.reply_text("❌ Эта команда должна быть вызвана в топике!")
        return
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Только администраторы группы могут настраивать уведомления в топиках!")
            return
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        await update.message.reply_text("❌ Не удалось проверить ваши права!")
        return
    
    cid_s = str(chat_id)
    if cid_s not in group_settings:
        group_settings[cid_s] = {"subscriptions": [], "weather": False, "selected_weather": []}
    
    if "weather_topics" not in group_settings[cid_s]:
        group_settings[cid_s]["weather_topics"] = []
    
    if thread_id not in group_settings[cid_s]["weather_topics"]:
        group_settings[cid_s]["weather_topics"].append(thread_id)
        save_json(GROUP_SETTINGS_FILE, group_settings)
        await update.message.reply_text(
            f"✅ Этот топик добавлен для уведомлений о ПОГОДЕ!\n\n"
            f"🌤️ Теперь все уведомления о погоде будут приходить сюда.\n"
            f"🆔 ID топика: `{thread_id}`",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("ℹ️ Этот топик уже добавлен для уведомлений о погоде!")

async def stop_weather_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_group_blocked(str(chat_id)):
        return
    user_id = update.effective_user.id
    thread_id = update.effective_message.message_thread_id
    
    if chat_id > 0 or not thread_id:
        await update.message.reply_text("❌ Эта команда работает только в топиках групп!")
        return
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Только администраторы могут управлять этим!")
            return
    except:
        pass
    
    cid_s = str(chat_id)
    if cid_s in group_settings and "weather_topics" in group_settings[cid_s]:
        if thread_id in group_settings[cid_s]["weather_topics"]:
            group_settings[cid_s]["weather_topics"].remove(thread_id)
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await update.message.reply_text("✅ Этот топик удалён из уведомлений о погоде!")
        else:
            await update.message.reply_text("ℹ️ Этот топик не был добавлен для уведомлений о погоде")
    else:
        await update.message.reply_text("ℹ️ Нет настроек для этого топика")

# ================= КОМАНДЫ ДЛЯ ЧЁРНОГО СПИСКА =================
async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав!")
        return
    
    if not blacklist:
        await update.message.reply_text("📋 Чёрный список пуст.")
        return
    
    msg = "🚫 <b>ЗАБЛОКИРОВАННЫЕ ГРУППЫ/КАНАЛЫ</b>\n\n"
    for cid in blacklist:
        try:
            chat = await context.bot.get_chat(int(cid))
            name = chat.title or "Без названия"
            msg += f"• {name} (ID: <code>{cid}</code>)\n"
        except:
            msg += f"• ID: <code>{cid}</code> (недоступно)\n"
    msg += f"\nВсего: {len(blacklist)} групп"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Укажите ID группы или канала:\n\n"
            "Пример: <code>/blacklist_add -1001234567890</code>\n\n"
            "💡 Чтобы узнать ID, используйте <code>/getid</code> в группе.",
            parse_mode=ParseMode.HTML
        )
        return
    
    chat_id = args[0]
    if chat_id in blacklist:
        await update.message.reply_text(f"ℹ️ Группа <code>{chat_id}</code> уже в чёрном списке.", parse_mode=ParseMode.HTML)
        return
    
    try:
        chat = await context.bot.get_chat(int(chat_id))
        name = chat.title or "Без названия"
    except:
        name = "Неизвестно"
    
    blacklist.append(chat_id)
    save_blacklist()
    await update.message.reply_text(
        f"✅ Группа/канал <b>{name}</b> (<code>{chat_id}</code>) добавлена в чёрный список!\n\n"
        f"📌 Теперь бот будет игнорировать все сообщения и уведомления в этом чате.",
        parse_mode=ParseMode.HTML
    )

async def blacklist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Укажите ID группы или канала:\n\n"
            "Пример: <code>/blacklist_remove -1001234567890</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    chat_id = args[0]
    if chat_id not in blacklist:
        await update.message.reply_text(f"ℹ️ Группа <code>{chat_id}</code> не в чёрном списке.", parse_mode=ParseMode.HTML)
        return
    
    try:
        chat = await context.bot.get_chat(int(chat_id))
        name = chat.title or "Без названия"
    except:
        name = "Неизвестно"
    
    blacklist.remove(chat_id)
    save_blacklist()
    await update.message.reply_text(
        f"✅ Группа/канал <b>{name}</b> (<code>{chat_id}</code>) удалена из чёрного списка!\n\n"
        f"📌 Бот снова будет работать в этом чате.",
        parse_mode=ParseMode.HTML
    )

# ================= ОБРАБОТЧИК КНОПОК =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = str(query.from_user.id)
    cid = str(query.message.chat_id)

    if is_group_blocked(cid):
        await query.answer()
        return

    logger.info(f"📥 ПОЛУЧЕН callback: {data} от {uid}")

    try:
        await query.answer()

        # ===== ПРОВЕРКА ПРАВ ДЛЯ АДМИН-КНОПОК =====
        if data.startswith("admin_") or data.startswith("acat_") or data.startswith("pg_aitm_") or data.startswith("aitm_") or data.startswith("adm_") or data.startswith("wthr_") or data.startswith("wpage_"):
            try:
                member = await context.bot.get_chat_member(int(cid), int(uid))
                if member.status not in ['creator', 'administrator']:
                    await query.answer("❌ Только администраторы могут настраивать группу!")
                    return
            except Exception as e:
                logger.error(f"Ошибка проверки прав для кнопки: {e}")
                await query.answer("❌ Не удалось проверить права!")
                return

        # ===== ОБЫЧНЫЕ КНОПКИ =====
        if data == "menu":
            await safe_edit(query, "🌱 <b>Grow a Garden 2 Tracker</b>\n\nВыбери категорию, затем нажми на предмет.\n✅ — получать уведомления\n❌ — не получать", reply_markup=get_main_menu())

        elif data == "show_mults":
            msg = format_multipliers_message()
            sent = await safe_edit(query, msg, reply_markup=get_main_menu())
            if sent:
                multiplier_messages[cid] = sent.message_id
                save_json(MULTIPLIER_MESSAGES_FILE, multiplier_messages)
            return

        elif data == "full_stock":
            try:
                data_api = fetch_with_retry(API_URL)
                if data_api:
                    msg = format_full_stock_message(data_api)
                    await safe_edit(query, msg, reply_markup=get_main_menu())
                else:
                    await safe_edit(query, "❌ Ошибка API", reply_markup=get_main_menu())
            except Exception as e:
                logger.error(f"Ошибка full_stock: {e}")
                await safe_edit(query, f"❌ Ошибка: {e}", reply_markup=get_main_menu())

        elif data == "my_subs":
            subscriptions = user_settings.get(uid, {}).get("subscriptions", [])
            if not subscriptions:
                await safe_edit(query, "📋 <b>Нет подписок</b>", reply_markup=get_main_menu())
            else:
                await safe_edit(query, "📋 <b>Твои подписки</b>", reply_markup=get_subscriptions_menu(uid))
            return

        elif data.startswith("sub_page_"):
            page = int(data.split("_")[2])
            await safe_edit(query, reply_markup=get_subscriptions_menu(uid, page))
            return

        elif data.startswith("unsub_"):
            item_name = data.replace("unsub_", "")
            subscriptions = user_settings.get(uid, {}).get("subscriptions", [])
            subscriptions = [s for s in subscriptions if s != item_name]
            user_settings[uid]["subscriptions"] = subscriptions
            save_json(DATA_FILE, user_settings)
            if subscriptions:
                await safe_edit(query, "📋 <b>Твои подписки</b>", reply_markup=get_subscriptions_menu(uid))
            else:
                await safe_edit(query, "📋 <b>Нет подписок</b>", reply_markup=get_main_menu())
            return

        elif data.startswith("cat_"):
            cat = data.split("_")[1]
            await safe_edit(query, f"📂 Категория: <b>{cat}</b>", reply_markup=get_items_menu(uid, cat))

        elif data.startswith("pg_itm_"):
            _, _, cat, pg = data.split("_")
            await safe_edit(query, reply_markup=get_items_menu(uid, cat, int(pg)))

        elif data.startswith("itm_"):
            _, cat, pg, name = data.split("_", 3)
            if uid not in user_settings:
                user_settings[uid] = {"subscriptions": []}
            subs = user_settings[uid]["subscriptions"]
            if name in subs:
                subs.remove(name)
                await query.answer(f"❌ {name} удалён")
            else:
                subs.append(name)
                await query.answer(f"✅ {name} добавлен")
            save_json(DATA_FILE, user_settings)
            await safe_edit(query, reply_markup=get_items_menu(uid, cat, int(pg)))

        # ===== АДМИН-КНОПКИ =====
        elif data == "admin_main":
            await safe_edit(query, "👑 <b>Админ-панель группы</b>", reply_markup=get_admin_menu(cid))

        elif data == "adm_close":
            await query.message.delete()
            return

        elif data == "acat_Погода":
            await safe_edit(query, "🌤️ <b>НАСТРОЙКА ПОГОДЫ</b>\n\n"
                                  "Выберите виды погоды для уведомлений:\n"
                                  "✅ — уведомления будут приходить\n"
                                  "❌ — уведомления не будут приходить",
                           reply_markup=get_weather_menu(cid))

        elif data.startswith("wthr_"):
            weather_key = data.replace("wthr_", "")
            s = group_settings.get(cid, {"subscriptions": [], "weather": False})
            
            if "selected_weather" not in s:
                s["selected_weather"] = []
            
            if weather_key in s["selected_weather"]:
                s["selected_weather"].remove(weather_key)
                await query.answer(f"❌ {WEATHER_TYPES.get(weather_key, {}).get('name', weather_key)} удалена")
            else:
                s["selected_weather"].append(weather_key)
                await query.answer(f"✅ {WEATHER_TYPES.get(weather_key, {}).get('name', weather_key)} добавлена")
            
            group_settings[cid] = s
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await safe_edit(query, reply_markup=get_weather_menu(cid))

        elif data.startswith("wpage_"):
            page = int(data.replace("wpage_", ""))
            await safe_edit(query, reply_markup=get_weather_menu(cid, page))

        elif data.startswith("acat_"):
            cat = data.replace("acat_", "")
            if cat == "Погода":
                return
            await safe_edit(query, f"👑 Настройка: <b>{cat}</b>", reply_markup=get_items_menu(uid, cat, is_admin=True, chat_id=cid))

        elif data.startswith("pg_aitm_"):
            _, _, cat, pg = data.split("_")
            await safe_edit(query, reply_markup=get_items_menu(uid, cat, int(pg), is_admin=True, chat_id=cid))

        elif data.startswith("aitm_"):
            _, cat, pg, name = data.split("_", 3)
            s = group_settings.get(cid, {"subscriptions": [], "weather": False})
            if name in s["subscriptions"]:
                s["subscriptions"].remove(name)
                await query.answer(f"❌ {name} удалён из группы")
            else:
                s["subscriptions"].append(name)
                await query.answer(f"✅ {name} добавлен в группу")
            group_settings[cid] = s
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await safe_edit(query, reply_markup=get_items_menu(uid, cat, int(pg), is_admin=True, chat_id=cid))

        elif data == "adm_clear":
            s = group_settings.get(cid, {"subscriptions": [], "weather": False})
            s["subscriptions"] = []
            s["selected_weather"] = []
            group_settings[cid] = s
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.answer("🗑️ Все подписки и настройки погоды очищены!")
            await safe_edit(query, "👑 <b>Админ-панель группы</b>\n\nВсе подписки и настройки погоды очищены!", reply_markup=get_admin_menu(cid))

    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}\n{traceback.format_exc()}")
        try:
            await query.answer("❌ Ошибка")
        except:
            pass

# ================= ФОНОВЫЕ ЗАДАЧИ =================
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data, last_weather_data, _processing

    if _processing:
        logger.info("⏭️ Пропускаем проверку, уже идёт обработка")
        return

    try:
        _processing = True
        data = fetch_with_retry(API_URL, max_retries=2, timeout=10)
        if not data:
            logger.warning("⚠️ Не удалось получить данные для проверки")
            return

        logger.info("🔍 Проверка стока и погоды...")

        # ===== ПОГОДА =====
        new_w = get_weather_type(data)
        weather_name = WEATHER_TYPES.get(new_w, {}).get('name', new_w)
        logger.info(f"🌤️ Текущая погода: {weather_name} (ключ: {new_w})")

        if last_weather_data is not None:
            if new_w != last_weather_data:
                old_name = WEATHER_TYPES.get(last_weather_data, {}).get('name', last_weather_data)
                logger.info(f"🔄 Погода изменилась: {old_name} → {weather_name}")
                
                for cid, s in group_settings.items():
                    if is_group_blocked(cid):
                        continue
                    
                    weather_topics = s.get("weather_topics", [])
                    if not weather_topics:
                        continue
                    
                    selected_weather = s.get("selected_weather", [])
                    
                    if not selected_weather or new_w in selected_weather:
                        msg = format_weather_message(new_w)
                        for thread_id in weather_topics:
                            try:
                                await context.bot.send_message(
                                    chat_id=int(cid),
                                    message_thread_id=thread_id,
                                    text=msg,
                                    parse_mode=ParseMode.HTML
                                )
                                logger.info(f"🌤️ Погода {new_w} отправлена в топик {thread_id} группы {cid}")
                            except Exception as e:
                                logger.error(f"❌ Ошибка отправки погоды в топик {thread_id}: {e}")
                    else:
                        logger.info(f"⏭️ Погода {new_w} не выбрана для группы {cid}")
            else:
                logger.info("🌤️ Погода не изменилась")
        else:
            logger.info("🌤️ Первый запуск, погода запомнена")
        last_weather_data = new_w

        # ===== СТОК =====
        new_stock = get_stock_signature(data)
        logger.info(f"📊 Новый сток (положительные позиции): {len(new_stock)}")

        if last_stock_data is None:
            last_stock_data = new_stock
            logger.info("🔄 Первый запуск, сток запомнен")
            return

        old_stock = last_stock_data.copy()
        added, removed, changed = get_changes(old_stock, new_stock)
        logger.info(f"📈 Изменения: +{len(added)} -{len(removed)} *{len(changed)}")

        if added or removed or changed:
            last_stock_data = new_stock

            for name, stock in added.items():
                save_history(name, 0, stock)
            for name, change in changed.items():
                save_history(name, change['old'], change['new'])
            for name in removed:
                save_history(name, old_stock.get(name, 0), 0)

            # --- ГРУППЫ ---
            logger.info(f"📢 Отправка уведомлений в топики...")
            for cid, s in group_settings.items():
                if is_group_blocked(cid):
                    logger.info(f"⏭️ Группа {cid} в чёрном списке, пропускаем")
                    continue
                subs = s.get("subscriptions", [])
                stock_topics = s.get("stock_topics", [])
                
                if not subs or not stock_topics:
                    logger.info(f"⏭️ Группа {cid}: нет подписок или топиков, пропускаем")
                    continue

                g_added = {n: s_val for n, s_val in added.items() if n in subs}
                g_changed = {n: c for n, c in changed.items() if n in subs}

                logger.info(f"📊 Группа {cid}: добавлено {len(g_added)}, изменено {len(g_changed)}")

                if g_added or g_changed:
                    msg = format_stock_update_message(g_added, g_changed, {})
                    if msg:
                        for thread_id in stock_topics:
                            try:
                                await context.bot.send_message(
                                    chat_id=int(cid),
                                    message_thread_id=thread_id,
                                    text=msg,
                                    parse_mode=ParseMode.HTML
                                )
                                logger.info(f"✅ Уведомление о стоке отправлено в топик {thread_id} группы {cid}")
                            except Exception as e:
                                logger.error(f"❌ Ошибка отправки стока в топик {thread_id}: {e}")
                    else:
                        logger.warning(f"⚠️ format_stock_update_message вернул None для группы {cid}")
                else:
                    logger.info(f"⏭️ Группа {cid}: нет изменений по подпискам")

            # --- ПОЛЬЗОВАТЕЛИ ---
            logger.info(f"📢 Отправка уведомлений пользователям...")
            for uid, s in user_settings.items():
                subs = s.get("subscriptions", [])
                if not subs:
                    continue

                u_added = {n: s_val for n, s_val in added.items() if n in subs}
                u_changed = {n: c for n, c in changed.items() if n in subs}

                if u_added or u_changed:
                    msg = format_stock_update_message(u_added, u_changed, {})
                    if msg:
                        try:
                            await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode=ParseMode.HTML)
                            logger.info(f"✅ Уведомление о стоке отправлено пользователю {uid}")
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки стока пользователю {uid}: {e}")

        else:
            logger.info("✅ Изменений стока нет")
            last_stock_data = new_stock

        # ===== РЕЗЕРВНОЕ КОПИРОВАНИЕ =====
        backup_file = os.path.join(BACKUP_DIR, f"backup_{get_msk_time().strftime('%Y-%m-%d')}.json")
        if not os.path.exists(backup_file):
            backup_data()

        # ===== ОЧИСТКА НЕАКТИВНЫХ ПОЛЬЗОВАТЕЛЕЙ =====
        if get_msk_time().weekday() == 0 and get_msk_time().hour == 3:
            clean_inactive_users()

    except Exception as e:
        logger.error(f"Ошибка в check_and_notify: {e}")
        traceback.print_exc()
    finally:
        _processing = False

async def update_predictions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_predictions_data()
        if not data:
            return
        msg = format_predict_msg(data)
        for cid, mid in list(predict_messages.items()):
            if is_group_blocked(cid):
                predict_messages.pop(cid, None)
                continue
            try:
                await context.bot.edit_message_text(chat_id=int(cid), message_id=mid, text=msg, parse_mode=ParseMode.HTML)
            except Exception:
                predict_messages.pop(cid, None)
        save_json(PREDICT_MESSAGES_FILE, predict_messages)
    except Exception as e:
        logger.error(f"Ошибка update_predictions: {e}")

async def update_multipliers_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        global multipliers_cache, _multipliers_cache_time
        multipliers_cache = {}
        _multipliers_cache_time = 0
        msg = format_multipliers_message()
        for cid, mid in list(multiplier_messages.items()):
            if is_group_blocked(cid):
                multiplier_messages.pop(cid, None)
                continue
            try:
                await context.bot.edit_message_text(chat_id=int(cid), message_id=mid, text=msg, parse_mode=ParseMode.HTML)
            except Exception:
                multiplier_messages.pop(cid, None)
        save_json(MULTIPLIER_MESSAGES_FILE, multiplier_messages)
    except Exception as e:
        logger.error(f"Ошибка update_multipliers: {e}")

# ================= ЗАПУСК =================
def main():
    logger.info("🚀 Запуск бота...")

    os.makedirs(BACKUP_DIR, exist_ok=True)

    # ===== ПРИНУДИТЕЛЬНАЯ ОЧИСТКА ВЕБХУКА =====
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true",
            timeout=10
        )
        if r.status_code == 200:
            logger.info("✅ Вебхук очищен успешно (HTTP)")
        else:
            logger.warning(f"⚠️ Ошибка очистки вебхука: {r.status_code} - {r.text}")
            
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo",
            timeout=10
        )
        if r.status_code == 200:
            webhook_info = r.json()
            logger.info(f"📡 Статус вебхука: {webhook_info}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось очистить вебхук: {e}")

    global user_settings, group_settings, predict_messages, multiplier_messages, blacklist
    user_settings = load_json(DATA_FILE, {})
    group_settings = load_json(GROUP_SETTINGS_FILE, {})
    predict_messages = load_json(PREDICT_MESSAGES_FILE, {})
    multiplier_messages = load_json(MULTIPLIER_MESSAGES_FILE, {})
    blacklist = load_json(BLACKLIST_FILE, [])

    import telegram
    logger.info(f"📦 Версия python-telegram-bot: {telegram.__version__}")

    load_items()
    get_multipliers()

    app = Application.builder() \
        .token(TOKEN) \
        .connect_timeout(30) \
        .read_timeout(30) \
        .build()

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))
        logger.info("✅ Вебхук удалён через bot.delete_webhook")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить вебхук через bot: {e}")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("multipliers", multipliers_command))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("start_topic", start_topic))
    app.add_handler(CommandHandler("stop_topic", stop_topic))
    app.add_handler(CommandHandler("start_weather_topic", start_weather_topic))
    app.add_handler(CommandHandler("stop_weather_topic", stop_weather_topic))
    app.add_handler(CommandHandler("blacklist", blacklist_command))
    app.add_handler(CommandHandler("blacklist_add", blacklist_add))
    app.add_handler(CommandHandler("blacklist_remove", blacklist_remove))
    app.add_handler(CallbackQueryHandler(button_handler))

    try:
        app.job_queue.run_repeating(check_and_notify, interval=10, first=5)
        app.job_queue.run_repeating(update_predictions_job, interval=30, first=10)
        app.job_queue.run_repeating(update_multipliers_job, interval=60, first=15)
        logger.info("✅ Фоновые задачи запущены")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска фоновых задач: {e}")

    logger.info("✅ Бот запущен! Доступны команды: /start, /admin, /weather, /predict, /multipliers, /getid, /start_topic, /stop_topic, /start_weather_topic, /stop_weather_topic, /blacklist, /blacklist_add, /blacklist_remove")

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            timeout=30
        )
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
