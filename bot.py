"""
Facebook UID Tracker Bot - Railway Optimized Version
Tối ưu memory và CPU để nằm trong free tier
"""

import os
import logging
import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

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
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "2"))  # Giảm xuống 2 phút
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ========== DATABASE (SQLite - Nhẹ nhàng) ==========
DB_FILE = "/tmp/bot_data.db"  # Railway có persistent storage

def init_database():
    """Khởi tạo database SQLite"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    
    # Tạo bảng users
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
    
    # Tạo bảng keys
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
    
    # Tạo bảng uids
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
            FOREIGN KEY (owner_id) REFERENCES users (id),
            FOREIGN KEY (key_id) REFERENCES access_keys (id),
            UNIQUE(uid, key_id)
        )
    ''')
    
    # Tạo indexes cho performance
    c.execute('CREATE INDEX IF NOT EXISTS idx_uids_key ON facebook_uids(key_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_uids_status ON facebook_uids(current_status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_keys_expired ON access_keys(expired_at)')
    
    conn.commit()
    conn.close()
    
    logging.info("Database initialized")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Để trả về dict
    return conn

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
    def create_key(owner_telegram_id: str, days: int = 30, notes: str = None):
        """Tạo key mới"""
        conn = get_db()
        c = conn.cursor()
        
        # Get or create user
        c.execute("SELECT id FROM users WHERE telegram_id = ?", (owner_telegram_id,))
        user = c.fetchone()
        
        if not user:
            c.execute(
                "INSERT INTO users (telegram_id) VALUES (?)",
                (owner_telegram_id,)
            )
            user_id = c.lastrowid
        else:
            user_id = user['id']
        
        # Generate unique key
        key_value = KeyManager.generate_key()
        while True:
            c.execute("SELECT id FROM access_keys WHERE key_value = ?", (key_value,))
            if not c.fetchone():
                break
            key_value = KeyManager.generate_key()
        
        # Create key
        expired_at = datetime.now() + timedelta(days=days)
        c.execute(
            """INSERT INTO access_keys 
               (key_value, owner_id, expired_at, notes) 
               VALUES (?, ?, ?, ?)""",
            (key_value, user_id, expired_at, notes)
        )
        
        conn.commit()
        conn.close()
        
        logging.info(f"Created key {key_value} for user {owner_telegram_id}")
        return key_value
    
    @staticmethod
    def validate_key(key_value: str) -> Tuple[bool, Optional[Dict], str]:
        """Validate key"""
        conn = get_db()
        c = conn.cursor()
        
        c.execute(
            """SELECT k.*, u.telegram_id, u.username 
               FROM access_keys k 
               JOIN users u ON k.owner_id = u.id 
               WHERE k.key_value = ?""",
            (key_value,)
        )
        key_data = c.fetchone()
        conn.close()
        
        if not key_data:
            return False, None, "Key không tồn tại"
        
        key_dict = dict(key_data)
        
        if key_dict['status'] != 'ACTIVE':
            return False, key_dict, f"Key đang ở trạng thái {key_dict['status']}"
        
        expired_at = datetime.fromisoformat(key_dict['expired_at']) if 'T' in key_dict['expired_at'] else datetime.strptime(key_dict['expired_at'], '%Y-%m-%d %H:%M:%S')
        if expired_at < datetime.now():
            return False, key_dict, "Key đã hết hạn"
        
        return True, key_dict, "Key hợp lệ"
    
    @staticmethod
    def revoke_key(key_value: str) -> bool:
        """Revoke key"""
        conn = get_db()
        c = conn.cursor()
        
        c.execute(
            "UPDATE access_keys SET status = 'DISABLED' WHERE key_value = ?",
            (key_value,)
        )
        updated = c.rowcount > 0
        
        if updated:
            c.execute(
                "UPDATE facebook_uids SET is_active = 0 WHERE key_id = (SELECT id FROM access_keys WHERE key_value = ?)",
                (key_value,)
            )
        
        conn.commit()
        conn.close()
        
        return updated

# ========== FACEBOOK CHECKER ==========
class FacebookChecker:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        self.last_request_time = 0
        self.min_interval = 1.0  # 1 giây giữa các request
    
    async def check_uid(self, uid: str) -> str:
        """Check UID status - lightweight version"""
        # Rate limiting
        now = datetime.now().timestamp()
        if now - self.last_request_time < self.min_interval:
            await asyncio.sleep(self.min_interval)
        
        try:
            # Phương pháp đơn giản nhất
            url = f"https://www.facebook.com/{uid}"
            response = await self.client.get(url, follow_redirects=False)
            
            if response.status_code in [200, 302]:
                return "LIVE"
            elif response.status_code == 404:
                return "DIE"
            else:
                return "DIE"
                
        except Exception as e:
            logging.error(f"Check error for {uid}: {e}")
            return "DIE"
        finally:
            self.last_request_time = datetime.now().timestamp()
    
    async def close(self):
        await self.client.aclose()

# ========== SCHEDULER ==========
class UIDScheduler:
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)
        self.checker = FacebookChecker()
        self.is_running = False
        
    async def start(self):
        """Start checking loop"""
        if self.is_running:
            return
        
        self.is_running = True
        asyncio.create_task(self._check_loop())
        logging.info(f"Scheduler started (interval: {CHECK_INTERVAL_MINUTES} minutes)")
    
    async def stop(self):
        """Stop scheduler"""
        self.is_running = False
        await self.checker.close()
    
    async def _check_loop(self):
        """Main checking loop"""
        while self.is_running:
            try:
                await self._check_batch()
            except Exception as e:
                logging.error(f"Check loop error: {e}")
            
            # Wait for next interval
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)
    
    async def _check_batch(self):
        """Check batch of UIDs"""
        conn = get_db()
        c = conn.cursor()
        
        # Get active UIDs (limit to 50 để tiết kiệm)
        c.execute(
            """SELECT f.*, u.telegram_id 
               FROM facebook_uids f 
               JOIN users u ON f.owner_id = u.id 
               WHERE f.is_active = 1 
               LIMIT 50"""
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
        uid = uid_data['uid']
        current_status = uid_data['current_status']
        
        # Check status
        new_status = await self.checker.check_uid(uid)
        
        # Update database
        conn = get_db()
        c = conn.cursor()
        
        now = datetime.now()
        
        if new_status != current_status:
            # Status changed
            c.execute(
                """UPDATE facebook_uids 
                   SET last_status = ?, 
                       current_status = ?, 
                       last_check_time = ?,
                       check_count = check_count + 1
                   WHERE id = ?""",
                (current_status, new_status, now, uid_data['id'])
            )
            
            # If DIE → LIVE, set done_date
            if current_status == 'DIE' and new_status == 'LIVE':
                c.execute(
                    "UPDATE facebook_uids SET done_date = ? WHERE id = ?",
                    (now, uid_data['id'])
                )
                
                # Send notification
                await self._send_notification(uid_data['telegram_id'], uid_data)
                
            logging.info(f"UID {uid} changed: {current_status} → {new_status}")
        else:
            # Status unchanged
            c.execute(
                """UPDATE facebook_uids 
                   SET last_check_time = ?,
                       check_count = check_count + 1
                   WHERE id = ?""",
                (now, uid_data['id'])
            )
        
        conn.commit()
        conn.close()
    
    async def _send_notification(self, chat_id: int, uid_data: Dict):
        """Send Telegram notification"""
        try:
            amount = uid_data['amount']
            amount_str = f"{amount:,.0f}đ".replace(",", ".")
            
            message = (
                "🎉 <b>DONE kèo Facebook</b>\n\n"
                f"👤 <b>Khách hàng:</b> {uid_data['customer_name']}\n"
                f"🆔 <b>UID:</b> <code>{uid_data['uid']}</code>\n"
                f"💰 <b>Số tiền:</b> {amount_str}\n"
                f"✅ <b>Ngày done:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")

# ========== TELEGRAM HANDLERS ==========
ADDING_UID = 1
ACTIVATING_KEY = 2

scheduler = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start"""
    user = update.effective_user
    
    # Save user to database
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
        "🤖 <b>Facebook UID Tracker</b>\n\n"
        "<b>Chức năng:</b>\n"
        "/activate - Kích hoạt KEY\n"
        "/add - Thêm UID mới\n"
        "/list - Danh sách UID\n"
        "/die - UID đang DIE\n"
        "/done - UID đã DONE\n"
        "/stats - Thống kê\n"
        "/mykey - Thông tin KEY\n"
        "/help - Hướng dẫn\n\n"
        f"🔄 Auto-check: {CHECK_INTERVAL_MINUTES} phút"
    )
    
    await update.message.reply_text(welcome, parse_mode="HTML")

