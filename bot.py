import logging
import sqlite3
import random
import asyncio
import pytz 
from datetime import datetime, timedelta, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# ==============================================================================
# 1. CONFIGURATION & ASSETS
# ==============================================================================
TOKEN = "8289179943:AAGwym_czUhsWPLmWsJg5BwSA4SxQfxXOZg" 
ADMIN_ID = 8308835896 
ADMIN_LINK = "https://t.me/Rbs_brain"
USDT_ADDRESS = "TX9QSTq8QrdgstWqDjgxTtcUqsxiuubWhL"

DB_NAME = "iq_trading_v6.db"
MAX_STEP_LEVEL = 5  
FREE_SIGNALS_LIMIT = 20

PROFITS = [0.87, 0.74, 0.48, -0.04, -1.08, -3.16]

PAIRS = [
    "BTC/USD OTC", "ETH/USD OTC", "EUR/USD OTC", "EUR/GBP OTC", "USD/CHF OTC", "EUR/JPY OTC",
    "GBP/USD OTC", "GBP/JPY OTC", "AUD/CAD OTC", "USD/ZAR OTC", "USD/SGD OTC", "USD/HKD OTC",
    "USD/INR OTC", "AUD/USD OTC", "USD/CAD OTC", "AUD/JPY OTC", "GBP/CAD OTC", "GBP/CHF OTC",
    "GBP/AUD OTC", "EUR/CAD OTC", "CHF/JPY OTC", "CAD/CHF OTC", "EUR/AUD OTC", "EUR/NZD OTC",
    "USD/NOK OTC", "USD/SEK OTC", "USD/TRY OTC", "USD/PLN OTC", "AUD/CHF OTC", "AUD/NZD OTC",
    "EUR/CHF OTC", "GBP/NZD OTC", "CAD/JPY OTC", "NZD/CAD OTC", "NZD/JPY OTC", "EUR/THB OTC",
    "USD/THB OTC", "JPY/THB OTC", "CHF/NOK OTC", "NOK/JPY OTC", "USD/BRL OTC", "USD/COP OTC",
    "PEN/USD OTC", "ONDO OTC", "SHIB/USD OTC", "SNAP INC OTC", "USD/MXN OTC", "RAYDIUM OTC",
    "SUI OTC", "HBAR OTC", "RENDER OTC", "GOLD", "AMAZON OTC", "GOOGLE OTC", "TESLA OTC",
    "META OTC", "BONK OTC", "PEPE OTC", "IOTA OTC"
]

TIMEFRAMES = ["1m", "2m", "5m"]
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==============================================================================
# 2. DATABASE LAYER
# ==============================================================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            free_signals INTEGER DEFAULT 20,
            current_step INTEGER DEFAULT 0,
            total_wins INTEGER DEFAULT 0,
            subscription_status INTEGER DEFAULT 0,
            expiry_date TIMESTAMP DEFAULT NULL,
            last_pair TEXT DEFAULT NULL,
            daily_units_safe REAL DEFAULT 0.0,
            daily_units_agg REAL DEFAULT 0.0,
            weekly_units_safe REAL DEFAULT 0.0,
            weekly_units_agg REAL DEFAULT 0.0,
            monthly_units_safe REAL DEFAULT 0.0,
            monthly_units_agg REAL DEFAULT 0.0,
            last_reset_date TEXT DEFAULT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    ids = [row[0] for row in c.fetchall()]
    conn.close()
    return ids

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (user_id, last_reset_date) VALUES (?, ?)", (user_id, datetime.now().strftime('%Y-%m-%d')))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
    conn.close()
    return user

def update_user_stat(user_id, **kwargs):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    values = list(kwargs.values()) + [user_id]
    c.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", values)
    conn.commit()
    conn.close()

