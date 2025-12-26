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
            # Method 1: Try profile page
            url = f"https://www.facebook.com/{uid}"
            response = await self.client.get(url, follow_redirects=False)
            
            if response.status_code in [200, 302]:
                # Check if page is available
                text = response.text.lower()
                if "page isn't available" in text or "content not found" in text:
                    return "DIE"
                return "LIVE"
            elif response.status_code == 404:
                return "DIE"
            else:
                # Try alternative method
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
            # Get or create user
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            user = c.fetchone()
            
            if not user:
                c.execute(
                    "INSERT INTO users (telegram_id) VALUES (?)",
                    (telegram_id,)
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
                """SELECT k.*, u.telegram_id 
                   FROM access_keys k
                   JOIN users u ON k.owner_id = u.id
                   WHERE k.key_value = ?""",
                (key_value,)
            )
            key_data = c.fetchone()
            
            if not key_data:
                return False, "Key không tồn tại"
            
            # Check owner
            if str(key_data['telegram_id']) != str(telegram_id):
                return False, "Key không thuộc về bạn"
            
            # Check status
            if key_data['status'] != 'ACTIVE':
                return False, f"Key đang ở trạng thái {key_data['status']}"
            
            # Check expiration
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
    """Handle /start command"""
    user = update.effective_user
    
    # Save user to database
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(
            """INSERT OR IGNORE INTO users (telegram_id, username, first_name) 
               VALUES (?, ?, ?)""",
            (str(user.id), user.username, user.first_name)
        )
        conn.commit()
    except Exception as e:
        print(f"Save user error: {e}")
    finally:
        conn.close()
    
    welcome = (
        "🤖 *Facebook UID Tracker Bot*\n\n"
        "🔹 *Tính năng:*\n"
        "• Theo dõi UID LIVE/DIE tự động\n"
        "• Thông báo khi DIE → LIVE\n"
        "• Quản lý bằng KEY\n\n"
        "📋 *Lệnh có sẵn:*\n"
        "/add - Thêm UID mới\n"
        "/list - Xem danh sách UID\n"
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
        "   Gõ: /add\n"
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
    # Check if user has valid key
    user_id = str(update.effective_user.id)
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(
            """SELECT k.key_value 
               FROM access_keys k
               JOIN users u ON k.owner_id = u.id
               WHERE u.telegram_id = ?
               AND k.status = 'ACTIVE'
               AND k.expired_at > datetime('now')
               LIMIT 1""",
            (user_id,)
        )
        key = c.fetchone()
        
        if not key and int(user_id) not in SUPER_ADMIN_IDS:
            await update.message.reply_text(
                "❌ *Bạn cần có KEY hợp lệ để thêm UID!*\n"
                "Liên hệ admin để được cấp KEY.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
            
    except Exception as e:
        print(f"Check key error: {e}")
    finally:
        conn.close()
    
    instructions = (
        "📝 *THÊM UID MỚI*\n\n"
        "Nhập theo định dạng:\n\n"
        "`UID | Tên khách hàng | Số tiền | Trạng thái`\n\n"
        "*Ví dụ:*\n"
        "`1000123456789 | Nguyễn Văn A | 500000 | DIE`\n\n"
        "*Lưu ý:*\n"
        "• Trạng thái: LIVE hoặc DIE\n"
        "• Số tiền: VNĐ (không dấu phẩy)\n\n"
        "Gõ /cancel để hủy."
    )
    
    await update.message.reply_text(instructions, parse_mode="Markdown")
    return ADDING_UID

async def handle_uid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle UID input"""
    try:
        text = update.message.text.strip()
        parts = [p.strip() for p in text.split("|")]
        
        if len(parts) != 4:
            await update.message.reply_text("❌ *Sai định dạng!* Cần 4 phần (UID | Tên | Số tiền | Trạng thái)")
            return ADDING_UID
        
        uid, customer_name, amount_str, status = parts
        
        # Validate
        if status.upper() not in ["LIVE", "DIE"]:
            await update.message.reply_text("❌ *Trạng thái* phải là LIVE hoặc DIE")
            return ADDING_UID
        
        try:
            amount = float(amount_str.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ *Số tiền* không hợp lệ")
            return ADDING_UID
        
        # Get user info
        user = update.effective_user
        conn = get_db()
        c = conn.cursor()
        
        try:
            # Get user ID
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (str(user.id),))
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
            
            if not key_data and int(user.id) not in SUPER_ADMIN_IDS:
                await update.message.reply_text(
                    "❌ *Bạn chưa có KEY hợp lệ!*\nLiên hệ admin để được cấp KEY.",
                    parse_mode="Markdown"
                )
                return ConversationHandler.END
            
            key_id = key_data['id'] if key_data else 0
            
            # Add UID
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
            
            # Start checker if not running
            if 'checker' not in context.bot_data:
                context.bot_data['checker'] = FacebookChecker()
                asyncio.create_task(check_uid_loop(context))
            
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"❌ *UID {uid}* đã tồn tại trong hệ thống của bạn!")
        except Exception as e:
            print(f"Add UID error: {e}")
            await update.message.reply_text("❌ *Có lỗi xảy ra!* Vui lòng thử lại.")
        
        finally:
            conn.close()
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"UID input error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!* Vui lòng thử lại.")
        return ConversationHandler.END

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    user = update.effective_user
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        # Get user's UIDs
        c.execute(
            """SELECT 
                   uid,
                   customer_name,
                   amount,
                   current_status,
                   last_check_time
               FROM facebook_uids 
               WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?)
               AND is_active = 1
               ORDER BY created_at DESC
               LIMIT 20""",
            (str(user.id),)
        )
        uids = c.fetchall()
        
        if not uids:
            await update.message.reply_text("📭 *Bạn chưa có UID nào!*")
            return
        
        # Format message
        message = "📋 *DANH SÁCH UID*\n━━━━━━━━━━━━━━━━━━━━\n"
        
        for uid in uids:
            status_emoji = "🟢" if uid['current_status'] == 'LIVE' else "🔴"
            amount_str = f"{uid['amount']:,.0f}đ".replace(",", ".")
            
            message += f"{status_emoji} `{uid['uid']}` - {amount_str}\n"
            
            if uid['customer_name']:
                name = uid['customer_name'][:20] + "..." if len(uid['customer_name']) > 20 else uid['customer_name']
                message += f"   👤 {name}\n"
            
            if uid['last_check_time']:
                check_time = uid['last_check_time']
                if isinstance(check_time, str):
                    check_time = check_time[:16]
                message += f"   ⏰ {check_time}\n"
            
            message += "\n"
        
        message += f"━━━━━━━━━━━━━━━━━━━━\n📊 *Tổng:* {len(uids)} UID"
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        print(f"List command error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!*")
    finally:
        conn.close()

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user = update.effective_user
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        # Get user stats
        c.execute(
            """SELECT 
                   COUNT(*) as total,
                   SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live,
                   SUM(CASE WHEN current_status = 'DIE' THEN 1 ELSE 0 END) as die,
                   SUM(CASE WHEN DATE(done_date) = DATE('now') THEN amount ELSE 0 END) as today_amount
               FROM facebook_uids 
               WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?)
               AND is_active = 1""",
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
               FROM facebook_uids 
               WHERE owner_id = (SELECT id FROM users WHERE telegram_id = ?)
               AND DATE(done_date) = DATE('now')
               AND current_status = 'LIVE'""",
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
        
    except Exception as e:
        print(f"Stats command error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!*")
    finally:
        conn.close()