async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /activate"""
    instructions = (
        "🔐 <b>KÍCH HOẠT KEY</b>\n\n"
        "Nhập KEY của bạn (ví dụ: FB-ABC123DEF456):\n\n"
        "Nhập KEY hoặc /cancel để hủy."
    )
    
    await update.message.reply_text(instructions, parse_mode="HTML")
    return ACTIVATING_KEY

async def handle_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle key input"""
    telegram_id = str(update.effective_user.id)
    key_value = update.message.text.strip().upper()
    
    # Check if admin
    if int(telegram_id) in SUPER_ADMIN_IDS:
        await update.message.reply_text(
            "👑 <b>Admin đã được kích hoạt!</b>\n"
            "Bạn có toàn quyền sử dụng hệ thống.",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    # Validate key
    is_valid, key_data, message = KeyManager.validate_key(key_value)
    
    if not is_valid:
        await update.message.reply_text(
            f"❌ <b>KEY không hợp lệ!</b>\n\n{message}",
            parse_mode="HTML"
        )
        return ACTIVATING_KEY
    
    # Save to user session (đơn giản: lưu vào database)
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET role = 'ACTIVE' WHERE telegram_id = ?",
        (telegram_id,)
    )
    conn.commit()
    conn.close()
    
    # Format expired date
    expired_at = key_data['expired_at']
    if 'T' in expired_at:
        expired_date = datetime.fromisoformat(expired_at).strftime('%d/%m/%Y')
    else:
        expired_date = expired_at[:10]
    
    success_msg = (
        "✅ <b>KÍCH HOẠT THÀNH CÔNG!</b>\n\n"
        f"🔑 <b>KEY:</b> <code>{key_value}</code>\n"
        f"👤 <b>Owner:</b> {key_data['username'] or 'N/A'}\n"
        f"⏰ <b>Hết hạn:</b> {expired_date}\n\n"
        "Bạn có thể sử dụng bot bằng các lệnh:\n"
        "/add - Thêm UID\n"
        "/list - Xem UID của bạn"
    )
    
    await update.message.reply_text(success_msg, parse_mode="HTML")
    return ConversationHandler.END

