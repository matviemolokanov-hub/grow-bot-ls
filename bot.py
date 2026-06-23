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
PREDICT_URL = "https://grow-a-garden-2-tracker.onrender.com/api/predictions"

# Путь для сохранения данных (совместимо с Railway Volume)
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DATA_FILE = os.path.join(DATA_DIR, "user_settings.json")
GROUP_SETTINGS_FILE = os.path.join(DATA_DIR, "group_settings.json")
ITEMS_CACHE_FILE = os.path.join(DATA_DIR, "items_cache.json")
PREDICT_MSG_FILE = os.path.join(DATA_DIR, "predict_messages.json")

CACHE_TTL = 300
ADMIN_IDS = [7632708290]

# Важные предметы для мониторинга в предсказаниях
IMPORTANT_ITEMS = [
    "Mushroom", "Moon Bloom", "Legendary Sprinkler", 
    "Super Watering Can", "Super Sprinkler", "Dragon's Breath",
    "Pomegranate", "Dragon Fruit", "Cherry"
]

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
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

def format_ts_to_msk(ts):
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime('%H:%M:%S')

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

# Инициализация данных
user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
predict_messages = load_json(PREDICT_MSG_FILE, {}) # {chat_id: [msg_id_moon, msg_id_stock]}
all_items = {}
last_stock_data = None
last_weather_data = None
_items_cache_time = 0

# ================= ЛОГИКА API И КЕША =================

def get_all_items_from_api(data):
    items = {}
    for shop_type, category in [("SeedShop_Normal", "Семена"), ("CrateShop", "Ящики"), ("GearShop", "Снаряжение")]:
        for item in data.get("shops", {}).get(shop_type, []):
            name = item.get('name')
            if name:
                items[name] = {'name': name, 'rarity': item.get('rarity', 'Common'), 'category': category}
    return items

def load_items():
    global all_items, _items_cache_time
    if time.time() - _items_cache_time < CACHE_TTL and all_items:
        return all_items
    cached = load_json(ITEMS_CACHE_FILE)
    if cached and time.time() - cached.get('timestamp', 0) < CACHE_TTL:
        all_items = cached.get('items', {})
        _items_cache_time = cached.get('timestamp', 0)
        return all_items
    try:
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code == 200:
            all_items = get_all_items_from_api(resp.json())
            _items_cache_time = time.time()
            save_json(ITEMS_CACHE_FILE, {'items': all_items, 'timestamp': _items_cache_time})
    except: pass
    return all_items

def get_stock_signature(data):
    sig = {}
    for shop in ["SeedShop_Normal", "CrateShop", "GearShop"]:
        for item in data.get("shops", {}).get(shop, []):
            sig[item.get('name')] = item.get('stock', 0)
    return sig

def get_changes(old, new):
    added = {n: s for n, s in new.items() if n not in old}
    removed = {n: s for n, s in old.items() if n not in new}
    changed = {n: {'old': old[n], 'new': new[n]} for n in new if n in old and old[n] != new[n]}
    return added, removed, changed

def get_weather_type(data):
    weather = data.get('weather', {})
    weathers = weather.get('weathers', {})
    for key in WEATHER_TYPES.keys():
        if key == "Clear": continue
        val = weathers.get(key)
        if val is True or val == "true" or (isinstance(val, dict) and val.get("playing") is True):
            return key
    phase = weather.get('phase', '')
    return phase if phase in WEATHER_TYPES else "Clear"

# ================= ПРЕДСКАЗАНИЯ =================

