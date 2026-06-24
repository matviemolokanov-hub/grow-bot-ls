import requests
import json
import logging
import os
import time
import traceback
import shutil
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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

# ================= ТИПЫ ПОГОДЫ =================
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

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

def format_timestamp(ts):
    if not ts or ts == 0:
        return "—"
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime('%H:%M:%S')

def format_timestamp_with_date(ts):
    """Форматирует время с датой: 'сегодня в 21:58:00' или '24.06 21:58:00'"""
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
    """Запрос к API с повторными попытками"""
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
    """Создание резервной копии настроек"""
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
    """Удаление неактивных пользователей"""
    try:
        removed = 0
        for uid, s in list(user_settings.items()):
            if 'last_active' in s:
                if time.time() - s['last_active'] > INACTIVE_DAYS * 24 * 3600:
                    del user_settings[uid]
                    removed += 1
            else:
                # Если нет last_active, добавляем сейчас и пропускаем
                user_settings[uid]['last_active'] = time.time()
        if removed > 0:
            save_json(DATA_FILE, user_settings)
            logger.info(f"🧹 Удалено неактивных пользователей: {removed}")
        return removed
    except Exception as e:
        logger.error(f"Ошибка очистки пользователей: {e}")
        return 0

def save_history(item, old_stock, new_stock):
    """Сохранение истории изменений стока"""
    try:
        history = load_json(HISTORY_FILE, {})
        if item not in history:
            history[item] = []
        history[item].append({
            'time': get_msk_time().isoformat(),
            'old': old_stock,
            'new': new_stock
        })
        # Храним последние 20 записей
        history[item] = history[item][-20:]
        save_json(HISTORY_FILE, history)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения истории для {item}: {e}")
        return False

def get_item_history(item, limit=10):
    """Получение истории изменений предмета"""
    history = load_json(HISTORY_FILE, {})
    if item not in history:
        return []
    return history[item][-limit:]

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
_pending_changes = {}
_last_notify_time = {}

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

    # Лунные фазы (с датой)
    weathers = sorted([w for w in data.get('weathers', []) if w.get('timestamp', 0) > now], key=lambda x: x['timestamp'])[:10]
    if weathers:
        msg += "🌙 <b>ЛУННЫЕ ФАЗЫ</b>\n"
        for w in weathers:
            name = w.get('name', 'Неизвестно')
            ts = w.get('timestamp', 0)
            info = WEATHER_TYPES.get(name, {"emoji": "🌙", "name": name})
            time_str = format_timestamp_with_date(ts)
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
            time_str = format_timestamp_with_date(ts)
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
            msg += f"    • {name} — {format_timestamp_with_date(ts)}\n"
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
    for key in WEATHER_TYPES:
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
    logger.info(f"📊 /multipliers от {chat_id}")
    msg = format_multipliers_message()
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    multiplier_messages[chat_id] = sent.message_id
    save_json(MULTIPLIER_MESSAGES_FILE, multiplier_messages)

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Эта команда работает только в личных сообщениях с ботом!")
        return
    uid = str(update.effective_user.id)
    logger.info(f"🚀 /start от {uid}")
    if uid not in user_settings:
        user_settings[uid] = {"subscriptions": []}
    # Обновляем время последней активности
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
        "📊 /multipliers — мультипликаторы предметов",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    logger.info(f"👑 /admin от {user_id} в чате {chat_id}")

    # Проверка: команда только для групп
    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    # Проверка: является ли пользователь администратором группы
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['creator', 'administrator']:
            await update.message.reply_text("❌ Только администраторы группы могут использовать эту команду!")
            return
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        await update.message.reply_text("❌ Не удалось проверить ваши права. Убедитесь, что бот является администратором группы.")
        return

    cid_s = str(chat_id)
    if cid_s not in group_settings:
        group_settings[cid_s] = {"subscriptions": [], "weather": False}
        save_json(GROUP_SETTINGS_FILE, group_settings)

    await update.message.reply_text(
        "👑 <b>Админ-панель группы</b>\n\n"
        "Настройте уведомления стока и погоды:\n\n"
        "🌤️ Погода — уведомления о смене погоды\n\n"
        "✅ — предмет уже в списке\n"
        "❌ — не в списке",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu(chat_id)
    )