def require_key(func):
    """Decorator to require valid key"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        telegram_id = str(update.effective_user.id)
        
        # Check if admin
        if int(telegram_id) in SUPER_ADMIN_IDS:
            return await func(update, context, *args, **kwargs)
        
        # Check if user has active key
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """SELECT k.* FROM access_keys k 
               JOIN users u ON k.owner_id = u.id 
               WHERE u.telegram_id = ? 
               AND k.status = 'ACTIVE' 
               AND k.expired_at > datetime('now')""",
            (telegram_id,)
        )
        key = c.fetchone()
        conn.close()
        
        if not key:
            await update.message.reply_text(
                "❌ <b>Vui lòng kích hoạt KEY trước!</b>\n\n"
                "Nhập /activate để kích hoạt KEY của bạn.",
                parse_mode="HTML"
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

@require_key
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add"""
    instructions = (
        "📝 <b>THÊM UID MỚI</b>\n\n"
        "Nhập theo định dạng:\n\n"
        "<code>UID | Tên khách hàng | Số tiền | Trạng thái</code>\n\n"
        "<b>Ví dụ:</b>\n"
        "<code>1000123456789 | Nguyễn Văn A | 500000 | DIE</code>"
    )
    
    await update.message.reply_text(instructions, parse_mode="HTML")
    return ADDING_UID