def get_predictions_data():
    try:
        resp = requests.get(PREDICT_URL, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except: return None

def format_moon_predict_msg(data):
    if not data: return "❌ Ошибка получения данных API"
    now = time.time()
    rare_phases = ["Goldmoon", "Rainbow Moon", "Blood Moon", "Starfall", "Midas", "Aurora"]
    upcoming = sorted([w for w in data.get("weathers", []) if w['name'] in rare_phases and w['timestamp'] > now], key=lambda x: x['timestamp'])[:10]
    
    msg = "🔮 <b>ПРЕДСКАЗАНИЯ ЛУННЫХ ФАЗ</b>\n"
    msg += f"<i>Обновлено: {get_msk_time().strftime('%H:%M:%S')} МСК</i>\n\n"
    if not upcoming: msg += "В ближайшее время редких фаз нет."
    else:
        for w in upcoming:
            info = WEATHER_TYPES.get(w['name'], {"emoji": "❓", "name": w['name']})
            msg += f"{info['emoji']} <b>{info['name']}</b> — {format_ts_to_msk(w['timestamp'])}\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_stock_predict_msg(data):
    if not data: return "❌ Ошибка получения данных API"
    now = time.time()
    all_p = data.get("seeds", []) + data.get("gears", []) + data.get("props", [])
    upcoming = sorted([i for i in all_p if i['name'] in IMPORTANT_ITEMS and i['timestamp'] > now], key=lambda x: x['timestamp'])[:10]
    
    msg = "📦 <b>ПРЕДСКАЗАНИЯ РЕДКОГО СТОКА</b>\n"
    msg += f"<i>Обновлено: {get_msk_time().strftime('%H:%M:%S')} МСК</i>\n\n"
    if not upcoming: msg += "Редких предметов не ожидается."
    else:
        for i in upcoming: msg += f"• <b>{i['name']}</b> — {format_ts_to_msk(i['timestamp'])}\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

# ================= ФОРМАТИРОВАНИЕ ДЛЯ ЧАТОВ =================

def format_weather_message(weather_key):
    info = WEATHER_TYPES.get(weather_key, {"emoji": "☀️", "name": "Обычная"})
    msg = f"🌤️ <b>ПОГОДА ИЗМЕНИЛАСЬ!</b>\n"
    msg += f"{info['emoji']} <b>{info['name']}</b>\n"
    msg += f"🕐 {get_msk_time().strftime('%H:%M:%S')} МСК\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_group_stock_message(added, changed, removed):
    msk = get_msk_time().strftime('%H:%M:%S')
    msg = f"📢 <b>ОБНОВЛЕНИЕ СТОКА!</b>\n🕐 {msk} МСК\n" + "─" * 20 + "\n\n"
    has = False
    if added:
        has = True
        msg += "<b>🟢 НОВОЕ В НАЛИЧИИ:</b>\n"
        for n, s in added.items(): msg += f"• {n} — {s} шт.\n"
    if changed:
        has = True
        msg += "\n<b>🟡 ОБНОВЛЕНО:</b>\n"
        for n, c in changed.items(): 
            if c['new'] > 0: msg += f"• {n} — {c['new']} шт.\n"
    if removed:
        has = True
        msg += "\n<b>🔴 ЗАКОНЧИЛОСЬ:</b>\n"
        for n in removed: msg += f"• {n}\n"
    
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg if has else None

# ================= КЛАВИАТУРЫ =================

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Семена", callback_data="category_Семена")],
        [InlineKeyboardButton("📦 Ящики", callback_data="category_Ящики")],
        [InlineKeyboardButton("⚙️ Снаряжение", callback_data="category_Снаряжение")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="view_subscriptions")],
        [InlineKeyboardButton("📦 Весь сток", callback_data="show_full_stock")],
    ])

def get_admin_menu(chat_id):
    sets = group_settings.get(str(chat_id), {"subscriptions": [], "weather": False})
    w_status = "✅" if sets.get("weather") else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📋 Список подписок ({len(sets['subscriptions'])})", callback_data="admin_view_subs")],
        [InlineKeyboardButton("➕ Добавить", callback_data="admin_add_items"), InlineKeyboardButton("➖ Удалить", callback_data="admin_remove_items")],
        [InlineKeyboardButton(f"{w_status} Уведомления о погоде", callback_data="admin_toggle_weather")],
        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="admin_clear_all")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="admin_close")],
    ])

