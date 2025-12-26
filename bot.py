"""
Facebook UID Tracker Bot - Full Premium Version
Tối ưu cho Railway - Tích hợp Menu nút bấm & Quản lý nâng cao
"""

import os
import logging
import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Chuyển ID admin thành list số nguyên
SUPER_ADMIN_IDS = [int(x.strip()) for x in os.getenv("SUPER_ADMIN_IDS", "").split(",") if x.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ========== DATABASE ==========
DB_FILE = "/tmp/bot_data.db"

def init_database():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    # Bảng users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        role TEXT DEFAULT 'USER',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Bảng keys
    c.execute('''CREATE TABLE IF NOT EXISTS access_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_value TEXT UNIQUE NOT NULL,
        owner_id INTEGER,
        expired_at TIMESTAMP NOT NULL,
        status TEXT DEFAULT 'ACTIVE',
        notes TEXT)''')
    # Bảng uids
    c.execute('''CREATE TABLE IF NOT EXISTS facebook_uids (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        amount REAL NOT NULL,
        current_status TEXT DEFAULT 'DIE',
        last_status TEXT,
        receive_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        done_date TIMESTAMP,
        last_check_time TIMESTAMP,
        owner_id INTEGER,
        key_id INTEGER,
        is_active BOOLEAN DEFAULT 1,
        check_count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()
    logging.info("Database initialized with full schemas")

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ========== UTILS & KEY MGT ==========
class KeyManager:
    @staticmethod
    def generate_key():
        import secrets, string
        return f"FB-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))}"
    
    @staticmethod
    def create_key(owner_telegram_id: str, days: int = 30):
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT id FROM users WHERE telegram_id = ?", (owner_telegram_id,))
        user = c.fetchone()
        user_id = user['id'] if user else None
        if not user_id:
            c.execute("INSERT INTO users (telegram_id) VALUES (?)", (owner_telegram_id,))
            user_id = c.lastrowid
        
        key_value = KeyManager.generate_key()
        expired_at = datetime.now() + timedelta(days=days)
        c.execute("INSERT INTO access_keys (key_value, owner_id, expired_at) VALUES (?, ?, ?)",
                  (key_value, user_id, expired_at))
        conn.commit(); conn.close()
        return key_value

# ========== CORE CHECKER ==========
class FacebookChecker:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10)
    async def check_uid(self, uid: str) -> str:
        try:
            url = f"https://www.facebook.com/{uid}"
            resp = await self.client.get(url, follow_redirects=False)
            return "LIVE" if resp.status_code in [200, 302] else "DIE"
        except: return "DIE"
    async def close(self): await self.client.aclose()

class UIDScheduler:
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)
        self.checker = FacebookChecker()
        self.is_running = False
    async def start(self):
        self.is_running = True
        asyncio.create_task(self._loop())
    async def _loop(self):
        while self.is_running:
            await self._check_batch()
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)
    async def _check_batch(self):
        conn = get_db(); uids = conn.execute("SELECT f.*, u.telegram_id FROM facebook_uids f JOIN users u ON f.owner_id = u.id WHERE f.is_active = 1").fetchall()
        for row in uids:
            new_stat = await self.checker.check_uid(row['uid'])
            if new_stat != row['current_status']:
                conn.execute("UPDATE facebook_uids SET current_status=?, last_status=? WHERE id=?", (new_stat, row['current_status'], row['id']))
                if new_stat == "LIVE":
                    await self.bot.send_message(row['telegram_id'], f"🎉 <b>DONE:</b> {row['customer_name']}\nID: <code>{row['uid']}</code>", parse_mode="HTML")
        conn.commit(); conn.close()
    async def stop(self): self.is_running = False; await self.checker.close()

# ========== UI HELPERS ==========
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Thêm UID", callback_data='menu_add'), InlineKeyboardButton("📋 Danh sách", callback_data='menu_list')],
        [InlineKeyboardButton("📊 Thống kê", callback_data='menu_stats'), InlineKeyboardButton("🔑 Kích hoạt KEY", callback_data='menu_activate')],
        [InlineKeyboardButton("❓ Trợ giúp", callback_data='menu_help')]
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db(); conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name) VALUES (?,?,?)", (str(user.id), user.username, user.first_name)); conn.commit(); conn.close()
    
    # Báo Admin có người mới
    for admin_id in SUPER_ADMIN_IDS:
        try: await context.bot.send_message(admin_id, f"👤 Người dùng mới: {user.first_name} (@{user.username})")
        except: pass

    await update.message.reply_text("🤖 <b>Facebook UID Tracker - Premium</b>\nChào mừng bạn! Hãy chọn chức năng bên dưới:", reply_markup=get_main_menu(), parse_mode="HTML")

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'menu_add': await query.message.reply_text("Gõ lệnh /add để bắt đầu thêm UID (hỗ trợ thêm hàng loạt bằng cách xuống dòng).")
    elif data == 'menu_list': await list_command(update, context)
    elif data == 'menu_stats': await stats_command(update, context)
    elif data == 'menu_activate': await query.message.reply_text("Gõ lệnh /activate để nhập KEY.")
    elif data == 'menu_help': await update.effective_message.reply_text("Hướng dẫn: /add dùng định dạng UID | Tên | Tiền | DIE")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = str(update.effective_user.id)
    conn = get_db(); stats = conn.execute("SELECT current_status, COUNT(*) as c FROM facebook_uids f JOIN users u ON f.owner_id=u.id WHERE u.telegram_id=? GROUP BY current_status", (tid,)).fetchall(); conn.close()
    msg = "📋 <b>Danh sách của bạn:</b>\n"
    for s in stats: msg += f"- {s['current_status']}: {s['c']} kèo\n"
    target = update.message if update.message else update.callback_query.message
    await target.reply_text(msg or "Chưa có dữ liệu.", parse_mode="HTML")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = str(update.effective_user.id)
    conn = get_db(); row = conn.execute("SELECT SUM(amount) as total FROM facebook_uids f JOIN users u ON f.owner_id=u.id WHERE u.telegram_id=? AND current_status='LIVE'", (tid,)).fetchone(); conn.close()
    total = row['total'] if row['total'] else 0
    target = update.message if update.message else update.callback_query.message
    await target.reply_text(f"💰 <b>Tổng tiền đã DONE:</b> {total:,.0f}đ", parse_mode="HTML")

# --- Thêm UID hàng loạt ---
ADDING_UID = 1
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Gửi danh sách UID theo định dạng (mỗi dòng 1 UID):\n<code>UID | Tên | Tiền | DIE</code>", parse_mode="HTML")
    return ADDING_UID

async def add_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = str(update.effective_user.id); lines = update.message.text.split('\n'); conn = get_db(); success = 0
    # Lấy key_id
    key = conn.execute("SELECT k.id, u.id as uid FROM access_keys k JOIN users u ON k.owner_id=u.id WHERE u.telegram_id=? AND k.status='ACTIVE'", (tid,)).fetchone()
    if not key and int(tid) not in SUPER_ADMIN_IDS:
        await update.message.reply_text("❌ Bạn cần kích hoạt KEY trước."); return ConversationHandler.END
    
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 3:
            try:
                conn.execute("INSERT INTO facebook_uids (uid, customer_name, amount, current_status, owner_id, key_id) VALUES (?,?,?,?,?,?)",
                             (parts[0], parts[1], float(parts[2]), parts[3].upper() if len(parts)==4 else "DIE", key['uid'] if key else 1, key['id'] if key else 1))
                success += 1
            except: continue
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Đã nạp thành công {success} UID."); return ConversationHandler.END

# ========== ADMIN ONLY ==========
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in SUPER_ADMIN_IDS: return
    try:
        tid = context.args[0]; days = int(context.args[1])
        key = KeyManager.create_key(tid, days)
        await update.message.reply_text(f"🔑 KEY mới cho {tid}: <code>{key}</code>", parse_mode="HTML")
    except: await update.message.reply_text("Cú pháp: /create_key {id} {ngày}")

# ========== MAIN ==========
async def main():
    init_database()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern='^menu_'))
    application.add_handler(CommandHandler("create_key", create_key))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={ADDING_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_handle)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    application.add_handler(add_conv)

    global scheduler
    scheduler = UIDScheduler(BOT_TOKEN); await scheduler.start()

    async with application:
        await application.initialize(); await application.start(); await application.updater.start_polling()
        try:
            while True: await asyncio.sleep(3600)
        except: pass
        finally:
            if scheduler: await scheduler.stop()
            await application.stop(); await application.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop(); loop.create_task(main())