@require_key
async def handle_uid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle UID input"""
    telegram_id = str(update.effective_user.id)
    
    try:
        text = update.message.text.strip()
        parts = [p.strip() for p in text.split("|")]
        
        if len(parts) != 4:
            await update.message.reply_text("❌ Sai định dạng! Cần 4 phần")
            return ADDING_UID
        
        uid, customer_name, amount_str, status = parts
        
        # Validate
        if status.upper() not in ['LIVE', 'DIE']:
            raise ValueError("Trạng thái phải là LIVE hoặc DIE")
        
        amount = float(amount_str.replace(",", ""))
        if amount <= 0:
            raise ValueError("Số tiền phải > 0")
        
        # Get user's active key
        conn = get_db()
        c = conn.cursor()
        
        c.execute(
            """SELECT k.id FROM access_keys k 
               JOIN users u ON k.owner_id = u.id 
               WHERE u.telegram_id = ? 
               AND k.status = 'ACTIVE' 
               AND k.expired_at > datetime('now') 
               LIMIT 1""",
            (telegram_id,)
        )
        key_data = c.fetchone()
        
        if not key_data:
            await update.message.reply_text("❌ Không tìm thấy KEY hợp lệ!")
            conn.close()
            return ConversationHandler.END
        
        key_id = key_data['id']
        
        # Get user_id
        c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        user_data = c.fetchone()
        user_id = user_data['id']
        
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
                "✅ <b>ĐÃ THÊM THÀNH CÔNG!</b>\n\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"👤 <b>Khách hàng:</b> {customer_name}\n"
                f"💰 <b>Số tiền:</b> {amount:,.0f}đ\n"
                f"📊 <b>Trạng thái:</b> {status}\n"
                f"📅 <b>Ngày nhận:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            
            await update.message.reply_text(success_msg, parse_mode="HTML")
            
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"❌ UID {uid} đã tồn tại trong hệ thống của bạn!")
        
        conn.close()
        return ConversationHandler.END
        
    except ValueError as e:
        await update.message.reply_text(f"❌ Lỗi: {str(e)}")
        return ADDING_UID
    except Exception as e:
        logging.error(f"Add UID error: {e}")
        await update.message.reply_text("❌ Có lỗi xảy ra!")
        return ADDING_UID

@require_key
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list"""
    telegram_id = str(update.effective_user.id)
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live,
                  SUM(CASE WHEN current_status = 'DIE' THEN 1 ELSE 0 END) as die
           FROM facebook_uids f
           JOIN users u ON f.owner_id = u.id
           WHERE u.telegram_id = ? AND f.is_active = 1""",
        (telegram_id,)
    )
    stats = c.fetchone()
    
    message = (
        f"📋 <b>THỐNG KÊ UID</b>\n\n"
        f"✅ <b>Đã DONE:</b> {stats['live'] or 0} kèo\n"
        f"❌ <b>Đang chờ:</b> {stats['die'] or 0} kèo\n"
        f"📊 <b>Tổng cộng:</b> {stats['total'] or 0} kèo\n\n"
        f"<i>Dùng /die để xem chi tiết UID đang DIE</i>\n"
        f"<i>Dùng /done để xem chi tiết UID đã DONE</i>"
    )
    
    await update.message.reply_text(message, parse_mode="HTML")
    conn.close()

# ... (các hàm die, done, stats tương tự)

# ========== ADMIN COMMANDS ==========
def admin_required(func):
    """Decorator for admin commands"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        telegram_id = update.effective_user.id
        
        if telegram_id not in SUPER_ADMIN_IDS:
            await update.message.reply_text("❌ Chỉ ADMIN mới có quyền này!")
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

