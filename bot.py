"""
Facebook UID Tracker Bot - Fixed Version for Railway
Stable, no event loop errors, optimized for production
"""

import os
import logging
import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import sys

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_IDS = [int(x.strip()) for x in os.getenv("SUPER_ADMIN_IDS", "").split(",") if x.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ========== DATABASE ==========
DB_FILE = "/tmp/bot_data.db"

def init_database():
    """Initialize database"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Keys table
        c.execute('''
            CREATE TABLE IF NOT EXISTS access_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_value TEXT UNIQUE NOT NULL,
                owner_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expired_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'ACTIVE',
                notes TEXT,
                FOREIGN KEY (owner_id) REFERENCES users (id)
            )
        ''')
        
        # UIDs table
        c.execute('''
            CREATE TABLE IF NOT EXISTS facebook_uids (
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
                check_count INTEGER DEFAULT 0,
                notes TEXT,
                FOREIGN KEY (owner_id) REFERENCES users (id),
                FOREIGN KEY (key_id) REFERENCES access_keys (id)
            )
        ''')
        
        # Create indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_uids_key ON facebook_uids(key_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_uids_owner ON facebook_uids(owner_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_keys_status ON access_keys(status)')
        
        conn.commit()
        conn.close()
        print(f"Database initialized at {DB_FILE}")
        
    except Exception as e:
        print(f"Database init error: {e}")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# ========== FACEBOOK CHECKER ==========
class FacebookChecker:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
    
    async def check_uid(self, uid: str) -> str:
        """Check if UID is LIVE or DIE"""
        try:
            url = f"https://www.facebook.com/{uid}"
            response = await self.client.get(url, follow_redirects=False)
            
            if response.status_code in [200, 302]:
                text = response.text.lower()
                if "page isn't available" in text or "content not found" in text:
                    return "DIE"
                return "LIVE"
            elif response.status_code == 404:
                return "DIE"
            else:
                return await self._check_alternative(uid)
                
        except Exception as e:
            print(f"Check error for {uid}: {e}")
            return "DIE"
    
    async def _check_alternative(self, uid: str) -> str:
        """Alternative check method"""
        try:
            url = f"https://graph.facebook.com/{uid}/picture?type=large&redirect=false"
            response = await self.client.get(url)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("data") and data["data"].get("url"):
                    return "LIVE"
                else:
                    return "DIE"
            else:
                return "DIE"
        except:
            return "DIE"
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()

# ========== KEY MANAGEMENT ==========
class KeyManager:
    @staticmethod
    def generate_key():
        """Generate random key"""
        import secrets
        import string
        alphabet = string.ascii_uppercase + string.digits
        return f"FB-{''.join(secrets.choice(alphabet) for _ in range(10))}"
    
    @staticmethod
    def create_key(telegram_id: str, days: int = 30, notes: str = None):
        """Create new key"""
        conn = get_db()
        c = conn.cursor()
        
        try:
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            user = c.fetchone()
            
            if not user:
                c.execute("INSERT INTO users (telegram_id) VALUES (?)", (telegram_id,))
                user_id = c.lastrowid
            else:
                user_id = user['id']
            
            key_value = KeyManager.generate_key()
            while True:
                c.execute("SELECT id FROM access_keys WHERE key_value = ?", (key_value,))
                if not c.fetchone():
                    break
                key_value = KeyManager.generate_key()
            
            expired_at = datetime.now() + timedelta(days=days)
            c.execute(
                "INSERT INTO access_keys (key_value, owner_id, expired_at, notes) VALUES (?, ?, ?, ?)",
                (key_value, user_id, expired_at, notes)
            )
            
            conn.commit()
            return key_value
            
        except Exception as e:
            print(f"Create key error: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def validate_key(key_value: str, telegram_id: str) -> Tuple[bool, str]:
        """Validate user's key"""
        conn = get_db()
        c = conn.cursor()
        
        try:
            c.execute(
                "SELECT k.*, u.telegram_id FROM access_keys k JOIN users u ON k.owner_id = u.id WHERE k.key_value = ?",
                (key_value,)
            )
            key_data = c.fetchone()
            
            if not key_data:
                return False, "Key không tồn tại"
            
            if str(key_data['telegram_id']) != str(telegram_id):
                return False, "Key không thuộc về bạn"
            
            if key_data['status'] != 'ACTIVE':
                return False, f"Key đang ở trạng thái {key_data['status']}"
            
            expired_at = datetime.fromisoformat(key_data['expired_at'])
            if expired_at < datetime.now():
                return False, "Key đã hết hạn"
            
            return True, "Key hợp lệ"
            
        except Exception as e:
            print(f"Validate key error: {e}")
            return False, "Lỗi hệ thống"
        finally:
            conn.close()

# ========== BOT HANDLERS ==========
ADDING_UID = 1

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
                   (str(user.id), user.username, user.first_name))
        conn.commit()
    finally:
        conn.close()
    
    welcome = ("🤖 *Facebook UID Tracker Bot*\n\n/add - Thêm UID\n/list - Danh sách\n/stats - Thống kê\n/mykey - Thông tin KEY\n/help - Hướng dẫn")
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = ("🆘 *HƯỚNG DẪN SỬ DỤNG*\n1. Kích hoạt KEY\n2. Thêm UID: /add\n3. /list - Xem danh sách\n4. /stats - Thống kê")
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT k.key_value FROM access_keys k JOIN users u ON k.owner_id = u.id WHERE u.telegram_id = ? AND k.status = 'ACTIVE' AND k.expired_at > datetime('now') LIMIT 1", (user_id,))
        if not c.fetchone() and int(user_id) not in SUPER_ADMIN_IDS:
            await update.message.reply_text("❌ Bạn cần có KEY hợp lệ!")
            return ConversationHandler.END
    finally:
        conn.close()
    
    await update.message.reply_text("📝 Nhập theo: `UID | Tên | Tiền | Trạng thái`", parse_mode="Markdown")
    return ADDING_UID