# ================= ОБРАБОТЧИК КНОПОК =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = str(query.from_user.id)
    cid = str(query.message.chat_id)

    logger.info(f"📥 ПОЛУЧЕН callback: {data} от {uid}")

    try:
        await query.answer()

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

        # АДМИНКА
        elif data == "admin_main":
            await safe_edit(query, "👑 <b>Админ-панель группы</b>", reply_markup=get_admin_menu(cid))

        elif data == "adm_close":
            await query.message.delete()
            return

        elif data.startswith("acat_"):
            cat = data.split("_")[1]
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

        elif data == "adm_tgl_w":
            s = group_settings.get(cid, {"subscriptions": [], "weather": False})
            s["weather"] = not s.get("weather", False)
            group_settings[cid] = s
            save_json(GROUP_SETTINGS_FILE, group_settings)
            status = "включены" if s["weather"] else "выключены"
            await query.answer(f"🌤️ Уведомления о погоде {status}")
            await safe_edit(query, reply_markup=get_admin_menu(cid))

        elif data == "adm_clear":
            s = group_settings.get(cid, {"subscriptions": [], "weather": False})
            s["subscriptions"] = []
            group_settings[cid] = s
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.answer("🗑️ Все подписки очищены")
            await safe_edit(query, "👑 <b>Админ-панель группы</b>\n\nВсе подписки очищены!", reply_markup=get_admin_menu(cid))

    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}\n{traceback.format_exc()}")
        try:
            await query.answer("❌ Ошибка")
        except:
            pass

# ================= ФОНОВЫЕ ЗАДАЧИ =================
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data, last_weather_data, _pending_changes, _last_notify_time

    try:
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
                msg = format_weather_message(new_w)
                sent_count = 0
                for cid, s in group_settings.items():
                    if s.get("weather"):
                        try:
                            await context.bot.send_message(chat_id=int(cid), text=msg, parse_mode=ParseMode.HTML)
                            sent_count += 1
                            logger.info(f"🌤️ Уведомление о погоде отправлено в группу {cid}")
                        except Exception as e:
                            logger.error(f"Ошибка отправки погоды в {cid}: {e}")
                if sent_count == 0:
                    logger.info("🌤️ Нет групп с включёнными уведомлениями о погоде")
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

        added, removed, changed = get_changes(last_stock_data, new_stock)
        logger.info(f"📈 Изменения: +{len(added)} -{len(removed)} *{len(changed)}")

        if added or removed or changed:
            # Сохраняем историю
            for name, stock in added.items():
                save_history(name, 0, stock)
            for name, change in changed.items():
                save_history(name, change['old'], change['new'])
            for name in removed:
                save_history(name, last_stock_data.get(name, 0), 0)

            # Буферизация изменений для групповых уведомлений
            current_time = time.time()
            for cid, s in group_settings.items():
                subs = s.get("subscriptions", [])
                if not subs:
                    continue

                # Собираем изменения для этой группы
                g_added = {n: s_val for n, s_val in added.items() if n in subs}
                g_changed = {n: c for n, c in changed.items() if n in subs}
                g_removed = {n for n in removed if n in subs}

                if g_added or g_changed or g_removed:
                    # Добавляем в буфер
                    if cid not in _pending_changes:
                        _pending_changes[cid] = {'added': {}, 'changed': {}, 'removed': set()}
                    # Объединяем изменения
                    for n, s_val in g_added.items():
                        _pending_changes[cid]['added'][n] = s_val
                    for n, c in g_changed.items():
                        _pending_changes[cid]['changed'][n] = c
                    for n in g_removed:
                        _pending_changes[cid]['removed'].add(n)

                    # Обновляем время последнего изменения
                    _last_notify_time[cid] = current_time

            # Отправляем уведомления пользователям сразу
            for uid, s in user_settings.items():
                subs = s.get("subscriptions", [])
                if not subs:
                    continue
                u_added = {n: s_val for n, s_val in added.items() if n in subs}
                u_changed = {n: c for n, c in changed.items() if n in subs}
                u_removed = {n for n in removed if n in subs}
                if u_added or u_changed or u_removed:
                    msg = format_stock_update_message(u_added, u_changed, u_removed)
                    if msg:
                        try:
                            await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode=ParseMode.HTML)
                            logger.info(f"📢 Сток отправлен пользователю {uid}")
                        except Exception as e:
                            logger.error(f"Ошибка отправки стока пользователю {uid}: {e}")

        last_stock_data = new_stock

        # ===== РЕЗЕРВНОЕ КОПИРОВАНИЕ (раз в день) =====
        backup_file = os.path.join(BACKUP_DIR, f"backup_{get_msk_time().strftime('%Y-%m-%d')}.json")
        if not os.path.exists(backup_file):
            backup_data()

        # ===== ОЧИСТКА НЕАКТИВНЫХ ПОЛЬЗОВАТЕЛЕЙ (раз в неделю) =====
        if get_msk_time().weekday() == 0 and get_msk_time().hour == 3:
            clean_inactive_users()

    except Exception as e:
        logger.error(f"Ошибка в check_and_notify: {e}")
        traceback.print_exc()