@admin_required
async def create_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /create_key (admin only)"""
    args = context.args
    
    if len(args) < 2:
        await update.message.reply_text(
            "❌ <b>Sai cú pháp!</b>\n\n"
            "Sử dụng: <code>/create_key {telegram_id} {số_ngày} [ghi_chú]</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        target_id = args[0]
        days = int(args[1])
        notes = " ".join(args[2:]) if len(args) > 2 else None
        
        if days <= 0:
            await update.message.reply_text("❌ Số ngày phải > 0")
            return
        
        key_value = KeyManager.create_key(target_id, days, notes)
        
        await update.message.reply_text(
            f"✅ <b>ĐÃ TẠO KEY!</b>\n\n"
            f"🔑 <b>Key:</b> <code>{key_value}</code>\n"
            f"👤 <b>User:</b> {target_id}\n"
            f"⏰ <b>Hạn:</b> {days} ngày\n"
            f"📝 <b>Ghi chú:</b> {notes or 'Không có'}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Create key error: {e}")
        await update.message.reply_text("❌ Có lỗi xảy ra!")

@admin_required 
async def system_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats_system (admin only)"""
    conn = get_db()
    c = conn.cursor()
    
    # Get stats
    c.execute("SELECT COUNT(*) as total FROM users")
    total_users = c.fetchone()['total']
    
    c.execute("SELECT COUNT(*) as total FROM facebook_uids")
    total_uids = c.fetchone()['total']
    
    c.execute("SELECT COUNT(*) as live FROM facebook_uids WHERE current_status = 'LIVE'")
    live_uids = c.fetchone()['live']
    
    c.execute("SELECT COUNT(*) as active FROM access_keys WHERE status = 'ACTIVE' AND expired_at > datetime('now')")
    active_keys = c.fetchone()['active']
    
    conn.close()
    
    message = (
        "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n\n"
        f"👥 <b>Người dùng:</b> {total_users}\n"
        f"🔑 <b>Key active:</b> {active_keys}\n"
        f"🆔 <b>UID tổng:</b> {total_uids}\n"
        f"✅ <b>UID LIVE:</b> {live_uids}\n"
        f"🔄 <b>Auto-check:</b> {CHECK_INTERVAL_MINUTES} phút\n\n"
        f"⏰ <b>Thời gian:</b> {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}"
    )
    
    await update.message.reply_text(message, parse_mode="HTML")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel"""
    await update.message.reply_text("❌ Đã hủy thao tác")
    return ConversationHandler.END

# ========== MAIN APPLICATION ==========
async def main():
    """Main function"""
    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=getattr(logging, LOG_LEVEL)
    )
    
    # Initialize database
    init_database()
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handlers
    uid_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            ADDING_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    
    key_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("activate", activate_command)],
        states={
            ACTIVATING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("die", list_command))  # Simplified
    application.add_handler(CommandHandler("done", list_command))  # Simplified
    application.add_handler(CommandHandler("stats", list_command))  # Simplified
    
    # Admin commands
    application.add_handler(CommandHandler("create_key", create_key_command))
    application.add_handler(CommandHandler("stats_system", system_stats_command))
    
    # Add conversation handlers
    application.add_handler(uid_conv_handler)
    application.add_handler(key_conv_handler)
    
    # Start scheduler
    global scheduler
    scheduler = UIDScheduler(BOT_TOKEN)
    await scheduler.start()
    
    # Run bot
    logging.info("Bot is starting...")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