def get_items_menu(user_id, category, page=0, is_admin=False, chat_id=None):
    items = load_items()
    items_list = sorted([n for n, i in items.items() if i['category'] == category])
    
    if is_admin:
        current_subs = group_settings.get(str(chat_id), {}).get("subscriptions", [])
        cb_prefix = f"admin_toggle_{category}_{page}"
    else:
        current_subs = user_settings.get(str(user_id), {}).get("subscriptions", [])
        cb_prefix = f"item_{category}_{page}"

    per_page = 10
    start, end = page * per_page, (page + 1) * per_page
    current_page_items = items_list[start:end]
    
    keyboard = []
    for name in current_page_items:
        status = "✅" if name in current_subs else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {name}", callback_data=f"{cb_prefix}_{name}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"{'admin_page' if is_admin else 'page'}_{category}_{page-1}"))
    if end < len(items_list): nav.append(InlineKeyboardButton("▶️", callback_data=f"{'admin_page' if is_admin else 'page'}_{category}_{page+1}"))
    if nav: keyboard.append(nav)
    
    back_cb = "admin_back" if is_admin else "back_to_menu"
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(keyboard)

# ================= ОБРАБОТЧИКИ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_settings:
        user_settings[uid] = {"subscriptions": []}
        save_json(DATA_FILE, user_settings)
    await update.message.reply_text("🌱 <b>Grow a Garden 2 Tracker</b>\n\nИспользуй меню для подписок или команды:\n/weather — Погода\n/predict — Предсказания", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = get_predictions_data()
    m1 = await update.message.reply_text(format_moon_predict_msg(data), parse_mode=ParseMode.HTML)
    m2 = await update.message.reply_text(format_stock_predict_msg(data), parse_mode=ParseMode.HTML)
    predict_messages[chat_id] = [m1.message_id, m2.message_id]
    save_json(PREDICT_MSG_FILE, predict_messages)

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get(API_URL, timeout=10).json()
        w_type = get_weather_type(resp)
        info = WEATHER_TYPES.get(w_type, {"emoji": "☀️", "name": "Обычная"})
        msg = f"🌤️ <b>ТЕКУЩАЯ ПОГОДА</b>\n\n{info['emoji']} <b>{info['name']}</b>\n🕐 {get_msk_time().strftime('%H:%M:%S')} МСК"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except: await update.message.reply_text("❌ Ошибка API")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0: return await update.message.reply_text("❌ Только для групп!")
    if update.effective_user.id not in ADMIN_IDS: return await update.message.reply_text("❌ Нет прав!")
    
    if str(chat_id) not in group_settings:
        group_settings[str(chat_id)] = {"subscriptions": [], "weather": False}
        save_json(GROUP_SETTINGS_FILE, group_settings)
    
    await update.message.reply_text("👑 <b>Админ-панель группы</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid, cid, data = str(query.from_user.id), str(query.message.chat_id), query.data
    await query.answer()

    # Пользовательские функции
    if data == "back_to_menu":
        await query.edit_message_text("🌱 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
    elif data.startswith("category_"):
        cat = data.split("_")[1]
        await query.edit_message_text(f"📂 <b>{cat}</b>", parse_mode=ParseMode.HTML, reply_markup=get_items_menu(uid, cat))
    elif data.startswith("item_"):
        _, cat, pg, name = data.split("_", 3)
        subs = user_settings.get(uid, {}).get("subscriptions", [])
        if name in subs: subs.remove(name)
        else: subs.append(name)
        user_settings[uid]["subscriptions"] = subs
        save_json(DATA_FILE, user_settings)
        await query.edit_message_reply_markup(reply_markup=get_items_menu(uid, cat, int(pg)))
    elif data.startswith("page_"):
        _, cat, pg = data.split("_")
        await query.edit_message_reply_markup(reply_markup=get_items_menu(uid, cat, int(pg)))

    # Админ функции
    elif data == "admin_back":
        await query.edit_message_text("👑 <b>Админ-панель группы</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(cid))
    elif data == "admin_toggle_weather":
        group_settings[cid]["weather"] = not group_settings[cid].get("weather", False)
        save_json(GROUP_SETTINGS_FILE, group_settings)
        await query.edit_message_reply_markup(reply_markup=get_admin_menu(cid))
    elif data == "admin_add_items":
        await query.edit_message_text("📂 <b>Выберите категорию:</b>", parse_mode=ParseMode.HTML, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"admin_cat_{c}")] for c in ["Семена", "Ящики", "Снаряжение"]] + [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))
    elif data.startswith("admin_cat_"):
        cat = data.split("_")[2]
        await query.edit_message_text(f"📂 <b>Добавление: {cat}</b>", parse_mode=ParseMode.HTML, reply_markup=get_items_menu(uid, cat, 0, True, cid))
    elif data.startswith("admin_toggle_"):
        _, _, cat, pg, name = data.split("_", 4)
        subs = group_settings[cid]["subscriptions"]
        if name in subs: subs.remove(name)
        else: subs.append(name)
        group_settings[cid]["subscriptions"] = subs
        save_json(GROUP_SETTINGS_FILE, group_settings)
        await query.edit_message_reply_markup(reply_markup=get_items_menu(uid, cat, int(pg), True, cid))
    elif data == "admin_close":
        await query.delete_message()

# ================= ФОНОВЫЕ ЗАДАЧИ =================

async def update_predictions_job(context: ContextTypes.DEFAULT_TYPE):
    if not predict_messages: return
    data = get_predictions_data()
    if not data: return
    
    m_text, s_text = format_moon_predict_msg(data), format_stock_predict_msg(data)
    dead_chats = []
    for cid, mids in predict_messages.items():
        try:
            await context.bot.edit_message_text(chat_id=int(cid), message_id=mids[0], text=m_text, parse_mode=ParseMode.HTML)
            await context.bot.edit_message_text(chat_id=int(cid), message_id=mids[1], text=s_text, parse_mode=ParseMode.HTML)
        except: dead_chats.append(cid)
    
    if dead_chats:
        for dc in dead_chats: predict_messages.pop(dc, None)
        save_json(PREDICT_MSG_FILE, predict_messages)

async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data, last_weather_data
    try:
        resp = requests.get(API_URL, timeout=10).json()
        new_sig, new_w = get_stock_signature(resp), get_weather_type(resp)

        # Погода
        if last_weather_data is not None and new_w != last_weather_data:
            msg = format_weather_message(new_w)
            for cid, sets in group_settings.items():
                if sets.get("weather"):
                    try: await context.bot.send_message(chat_id=int(cid), text=msg, parse_mode=ParseMode.HTML)
                    except: pass
        last_weather_data = new_w

        # Сток
        if last_stock_data is not None:
            added, removed, changed = get_changes(last_stock_data, new_sig)
            if added or removed or changed:
                # Группы
                for cid, sets in group_settings.items():
                    subs = sets.get("subscriptions", [])
                    g_added = {n: s for n, s in added.items() if n in subs}
                    g_changed = {n: c for n, c in changed.items() if n in subs}
                    g_removed = [n for n in removed if n in subs]
                    alert = format_group_stock_message(g_added, g_changed, g_removed)
                    if alert:
                        try: await context.bot.send_message(chat_id=int(cid), text=alert, parse_mode=ParseMode.HTML)
                        except: pass
                # ЛС
                for uid, sets in user_settings.items():
                    subs = sets.get("subscriptions", [])
                    u_added = {n: s for n, s in added.items() if n in subs}
                    u_changed = {n: c for n, c in changed.items() if n in subs}
                    u_removed = [n for n in removed if n in subs]
                    if u_added or u_changed or u_removed:
                        msg = f"📢 <b>Обновление твоего стока!</b>\n\n"
                        if u_added: msg += "🟢 Появились:\n" + "\n".join([f"• {n}" for n in u_added]) + "\n"
                        if u_changed: msg += "🟡 Изменились:\n" + "\n".join([f"• {n}" for n in u_changed]) + "\n"
                        if u_removed: msg += "🔴 Исчезли:\n" + "\n".join([f"• {n}" for n in u_removed])
                        try: await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode=ParseMode.HTML)
                        except: pass
        last_stock_data = new_sig
    except: pass

def main():
    if not TOKEN: return print("Нет токена!")
    load_items()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.job_queue.run_repeating(check_and_notify, interval=10, first=5)
    app.job_queue.run_repeating(update_predictions_job, interval=30, first=10)
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