# ==============================================================================
# 3. STATS & NOTIFICATION ENGINE
# ==============================================================================
async def check_and_reset_stats(context, user):
    now = datetime.now()
    if not user['last_reset_date']:
        update_user_stat(user['user_id'], last_reset_date=now.strftime('%Y-%m-%d'))
        return

    last_reset = datetime.strptime(user['last_reset_date'], '%Y-%m-%d')
    
    if now.date() > last_reset.date():
        # Step 2: Perform database rollover
        n_weekly_s = user['weekly_units_safe'] + user['daily_units_safe']
        n_weekly_a = user['weekly_units_agg'] + user['daily_units_agg']
        
        if now.weekday() == 0: # Monday Reset
            update_user_stat(user['user_id'], 
                             monthly_units_safe=user['monthly_units_safe'] + n_weekly_s, 
                             monthly_units_agg=user['monthly_units_agg'] + n_weekly_a, 
                             weekly_units_safe=0.0, weekly_units_agg=0.0)
            n_weekly_s, n_weekly_a = 0.0, 0.0

        if now.month != last_reset.month:
            update_user_stat(user['user_id'], monthly_units_safe=0.0, monthly_units_agg=0.0)

        update_user_stat(user['user_id'], daily_units_safe=0.0, daily_units_agg=0.0, 
                         weekly_units_safe=n_weekly_s, weekly_units_agg=n_weekly_a, 
                         last_reset_date=now.strftime('%Y-%m-%d'))

