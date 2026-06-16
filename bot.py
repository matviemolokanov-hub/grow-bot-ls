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
CHANNEL_ID = -1003618091927
DATA_FILE = "user_settings.json"
GROUP_SETTINGS_FILE = "group_settings.json"
ITEMS_CACHE_FILE = "items_cache.json"
CACHE_TTL = 300

# ================= АДМИНЫ =================
ADMIN_IDS = [7632708290, 5634818913]

# ================= РЕДКИЕ РЕДКОСТИ =================
RARE_RARITIES = ["Legendary", "Mythic", "Super"]

# ================= КОНКРЕТНЫЕ ПРЕДМЕТЫ ДЛЯ ОТПРАВКИ =================
FORCED_ITEMS = [
    "Mushroom",
    "Moon Bloom",
    "Legendary Sprinkler",
    "Super Watering Can",
    "Super Sprinkler"
]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= ВРЕМЯ МСК =================
def get_msk_time():
    return datetime.now(timezone(timedelta(hours=3)))

# ================= СОХРАНЕНИЕ НАСТРОЕК =================
def load_json(filename, default=None):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

user_settings = load_json(DATA_FILE, {})
group_settings = load_json(GROUP_SETTINGS_FILE, {})
all_items = {}
last_stock_data = None
_items_cache_time = 0

# ================= КЕШИРОВАНИЕ ПРЕДМЕТОВ =================
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
        logger.info(f"Загружено из кеша: {len(all_items)} предметов")
        return all_items

    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code == 200:
            all_items = get_all_items_from_api(resp.json())
            _items_cache_time = time.time()
            save_json(ITEMS_CACHE_FILE, {
                'items': all_items,
                'timestamp': _items_cache_time
            })
            logger.info(f"Загружено из API: {len(all_items)} предметов")
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

def format_rare_stock_for_channel(data):
    msk_time = get_msk_time()

    rarity_emojis = {
        "Epic": "🟣",
        "Legendary": "⭐",
        "Mythic": "🔮",
        "Super": "🌟"
    }

    msg = "🔥 <b>ОБНАРУЖЕН РЕДКИЙ СТОК!</b>\n"
    msg += f"🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n"

    has_rare = False

    for shop_type, shop_name in [("SeedShop_Normal", "🌱 Семена"), 
                                 ("GearShop", "⚙️ Снаряжение")]:
        rare_items = []
        for item in data.get("shops", {}).get(shop_type, []):
            name = item.get('name')
            rarity = item.get('rarity', 'Common')
            stock = item.get('stock', 0)
            
            if (rarity in RARE_RARITIES or name in FORCED_ITEMS) and stock > 0:
                rarity_emoji = rarity_emojis.get(rarity, "⭐")
                rare_items.append(f"{rarity_emoji} <b>{name}</b> — {stock} шт. ({rarity})")
                has_rare = True
        
        if rare_items:
            msg += f"{shop_name}:\n"
            msg += "\n".join(rare_items) + "\n\n"

    if not has_rare:
        return None

    msg += "\n🤖 Наш бот: @growagardenstock235_bot"
    return msg

def format_group_stock_message(added_items, changed_items):
    """Форматирует сообщение для группы по подпискам"""
    msk_time = get_msk_time()
    msg = f"📢 <b>Появился сток!</b>\n🕐 {msk_time.strftime('%H:%M:%S')} МСК\n\n"
    
    if added_items:
        msg += "🟢 <b>Появились:</b>\n"
        for name, stock in added_items.items():
            msg += f"• {name} — {stock} шт.\n"
        msg += "\n"
    
    if changed_items:
        msg += "🟡 <b>Изменилось количество:</b>\n"
        for name, change in changed_items.items():
            msg += f"• {name}: {change['old']} → {change['new']} шт.\n"
    
    return msg