async def handle_uid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4:
            await update.message.reply_text("❌ Sai định dạng!")
            return ADDING_UID
        
        uid, customer_name, amount_str, status = parts
        amount = float(amount_str.replace(",", ""))
        
        user = update.effective_user
        conn = get_db()
        c = conn.cursor()
        try:
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (str(user.id),))
            user_data = c.fetchone()
            user_id = user_data['id']
            
            c.execute("INSERT INTO facebook_uids (uid, customer_name, amount, current_status, owner_id) VALUES (?, ?, ?, ?, ?)",
                       (uid, customer_name, amount, status.upper(), user_id))
            conn.commit()
            await update.message.reply_text(f"✅ Đã thêm UID: {uid}")
        finally:
            conn.close()
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Có lỗi xảy ra!")
        return ConversationHandler.END

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    try:
        uids = conn.execute("SELECT uid, customer_name, current_status FROM facebook_uids WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?)", (str(user.id),)).fetchall()
        if not uids:
            await update.message.reply_text("📭 Danh sách trống!")
            return
        msg = "📋 *DANH SÁCH:*\n" + "\n".join([f"- `{r['uid']}`: {r['current_status']}" for r in uids])
        await update.message.reply_text(msg, parse_mode="Markdown")
    finally:
        conn.close()

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    try:
        stats = conn.execute("SELECT COUNT(*) as total, SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live FROM facebook_uids WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?)", (str(user.id),)).fetchone()
        await update.message.reply_text(f"📊 *THỐNG KÊ*\nTổng: {stats['total']}\nLIVE: {stats['live']}", parse_mode="Markdown")
    finally:
        conn.close()

async def mykey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    try:
        key = conn.execute("SELECT key_value, expired_at FROM access_keys WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?) LIMIT 1", (str(user.id),)).fetchone()
        if not key:
            await update.message.reply_text("❌ Bạn chưa có KEY!")
            return
        await update.message.reply_text(f"🔑 *KEY:* `{key['key_value']}`\nHết hạn: {key['expired_at']}")
    finally:
        conn.close()

async def create_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if int(update.effective_user.id) not in SUPER_ADMIN_IDS:
        return
    args = context.args
    if len(args) < 2: return
    key = KeyManager.create_key(args[0], int(args[1]))
    await update.message.reply_text(f"✅ Đã tạo KEY: `{key}`")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Đã hủy.")
    return ConversationHandler.END

# ========== BACKGROUND CHECKER ==========
async def check_all_uids(context: ContextTypes.DEFAULT_TYPE):
    """Background task to check UIDs"""
    checker = FacebookChecker()
    conn = get_db()
    try:
        uids = conn.execute("SELECT f.id, f.uid, f.current_status, u.telegram_id, f.customer_name, f.amount FROM facebook_uids f JOIN users u ON f.owner_id = u.id WHERE f.is_active = 1").fetchall()
        for r in uids:
            new_status = await checker.check_uid(r['uid'])
            if new_status == "LIVE" and r['current_status'] == "DIE":
                await context.bot.send_message(chat_id=r['telegram_id'], text=f"🎉 *DONE KÈO*\nUID: `{r['uid']}`", parse_mode="Markdown")
                conn.execute("UPDATE facebook_uids SET current_status='LIVE', done_date=CURRENT_TIMESTAMP WHERE id=?", (r['id'],))
            conn.execute("UPDATE facebook_uids SET last_check_time=CURRENT_TIMESTAMP WHERE id=?", (r['id'],))
        conn.commit()
    finally:
        conn.close()
        await checker.close()

# ========== MAIN APPLICATION (KHỚP NỐI HANDLER) ==========
async def main():
    logging.basicConfig(level=getattr(logging, LOG_LEVEL), format='%(asctime)s - %(message)s')
    init_database()
    
    # 1. Khởi tạo Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 2. Đăng ký ĐẦY ĐỦ các lệnh (Fix lỗi không phản hồi lệnh)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("mykey", mykey_command))
    application.add_handler(CommandHandler("create_key", create_key_command))
    
    # 3. Đăng ký luồng thêm UID
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={ADDING_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid_input)]},
        fallbacks=[CommandHandler("cancel", cancel_command)]
    ))

    # 4. Kích hoạt JobQueue (Chạy ngầm mỗi X phút)
    if application.job_queue:
        application.job_queue.run_repeating(check_all_uids, interval=CHECK_INTERVAL_MINUTES * 60, first=10)

    # 5. Chạy Bot an toàn cho Railway (Fix lỗi Loop và Healthcheck)
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logging.info("🚀 Bot đang Online!")
        try:
            while True: await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError): pass
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running(): loop.create_task(main())
        else: loop.run_until_complete(main())
    except RuntimeError:
        asyncio.run(main())
