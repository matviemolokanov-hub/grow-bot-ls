import httpx
import json
import logging
import asyncio
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
ADMIN_IDS = [7632708290, 5634818913]

# ================= ТИПЫ ПОГОДЫ =================
WEATHER_TYPES = {
    "Rain": {"emoji": "🌧️", "name": "Дождь"},
    "Snowfall": {"emoji": "❄️", "name": "Снегопад"},
    "Thunderstorm": {"emoji": "⛈️", "name": "Гроза"},
    "Blood Moon": {"emoji": "🌕", "name": "Кровавая Луна"},
    "Starfall": {"emoji": "⭐", "name": "Звездопад"},
    "Midas": {"emoji": "✨", "name": "Золотая ночь"},
    "Goldmoon": {"emoji": "🌙", "name": "Золотая Луна"},
    "Rainbow Moon": {"emoji": "🌈", "name": "Радужная Луна"},
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

def load_json(filename, default=None):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return default if default is not None else {}

def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")
        return False

# Загрузка настроек
user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
all_items = {}
last_stock_data = None
last_weather_data = "INITIAL" # Для корректного старта

# ================= РАБОТА С API =================
async def load_items():
    global all_items
    cached = load_json(ITEMS_CACHE_FILE)
    if cached and time.time() - cached.get('timestamp', 0) < CACHE_TTL:
        all_items = cached.get('items', {})
        return all_items

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(API_URL, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = {}
                for shop_type, cat in [("SeedShop_Normal", "Семена"), ("CrateShop", "Ящики"), ("GearShop", "Снаряжение")]:
                    for item in data.get("shops", {}).get(shop_type, []):
                        items[item['name']] = {
                            'name': item['name'],
                            'rarity': item.get('rarity', 'Common'),
                            'category': cat,
                        }
                all_items = items
                save_json(ITEMS_CACHE_FILE, {'items': all_items, 'timestamp': time.time()})
                logger.info(f"База предметов обновлена: {len(all_items)}")
    except Exception as e:
        logger.error(f"Ошибка загрузки предметов: {e}")
    return all_items

def get_stock_signature(data):
    signature = {}
    for shop_type in ["SeedShop_Normal", "CrateShop", "GearShop"]:
        for item in data.get("shops", {}).get(shop_type, []):
            signature[item.get('name')] = item.get('stock', 0)
    return signature

def get_weather_type(data):
    weather = data.get('weather', {})
    weathers = weather.get('weathers', {})
    for key in WEATHER_TYPES.keys():
        val = weathers.get(key)
        if val is True or val == "true": return key
        if isinstance(val, dict) and val.get("playing") is True: return key
    
    phase = weather.get('phase', '')
    if phase in WEATHER_TYPES: return phase
    return None

# ================= ФОРМАТИРОВАНИЕ =================
def format_weather_message(weather_key):
    msk_time = get_msk_time()
    if weather_key:
        info = WEATHER_TYPES.get(weather_key, {"emoji": "☁️", "name": weather_key})
        msg = f"🌤️ <b>ПОГОДА ИЗМЕНИЛАСЬ!</b>\n"
        msg += f"{info['emoji']} <b>{info['name']}</b>\n"
    else:
        msg = f"☀️ <b>ПОГОДА СТАЛА ОБЫЧНОЙ</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n"
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_group_stock_message(added, changed, removed):
    msk_time = get_msk_time()
    msg = "📢 <b>ОБНОВЛЕНИЕ СТОКА!</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n"
    msg += "─" * 25 + "\n"
    
    if added:
        msg += "\n🟢 <b>Появились:</b>\n"
        for name, stock in added.items():
            info = all_items.get(name, {})
            msg += f"  • {name} — <b>{stock} шт.</b> ({info.get('rarity', 'Common')})\n"
    
    if changed:
        msg += "\n🟡 <b>Изменилось количество:</b>\n"
        for name, change in changed.items():
            msg += f"  • {name} — <b>{change['new']} шт.</b>\n"
            
    if removed:
        msg += "\n🔴 <b>Закончились:</b>\n"
        for name in removed:
            msg += f"  • {name}\n"
            
    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_full_stock_message(data):
    msk_time = get_msk_time()
    msg = f"📦 <b>ТЕКУЩИЙ СТОК</b>\n🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n"
    for shop_type, shop_name in [("SeedShop_Normal", "🌾 Семена"), ("CrateShop", "📦 Ящики"), ("GearShop", "⚙️ Снаряжение")]:
        msg += f"<b>{shop_name}:</b>\n"
        items = data.get("shops", {}).get(shop_type, [])
        has = False
        for item in items:
            if item.get("stock", 0) > 0:
                msg += f"• {item['name']} — {item['stock']} шт.\n"
                has = True
        if not has: msg += "Нет в наличии\n"
        msg += "\n"
    return msg

# ================= МЕНЮ (ВСЕ ВАШИ ФУНКЦИИ) =================
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
    weather_status = "✅" if settings.get("weather", False) else "❌"
    keyboard = [
        [InlineKeyboardButton(f"📋 Настройки группы", callback_data="admin_view_subs")],
        [InlineKeyboardButton("➕ Добавить предметы", callback_data="admin_add_items")],
        [InlineKeyboardButton("➖ Удалить предметы", callback_data="admin_remove_items")],
        [InlineKeyboardButton(f"{weather_status} Уведомления о погоде", callback_data="admin_toggle_weather")],
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
    items_list = [name for name, info in all_items.items() if info.get('category') == category]
    items_list.sort()
    subs = group_settings.get(str(chat_id), {}).get("subscriptions", [])
    total_pages = (len(items_list) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    current_items = items_list[start:start + items_per_page]
    keyboard = []
    for name in current_items:
        is_selected = name in subs
        keyboard.append([InlineKeyboardButton(f"{'✅' if is_selected else '❌'} {name}", callback_data=f"admin_{action}_{category}_{page}_{name}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_page_{action}_{category}_{page-1}"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_page_{action}_{category}_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back_to_categories")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_subscriptions_menu(chat_id, page=0):
    subs = group_settings.get(str(chat_id), {}).get("subscriptions", [])
    items_per_page = 10
    total_pages = (len(subs) + items_per_page - 1) // items_per_page
    start = page * 10
    current = subs[start:start+10]
    keyboard = [[InlineKeyboardButton(f"❌ {s}", callback_data=f"admin_unsub_{s}")] for s in current]
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_subs_page_{page-1}"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_subs_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)

def get_items_menu(user_id, category, page=0):
    items_list = [name for name, info in all_items.items() if info.get('category') == category]
    items_list.sort()
    subs = user_settings.get(str(user_id), {}).get("subscriptions", [])
    start = page * 10
    current = items_list[start:start+10]
    keyboard = []
    for name in current:
        keyboard.append([InlineKeyboardButton(f"{'✅' if name in subs else '❌'} {name}", callback_data=f"item_{category}_{page}_{name}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{category}_{page-1}"))
    if page < (len(items_list)-1)//10: nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{category}_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_subscriptions_menu(user_id, page=0):
    subs = user_settings.get(str(user_id), {}).get("subscriptions", [])
    start = page * 10
    current = subs[start:start+10]
    keyboard = [[InlineKeyboardButton(f"❌ {s}", callback_data=f"unsub_{s}")] for s in current]
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"sub_page_{page-1}"))
    if page < (len(subs)-1)//10: nav.append(InlineKeyboardButton("▶️", callback_data=f"sub_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

# ================= ФОНОВАЯ ПРОВЕРКА (ИСПРАВЛЕНА) =================
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data, last_weather_data
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(API_URL, timeout=15)
            if resp.status_code != 200: return
            data = resp.json()

        # ПОГОДА
        current_weather = get_weather_type(data)
        if last_weather_data != "INITIAL" and current_weather != last_weather_data:
            logger.info(f"Смена погоды: {last_weather_data} -> {current_weather}")
            msg = format_weather_message(current_weather)
            for cid, settings in group_settings.items():
                if settings.get("weather"):
                    try: await context.bot.send_message(int(cid), msg, parse_mode=ParseMode.HTML)
                    except: pass
        last_weather_data = current_weather

        # СТОК
        new_sig = get_stock_signature(data)
        if last_stock_data is None:
            last_stock_data = new_sig
            return

        added = {n: s for n, s in new_sig.items() if n not in last_stock_data and s > 0}
        removed = {n for n in last_stock_data if n not in new_sig}
        changed = {n: {'old': last_stock_data[n], 'new': new_sig[n]} 
                   for n in new_sig if n in last_stock_data and new_sig[n] != last_stock_data[n]}

        if added or removed or changed:
            if not all_items: await load_items()
            
            # Группы
            for cid, settings in group_settings.items():
                subs = settings.get("subscriptions", [])
                g_added = {n: s for n, s in added.items() if n in subs}
                g_changed = {n: c for n, c in changed.items() if n in subs}
                g_removed = {n for n in removed if n in subs}
                if g_added or g_changed or g_removed:
                    msg = format_group_stock_message(g_added, g_changed, g_removed)
                    try: await context.bot.send_message(int(cid), msg, parse_mode=ParseMode.HTML)
                    except: pass
            
            # Личка
            for uid, settings in user_settings.items():
                subs = settings.get("subscriptions", [])
                u_added = {n: s for n, s in added.items() if n in subs}
                u_changed = {n: c for n, c in changed.items() if n in subs}
                u_removed = {n for n in removed if n in subs}
                if u_added or u_changed or u_removed:
                    msg = format_group_stock_message(u_added, u_changed, u_removed)
                    try: await context.bot.send_message(int(uid), msg, parse_mode=ParseMode.HTML)
                    except: pass

        last_stock_data = new_sig
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")

# ================= ОБРАБОТЧИКИ КОМАНД =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_settings:
        user_settings[uid] = {"subscriptions": []}
        save_json(DATA_FILE, user_settings)
    await load_items()
    await update.message.reply_text("🌱 <b>Grow a Garden 2 Tracker</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(API_URL)
            w = get_weather_type(r.json())
            if w:
                info = WEATHER_TYPES.get(w, {"emoji": "☁️", "name": w})
                await update.message.reply_text(f"{info['emoji']} Сейчас: <b>{info['name']}</b>", parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text("☀️ Сейчас <b>обычная погода</b>")
        except: await update.message.reply_text("❌ Ошибка API")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0: return await update.message.reply_text("❌ Только для групп")
    if update.effective_user.id not in ADMIN_IDS: return
    if str(chat_id) not in group_settings:
        group_settings[str(chat_id)] = {"subscriptions": [], "weather": False}
        save_json(GROUP_SETTINGS_FILE, group_settings)
    await update.message.reply_text("👑 <b>Админ-панель группы</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = str(query.from_user.id)
    cid = str(query.message.chat_id)
    await query.answer()

    if data == "back_to_menu":
        await query.edit_message_text("🌱 <b>Главное меню:</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
    elif data == "show_full_stock":
        async with httpx.AsyncClient() as client:
            r = await client.get(API_URL)
            await query.edit_message_text(format_full_stock_message(r.json()), parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
    elif data == "view_subscriptions":
        await query.edit_message_text("📋 <b>Ваши подписки:</b>", parse_mode=ParseMode.HTML, reply_markup=get_subscriptions_menu(uid))
    elif data.startswith("category_"):
        cat = data.split("_")[1]
        await query.edit_message_text(f"📂 <b>{cat}</b>", parse_mode=ParseMode.HTML, reply_markup=get_items_menu(uid, cat))
    elif data.startswith("item_"):
        parts = data.split("_")
        cat, pg, item = parts[1], int(parts[2]), "_".join(parts[3:])
        subs = user_settings.get(uid, {}).get("subscriptions", [])
        if item in subs: subs.remove(item)
        else: subs.append(item)
        user_settings[uid]["subscriptions"] = subs
        save_json(DATA_FILE, user_settings)
        await query.edit_message_reply_markup(reply_markup=get_items_menu(uid, cat, pg))
    elif data.startswith("page_"):
        parts = data.split("_")
        await query.edit_message_reply_markup(reply_markup=get_items_menu(uid, parts[1], int(parts[2])))
    elif data.startswith("unsub_"):
        item = data.replace("unsub_", "")
        user_settings[uid]["subscriptions"].remove(item)
        save_json(DATA_FILE, user_settings)
        await query.edit_message_text("📋 <b>Ваши подписки:</b>", parse_mode=ParseMode.HTML, reply_markup=get_subscriptions_menu(uid))
    
    # АДМИНКА
    elif data == "admin_close": await query.edit_message_text("Закрыто.")
    elif data == "admin_back": await query.edit_message_text("👑 <b>Админ-панель</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(cid))
    elif data == "admin_toggle_weather":
        group_settings[cid]["weather"] = not group_settings[cid].get("weather", False)
        save_json(GROUP_SETTINGS_FILE, group_settings)
        await query.edit_message_reply_markup(reply_markup=get_admin_menu(cid))
    elif data == "admin_add_items": await query.edit_message_text("Категория:", reply_markup=get_admin_category_menu("add"))
    elif data == "admin_remove_items": await query.edit_message_text("Категория:", reply_markup=get_admin_category_menu("remove"))
    elif data == "admin_view_subs": await query.edit_message_text("Подписки группы:", reply_markup=get_admin_subscriptions_menu(cid))
    elif data == "admin_back_to_categories": 
        action = context.user_data.get("admin_action", "add")
        await query.edit_message_text("Категория:", reply_markup=get_admin_category_menu(action))
    elif data.startswith("admin_add_") or data.startswith("admin_remove_"):
        parts = data.split("_")
        if len(parts) == 3:
            cat_map = {"seed": "Семена", "crate": "Ящики", "gear": "Снаряжение"}
            cat_name = cat_map.get(parts[2], parts[2])
            context.user_data["admin_action"] = parts[1]
            await query.edit_message_text(f"Предметы ({cat_name}):", reply_markup=get_admin_items_menu(cid, cat_name, parts[1]))
        elif len(parts) >= 5:
            action, cat, pg, item = parts[1], parts[2], int(parts[3]), "_".join(parts[4:])
            subs = group_settings[cid]["subscriptions"]
            if action == "add" and item not in subs: subs.append(item)
            elif action == "remove" and item in subs: subs.remove(item)
            group_settings[cid]["subscriptions"] = subs
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.edit_message_reply_markup(reply_markup=get_admin_items_menu(cid, cat, action, pg))

# ================= ЗАПУСК =================
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("weather", weather_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(check_and_notify, interval=15, first=5)
    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
