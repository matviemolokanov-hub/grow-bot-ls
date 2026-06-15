import requests
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import os

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://grow-a-garden-2-tracker.onrender.com/api/stock"
DATA_FILE = "user_settings.json"
GROUP_SETTINGS_FILE = "group_settings.json"

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

def load_group_settings():
    try:
        with open(GROUP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_group_settings(settings):
    with open(GROUP_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

user_settings = load_settings()
group_settings = load_group_settings()
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
    changed = {n: {'old': old[n], 'new': new[n]} for n in new if n in old and old[n] != new[n]}
    return added, removed, changed

def format_full_stock_message(data):
    msg = f"📦 <b>ТЕКУЩИЙ СТОК Grow a Garden 2</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
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

def format_stock_for_group(data, subscriptions):
    """Форматирует сток только для подписанных предметов (для группы)"""
    msg = f"🌱 <b>НОВЫЙ СТОК В GROUP!</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
    has_items = False
    
    for shop_type, shop_name in [("SeedShop_Normal", "🌾 Семена"), 
                                   ("CrateShop", "📦 Ящики"), 
                                   ("GearShop", "⚙️ Снаряжение")]:
        shop_items = []
        for item in data.get("shops", {}).get(shop_type, []):
            name = item.get('name')
            stock = item.get('stock', 0)
            if name in subscriptions and stock > 0:
                shop_items.append(f"• {name} — {stock} шт. ({item.get('rarity', 'Common')})")
        if shop_items:
            msg += f"{shop_name}:\n" + "\n".join(shop_items) + "\n\n"
            has_items = True
    
    if not has_items:
        msg += "Нет подписанных предметов в наличии\n"
    
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
    """Админ-панель для группы"""
    settings = group_settings.get(str(chat_id), {"subscriptions": []})
    subs_count = len(settings.get("subscriptions", []))
    keyboard = [
        [InlineKeyboardButton(f"📋 Настройки группы ({subs_count})", callback_data="admin_view_subs")],
        [InlineKeyboardButton("➕ Добавить предметы в группу", callback_data="admin_add_items")],
        [InlineKeyboardButton("➖ Удалить предметы из группы", callback_data="admin_remove_items")],
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
    settings = group_settings.get(str(chat_id), {"subscriptions": []})
    subscriptions = settings.get("subscriptions", [])
    
    items_list = [name for name, info in all_items.items() if info['category'] == category]
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
    settings = group_settings.get(str(chat_id), {"subscriptions": []})
    subscriptions = settings.get("subscriptions", [])
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
    subscriptions = user_settings.get(str(user_id), {}).get("subscriptions", [])
    
    items_list = [name for name, info in all_items.items() if info['category'] == category]
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
    settings = user_settings.get(str(user_id), {"subscriptions": []})
    subscriptions = settings.get("subscriptions", [])
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

# ================= ПРОВЕРКА АДМИНА =================

async def is_admin(update: Update, chat_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором чата"""
    try:
        chat_member = await update.get_bot().get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except:
        return False

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
        "Выбери категорию, затем нажми на предмет.\n"
        "✅ — получать уведомления\n"
        "❌ — не получать\n\n"
        f"📦 <b>Всего предметов:</b> {len(all_items)}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu()
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-панель (только для админов группы)"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Проверяем, что команда вызвана в группе
    if chat_id > 0:
        await update.message.reply_text("❌ Эта команда работает только в группах и каналах!")
        return
    
    # Проверяем, является ли пользователь админом
    if not await is_admin(update, chat_id, user_id):
        await update.message.reply_text("❌ Только администраторы группы могут использовать эту команду!")
        return
    
    if str(chat_id) not in group_settings:
        group_settings[str(chat_id)] = {"subscriptions": []}
        save_group_settings(group_settings)
    
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
        save_settings(user_settings)
    
    if not all_items:
        await update_all_items()
    
    data = query.data
    
    # === ЛИЧНЫЕ НАСТРОЙКИ (без админа) ===
    if data == "back_to_menu":
        await query.edit_message_text(
            "🌱 <b>Grow a Garden 2 Tracker</b>\n\n"
            "Выбери категорию, затем нажми на предмет.\n"
            "✅ — получать уведомления\n"
            "❌ — не получать\n\n"
            f"📦 <b>Всего предметов:</b> {len(all_items)}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu())
    
    elif data == "show_full_stock":
        try:
            resp = requests.get(API_URL, timeout=15)
            if resp.status_code == 200:
                msg = format_full_stock_message(resp.json())
                await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
            else:
                await query.edit_message_text("❌ Ошибка получения стока", reply_markup=get_main_menu())
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_main_menu())
    
    elif data == "view_subscriptions":
        subscriptions = user_settings[user_id].get("subscriptions", [])
        if not subscriptions:
            await query.edit_message_text("📋 <b>У тебя пока нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
        else:
            await query.edit_message_text("📋 <b>Твои подписки</b>\n\nНажми на предмет, чтобы отписаться:", parse_mode=ParseMode.HTML, reply_markup=get_subscriptions_menu(user_id))
    
    elif data.startswith("sub_page_"):
        page = int(data.split("_")[2])
        await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id, page))
    
    elif data.startswith("unsub_"):
        item_name = data.replace("unsub_", "")
        subscriptions = user_settings[user_id].get("subscriptions", [])
        subscriptions = [s for s in subscriptions if s != item_name]
        user_settings[user_id]["subscriptions"] = subscriptions
        save_settings(user_settings)
        await query.answer(f"❌ {item_name} удалён")
        if subscriptions:
            await query.edit_message_reply_markup(reply_markup=get_subscriptions_menu(user_id))
        else:
            await query.edit_message_text("📋 <b>У тебя пока нет подписок</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())
    
    elif data.startswith("category_"):
        category = data.replace("category_", "")
        await query.edit_message_text(
            f"📂 <b>{category}</b>\n\n✅ — получать уведомления\n❌ — не получать",
            parse_mode=ParseMode.HTML,
            reply_markup=get_items_menu(user_id, category, 0))
        context.user_data['current_category'] = category
    
    elif data.startswith("item_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        item_name = "_".join(parts[3:])
        
        subscriptions = user_settings[user_id].get("subscriptions", [])
        
        if item_name in subscriptions:
            subscriptions.remove(item_name)
            await query.answer(f"❌ {item_name} — уведомления выключены")
        else:
            subscriptions.append(item_name)
            await query.answer(f"✅ {item_name} — уведомления включены")
        
        user_settings[user_id]["subscriptions"] = subscriptions
        save_settings(user_settings)
        
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, page))
    
    elif data.startswith("page_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2])
        await query.edit_message_reply_markup(reply_markup=get_items_menu(user_id, category, page))
    
    # === АДМИН-ПАНЕЛЬ ===
    elif data.startswith("admin_"):
        # Проверяем, что пользователь админ
        if not await is_admin(update, chat_id, int(user_id)):
            await query.edit_message_text("❌ Только администраторы могут использовать админ-панель!")
            return
        
        # Закрыть админ-панель
        if data == "admin_close":
            await query.edit_message_text("👑 Админ-панель закрыта", reply_markup=None)
        
        # Назад в админ-меню
        elif data == "admin_back":
            await query.edit_message_text(
                "👑 <b>Админ-панель</b>\n\n"
                "Выбери действие:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu(chat_id))
        
        elif data == "admin_back_to_categories":
            await query.edit_message_text(
                "📂 <b>Выбери категорию:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu(context.user_data.get('admin_action', 'add')))
        
        elif data == "admin_view_subs":
            subscriptions = group_settings.get(str(chat_id), {}).get("subscriptions", [])
            if not subscriptions:
                await query.edit_message_text("📋 <b>В этой группе пока нет подписанных предметов</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu(chat_id))
            else:
                await query.edit_message_text("📋 <b>Подписки группы</b>\n\nНажми на предмет, чтобы удалить:", parse_mode=ParseMode.HTML, reply_markup=get_admin_subscriptions_menu(chat_id))
        
        elif data == "admin_add_items":
            context.user_data['admin_action'] = 'add'
            await query.edit_message_text(
                "📂 <b>Выбери категорию для добавления:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu('add'))
        
        elif data == "admin_remove_items":
            context.user_data['admin_action'] = 'remove'
            await query.edit_message_text(
                "📂 <b>Выбери категорию для удаления:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_category_menu('remove'))
        
        elif data == "admin_clear_all":
            group_settings[str(chat_id)] = {"subscriptions": []}
            save_group_settings(group_settings)
            await query.answer("🗑️ Все подписки группы очищены")
            await query.edit_message_text(
                "👑 <b>Админ-панель</b>\n\nВсе подписки очищены!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu(chat_id))
        
 
