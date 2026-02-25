import requests
import json
import os
import time
import random
import asyncio
import re
import hashlib
import bcrypt
import sqlite3
from collections import defaultdict
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler,
    filters, CallbackQueryHandler, ConversationHandler
)

# ==================== الإعدادات ====================
TELEGRAM_TOKEN = "8554332204:AAFkEPO7TEWqD-gkB7tjKNSNRiL_NiBknNo"
CASHIER_USERNAME = "al-earob4@agent.nsp"
CASHIER_PASSWORD = "G1123456@Aa"
PARENT_ID = "2539309"

BASE_URL = "https://agents.ichancy.com"
SIGN_IN_URL = f"{BASE_URL}/global/api/User/signIn"
REGISTER_URL = f"{BASE_URL}/global/api/Player/registerPlayer"
SEARCH_PLAYER_URL = f"{BASE_URL}/global/api/Player/getPlayersForCurrentAgent"
BALANCE_URL = f"{BASE_URL}/global/api/Player/getPlayerBalanceById"
DEPOSIT_URL = f"{BASE_URL}/global/api/Player/depositToPlayer"
WITHDRAW_URL = f"{BASE_URL}/global/api/Player/withdrawFromPlayer"

# قناة المشرفين (لإرسال طلبات الإيداع/السحب)
ADMIN_CHANNEL = -1003779524664
ADMIN_USER_ID = 7240317228  # المعرف الخاص للمشرف الرئيسي

# بيانات المحافظ
WALLETS = {
    "شام كاش": "f0a9dd9bbae2fc8ccdb6f06783c61462",  # قد يكون رابطاً أو معرفاً
    "سريتل كاش": "49478985",
    "USDT BEP-20": "0xb144ea4e6EffC0dAFef1c4D66af20B5C5A778ABE"
}

# ملفات التخزين
ACCOUNTS_FILE = "accounts.json"      # تخزين كلمات المرور (مشفرة)
USER_COUNTER_FILE = "user_counter.txt"   # عداد المستخدمين (يبدأ من 13)
PASS_COUNTER_FILE = "pass_counter.txt"    # عداد كلمات المرور (يبدأ من 12345)
USERS_DB_FILE = "users.db"            # قاعدة بيانات لتخزين معرفات المستخدمين (للنشر)
DYNAMIC_BUTTONS_FILE = "dynamic_buttons.json"  # تخزين الأزرار الديناميكية

# إعداد جلسة مشتركة مع API (مع إعادة تسجيل الدخول تلقائياً)
class APIClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/dashboard",
        })
        self.logged_in = False

    async def ensure_login(self):
        if not self.logged_in:
            payload = {"username": CASHIER_USERNAME, "password": CASHIER_PASSWORD}
            try:
                response = self.session.post(SIGN_IN_URL, json=payload)
                if response.status_code == 200 and response.json().get('status') == True:
                    self.logged_in = True
                else:
                    raise Exception("فشل تسجيل الدخول إلى الكاشيرة")
            except Exception as e:
                raise Exception(f"خطأ في تسجيل الدخول: {e}")

    async def request(self, method, url, **kwargs):
        await self.ensure_login()
        response = self.session.request(method, url, **kwargs)
        if response.status_code == 401:  # جلسة منتهية
            self.logged_in = False
            await self.ensure_login()
            response = self.session.request(method, url, **kwargs)
        return response

api_client = APIClient()

# ==================== دوال الحماية ====================

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode(), salt)
    return hashed.decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

# منع السبام
last_message_time = defaultdict(float)

async def is_spamming(user_id: int, cooldown: float = 2.0) -> bool:
    now = time.time()
    if now - last_message_time[user_id] < cooldown:
        return True
    last_message_time[user_id] = now
    return False

# أقفال لكل حساب (لمنع التداخل في العمليات)
account_locks = defaultdict(asyncio.Lock)

# التحقق من صحة المدخلات
def validate_username_input(username: str) -> bool:
    """الاسم الذي يدخله المستخدم: أحرف إنجليزية فقط، طول 3-20."""
    return re.match(r"^[a-zA-Z]{3,20}$", username) is not None

def validate_amount(amount_str: str) -> int | None:
    try:
        amount = int(amount_str)
        if amount <= 0:
            return None
        return amount
    except ValueError:
        return None

