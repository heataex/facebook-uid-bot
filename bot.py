"""
Facebook UID Tracker Bot - Railway Fixed Version
FIXED: RuntimeError: This event loop is already running
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
    CallbackQueryHandler,
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
    """Khởi tạo database đơn giản cho Railway"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        
        # Bảng users
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                role TEXT DEFAULT 'USER',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        # Bảng keys
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
        
        # Bảng uids
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
                FOREIGN KEY (key_id) REFERENCES access_keys (id),
                UNIQUE(uid, key_id)
            )
        ''')
        
        # Indexes
        c.execute('CREATE INDEX IF NOT EXISTS idx_uids_key ON facebook_uids(key_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_uids_status ON facebook_uids(current_status)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_keys_expired ON access_keys(expired_at)')
        
        conn.commit()
        conn.close()
        
        logging.info("Database initialized successfully")
        
    except Exception as e:
        logging.error(f"Database init error: {e}")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ========== SIMPLE CHECKER ==========
class FacebookChecker:
    """Simple Facebook UID checker"""
    
    def __init__(self):
        self.client = None
        self.rate_limit = 1.0  # seconds between requests
    
    async def check_uid(self, uid: str) -> str:
        """Check if UID is LIVE or DIE"""
        try:
            if not self.client:
                self.client = httpx.AsyncClient(
                    timeout=10,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                )
            
            # Try multiple methods
            methods = [
                self._check_profile_page,
                self._check_graph_api,
                self._check_mobile_page
            ]
            
            for method in methods:
                result = await method(uid)
                if result != "ERROR":
                    return result
                await asyncio.sleep(0.5)
            
            return "DIE"
            
        except Exception as e:
            logging.error(f"Check error for {uid}: {e}")
            return "DIE"
    
    async def _check_profile_page(self, uid: str) -> str:
        """Check using profile page"""
        try:
            url = f"https://www.facebook.com/{uid}"
            response = await self.client.get(url, follow_redirects=False)
            
            if response.status_code in [200, 302]:
                content = response.text.lower()
                if "page isn't available" in content or "content not found" in content:
                    return "DIE"
                return "LIVE"
            elif response.status_code == 404:
                return "DIE"
            else:
                return "ERROR"
                
        except Exception:
            return "ERROR"
    
    async def _check_graph_api(self, uid: str) -> str:
        """Check using Graph API"""
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
                return "ERROR"
                
        except Exception:
            return "ERROR"
    
    async def _check_mobile_page(self, uid: str) -> str:
        """Check using mobile page"""
        try:
            url = f"https://m.facebook.com/{uid}"
            response = await self.client.get(url, follow_redirects=False)
            
            if response.status_code in [200, 302]:
                return "LIVE"
            elif response.status_code == 404:
                return "DIE"
            else:
                return "ERROR"
                
        except Exception:
            return "ERROR"
    
    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()

# ========== BOT HANDLERS ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    # Save user
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT OR IGNORE INTO users (telegram_id, username, first_name) 
           VALUES (?, ?, ?)""",
        (str(user.id), user.username, user.first_name)
    )
    conn.commit()
    conn.close()
    
    welcome = (
        "👋 *Chào mừng đến với FB UID Tracker*\n\n"
        "🔹 *Tính năng:*\n"
        "• Theo dõi UID LIVE/DIE\n"
        "• Thông báo realtime\n"
        "• Quản lý bằng KEY\n\n"
        "📋 *Lệnh có sẵn:*\n"
        "/add - Thêm UID mới\n"
        "/list - Danh sách UID\n"
        "/stats - Thống kê\n"
        "/mykey - Thông tin KEY\n"
        "/help - Hướng dẫn\n\n"
        f"🔄 Auto-check: {CHECK_INTERVAL_MINUTES} phút"
    )
    
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "🆘 *HƯỚNG DẪN SỬ DỤNG*\n\n"
        "1. *Kích hoạt KEY:*\n"
        "   Liên hệ admin để được cấp KEY\n\n"
        "2. *Thêm UID:*\n"
        "   /add\n"
        "   Format: `UID | Tên KH | Số tiền | Trạng thái`\n"
        "   Ví dụ: `1000123456789 | Nguyễn Văn A | 500000 | DIE`\n\n"
        "3. *Theo dõi:*\n"
        "   • Bot tự động check mỗi {CHECK_INTERVAL_MINUTES} phút\n"
        "   • Thông báo khi DIE → LIVE\n"
        "   • /list - Xem danh sách UID\n\n"
        "4. *Thống kê:*\n"
        "   • /stats - Xem thống kê\n"
        "   • /mykey - Thông tin KEY\n\n"
        "📞 *Hỗ trợ:* Liên hệ admin nếu cần giúp đỡ"
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command"""
    instructions = (
        "📝 *THÊM UID MỚI*\n\n"
        "Nhập theo định dạng:\n\n"
        "`UID | Tên khách hàng | Số tiền | Trạng thái`\n\n"
        "*Ví dụ:*\n"
        "`1000123456789 | Nguyễn Văn A | 500000 | DIE`\n\n"
        "*Lưu ý:*\n"
        "• Trạng thái: LIVE hoặc DIE\n"
        "• Số tiền: VNĐ (không dấu phẩy)"
    )
    
    await update.message.reply_text(instructions, parse_mode="Markdown")
    return 1  # Conversation state

async def handle_add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle UID input"""
    try:
        text = update.message.text.strip()
        parts = [p.strip() for p in text.split("|")]
        
        if len(parts) != 4:
            await update.message.reply_text("❌ *Sai định dạng!* Cần 4 phần")
            return 1
        
        uid, customer_name, amount_str, status = parts
        
        # Validate
        if status.upper() not in ["LIVE", "DIE"]:
            await update.message.reply_text("❌ *Trạng thái* phải là LIVE hoặc DIE")
            return 1
        
        try:
            amount = float(amount_str.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ *Số tiền* không hợp lệ")
            return 1
        
        # Get user info
        user = update.effective_user
        conn = get_db()
        c = conn.cursor()
        
        # Get or create user
        c.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (str(user.id),)
        )
        user_data = c.fetchone()
        
        if not user_data:
            c.execute(
                "INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
                (str(user.id), user.username, user.first_name)
            )
            user_id = c.lastrowid
        else:
            user_id = user_data['id']
        
        # Get active key for user
        c.execute(
            """SELECT k.id FROM access_keys k 
               WHERE k.owner_id = ? 
               AND k.status = 'ACTIVE'
               AND k.expired_at > datetime('now')
               LIMIT 1""",
            (user_id,)
        )
        key_data = c.fetchone()
        
        if not key_data:
            await update.message.reply_text(
                "❌ *Bạn chưa có KEY hợp lệ!*\nLiên hệ admin để được cấp KEY.",
                parse_mode="Markdown"
            )
            conn.close()
            return ConversationHandler.END
        
        key_id = key_data['id']
        
        # Add UID
        try:
            c.execute(
                """INSERT INTO facebook_uids 
                   (uid, customer_name, amount, current_status, owner_id, key_id) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uid, customer_name, amount, status.upper(), user_id, key_id)
            )
            conn.commit()
            
            success_msg = (
                "✅ *ĐÃ THÊM THÀNH CÔNG!*\n\n"
                f"🆔 *UID:* `{uid}`\n"
                f"👤 *Khách hàng:* {customer_name}\n"
                f"💰 *Số tiền:* {amount:,.0f}đ\n"
                f"📊 *Trạng thái:* {status}\n"
                f"📅 *Ngày nhận:* {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            
            await update.message.reply_text(success_msg, parse_mode="Markdown")
            
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"❌ *UID {uid}* đã tồn tại trong hệ thống của bạn!")
        
        conn.close()
        return ConversationHandler.END
        
    except Exception as e:
        logging.error(f"Add UID error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!* Vui lòng thử lại.")
        return ConversationHandler.END

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    user = update.effective_user
    
    conn = get_db()
    c = conn.cursor()
    
    # Get user's UIDs
    c.execute(
        """SELECT 
               f.uid,
               f.customer_name,
               f.amount,
               f.current_status,
               f.done_date,
               f.last_check_time
           FROM facebook_uids f
           JOIN users u ON f.owner_id = u.id
           WHERE u.telegram_id = ? 
           AND f.is_active = 1
           ORDER BY f.created_at DESC
           LIMIT 20""",
        (str(user.id),)
    )
    uids = c.fetchall()
    
    if not uids:
        await update.message.reply_text("📭 *Bạn chưa có UID nào!*")
        conn.close()
        return
    
    # Format message
    message = "📋 *DANH SÁCH UID*\n━━━━━━━━━━━━━━━━━━━━\n"
    
    for uid in uids:
        status_emoji = "🟢" if uid['current_status'] == 'LIVE' else "🔴"
        amount_str = f"{uid['amount']:,.0f}đ".replace(",", ".")
        
        message += f"{status_emoji} `{uid['uid']}` - {amount_str}\n"
        
        if uid['customer_name']:
            message += f"   👤 {uid['customer_name'][:20]}...\n"
        
        if uid['last_check_time']:
            check_time = uid['last_check_time']
            if isinstance(check_time, str):
                check_time = check_time[:16]
            message += f"   ⏰ {check_time}\n"
        
        message += "\n"
    
    message += f"━━━━━━━━━━━━━━━━━━━━\n📊 *Tổng:* {len(uids)} UID"
    
    await update.message.reply_text(message, parse_mode="Markdown")
    conn.close()

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user = update.effective_user
    
    conn = get_db()
    c = conn.cursor()
    
    # Get user stats
    c.execute(
        """SELECT 
               COUNT(*) as total,
               SUM(CASE WHEN f.current_status = 'LIVE' THEN 1 ELSE 0 END) as live,
               SUM(CASE WHEN f.current_status = 'DIE' THEN 1 ELSE 0 END) as die,
               SUM(CASE WHEN DATE(f.done_date) = DATE('now') THEN f.amount ELSE 0 END) as today_amount
           FROM facebook_uids f
           JOIN users u ON f.owner_id = u.id
           WHERE u.telegram_id = ? 
           AND f.is_active = 1""",
        (str(user.id),)
    )
    stats = c.fetchone()
    
    total = stats['total'] or 0
    live = stats['live'] or 0
    die = stats['die'] or 0
    today_amount = stats['today_amount'] or 0
    
    # Get today's done count
    c.execute(
        """SELECT COUNT(*) as done_today
           FROM facebook_uids f
           JOIN users u ON f.owner_id = u.id
           WHERE u.telegram_id = ?
           AND DATE(f.done_date) = DATE('now')
           AND f.current_status = 'LIVE'""",
        (str(user.id),)
    )
    today_done = c.fetchone()['done_today'] or 0
    
    message = (
        "📊 *THỐNG KÊ CỦA BẠN*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 *Tổng UID:* {total}\n"
        f"🟢 *LIVE:* {live}\n"
        f"🔴 *DIE:* {die}\n"
        f"🎯 *DONE hôm nay:* {today_done}\n"
        f"💰 *Tiền hôm nay:* {today_amount:,.0f}đ\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *Ngày:* {datetime.now().strftime('%d/%m/%Y')}"
    )
    
    await update.message.reply_text(message, parse_mode="Markdown")
    conn.close()

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command"""
    await update.message.reply_text("❌ *Đã hủy thao tác*")
    return ConversationHandler.END

# ========== SCHEDULER ==========
class UIDChecker:
    """Background UID checker"""
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.checker = FacebookChecker()
        self.running = False
    
    async def start(self):
        """Start checking loop"""
        if self.running:
            return
        
        self.running = True
        logging.info(f"UID checker started (interval: {CHECK_INTERVAL_MINUTES} minutes)")
        
        # Run in background
        asyncio.create_task(self._check_loop())
    
    async def stop(self):
        """Stop checker"""
        self.running = False
        await self.checker.close()
    
    async def _check_loop(self):
        """Main checking loop"""
        while self.running:
            try:
                await self._check_all_uids()
            except Exception as e:
                logging.error(f"Check loop error: {e})
            
            # Wait for next interval
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)
    
    async def _check_all_uids(self):
        """Check all active UIDs"""
        conn = get_db()
        c = conn.cursor()
        
        # Get active UIDs
        c.execute(
            """SELECT 
                   f.id,
                   f.uid,
                   f.current_status,
                   f.owner_id,
                   u.telegram_id
               FROM facebook_uids f
               JOIN users u ON f.owner_id = u.id
               WHERE f.is_active = 1
               LIMIT 50"""  # Limit to 50 per batch
        )
        uids = c.fetchall()
        
        if not uids:
            conn.close()
            return
        
        logging.info(f"Checking {len(uids)} UIDs")
        
        for uid_data in uids:
            try:
                await self._check_single_uid(uid_data)
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                logging.error(f"Error checking UID {uid_data['uid']}: {e}")
        
        conn.close()
    
    async def _check_single_uid(self, uid_data):
        """Check single UID"""
        current_status = uid_data['current_status']
        new_status = await self.checker.check_uid(uid_data['uid'])
        
        if new_status != current_status:
            # Update database
            conn = get_db()
            c = conn.cursor()
            
            now = datetime.now()
            
            c.execute(
                """UPDATE facebook_uids 
                   SET last_status = ?,
                       current_status = ?,
                       last_check_time = ?,
                       check_count = check_count + 1
                   WHERE id = ?""",
                (current_status, new_status, now, uid_data['id'])
            )
            
            # If DIE → LIVE, send notification
            if current_status == 'DIE' and new_status == 'LIVE':
                c.execute(
                    "UPDATE facebook_uids SET done_date = ? WHERE id = ?",
                    (now, uid_data['id'])
                )
                
                # Get UID details for notification
                c.execute(
                    "SELECT uid, customer_name, amount FROM facebook_uids WHERE id = ?",
                    (uid_data['id'],)
                )
                uid_details = c.fetchone()
                
                # Send notification
                await self._send_notification(
                    uid_data['telegram_id'],
                    dict(uid_details)
                )
                
                logging.info(f"UID {uid_data['uid']} changed: DIE → LIVE")
            
            conn.commit()
            conn.close()
    
    async def _send_notification(self, chat_id: int, uid_data: Dict):
        """Send DONE notification"""
        try:
            bot = Bot(token=self.bot_token)
            
            amount_str = f"{uid_data['amount']:,.0f}đ".replace(",", ".")
            message = (
                "🎉 *DONE KÈO FACEBOOK*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Khách hàng:* {uid_data['customer_name']}\n"
                f"🆔 *UID:* `{uid_data['uid']}`\n"
                f"💰 *Số tiền:* {amount_str}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ *Ngày done:* {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")

# ========== MAIN FUNCTION - FIXED FOR RAILWAY ==========
async def main():
    """Main function - FIXED for Railway"""
    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=getattr(logging, LOG_LEVEL)
    )
    
    # Initialize database
    init_database()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handler for adding UID
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_uid)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(conv_handler)
    
    # Start UID checker in background
    checker = UIDChecker(BOT_TOKEN)
    await checker.start()
    
    logging.info("Bot starting...")
    
    # FIX: Use run_polling() directly without asyncio.run()
    await application.run_polling()

# ========== ENTRY POINT FOR RAILWAY ==========
def run_bot():
    """Entry point for Railway - FIXED"""
    try:
        # FIX: Get existing event loop or create new one
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Run the bot
        if loop.is_running():
            # If loop is already running (Railway case), create task
            loop.create_task(main())
        else:
            # If no loop running, run it
            loop.run_until_complete(main())
            
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot error: {e}")
        raise

# FIX: For Railway deployment
if __name__ == "__main__":
    run_bot()