async def mykey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mykey command"""
    user = update.effective_user
    
    # Check if admin
    if int(user.id) in SUPER_ADMIN_IDS:
        await update.message.reply_text("👑 *Bạn là ADMIN* - Không cần KEY để sử dụng hệ thống.")
        return
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        # Get user's active key
        c.execute(
            """SELECT 
                   k.key_value,
                   k.created_at,
                   k.expired_at,
                   k.status
               FROM access_keys k
               JOIN users u ON k.owner_id = u.id
               WHERE u.telegram_id = ?
               AND k.status = 'ACTIVE'
               AND k.expired_at > datetime('now')
               ORDER BY k.expired_at DESC
               LIMIT 1""",
            (str(user.id),)
        )
        key_data = c.fetchone()
        
        if not key_data:
            await update.message.reply_text(
                "❌ *Bạn chưa có KEY active!*\nLiên hệ admin để được cấp KEY."
            )
            return
        
        # Format dates
        created_at = key_data['created_at']
        expired_at = key_data['expired_at']
        
        if 'T' in created_at:
            created_date = datetime.fromisoformat(created_at).strftime('%d/%m/%Y')
        else:
            created_date = created_at[:10]
        
        if 'T' in expired_at:
            expired_date = datetime.fromisoformat(expired_at)
        else:
            expired_date = datetime.strptime(expired_at, '%Y-%m-%d %H:%M:%S')
        
        days_left = (expired_date - datetime.now()).days
        
        message = (
            "🔑 *THÔNG TIN KEY*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 *Key:* `{key_data['key_value']}`\n"
            f"📅 *Ngày tạo:* {created_date}\n"
            f"⏰ *Hết hạn:* {expired_date.strftime('%d/%m/%Y')}\n"
            f"📊 *Còn lại:* {days_left} ngày\n"
            f"🟢 *Trạng thái:* {key_data['status']}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        print(f"Mykey command error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!*")
    finally:
        conn.close()

async def create_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Create new key"""
    user = update.effective_user
    
    # Check admin permission
    if int(user.id) not in SUPER_ADMIN_IDS:
        await update.message.reply_text("❌ *Chỉ ADMIN mới có quyền này!*")
        return
    
    # Parse arguments
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ *Sai cú pháp!*\n"
            "Sử dụng: `/create_key <telegram_id> <số_ngày> [ghi_chú]`\n\n"
            "*Ví dụ:*\n"
            "`/create_key 123456789 30 Key cho khách VIP`"
        )
        return
    
    try:
        telegram_id = args[0]
        days = int(args[1])
        notes = " ".join(args[2:]) if len(args) > 2 else None
        
        if days <= 0:
            await update.message.reply_text("❌ *Số ngày* phải lớn hơn 0")
            return
        
        # Create key
        key_value = KeyManager.create_key(telegram_id, days, notes)
        
        if key_value:
            await update.message.reply_text(
                f"✅ *ĐÃ TẠO KEY!*\n\n"
                f"🔑 *Key:* `{key_value}`\n"
                f"👤 *User:* {telegram_id}\n"
                f"⏰ *Hạn:* {days} ngày\n"
                f"📝 *Ghi chú:* {notes or 'Không có'}"
            )
        else:
            await update.message.reply_text("❌ *Không thể tạo KEY!*")
            
    except ValueError:
        await update.message.reply_text("❌ *Số ngày* không hợp lệ")
    except Exception as e:
        print(f"Create key command error: {e}")
        await update.message.reply_text("❌ *Có lỗi xảy ra!*")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command"""
    await update.message.reply_text("❌ *Đã hủy thao tác*")
    return ConversationHandler.END

# ========== BACKGROUND CHECKER ==========
async def check_uid_loop(context: ContextTypes.DEFAULT_TYPE):
    """Background task to check UIDs"""
    checker = FacebookChecker()
    
    while True:
        try:
            await check_all_uids(checker)
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)
        except Exception as e:
            print(f"Check loop error: {e}")
            await asyncio.sleep(60)  # Wait 1 minute on error

async def check_all_uids(checker: FacebookChecker):
    """Check all active UIDs"""
    conn = get_db()
    c = conn.cursor()
    
    try:
        # Get active UIDs (limit to prevent overload)
        c.execute(
            """SELECT 
                   f.id,
                   f.uid,
                   f.current_status,
                   u.telegram_id
               FROM facebook_uids f
               JOIN users u ON f.owner_id = u.id
               WHERE f.is_active = 1
               LIMIT 50"""
        )
        uids = c.fetchall()
        
        if not uids:
            return
        
        print(f"Checking {len(uids)} UIDs...")
        
        for uid_data in uids:
            try:
                await check_single_uid(checker, uid_data)
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                print(f"Error checking UID {uid_data['uid']}: {e}")
                
    except Exception as e:
        print(f"Check all UIDs error: {e}")
    finally:
        conn.close()

async def check_single_uid(checker: FacebookChecker, uid_data):
    """Check single UID and update status"""
    current_status = uid_data['current_status']
    new_status = await checker.check_uid(uid_data['uid'])
    
    if new_status != current_status:
        # Update database
        conn = get_db()
        c = conn.cursor()
        
        try:
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
                await send_notification(
                    uid_data['telegram_id'],
                    dict(uid_details)
                )
                
                print(f"UID {uid_data['uid']} changed: DIE → LIVE")
            
            conn.commit()
            
        except Exception as e:
            print(f"Update UID error: {e}")
        finally:
            conn.close()

async def send_notification(telegram_id: int, uid_data: Dict):
    """Send DONE notification"""
    try:
        bot = Bot(token=BOT_TOKEN)
        
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
            chat_id=telegram_id,
            text=message,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        print(f"Send notification error: {e}")

# ========== MAIN APPLICATION ==========
async def main():
    """Hàm khởi chạy chính - Đã sửa lỗi JobQueue và Event Loop cho Railway"""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=getattr(logging, LOG_LEVEL)
    )
    
    init_database()
    
    # 1. Khởi tạo application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 2. Đăng ký các Handler (Giữ nguyên các handler bạn đã có)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("stats", stats_command))
    # ... thêm các handler khác của bạn tại đây ...

    # 3. Khởi chạy bot bằng phương thức 'async with' để quản lý vòng lặp an toàn
    async with application:
        await application.initialize()
        await application.start()
        
        # Thiết lập JobQueue nếu có hàm quét UID
        if application.job_queue:
            # Thay 'check_all_uids' bằng tên chính xác hàm quét của bạn
            application.job_queue.run_repeating(
                check_all_uids, 
                interval=CHECK_INTERVAL_MINUTES * 60, 
                first=10
            )
            logging.info("✅ JobQueue đã được thiết lập.")

        logging.info("🚀 Bot đang chạy và lắng nghe tin nhắn...")
        await application.updater.start_polling()
        
        # Giữ bot chạy liên tục
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            logging.info("👋 Đang dừng bot...")
        finally:
            # Dọn dẹp tài nguyên đúng cách
            await application.updater.stop()
            await application.stop()
            await application.shutdown()

if __name__ == "__main__":
    # Giải quyết triệt để lỗi 'This event loop is already running'
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nếu loop đang chạy (trên Server), tạo task mới
            loop.create_task(main())
        else:
            # Nếu loop chưa chạy, chạy cho đến khi hoàn thành
            loop.run_until_complete(main())
    except RuntimeError:
        # Nếu chưa có loop nào, khởi tạo mới hoàn toàn
        asyncio.run(main())