# معرف فريد للمعاملة (لمنع التكرار)
processed_tx = set()

def generate_tx_id(username: str, amount: int, timestamp: float) -> str:
    data = f"{username}:{amount}:{timestamp}"
    full_hash = hashlib.sha256(data.encode()).hexdigest()
    return full_hash[:16]   # نأخذ أول 16 حرفاً فقط

# ==================== دوال مساعدة ====================

def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, 'w') as f:
        json.dump(accounts, f, indent=4)

def get_next_user_number():
    """رقم تصاعدي لاسم المستخدم (يبدأ من 13)"""
    start = 13
    if os.path.exists(USER_COUNTER_FILE):
        with open(USER_COUNTER_FILE, 'r') as f:
            last = int(f.read().strip())
    else:
        last = start - 1
    next_num = last + 1
    with open(USER_COUNTER_FILE, 'w') as f:
        f.write(str(next_num))
    return next_num

def get_next_pass_number():
    """رقم تصاعدي لكلمة المرور (يبدأ من 12345)"""
    start = 12345
    if os.path.exists(PASS_COUNTER_FILE):
        with open(PASS_COUNTER_FILE, 'r') as f:
            last = int(f.read().strip())
    else:
        last = start - 1
    next_num = last + 1
    with open(PASS_COUNTER_FILE, 'w') as f:
        f.write(str(next_num))
    return next_num