# ================= КНОПКИ И МЕНЮ =================
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
    settings = group_settings.get(str(chat_id), {"subscriptions": []})
    subs_count = len(settings.get("subscriptions", []))
    keyboard = [
        [InlineKeyboardButton(f"📋 Настройки группы ({subs_count})", callback_data="admin_view_subs")],
        [InlineKeyboardButton("➕ Добавить предметы", callback_data="admin_add_items")],
        [InlineKeyboardButton("➖ Удалить предметы", callback_data="admin_remove_items")],
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

async def is_admin(update: Update, chat_id: int, user_id: int) -> bool:
    try:
        chat_member = await update.get_bot().get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except:
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {"subscriptions": []}
        save_json(DATA_FILE, user_settings)

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
        await update.message.reply_text("❌ Эта команда работает только в группах и каналах!")
        return

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав на использование админ-панели!")
        return

    if not await is_admin(update, chat_id, user_id):
        await update.message.reply_text("❌ Вы должны быть администратором группы, чтобы использовать админ-панель!")
        return

    if str(chat_id) not in group_settings:
        group_settings[str(chat_id)] = {"subscriptions": []}
        save_json(GROUP_SETTINGS_FILE, group_settings)

    await update.message.reply_text(
        "👑 <b>Админ-панель</b>\n\n"
        "Здесь ты можешь настроить, какие предметы будут автоматически отправляться в эту группу при появлении в стоке.\n\n"
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
                await query.edit_message_text(
                    format_full_stock_message(resp.json()),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_main_menu()
                )
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
        save_json(DATA_FILE, user_settings)
        if subscriptions:
            await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id))
        else:
            await query.edit_message_text("📋 <b>Нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
        return

    elif data.startswith("category_"):
        category = data.replace("category_", "")
        await query.edit_message_text(
            f"📂 <b>{category}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_items_menu(user_id, category, 0)
        )
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
        else:
            subscriptions.append(item_name)
        
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
        
        if not await is_admin(update, chat_id, int(user_id)):
            await query.edit_message_text("❌ Вы должны быть администратором группы!")
            return
        
        if data == "admin_close":
            await query.edit_message_text("👑 Админ-панель закрыта", reply_markup=None)
            return
        
        elif data == "admin_back":
            await query.edit_message_text(
                "👑 <b>Админ-панель</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu(chat_id))
            return
        
        elif data == "admin_back_to_categories":
            await query.edit_message_text(
                "📂 <b>Выбери категорию:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu(context.user_data.get('admin_action', 'add')))
            return
        
        elif data == "admin_view_subs":
            subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
            if not subscriptions:
                await query.edit_message_text("📋 <b>Нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            else:
                await query.edit_message_text("📋 <b>Подписки группы</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_subscriptions_menu(chat_id))
            return
        
        elif data == "admin_add_items":
            context.user_data['admin_action'] = 'add'
            await query.edit_message_text(
                "📂 <b>Выбери категорию:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu('add'))
            return
        
        elif data == "admin_remove_items":
            context.user_data['admin_action'] = 'remove'
            await query.edit_message_text(
                "📂 <b>Выбери категорию:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu('remove'))
            return
        
        elif data == "admin_clear_all":
            group_settings[str(chat_id)] = {"subscriptions": []}
            save_json(GROUP_SETTINGS_FILE, group_settings)
            await query.edit_message_text(
                "👑 <b>Админ-панель</b>\n\nВсе подписки очищены!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu(chat_id))
            return
        
        elif data.startswith("admin_add_") or data.startswith("admin_remove_"):
            parts = data.split("_")
            action = parts[1]
            category = parts[2] if len(parts) > 2 else ""
            
            if len(parts) == 3:
                category_name = {"seed": "Семена", "crate": "Ящики", "gear": "Снаряжение"}.get(category, category)
                await query.edit_message_text(
                    f"📂 <b>{category_name}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_admin_items_menu(chat_id, category_name, action, 0)
                )
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
                    else:
                        await query.answer(f"⚠️ {item_name} уже в списке")
                        return
                else:
                    if item_name in subscriptions:
                        subscriptions.remove(item_name)
                    else:
                        await query.answer(f"⚠️ {item_name} не в списке")
                        return
                
                group_settings[str(chat_id)] = {"subscriptions": subscriptions}
                save_json(GROUP_SETTINGS_FILE, group_settings)
                await query.edit_message_reply_markup(
                    reply_markup=get_admin_items_menu(chat_id, category, action, page)
                )
                return
        
        elif data.startswith("admin_page_"):
            parts = data.split("_")
            action = parts[2]
            category = parts[3]
            page = int(parts[4])
            await query.edit_message_reply_markup(
                reply_markup=get_admin_items_menu(chat_id, category, action, page)
            )
            return
        
        elif data.startswith("admin_subs_page_"):
            page = int(data.split("_")[3])
            await query.edit_message_reply_markup(
                reply_markup=get_admin_subscriptions_menu(chat_id, page)
            )
            return
        
        elif data.startswith("admin_unsub_"):
            item_name = data.replace("admin_unsub_", "")
            subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
            subscriptions = [s for s in subscriptions if s != item_name]
            group_settings[str(chat_id)] = {"subscriptions": subscriptions}
            save_json(GROUP_SETTINGS_FILE, group_settings)
            if subscriptions:
                await query.edit_message_reply_markup(reply_markup=get_admin_subscriptions_menu(chat_id))
            else:
                await query.edit_message_text("📋 <b>Нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            return

# ================= ФОНОВАЯ ПРОВЕРКА =================
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    global last_stock_data
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"API вернул код {resp.status_code}")
            return

        data = resp.json()
        new_stock_sig = get_stock_signature(data)

        # === Редкий сток в канал (оставлено) ===
        rare_msg = format_rare_stock_for_channel(data)
        if rare_msg:
            try:
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=rare_msg,
                    parse_mode=ParseMode.HTML
                )
                logger.info("✅ Редкий сток отправлен в канал")
            except Exception as e:
                logger.error(f"❌ Не отправлено в канал: {e}")

        if last_stock_data is not None:
            added, removed, changed = get_changes(last_stock_data, new_stock_sig)
            
            if added or removed or changed:
                logger.info(f"Изменения: +{len(added)} -{len(removed)} ~{len(changed)}")
                
                # === ЛС пользователей ===
                for user_id, settings in user_settings.items():
                    subscriptions = settings.get("subscriptions", [])
                    if not subscriptions:
                        continue
                    
                    user_added = {n: s for n, s in added.items() if n in subscriptions}
                    user_removed = {n: s for n, s in removed.items() if n in subscriptions}
                    user_changed = {n: c for n, c in changed.items() if n in subscriptions}
                    
                    if user_added or user_removed or user_changed:
                        msg = f"📢 <b>Изменения в стоке!</b>\n🕐 {get_msk_time().strftime('%H:%M:%S')} МСК\n\n"
                        if user_added:
                            msg += "🟢 <b>Появились:</b>\n" + "\n".join([f"• {n} — {s} шт." for n, s in user_added.items()]) + "\n\n"
                        if user_changed:
                            msg += "🟡 <b>Изменилось количество:</b>\n" + "\n".join([f"• {n}: {c['old']} → {c['new']} шт." for n, c in user_changed.items()]) + "\n\n"
                        if user_removed:
                            msg += "🔴 <b>Пропали:</b>\n" + "\n".join([f"• {n}" for n in user_removed]) + "\n"
                        
                        try:
                            await context.bot.send_message(int(user_id), msg, parse_mode=ParseMode.HTML)
                        except Exception as e:
                            logger.error(f"❌ Не отправлено в ЛС {user_id}: {e}")
                
                # === ГРУППЫ (только по подпискам) ===
                for chat_id_str, settings in group_settings.items():
                    subscriptions = settings.get("subscriptions", [])
                    if not subscriptions:
                        continue
                    
                    group_added = {n: s for n, s in added.items() if n in subscriptions}
                    group_changed = {n: c for n, c in changed.items() if n in subscriptions}
                    
                    if group_added or group_changed:
                        msg = format_group_stock_message(group_added, group_changed)
                        
                        try:
                            await context.bot.send_message(int(chat_id_str), msg, parse_mode=ParseMode.HTML)
                            logger.info(f"✅ Уведомление отправлено в группу {chat_id_str}")
                        except Exception as e:
                            logger.error(f"❌ Не отправлено в группу {chat_id_str}: {e}")
        
        last_stock_data = new_stock_sig
            
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")

# ================= ЗАПУСК =================
def main():
    if not TOKEN:
        logger.error("❌ Токен не найден!")
        return

    load_items()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(check_and_notify, interval=60, first=10)

    logger.info("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