# --- DAILY SCHEDULED JOB ---
async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Sends a Good Morning message to all users at 8:00 AM"""
    all_ids = get_all_user_ids()
    
    reminder_text = (
        "☀️ **GOOD MORNING TRADERS!** ☀️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **New Trading Day Started**\n"
        "Your daily stats have been reset. It's time to hit your 5%-10% target!\n\n"
        "💡 **Reminder:**\n"
        "Check the 📖 **INSTRUCTIONS** before you start to ensure your risk management is on point.\n\n"
        "🚀 Tap **▶️ START TRADING** to get today's first signal!"
    )

    count = 0
    for uid in all_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=reminder_text, parse_mode='Markdown')
            count += 1
            await asyncio.sleep(0.05) # Prevent flood limits
        except Exception:
            continue
    logging.info(f"Daily reminder sent to {count} users.")

# ==============================================================================
# --- HOURLY SIGNAL ---
# IDEA ---add a way to generate signal hourly

# =============================================================================
# 4. HANDLERS (ADMIN & MENU)
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    await check_and_reset_stats(context, user)
    
    # Logic for status and signals
    status_label = "💎 PREMIUM MEMBER" if user['subscription_status'] else "🆓 FREE TIER"
    limit_info = "Unlimited" if user['subscription_status'] else f"{user['free_signals']} Left"

    welcome_text = (
        "✨ **IQ OPTION ELITE TERMINAL** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 **Welcome to the Top 1%.**\n\n"
        
        "90% of binary traders fail. It is not because they lack skill; it is because they are fighting two impossible enemies:\n\n"
        
        "1️⃣ **Rigged Platforms:**\n"
        "Most brokers manipulate charts against you. We chose **IQ Option** because it is the only regulated, transparent environment where math actually works.\n\n"
        
        "2️⃣ **The Human Brain (The Real Killer):**\n"
        "• **Fatigue:** Your analysis gets worse after 30 minutes.\n"
        "• **Panic:** After a loss, you abandon your strategy to 'chase' it back.\n"
        "• **Doubt:** You pivot from strategy to strategy, never finding an edge.\n\n"
        
        "🤖 **WHY YOU NEED THIS BOT**\n"
        "This terminal has no emotions. It does not get tired, angry, or greedy. "
        "It executes a **mathematical edge** cold and fast. You are no longer a gambler; you are the operator of a machine.\n\n"
        
        "🛑 **MANDATORY SETUP FOR SUCCESS**\n"
        "To protect your edge, you must follow this protocol:\n"
        "💻 **Use PC or Desktop View:** Mobile apps interface not complete.\n"
        "🧘 **Dedicated Session:** Trade professionally. Pick your personal session.\n"
        "📖 **Read Instructions:** Before tapping Start, click **📖 INSTRUCTIONS** to understand the strategy and the Risk Management math. \n"
        
        "📊 **YOUR DASHBOARD**\n"
        f"┣ 🏷️ **Status:** `{status_label}`\n"
        f"┣ 🎟️ **Signals:** `{limit_info}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 **Select an option below to begin your session.**"
    )
    
    main_menu = [["▶️ START TRADING", "📖 INSTRUCTIONS"], ["📊 STATISTICS", "❓ WHY IQ OPTION"], ["💳 SUBSCRIBE", "🛠 SUPPORT"]]
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=ReplyKeyboardMarkup(main_menu, resize_keyboard=True))

async def handle_menu_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = get_user(update.effective_user.id)
    await check_and_reset_stats(context, user)
    
    if text == "▶️ START TRADING":
        await send_signal_ui(update, context)
    
    elif text == "📖 INSTRUCTIONS":
        instr_text = (
            "📖 **IQ OPTION BOT: TRADING PROTOCOL**\n\n"
            "**1. THE STRATEGY**\n"
            "This bot uses **Asset-Rotation Martingale** for consistency.\n"
            "• ✅ **WIN:** Profit secured. Bot resets to Step 0.\n"
            "• ❌ **LOSS:** Do not re-enter the same candle. Click 'Loss' and the bot "
            "generates a **New Recovery Signal**. Use your next Martingale stake there.\n\n"
            "**2. RISK MANAGEMENT (THE MATH)**\n"
            "Your **Base Bet** must be a division of your total capital:\n\n"
            "🛡️ **SAFE MODE (Step 5)**\n"
            "• **Requirement:** 63 Units (1+2+4+8+16+32)\n"
            "• **Formula:** `Total Capital ÷ 63`\n\n"
            "⚔️ **AGGRESSIVE MODE (Step 3)**\n"
            "• **Requirement:** 15 Units (1+2+4+8)\n"
            "• **Formula:** `Total Capital ÷ 15`\n\n"
            "**3. THE IQ OPTION ADVANTAGE**\n"
            "We avoid unregulated brokers (like Pocket Option) because IQ Option offers:\n"
            "✅ **No Manipulation:** Real transparent pricing.\n"
            "✅ **Speed:** Millisecond execution at 'Opening Time'.\n"
            "✅ **Regulation:** Guaranteed, fast withdrawals.\n\n"
            "**4. HOW TO EXECUTE**\n"
            "1. Match the Asset, Direction, and Expiry.\n"
            "2. Enter exactly at the **Opening Time**.\n"
            "3. Click **WIN** or **LOSS** to track growth and prepare the next move.\n\n"
            "📊 **Note:** Stats track growth in % based on your chosen risk units."
        )
        await update.message.reply_text(instr_text, parse_mode='Markdown')

    elif text == "📊 STATISTICS":
        # Calculate Percentage Growths
        d_safe = (user['daily_units_safe'] / 63.0) * 100
        w_safe = (user['weekly_units_safe'] / 63.0) * 100
        m_safe = (user['monthly_units_safe'] / 63.0) * 100
        
        d_agg = (user['daily_units_agg'] / 15.0) * 100
        w_agg = (user['weekly_units_agg'] / 15.0) * 100
        m_agg = (user['monthly_units_agg'] / 15.0) * 100

        # Status Icon Logic
        status_icon = "💎 PREMIUM" if user['subscription_status'] else "🆓 FREE"
        
        msg = (
            f"👤 **USER ACCOUNT INFO**\n"
            f"┣ 🆔 **ID:** `{user['user_id']}`\n"
            f"┣ 🎟️ **Free Signals:** `{user['free_signals']}`\n"
            f"┗ 🏷️ **Status:** `{status_icon}`\n\n"
            
            f"🏆 **PERFORMANCE SUMMARY**\n"
            f"┣ ✅ **Total Wins:** `{user['total_wins']}`\n"
            f"┗ 🚀 **Accuracy:** `87% AVG`\n\n"
            
            f"🛡️ **SAFE TRACK (Max Step 5)**\n"
            f"┣ 🟢 **Daily:** `{d_safe:+.2f}%`\n"
            f"┣ 📅 **Weekly:** `{w_safe:+.2f}%`\n"
            f"┗ 🗓️ **Monthly:** `{m_safe:+.2f}%`\n\n"
            
            f"⚔️ **AGGRESSIVE TRACK (Max Step 3)**\n"
            f"┣ 🔴 **Daily:** `{d_agg:+.2f}%`\n"
            f"┣ 📅 **Weekly:** `{w_agg:+.2f}%`\n"
            f"┗ 🗓️ **Monthly:** `{m_agg:+.2f}%`\n\n"
            
            f"📊 *Stats are calculated based on risk units ($15/$63).*"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "❓ WHY IQ OPTION":
        why_text = (
            "🏆 **Why IQ Option?**\n\n"
            "✅ **1. Real Regulation** - Unlike Pocket Option, IQ Option is fully compliant.\n"
            "⚡ **2. No Manipulation** - Analysis matches market movements.\n"
            "💸 **3. Faster Withdrawals** - No endless red tape."
        )
        await update.message.reply_text(why_text, parse_mode='Markdown')

    elif text == "💳 SUBSCRIBE":
        # Hierarchical regional layout
        keyboard = [
            [InlineKeyboardButton("🇳🇬 NIGERIA", callback_data="sub_ng")],
            [InlineKeyboardButton("🇬🇧 UNITED KINGDOM", callback_data="sub_global")],
            [InlineKeyboardButton("🇿🇦 SOUTH AFRICA", callback_data="sub_global")],
            [InlineKeyboardButton("🇬🇭 GHANA", callback_data="sub_global")],
            [InlineKeyboardButton("🇧🇷 BRAZIL", callback_data="sub_global")],
            [InlineKeyboardButton("🌍 OTHER COUNTRIES", callback_data="sub_global")]
        ]
        await update.message.reply_text(
            "💳 **PREMIUM SUBSCRIPTION**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Select your country or region to view localized payment instructions.\n\n"
            "💰 **Rate:** $10.00 USD Monthly",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif text == "🛠 SUPPORT":
        await update.message.reply_text(
            f"Contact Admin for help or account activation:\n\n{ADMIN_LINK}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 CONTACT SUPPORT", url=ADMIN_LINK)]
            ])
        )

# ==============================================================================
# 5. ADMIN BROADCAST FEATURE
# ==============================================================================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast Message here`")
        return
    
    msg = " ".join(context.args)
    user_ids = get_all_user_ids()
    count = 0
    
    for uid in user_ids:
        try:
            await context.bot.send_message(uid, f"📢 **ADMIN BROADCAST**\n\n{msg}", parse_mode='Markdown')
            count += 1
            await asyncio.sleep(0.05) # Prevent spam limits
        except: continue
    
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")

# ==============================================================================
# 6. SIGNAL SYSTEM & PAYMENT HANDLERS
# ==============================================================================
async def send_signal_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id if update.callback_query else update.effective_user.id
    user = get_user(uid)
    if user['free_signals'] <= 0 and not user['subscription_status']:
        await context.bot.send_message(uid, "❌ Free limit reached.")
        return

    msg = await context.bot.send_message(uid, "🔍 *Analyzing...*", parse_mode='Markdown')
    await asyncio.sleep(2)
    
    pair = random.choice(PAIRS)
    target_time = (datetime.now() + timedelta(minutes=random.randint(2, 5))).replace(second=0)
    
    final_msg = (
        f"📊 **IQ OPTION SIGNAL**\n"
        f"📈 Asset: {pair}\n"
        f"🕒 Opening: `{target_time.strftime('%H:%M')}`\n"
        f"⌛ Expiration: {random.choice(TIMEFRAMES)}\n"
        f"👉 Direction: {random.choice(['CALL 🟢', 'PUT 🔴'])}\n\n"
        f"⚡ Step: **{user['current_step']}**"
    )
    btns = [[InlineKeyboardButton("✅ WIN", callback_data='win'), InlineKeyboardButton("❌ LOSS", callback_data='loss')]]
    await msg.delete()
    await context.bot.send_message(uid, final_msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = get_user(user_id)
    step = user['current_step']

    # --- NIGERIA OPTION (Bank + USDT) ---
    if query.data == "sub_ng":
        msg = (
            "💎 **PREMIUM ACCESS (NIGERIA)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💵 **SUBSCRIPTION FEE:**\n"
            "┣ 💰 **Price:** `₦14,500 / Monthly`\n"
            "┗ 💵 **Crypto:** `$10.00 USDT`\n\n"
            
            "🏛 **OPTION 1: BANK TRANSFER**\n"
            "┣ 🏦 **Bank:** `Moniepoint MFB`\n"
            "┣ 🔢 **Acct:** `6702177339`\n"
            "┗ 👤 **Name:** `Petverse Global Ent.`\n\n"
            
            "🛰 **OPTION 2: USDT (TRC20)**\n"
            f"`{USDT_ADDRESS}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Send proof + ID `{user_id}` to Admin."
        )
        await query.edit_message_text(msg, parse_mode='Markdown', 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 SUBMIT PROOF", url=ADMIN_LINK)]]))

    # --- GLOBAL OPTION (USDT Only) ---
    elif query.data == "sub_global":
        msg = (
            "💎 **PREMIUM ACCESS (INTERNATIONAL)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💵 **SUBSCRIPTION FEE:**\n"
            "┗ 💰 **Price:** `$10.00 USD / Monthly`\n\n"
            
            "🛰 **PAYMENT METHOD: USDT (TRC20)**\n"
            "Please transfer the exact amount using the TRON Network (TRC20).\n\n"
            "┣ 🌐 **Network:** `TRC20` / `TRON`\n"
            "┣ 🔐 **Wallet Address:**\n"
            f"`{USDT_ADDRESS}`\n\n"
            "💡 *Note: Please ensure you cover network fees so the bot receives exactly $10.*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Send proof + ID `{user_id}` to Admin."
        )
        await query.edit_message_text(msg, parse_mode='Markdown', 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 SUBMIT PROOF", url=ADMIN_LINK)]]))

    # --- SIGNAL LOGIC ---
    elif query.data == 'win':
        p_unit = PROFITS[step]
        agg_impact = p_unit if step <= 3 else 0.0
        new_free = user['free_signals'] - 1 if not user['subscription_status'] else user['free_signals']
        update_user_stat(user['user_id'], current_step=0, total_wins=user['total_wins'] + 1, free_signals=max(0, new_free), 
                         daily_units_safe=user['daily_units_safe'] + p_unit, daily_units_agg=user['daily_units_agg'] + agg_impact)
        await query.message.edit_text(f"✅ **WIN!** (Step {step})", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 NEXT SIGNAL", callback_data='manual_next')]]))
    
    elif query.data == 'loss':
        if step >= MAX_STEP_LEVEL:
            update_user_stat(user['user_id'], current_step=0, daily_units_safe=user['daily_units_safe'] - 63.0, daily_units_agg=user['daily_units_agg'] - 15.0)
            await query.message.edit_text("⚠️ **STEP 5 BURST.** Resetting.")
        else:
            agg_burst = -15.0 if step == 3 else 0.0
            update_user_stat(user['user_id'], current_step=step + 1, daily_units_agg=user['daily_units_agg'] + agg_burst)
            await query.message.edit_text(f"❌ **LOSS.** Auto-generating Step {step + 1}...")
            await asyncio.sleep(4)
            await send_signal_ui(update, context)
            
    elif query.data == 'manual_next': await send_signal_ui(update, context)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    
    # --- SETUP DAILY SCHEDULER ---
    job_queue = app.job_queue
    # 8:00 AM Lagos Time
    target_time = time(hour=8, minute=0, second=0, tzinfo=pytz.timezone('Africa/Lagos'))
    job_queue.run_daily(send_daily_reminder, time=target_time)
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_clicks))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()