# تخزين معرفات المستخدمين الذين تفاعلوا مع البوت (للنشر)
def init_users_db():
    conn = sqlite3.connect(USERS_DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(USERS_DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    finally:
        conn.close()

def get_all_users():
    conn = sqlite3.connect(USERS_DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

# الأزرار الديناميكية
def load_dynamic_buttons():
    if os.path.exists(DYNAMIC_BUTTONS_FILE):
        with open(DYNAMIC_BUTTONS_FILE, 'r') as f:
            return json.load(f)
    return {}  # {button_text: url_or_data}

def save_dynamic_buttons(buttons):
    with open(DYNAMIC_BUTTONS_FILE, 'w') as f:
        json.dump(buttons, f, indent=4)

# دوال API (معدلة لاستخدام api_client)
async def login_to_cashier():
    try:
        await api_client.ensure_login()
        return True
    except Exception:
        return False

async def search_player(login, max_attempts=3, delay=2):
    for attempt in range(max_attempts):
        payload = {
            "start": 0,
            "limit": 20,
            "filter": {
                "withoutTotalCount": {"action": "=", "value": True},
                "userName": {"action": "like", "value": login, "valueLabel": login}
            },
            "isNextPage": False
        }
        try:
            response = await api_client.request("POST", SEARCH_PLAYER_URL, json=payload)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == True and data.get('result'):
                    records = data['result'].get('records')
                    if records:
                        for player in records:
                            if player.get('username') == login:
                                return player.get('playerId')
            await asyncio.sleep(delay)
        except Exception:
            await asyncio.sleep(delay)
    return None

async def create_player(login, password):
    email = f"{login}@agent.nsp"
    payload = {
        "player": {
            "email": email,
            "password": password,
            "login": login,
            "parentId": PARENT_ID
        }
    }
    try:
        response = await api_client.request("POST", REGISTER_URL, json=payload)
        if response.status_code == 200:
            data = response.json()
            notifications = data.get('notification', [])
            if notifications and any(n.get('status') == 'error' for n in notifications):
                error_msg = notifications[0].get('content', 'خطأ غير معروف')
                return False, error_msg
            if data.get('status') == True:
                return True, None
        return False, "فشل غير معروف"
    except Exception as e:
        return False, str(e)

async def get_balance(player_id):
    payload = {"playerId": player_id}
    try:
        response = await api_client.request("POST", BALANCE_URL, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == True:
                result = data.get('result')
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get('balance', 0)
        return None
    except Exception:
        return None

async def deposit(player_id, amount):
    payload = {
        "amount": amount,
        "comment": None,
        "playerId": player_id,
        "currencyCode": "NSP",
        "currency": "NSP",
        "moneyStatus": 5
    }
    try:
        response = await api_client.request("POST", DEPOSIT_URL, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}", "details": response.text}
    except Exception as e:
        return {"error": str(e)}

async def withdraw(player_id, amount):
    payload = {
        "amount": -amount,
        "comment": None,
        "playerId": player_id,
        "currencyCode": "NSP",
        "currency": "NSP",
        "moneyStatus": 5
    }
    try:
        response = await api_client.request("POST", WITHDRAW_URL, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}", "details": response.text}
    except Exception as e:
        return {"error": str(e)}

# ==================== دوال البوت (الأزرار والحالات) ====================

# حالات المحادثة
WAITING_USERNAME, WAITING_PASSWORD, WAITING_AMOUNT, WAITING_PROOF, WAITING_WITHDRAW_WALLET, WAITING_DELETE_BUTTON = range(6)

# رسالة الترحيب
WELCOME_MESSAGE = """
✨ أهلًا وسهلًا بك في بوت إيشـانسي ✨
نحن سعداء بوجودك معنا 🤍
يرجى اختيار طلبك من القائمة في الأسفل ⬇️

🔒 نؤكد لك أن جميع الخدمات تُقدَّم بسرية تامة وبأعلى درجات الموثوقية،
وهدفنا هو راحتك وتقديم تجربة سهلة وآمنة من البداية حتى النهاية.

📩 في حال واجهتك أي مشكلة أو كان لديك أي استفسار،
لا تتردد بالتواصل مباشرة مع الدعم

نتمنى لك تجربة موفقة وحظًا سعيدًا
شكرًا لثقتك بنا 🥰❤️
"""

# الأزرار الرئيسية
def main_menu_keyboard(user_id):
    keyboard = [
        [InlineKeyboardButton("➕ إنشاء حساب", callback_data="create")],
        [InlineKeyboardButton("💰 إيداع رصيد", callback_data="deposit")],
        [InlineKeyboardButton("📤 سحب رصيد", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 أيشانسي", url="https://www.ichancy.com/ar")],
        [InlineKeyboardButton("🆘 الدعم", url="tg://user?id=7240317228")],
        [InlineKeyboardButton("👑 إنشاء كاشيرة/ماستر", url="tg://user?id=7240317228")],
        [InlineKeyboardButton("📢 قناة العروض", url="https://t.me/ichancyHH_bot1")],
    ]
    # إضافة الأزرار الديناميكية للمشرف
    if user_id == ADMIN_USER_ID:
        dyn_buttons = load_dynamic_buttons()
        for text, data in dyn_buttons.items():
            if data.startswith(('http://', 'https://', 'tg://')):
                keyboard.append([InlineKeyboardButton(text, url=data)])
            else:
                keyboard.append([InlineKeyboardButton(text, callback_data=f"dyn_{text}")])
        # زر لإضافة زر ديناميكي جديد
        keyboard.append([InlineKeyboardButton("⚙️ إضافة زر ديناميكي", callback_data="add_dynamic")])
        # زر لحذف زر ديناميكي
        if dyn_buttons:
            keyboard.append([InlineKeyboardButton("🗑 حذف زر ديناميكي", callback_data="delete_dynamic")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    await update.message.reply_text(
        WELCOME_MESSAGE,
        reply_markup=main_menu_keyboard(user_id)
    )

# معالج الضغط على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "create":
        context.user_data["action"] = "create"
        context.user_data["step"] = WAITING_USERNAME
        await query.edit_message_text("📝 أرسل الاسم المطلوب (أحرف إنجليزية فقط، بدون أرقام):")

    elif data == "deposit":
        context.user_data["action"] = "deposit"
        context.user_data["step"] = WAITING_USERNAME
        await query.edit_message_text("💰 أرسل اسم المستخدم:")

    elif data == "withdraw":
        context.user_data["action"] = "withdraw"
        context.user_data["step"] = WAITING_USERNAME
        await query.edit_message_text("📤 أرسل اسم المستخدم:")

    elif data == "add_dynamic" and user_id == ADMIN_USER_ID:
        context.user_data["action"] = "add_dynamic"
        context.user_data["step"] = "waiting_button_text"
        await query.edit_message_text("🔘 أرسل نص الزر الجديد:")

    elif data == "delete_dynamic" and user_id == ADMIN_USER_ID:
        dyn_buttons = load_dynamic_buttons()
        if not dyn_buttons:
            await query.edit_message_text("⚠️ لا توجد أزرار ديناميكية لحذفها.")
            return
        keyboard = []
        for text in dyn_buttons.keys():
            keyboard.append([InlineKeyboardButton(text, callback_data=f"delbtn_{text}")])
        keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_delete")])
        await query.edit_message_text("اختر الزر الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("delbtn_") and user_id == ADMIN_USER_ID:
        button_text = data[7:]
        dyn_buttons = load_dynamic_buttons()
        if button_text in dyn_buttons:
            del dyn_buttons[button_text]
            save_dynamic_buttons(dyn_buttons)
            await query.edit_message_text(f"✅ تم حذف الزر '{button_text}' بنجاح.")
        else:
            await query.edit_message_text("⚠️ الزر غير موجود.")

    elif data == "cancel_delete":
        await query.edit_message_text("تم الإلغاء.", reply_markup=main_menu_keyboard(user_id))

    elif data.startswith("dyn_"):
        button_text = data[4:]
        await query.edit_message_text(f"تم الضغط على زر {button_text}، يمكنك تنفيذ الإجراء المناسب.")

    elif data.startswith("approve_deposit_"):
        tx_id = data.replace("approve_deposit_", "")
        await process_approval(update, context, tx_id, "deposit", approved=True)

    elif data.startswith("reject_deposit_"):
        tx_id = data.replace("reject_deposit_", "")
        await process_approval(update, context, tx_id, "deposit", approved=False)

    elif data.startswith("approve_withdraw_"):
        tx_id = data.replace("approve_withdraw_", "")
        await process_approval(update, context, tx_id, "withdraw", approved=True)

    elif data.startswith("reject_withdraw_"):
        tx_id = data.replace("reject_withdraw_", "")
        await process_approval(update, context, tx_id, "withdraw", approved=False)

# معالج الرسائل النصية
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if await is_spamming(user_id):
        await update.message.reply_text("⏳ تمهل قليلاً قبل إرسال رسالة أخرى.")
        return

    # النشر للمشرف
    if user_id == ADMIN_USER_ID and text == "نشر":
        context.user_data["action"] = "broadcast"
        context.user_data["step"] = "waiting_content"
        await update.message.reply_text("📢 أرسل المحتوى الذي تريد نشره (نص، صورة، فيديو) خلال 60 ثانية.")
        asyncio.create_task(broadcast_timeout(context, user_id))
        return

    if "step" not in context.user_data:
        await update.message.reply_text("❌ لم تبدأ أي عملية. استخدم الأزرار للبدء.")
        return

    action = context.user_data.get("action")
    step = context.user_data["step"]

    # إنشاء حساب
    if action == "create":
        if step == WAITING_USERNAME:
            if not validate_username_input(text):
                await update.message.reply_text("❌ الاسم يجب أن يكون أحرف إنجليزية فقط (3-20 حرف). حاول مجدداً:")
                return
            if not await login_to_cashier():
                await update.message.reply_text("❌ فشل الاتصال بالكاشيرة. حاول لاحقاً.")
                context.user_data.clear()
                return
            player_id = await search_player(text, max_attempts=1)
            if player_id:
                await update.message.reply_text("❌ هذا الاسم موجود بالفعل. أدخل اسماً آخر:")
                return
            context.user_data["raw_username"] = text
            user_num = get_next_user_number()
            final_username = f"{text}H_H{user_num}"
            pass_num = get_next_pass_number()
            password = f"{text}{pass_num}"
            context.user_data["final_username"] = final_username
            context.user_data["password"] = password
            await update.message.reply_text(f"⏳ جاري إنشاء الحساب {final_username}...")

            success, error = await create_player(final_username, password)
            if success:
                accounts = load_accounts()
                accounts[final_username] = hash_password(password)
                save_accounts(accounts)
                await update.message.reply_text("✅ تم الإنشاء بنجاح. جاري التحقق...")
                player_id = await search_player(final_username, max_attempts=5, delay=3)
                if player_id:
                    await update.message.reply_text(
                        f"✅ تم إنشاء الحساب!\n"
                        f"👤 اسم المستخدم: {final_username}\n"
                        f"🔑 كلمة المرور: {password}\n"
                        f"احتفظ بها جيداً.\n"
                        f"انتهينا من إنشاء حساب، ننتقل إلى إيداع رصيد؟ (استخدم الأزرار)"
                    )
                else:
                    await update.message.reply_text("⚠️ تم الإنشاء لكن لم يظهر في القائمة بعد. يرجى المحاولة يدوياً لاحقاً.")
            else:
                await update.message.reply_text(f"❌ فشل الإنشاء: {error}")
            context.user_data.clear()
            await update.message.reply_text(WELCOME_MESSAGE, reply_markup=main_menu_keyboard(user_id))

    # إيداع وسحب
    elif action in ("deposit", "withdraw"):
        if step == WAITING_USERNAME:
            context.user_data["username"] = text
            context.user_data["step"] = WAITING_PASSWORD
            await update.message.reply_text("🔑 أرسل كلمة المرور:")

        elif step == WAITING_PASSWORD:
            username = context.user_data["username"]
            password = text
            accounts = load_accounts()
            if username not in accounts:
                await update.message.reply_text("❌ هذا الحساب غير مسجل في البوت.")
                context.user_data.clear()
                return
            if not verify_password(password, accounts[username]):
                await update.message.reply_text("❌ كلمة المرور غير صحيحة.")
                context.user_data.clear()
                return

            if not await login_to_cashier():
                await update.message.reply_text("❌ فشل الاتصال بالكاشيرة.")
                context.user_data.clear()
                return

            player_id = await search_player(username, max_attempts=3, delay=2)
            if not player_id:
                await update.message.reply_text("❌ لم يتم العثور على اللاعب في الكاشيرة.")
                context.user_data.clear()
                return

            context.user_data["player_id"] = player_id
            context.user_data["step"] = WAITING_AMOUNT
            balance = await get_balance(player_id)
            action_ar = "إيداع" if action == "deposit" else "سحب"
            await update.message.reply_text(
                f"✅ تم العثور على اللاعب.\n"
                f"💰 رصيدك الحالي: {balance} NSP\n"
                f"أرسل المبلغ الذي تريد {action_ar} (عدد صحيح):"
            )

        elif step == WAITING_AMOUNT:
            amount = validate_amount(text)
            if amount is None:
                await update.message.reply_text("❌ المبلغ غير صالح. أرسل رقماً صحيحاً موجباً:")
                return

            min_amount = 20000 if action == "deposit" else 50000
            if amount < min_amount:
                action_ar = "إيداع" if action == "deposit" else "سحب"
                await update.message.reply_text(f"❌ الحد الأدنى لـ {action_ar} هو {min_amount}. أعد المحاولة.")
                context.user_data.clear()
                return

            if action == "withdraw":
                balance = await get_balance(context.user_data["player_id"])
                if amount > balance:
                    await update.message.reply_text(f"❌ رصيدك {balance} لا يكفي لهذا المبلغ.")
                    context.user_data.clear()
                    return

            context.user_data["amount"] = amount
            keyboard = []
            for wallet_name in WALLETS.keys():
                keyboard.append([InlineKeyboardButton(wallet_name, callback_data=f"wallet_{wallet_name}")])
            await update.message.reply_text(
                "اختر وسيلة الدفع/السحب:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["step"] = "waiting_wallet"

        elif step == WAITING_PROOF:
            if action == "deposit":
                await send_deposit_request(update, context)
            else:
                pass

    # إضافة زر ديناميكي
    elif action == "add_dynamic":
        if step == "waiting_button_text":
            context.user_data["new_button_text"] = text
            context.user_data["step"] = "waiting_button_data"
            await update.message.reply_text("🔗 أرسل الرابط أو البيانات المرتبطة بالزر (مثال: https://t.me/... أو نص عادي):")

        elif step == "waiting_button_data":
            button_text = context.user_data["new_button_text"]
            button_data = text
            buttons = load_dynamic_buttons()
            buttons[button_text] = button_data
            save_dynamic_buttons(buttons)
            await update.message.reply_text("✅ تم إضافة الزر الديناميكي بنجاح.")
            context.user_data.clear()
            await update.message.reply_text(WELCOME_MESSAGE, reply_markup=main_menu_keyboard(user_id))

    # النشر
    elif action == "broadcast":
        if step == "waiting_content":
            await broadcast_content(update, context)

# دالة النشر (تدعم النص والصورة والفيديو)
async def broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    count = 0
    for uid in users:
        try:
            if update.message.text:
                await context.bot.send_message(uid, update.message.text)
            elif update.message.photo:
                await context.bot.send_photo(uid, update.message.photo[-1].file_id, caption=update.message.caption)
            elif update.message.video:
                await context.bot.send_video(uid, update.message.video.file_id, caption=update.message.caption)
            elif update.message.document:
                await context.bot.send_document(uid, update.message.document.file_id, caption=update.message.caption)
            await asyncio.sleep(0.1)
            count += 1
        except Exception as e:
            print(f"فشل إرسال إلى {uid}: {e}")
    await update.message.reply_text(f"✅ تم إرسال المحتوى إلى {count} مستخدم.")
    context.user_data.clear()

async def broadcast_timeout(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    await asyncio.sleep(60)
    if context.user_data.get("action") == "broadcast" and context.user_data.get("step") == "waiting_content":
        await context.bot.send_message(user_id, "⏰ انتهت مهلة النشر. أعد المحاولة.")
        context.user_data.clear()

# معالج الصور (لإثبات الدفع وللنشر)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # التحقق من حالة النشر أولاً
    if context.user_data.get("action") == "broadcast" and context.user_data.get("step") == "waiting_content":
        await broadcast_content(update, context)
        return

    if "step" not in context.user_data:
        return

    action = context.user_data.get("action")
    step = context.user_data["step"]

    if action == "deposit" and step == WAITING_PROOF:
        photo_file = update.message.photo[-1]
        context.user_data["proof_file_id"] = photo_file.file_id
        await send_deposit_request(update, context)

    elif action == "withdraw" and step == WAITING_WITHDRAW_WALLET:
        photo_file = update.message.photo[-1]
        context.user_data["wallet_proof_file_id"] = photo_file.file_id
        await send_withdraw_request(update, context)

# معالج الفيديو (للنشر)
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("action") == "broadcast" and context.user_data.get("step") == "waiting_content":
        await broadcast_content(update, context)

# معالج المستندات (للنشر)
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("action") == "broadcast" and context.user_data.get("step") == "waiting_content":
        await broadcast_content(update, context)

# معالج النصوص لعناوين المحفظة في السحب
async def handle_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "step" not in context.user_data:
        return
    if context.user_data.get("action") == "withdraw" and context.user_data.get("step") == WAITING_WITHDRAW_WALLET:
        context.user_data["wallet_address"] = update.message.text
        await send_withdraw_request(update, context)

# معالج اختيار المحفظة
async def wallet_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    wallet = query.data.replace("wallet_", "")
    context.user_data["wallet"] = wallet
    action = context.user_data["action"]

    if action == "deposit":
        wallet_info = WALLETS.get(wallet, wallet)
        await query.edit_message_text(
            f"💳 وسيلة الدفع: {wallet}\n"
            f"📋 بيانات المحفظة:\n{wallet_info}\n\n"
            f"💰 المبلغ: {context.user_data['amount']}\n"
            f"🔹 قم بتحويل المبلغ إلى المحفظة أعلاه.\n"
            f"ثم أرسل صورة أو رابط تأكيد الدفع."
        )
        context.user_data["step"] = WAITING_PROOF

    elif action == "withdraw":
        await query.edit_message_text(
            f"💰 وسيلة السحب: {wallet}\n"
            f"أرسل عنوان محفظتك (رابط، رقم، أو صورة باركود):"
        )
        context.user_data["step"] = WAITING_WITHDRAW_WALLET

# دوال إرسال الطلبات إلى القناة (بدون تغيير)
async def send_deposit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = context.user_data["username"]
    amount = context.user_data["amount"]
    wallet = context.user_data.get("wallet")
    proof = context.user_data.get("proof")

    tx_id = generate_tx_id(username, amount, time.time())
    context.bot_data[tx_id] = {
        "user_id": user_id,
        "username": username,
        "amount": amount,
        "wallet": wallet,
        "type": "deposit",
        "player_id": context.user_data["player_id"]
    }

    message = f"طلب إيداع جديد\n"
    message += f"👤 اسم المستخدم: {username}\n"
    message += f"💰 المبلغ: {amount}\n"
    message += f"💳 وسيلة الدفع: {wallet}\n"
    if proof:
        message += f"📎 إثبات: {proof}\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_deposit_{tx_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"reject_deposit_{tx_id}")]
    ])

    if context.user_data.get("proof_file_id"):
        await context.bot.send_photo(
            chat_id=ADMIN_CHANNEL,
            photo=context.user_data["proof_file_id"],
            caption=message,
            reply_markup=keyboard
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_CHANNEL,
            text=message,
            reply_markup=keyboard
        )

    await update.message.reply_text("✅ تم إرسال طلبك إلى المشرفين. سيتم إعلامك عند الموافقة.")
    context.user_data.clear()

async def send_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = context.user_data["username"]
    amount = context.user_data["amount"]
    wallet = context.user_data.get("wallet")
    wallet_address = context.user_data.get("wallet_address")

    discounted = amount * 0.9
    tx_id = generate_tx_id(username, amount, time.time())
    context.bot_data[tx_id] = {
        "user_id": user_id,
        "username": username,
        "amount": amount,
        "wallet": wallet,
        "type": "withdraw",
        "player_id": context.user_data["player_id"]
    }

    message = f"طلب سحب جديد\n"
    message += f"👤 اسم المستخدم: {username}\n"
    message += f"💰 المبلغ: {amount} (بعد الخصم 10%: {int(discounted)})\n"
    message += f"💳 وسيلة السحب: {wallet}\n"
    message += f"📬 عنوان المحفظة: {wallet_address}\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_withdraw_{tx_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"reject_withdraw_{tx_id}")]
    ])

    if context.user_data.get("wallet_proof_file_id"):
        await context.bot.send_photo(
            chat_id=ADMIN_CHANNEL,
            photo=context.user_data["wallet_proof_file_id"],
            caption=message,
            reply_markup=keyboard
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_CHANNEL,
            text=message,
            reply_markup=keyboard
        )

    await update.message.reply_text("✅ تم إرسال طلب السحب إلى المشرفين. سيتم إعلامك عند الموافقة.")
    context.user_data.clear()

async def process_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, tx_id: str, op_type: str, approved: bool):
    query = update.callback_query
    await query.answer()

    data = context.bot_data.get(tx_id)
    if not data:
        await query.edit_message_text("⚠️ العملية منتهية الصلاحية.")
        return

    user_id = data["user_id"]
    username = data["username"]
    amount = data["amount"]
    player_id = data["player_id"]

    if approved:
        if op_type == "deposit":
            result = await deposit(player_id, amount)
            if result.get('status') == True:
                new_balance = await get_balance(player_id)
                await context.bot.send_message(
                    user_id,
                    f"✅ تمت الموافقة على طلب الإيداع.\n💰 رصيدك الحالي: {new_balance} NSP"
                )
                await query.edit_message_reply_markup(reply_markup=None)
                await query.edit_message_caption(caption=query.message.caption + "\n\n✅ تمت الموافقة")
            else:
                await context.bot.send_message(user_id, "❌ حدث خطأ في تنفيذ الإيداع.")
        else:
            result = await withdraw(player_id, amount)
            if result.get('status') == True:
                new_balance = await get_balance(player_id)
                await context.bot.send_message(
                    user_id,
                    f"✅ تمت الموافقة على طلب السحب.\n💰 رصيدك الحالي: {new_balance} NSP"
                )
                await query.edit_message_reply_markup(reply_markup=None)
                await query.edit_message_caption(caption=query.message.caption + "\n\n✅ تمت الموافقة")
            else:
                await context.bot.send_message(user_id, "❌ حدث خطأ في تنفيذ السحب.")
    else:
        await context.bot.send_message(
            user_id,
            "❌ تم رفض طلبك. تواصل مع الدعم."
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ مرفوض")

    del context.bot_data[tx_id]

# ==================== تشغيل البوت ====================
def main():
    init_users_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(?!wallet_).*$"))
    app.add_handler(CallbackQueryHandler(wallet_choice_handler, pattern="^wallet_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("✅ البوت المتكامل يعمل...")
    app.run_polling()

if __name__ == "__main__":
    main()