async def send_buffered_notifications(context: ContextTypes.DEFAULT_TYPE):
    """Отправка буферизированных уведомлений группам (раз в 2 минуты)"""
    global _pending_changes, _last_notify_time

    current_time = time.time()
    for cid, last_time in list(_last_notify_time.items()):
        # Если прошло больше 2 минут с последнего изменения
        if current_time - last_time > 120:
            if cid in _pending_changes:
                changes = _pending_changes[cid]
                msg = format_stock_update_message(
                    changes['added'],
                    changes['changed'],
                    changes['removed']
                )
                if msg:
                    try:
                        await context.bot.send_message(chat_id=int(cid), text=msg, parse_mode=ParseMode.HTML)
                        logger.info(f"📢 Буферизированный сток отправлен в группу {cid}")
                    except Exception as e:
                        logger.error(f"Ошибка отправки буферизированного стока в {cid}: {e}")
                # Очищаем буфер
                del _pending_changes[cid]
                del _last_notify_time[cid]

async def update_predictions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_predictions_data()
        if not data:
            return
        msg = format_predict_msg(data)
        for cid, mid in list(predict_messages.items()):
            try:
                await context.bot.edit_message_text(chat_id=int(cid), message_id=mid, text=msg, parse_mode=ParseMode.HTML)
            except Exception:
                predict_messages.pop(cid, None)
        save_json(PREDICT_MESSAGES_FILE, predict_messages)
    except Exception as e:
        logger.error(f"Ошибка update_predictions: {e}")

async def update_multipliers_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        # Принудительно обновляем кеш
        global multipliers_cache, _multipliers_cache_time
        multipliers_cache = {}
        _multipliers_cache_time = 0
        msg = format_multipliers_message()
        for cid, mid in list(multiplier_messages.items()):
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

    # Создаём папку для бэкапов
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Очистка вебхука
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=10)
        logger.info(f"Вебхук очищен: {r.status_code}")
    except Exception as e:
        logger.warning(f"Не удалось очистить вебхук: {e}")

    global user_settings, group_settings, predict_messages, multiplier_messages
    user_settings = load_json(DATA_FILE, {})
    group_settings = load_json(GROUP_SETTINGS_FILE, {})
    predict_messages = load_json(PREDICT_MESSAGES_FILE, {})
    multiplier_messages = load_json(MULTIPLIER_MESSAGES_FILE, {})

    import telegram
    logger.info(f"📦 Версия python-telegram-bot: {telegram.__version__}")

    load_items()
    get_multipliers()

    app = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("multipliers", multipliers_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Фоновые задачи
    try:
        # Основная проверка (сток + погода)
        app.job_queue.run_repeating(check_and_notify, interval=10, first=5)

        # Отправка буферизированных уведомлений группам (раз в 2 минуты)
        app.job_queue.run_repeating(send_buffered_notifications, interval=120, first=60)

        # Обновление предсказаний
        app.job_queue.run_repeating(update_predictions_job, interval=30, first=10)

        # Обновление мультипликаторов
        app.job_queue.run_repeating(update_multipliers_job, interval=60, first=15)

        logger.info("✅ Фоновые задачи запущены")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска фоновых задач: {e}")

    logger.info("✅ Бот запущен! Доступны команды: /start, /admin, /weather, /predict, /multipliers")

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
