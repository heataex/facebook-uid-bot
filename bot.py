"""
Facebook UID Tracker Bot - Premium Version
Với Dashboard, Report, UX/UI cải thiện
"""

import os
import logging
import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import calendar

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
from telegram.error import TelegramError

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_IDS = [int(x.strip()) for x in os.getenv("SUPER_ADMIN_IDS", "").split(",") if x.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ========== DATABASE ==========
DB_FILE = "/tmp/bot_data.db"

class StatusEnum(str, Enum):
    LIVE = "LIVE"
    DIE = "DIE"

class KeyStatusEnum(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    DISABLED = "DISABLED"

def init_database():
    """Khởi tạo database với các bảng mới"""
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
            is_active BOOLEAN DEFAULT 1,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng keys với notification status
    c.execute('''
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_value TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expired_at TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            notes TEXT,
            notify_3_days BOOLEAN DEFAULT 0,
            notify_1_day BOOLEAN DEFAULT 0,
            FOREIGN KEY (owner_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng uids với notes và tags
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
            owner_id INTEGER NOT NULL,
            key_id INTEGER NOT NULL,
            notes TEXT,
            tags TEXT DEFAULT '[]',
            is_active BOOLEAN DEFAULT 1,
            check_count INTEGER DEFAULT 0,
            is_vip BOOLEAN DEFAULT 0,
            FOREIGN KEY (owner_id) REFERENCES users (id),
            FOREIGN KEY (key_id) REFERENCES access_keys (id),
            UNIQUE(uid, key_id)
        )
    ''')
    
    # Bảng daily stats cho report nhanh
    c.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            total_uids INTEGER DEFAULT 0,
            live_uids INTEGER DEFAULT 0,
            die_uids INTEGER DEFAULT 0,
            done_today INTEGER DEFAULT 0,
            amount_today REAL DEFAULT 0,
            UNIQUE(date)
        )
    ''')
    
    # Indexes
    indexes = [
        'CREATE INDEX IF NOT EXISTS idx_uids_key ON facebook_uids(key_id)',
        'CREATE INDEX IF NOT EXISTS idx_uids_status ON facebook_uids(current_status)',
        'CREATE INDEX IF NOT EXISTS idx_keys_expired ON access_keys(expired_at)',
        'CREATE INDEX IF NOT EXISTS idx_uids_done_date ON facebook_uids(done_date)',
        'CREATE INDEX IF NOT EXISTS idx_uids_owner ON facebook_uids(owner_id)',
        'CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date)'
    ]
    
    for idx in indexes:
        c.execute(idx)
    
    conn.commit()
    conn.close()
    
    logging.info("Database initialized with premium features")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ========== UI FORMATTER ==========
class UIFormatter:
    """Class định dạng tin nhắn đẹp cho Telegram"""
    
    @staticmethod
    def separator(emoji: str = "━", length: int = 25) -> str:
        """Tạo separator line"""
        return emoji * length
    
    @staticmethod
    def format_done_message(uid_data: Dict) -> str:
        """Format message khi DONE kèo"""
        amount = uid_data['amount']
        amount_str = f"{amount:,.0f}đ".replace(",", ".")
        
        receive_date = datetime.fromisoformat(uid_data['receive_date']) if 'T' in uid_data['receive_date'] else datetime.strptime(uid_data['receive_date'], '%Y-%m-%d %H:%M:%S')
        done_date = uid_data.get('done_date')
        if done_date:
            done_date = datetime.fromisoformat(done_date) if 'T' in done_date else datetime.strptime(done_date, '%Y-%m-%d %H:%M:%S')
        else:
            done_date = datetime.now()
        
        return (
            f"{UIFormatter.separator('━', 30)}\n"
            f"✅ *DONE KÈO FACEBOOK*\n"
            f"{UIFormatter.separator('━', 30)}\n"
            f"👤 *Khách hàng:* {uid_data['customer_name']}\n"
            f"🆔 *UID:* `{uid_data['uid']}`\n"
            f"💰 *Số tiền:* {amount_str}\n\n"
            f"📅 *Ngày nhận:* {receive_date.strftime('%d/%m/%Y')}\n"
            f"🎉 *Ngày DONE:* {done_date.strftime('%d/%m/%Y %H:%M')}\n"
            f"{UIFormatter.separator('━', 30)}"
        )
    
    @staticmethod
    def format_stats_message(stats: Dict, user_telegram_id: str = None) -> str:
        """Format message thống kê"""
        today = datetime.now().strftime('%d/%m/%Y')
        
        # Status emojis
        live_emoji = "🟢"
        die_emoji = "🔴"
        done_emoji = "🎯"
        total_emoji = "📊"
        
        message = f"📈 *THỐNG KÊ HỆ THỐNG*\n{UIFormatter.separator('━', 25)}\n"
        
        if user_telegram_id:
            message += f"👤 *User:* {user_telegram_id}\n{UIFormatter.separator('━', 25)}\n"
        
        message += (
            f"{total_emoji} *Tổng UID:* {stats.get('total', 0)}\n"
            f"{live_emoji} *LIVE:* {stats.get('live', 0)}\n"
            f"{die_emoji} *DIE:* {stats.get('die', 0)}\n"
            f"{done_emoji} *DONE hôm nay:* {stats.get('done_today', 0)}\n"
            f"💰 *Tổng tiền hôm nay:* {stats.get('amount_today', 0):,.0f}đ\n"
            f"🔄 *Check count:* {stats.get('total_checks', 0)}\n"
            f"{UIFormatter.separator('━', 25)}\n"
            f"📅 *Ngày:* {today}"
        )
        
        return message
    
    @staticmethod
    def format_uid_list(uids: List[Dict]) -> str:
        """Format danh sách UID"""
        if not uids:
            return f"{UIFormatter.separator('━', 20)}\n📭 *Không có UID nào*\n{UIFormatter.separator('━', 20)}"
        
        message = f"📋 *DANH SÁCH UID*\n{UIFormatter.separator('━', 25)}\n"
        
        for uid in uids[:15]:  # Giới hạn 15 UID để không quá dài
            status_emoji = "🟢" if uid['current_status'] == 'LIVE' else "🔴"
            vip_emoji = "⭐" if uid.get('is_vip') else ""
            amount_str = f"{uid['amount']:,.0f}đ".replace(",", ".")
            
            message += f"{status_emoji}{vip_emoji} UID: `{uid['uid']}` | {amount_str}\n"
            
            # Thêm note nếu có
            if uid.get('notes'):
                message += f"   📝 {uid['notes'][:30]}...\n"
        
        if len(uids) > 15:
            message += f"\n... và {len(uids) - 15} UID khác"
        
        message += f"\n{UIFormatter.separator('━', 25)}"
        return message
    
    @staticmethod
    def format_key_info(key_data: Dict) -> str:
        """Format thông tin KEY"""
        expired_date = datetime.fromisoformat(key_data['expired_at']) if 'T' in key_data['expired_at'] else datetime.strptime(key_data['expired_at'], '%Y-%m-%d %H:%M:%S')
        days_left = (expired_date - datetime.now()).days
        
        status_emoji = "🟢" if key_data['status'] == 'ACTIVE' else "🔴"
        days_emoji = "⚠️" if days_left <= 3 else "⏳"
        
        message = (
            f"🔑 *THÔNG TIN KEY*\n"
            f"{UIFormatter.separator('━', 25)}\n"
            f"📝 *Key:* `{key_data['key_value']}`\n"
            f"{status_emoji} *Trạng thái:* {key_data['status']}\n"
            f"👤 *Owner:* @{key_data.get('username', 'N/A')}\n"
            f"📅 *Ngày tạo:* {key_data['created_at'][:10]}\n"
            f"{days_emoji} *Hết hạn:* {expired_date.strftime('%d/%m/%Y')}\n"
            f"⏰ *Còn lại:* {days_left} ngày\n"
        )
        
        # Stats cho key này
        if 'uid_count' in key_data:
            message += f"\n📊 *Thống kê KEY:*\n"
            message += f"   📈 Tổng UID: {key_data.get('uid_count', 0)}\n"
            message += f"   ✅ LIVE: {key_data.get('live_count', 0)}\n"
            message += f"   ❌ DIE: {key_data.get('die_count', 0)}\n"
        
        message += f"{UIFormatter.separator('━', 25)}"
        return message
    
    @staticmethod
    def format_daily_report(report_data: List[Dict]) -> str:
        """Format báo cáo ngày"""
        today = datetime.now().strftime('%d/%m/%Y')
        total_amount = sum(item['amount'] for item in report_data)
        
        message = (
            f"📊 *BÁO CÁO NGÀY {today}*\n"
            f"{UIFormatter.separator('━', 30)}\n"
        )
        
        if not report_data:
            message += f"📭 *Không có kèo nào DONE hôm nay*\n"
        else:
            for i, item in enumerate(report_data, 1):
                amount_str = f"{item['amount']:,.0f}đ".replace(",", ".")
                message += (
                    f"{i}. `{item['uid']}`\n"
                    f"   👤 {item['customer_name']}\n"
                    f"   💰 {amount_str}\n"
                    f"   ⏰ {item.get('done_time', 'N/A')}\n"
                )
            
            message += f"\n💰 *Tổng tiền:* {total_amount:,.0f}đ\n"
            message += f"🎯 *Tổng kèo:* {len(report_data)}\n"
        
        message += f"{UIFormatter.separator('━', 30)}"
        return message
    
    @staticmethod
    def format_key_expiration_notification(key_data: Dict, days_left: int) -> str:
        """Format thông báo KEY sắp hết hạn"""
        if days_left == 3:
            return (
                f"⏰ *THÔNG BÁO GIA HẠN KEY*\n"
                f"{UIFormatter.separator('━', 25)}\n"
                f"🔑 Key `{key_data['key_value']}` của bạn\n"
                f"⏳ *Còn {days_left} ngày* sẽ hết hạn\n\n"
                f"💡 *Lời khuyên:*\n"
                f"• Liên hệ admin để gia hạn\n"
                f"• Tránh gián đoạn dịch vụ\n"
                f"{UIFormatter.separator('━', 25)}"
            )
        else:  # 1 day
            return (
                f"🚨 *KEY SẮP HẾT HẠN*\n"
                f"{UIFormatter.separator('━', 25)}\n"
                f"🔑 Key `{key_data['key_value']}`\n"
                f"⏳ *Còn 1 ngày* sẽ hết hạn\n\n"
                f"⚠️ *Cảnh báo:*\n"
                f"• Dịch vụ sẽ tạm dừng\n"
                f"• UID sẽ ngừng check\n"
                f"• Liên hệ admin NGAY\n"
                f"{UIFormatter.separator('━', 25)}"
            )

# ========== STATS MANAGER ==========
class StatsManager:
    """Quản lý thống kê và báo cáo"""
    
    @staticmethod
    def get_user_stats(telegram_id: str) -> Dict:
        """Lấy thống kê của user"""
        conn = get_db()
        c = conn.cursor()
        
        # Get user ID
        c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        user = c.fetchone()
        
        if not user:
            conn.close()
            return {}
        
        user_id = user['id']
        today = datetime.now().date().isoformat()
        
        # Tổng UID của user
        c.execute(
            """SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live,
                SUM(CASE WHEN current_status = 'DIE' THEN 1 ELSE 0 END) as die
             FROM facebook_uids 
             WHERE owner_id = ? AND is_active = 1""",
            (user_id,)
        )
        basic_stats = dict(c.fetchone())
        
        # UID DONE hôm nay
        c.execute(
            """SELECT 
                COUNT(*) as done_today,
                COALESCE(SUM(amount), 0) as amount_today
             FROM facebook_uids 
             WHERE owner_id = ? 
             AND DATE(done_date) = DATE('now')
             AND current_status = 'LIVE'""",
            (user_id,)
        )
        today_stats = dict(c.fetchone())
        
        # Tổng số lần check
        c.execute(
            "SELECT COALESCE(SUM(check_count), 0) as total_checks FROM facebook_uids WHERE owner_id = ?",
            (user_id,)
        )
        checks = dict(c.fetchone())
        
        conn.close()
        
        return {
            **basic_stats,
            **today_stats,
            **checks
        }
    
    @staticmethod
    def get_system_stats() -> Dict:
        """Lấy thống kê toàn hệ thống (admin only)"""
        conn = get_db()
        c = conn.cursor()
        
        today = datetime.now().date().isoformat()
        
        # Total stats
        c.execute(
            """SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live,
                SUM(CASE WHEN current_status = 'DIE' THEN 1 ELSE 0 END) as die
             FROM facebook_uids WHERE is_active = 1"""
        )
        basic_stats = dict(c.fetchone())
        
        # Today's done stats
        c.execute(
            """SELECT 
                COUNT(*) as done_today,
                COALESCE(SUM(amount), 0) as amount_today
             FROM facebook_uids 
             WHERE DATE(done_date) = DATE('now')
             AND current_status = 'LIVE'"""
        )
        today_stats = dict(c.fetchone())
        
        # Key stats
        c.execute(
            """SELECT 
                COUNT(*) as total_keys,
                COUNT(CASE WHEN status = 'ACTIVE' AND expired_at > datetime('now') THEN 1 END) as active_keys,
                COUNT(CASE WHEN expired_at BETWEEN datetime('now') AND datetime('now', '+3 days') THEN 1 END) as expiring_keys
             FROM access_keys"""
        )
        key_stats = dict(c.fetchone())
        
        # User stats
        c.execute(
            """SELECT 
                COUNT(*) as total_users,
                COUNT(CASE WHEN role = 'ADMIN' THEN 1 END) as admin_users
             FROM users WHERE is_active = 1"""
        )
        user_stats = dict(c.fetchone())
        
        conn.close()
        
        return {
            **basic_stats,
            **today_stats,
            **key_stats,
            **user_stats
        }
    
    @staticmethod
    def get_key_stats(key_value: str = None) -> List[Dict]:
        """Lấy thống kê theo KEY (admin)"""
        conn = get_db()
        c = conn.cursor()
        
        if key_value:
            # Stats cho 1 key cụ thể
            c.execute(
                """SELECT 
                    k.key_value,
                    k.created_at,
                    k.expired_at,
                    k.status,
                    u.username,
                    COUNT(f.id) as uid_count,
                    SUM(CASE WHEN f.current_status = 'LIVE' THEN 1 ELSE 0 END) as live_count,
                    SUM(CASE WHEN f.current_status = 'DIE' THEN 1 ELSE 0 END) as die_count
                 FROM access_keys k
                 LEFT JOIN users u ON k.owner_id = u.id
                 LEFT JOIN facebook_uids f ON k.id = f.key_id
                 WHERE k.key_value = ?
                 GROUP BY k.id""",
                (key_value,)
            )
            result = c.fetchone()
            conn.close()
            return [dict(result)] if result else []
        else:
            # Stats cho tất cả keys
            c.execute(
                """SELECT 
                    k.key_value,
                    k.created_at,
                    k.expired_at,
                    k.status,
                    u.username,
                    u.telegram_id,
                    COUNT(f.id) as uid_count,
                    SUM(CASE WHEN f.current_status = 'LIVE' THEN 1 ELSE 0 END) as live_count,
                    (julianday(k.expired_at) - julianday('now')) as days_left
                 FROM access_keys k
                 LEFT JOIN users u ON k.owner_id = u.id
                 LEFT JOIN facebook_uids f ON k.id = f.key_id AND f.is_active = 1
                 GROUP BY k.id
                 ORDER BY k.expired_at"""
            )
            results = c.fetchall()
            conn.close()
            return [dict(row) for row in results]
    
    @staticmethod
    def get_daily_report(telegram_id: str = None) -> List[Dict]:
        """Lấy báo cáo UID DONE trong ngày"""
        conn = get_db()
        c = conn.cursor()
        
        today = datetime.now().date().isoformat()
        
        if telegram_id:
            # Report cho user cụ thể
            c.execute(
                """SELECT 
                    f.uid,
                    f.customer_name,
                    f.amount,
                    f.receive_date,
                    f.done_date,
                    TIME(f.done_date) as done_time
                 FROM facebook_uids f
                 JOIN users u ON f.owner_id = u.id
                 WHERE u.telegram_id = ?
                 AND DATE(f.done_date) = DATE('now')
                 AND f.current_status = 'LIVE'
                 ORDER BY f.done_date DESC""",
                (telegram_id,)
            )
        else:
            # Report toàn hệ thống (admin)
            c.execute(
                """SELECT 
                    f.uid,
                    f.customer_name,
                    f.amount,
                    f.receive_date,
                    f.done_date,
                    u.telegram_id,
                    TIME(f.done_date) as done_time
                 FROM facebook_uids f
                 JOIN users u ON f.owner_id = u.id
                 WHERE DATE(f.done_date) = DATE('now')
                 AND f.current_status = 'LIVE'
                 ORDER BY f.done_date DESC"""
            )
        
        results = c.fetchall()
        conn.close()
        return [dict(row) for row in results]
    
    @staticmethod
    def update_daily_stats():
        """Cập nhật daily stats (chạy hàng ngày)"""
        conn = get_db()
        c = conn.cursor()
        
        today = datetime.now().date().isoformat()
        
        # Xóa stats cũ nếu có
        c.execute("DELETE FROM daily_stats WHERE date = ?", (today,))
        
        # Tính stats mới
        c.execute(
            """INSERT INTO daily_stats (date, total_uids, live_uids, die_uids, done_today, amount_today)
             SELECT 
                DATE('now'),
                COUNT(*) as total_uids,
                SUM(CASE WHEN current_status = 'LIVE' THEN 1 ELSE 0 END) as live_uids,
                SUM(CASE WHEN current_status = 'DIE' THEN 1 ELSE 0 END) as die_uids,
                SUM(CASE WHEN DATE(done_date) = DATE('now') AND current_status = 'LIVE' THEN 1 ELSE 0 END) as done_today,
                SUM(CASE WHEN DATE(done_date) = DATE('now') AND current_status = 'LIVE' THEN amount ELSE 0 END) as amount_today
             FROM facebook_uids
             WHERE is_active = 1"""
        )
        
        conn.commit()
        conn.close()

# ========== KEY NOTIFICATION MANAGER ==========
class KeyNotificationManager:
    """Quản lý thông báo KEY sắp hết hạn"""
    
    @staticmethod
    def check_expiring_keys():
        """Kiểm tra KEY sắp hết hạn và gửi thông báo"""
        conn = get_db()
        c = conn.cursor()
        
        # KEY còn 3 ngày
        c.execute(
            """SELECT k.*, u.telegram_id 
             FROM access_keys k
             JOIN users u ON k.owner_id = u.id
             WHERE k.status = 'ACTIVE'
             AND k.expired_at BETWEEN datetime('now', '+3 days') AND datetime('now', '+3 days', '+1 hour')
             AND k.notify_3_days = 0"""
        )
        keys_3_days = c.fetchall()
        
        # KEY còn 1 ngày
        c.execute(
            """SELECT k.*, u.telegram_id 
             FROM access_keys k
             JOIN users u ON k.owner_id = u.id
             WHERE k.status = 'ACTIVE'
             AND k.expired_at BETWEEN datetime('now', '+1 day') AND datetime('now', '+1 day', '+1 hour')
             AND k.notify_1_day = 0"""
        )
        keys_1_day = c.fetchall()
        
        conn.close()
        
        return {
            '3_days': [dict(row) for row in keys_3_days],
            '1_day': [dict(row) for row in keys_1_day]
        }
    
    @staticmethod
    def mark_notification_sent(key_id: int, notification_type: str):
        """Đánh dấu đã gửi thông báo"""
        conn = get_db()
        c = conn.cursor()
        
        if notification_type == '3_days':
            c.execute(
                "UPDATE access_keys SET notify_3_days = 1 WHERE id = ?",
                (key_id,)
            )
        else:  # 1_day
            c.execute(
                "UPDATE access_keys SET notify_1_day = 1 WHERE id = ?",
                (key_id,)
            )
        
        conn.commit()
        conn.close()

# ========== KEYBOARD MANAGER ==========
class KeyboardManager:
    """Quản lý Inline Keyboard"""
    
    @staticmethod
    def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
        """Tạo main menu keyboard"""
        keyboard = [
            [
                InlineKeyboardButton("➕ Thêm UID", callback_data="add_uid"),
                InlineKeyboardButton("📋 Danh sách", callback_data="list_uids")
            ],
            [
                InlineKeyboardButton("📊 Thống kê", callback_data="stats"),
                InlineKeyboardButton("🔑 KEY của tôi", callback_data="my_key")
            ],
            [
                InlineKeyboardButton("📅 Báo cáo ngày", callback_data="daily_report"),
                InlineKeyboardButton("📝 Ghi chú UID", callback_data="add_note")
            ]
        ]
        
        if is_admin:
            keyboard.append([
                InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")
            ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def admin_panel() -> InlineKeyboardMarkup:
        """Tạo admin panel keyboard"""
        keyboard = [
            [
                InlineKeyboardButton("🗝️ Tạo KEY", callback_data="admin_create_key"),
                InlineKeyboardButton("📊 System Stats", callback_data="admin_system_stats")
            ],
            [
                InlineKeyboardButton("📋 Key Stats", callback_data="admin_key_stats"),
                InlineKeyboardButton("👥 User List", callback_data="admin_user_list")
            ],
            [
                InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def uid_actions_menu(uid_id: int) -> InlineKeyboardMarkup:
        """Menu actions cho UID"""
        keyboard = [
            [
                InlineKeyboardButton("📝 Thêm ghi chú", callback_data=f"note_{uid_id}"),
                InlineKeyboardButton("⭐ Đánh dấu VIP", callback_data=f"vip_{uid_id}")
            ],
            [
                InlineKeyboardButton("🛑 Tạm dừng", callback_data=f"pause_{uid_id}"),
                InlineKeyboardButton("🗑️ Xóa", callback_data=f"delete_{uid_id}")
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="back_to_list")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

# ========== BOT HANDLERS (UPDATED) ==========
class BotHandlers:
    """Class chứa tất cả handlers cho bot"""
    
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)
        self.scheduler = None
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message mới"""
        user = update.effective_user
        
        # Save user
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """INSERT OR IGNORE INTO users (telegram_id, username, first_name, last_active) 
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(telegram_id) DO UPDATE SET 
               username = excluded.username,
               last_active = CURRENT_TIMESTAMP""",
            (str(user.id), user.username, user.first_name)
        )
        conn.commit()
        conn.close()
        
        # Check if user is admin
        is_admin = int(user.id) in SUPER_ADMIN_IDS
        
        welcome_message = (
            "👋 *Chào mừng đến với FB KÈO BOT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔹 *Theo dõi UID LIVE / DIE tự động*\n"
            "🔹 *DONE kèo thông báo realtime*\n"
            "🔹 *Minh bạch – Ổn định – Dễ quản lý*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        if is_admin:
            welcome_message += "👑 *Bạn đang đăng nhập với quyền ADMIN*\n\n"
            welcome_message += "👉 Vui lòng kích hoạt KEY để bắt đầu sử dụng"
            keyboard = KeyboardManager.main_menu(is_admin=True)
        else:
            welcome_message += "👉 Vui lòng kích hoạt KEY để bắt đầu sử dụng"
            keyboard = KeyboardManager.main_menu(is_admin=False)
        
        await update.message.reply_text(
            welcome_message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị thống kê"""
        telegram_id = str(update.effective_user.id)
        is_admin = int(telegram_id) in SUPER_ADMIN_IDS
        
        if is_admin:
            stats = StatsManager.get_system_stats()
            message = UIFormatter.format_stats_message(stats)
        else:
            stats = StatsManager.get_user_stats(telegram_id)
            message = UIFormatter.format_stats_message(stats, telegram_id)
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=KeyboardManager.main_menu(is_admin)
        )
    
    async def daily_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Báo cáo UID DONE trong ngày"""
        telegram_id = str(update.effective_user.id)
        is_admin = int(telegram_id) in SUPER_ADMIN_IDS
        
        if is_admin:
            report_data = StatsManager.get_daily_report()
        else:
            report_data = StatsManager.get_daily_report(telegram_id)
        
        message = UIFormatter.format_daily_report(report_data)
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=KeyboardManager.main_menu(is_admin)
        )
    
    async def mykey_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị thông tin KEY của user"""
        telegram_id = str(update.effective_user.id)
        is_admin = int(telegram_id) in SUPER_ADMIN_IDS
        
        if is_admin:
            await update.message.reply_text(
                "👑 *Bạn là ADMIN*\nKhông cần KEY để sử dụng hệ thống.",
                parse_mode="Markdown"
            )
            return
        
        conn = get_db()
        c = conn.cursor()
        
        # Get user's active key
        c.execute(
            """SELECT k.*, u.username 
             FROM access_keys k
             JOIN users u ON k.owner_id = u.id
             WHERE u.telegram_id = ?
             AND k.status = 'ACTIVE'
             AND k.expired_at > datetime('now')
             ORDER BY k.expired_at DESC
             LIMIT 1""",
            (telegram_id,)
        )
        key_data = c.fetchone()
        conn.close()
        
        if not key_data:
            await update.message.reply_text(
                "❌ *Bạn chưa có KEY active!*\nLiên hệ admin để được cấp KEY.",
                parse_mode="Markdown"
            )
            return
        
        key_dict = dict(key_data)
        # Thêm stats cho key này
        key_stats = StatsManager.get_key_stats(key_dict['key_value'])
        if key_stats:
            key_dict.update(key_stats[0])
        
        message = UIFormatter.format_key_info(key_dict)
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=KeyboardManager.main_menu(is_admin)
        )
    
    async def admin_key_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin: Xem thống kê theo KEY"""
        telegram_id = str(update.effective_user.id)
        
        if int(telegram_id) not in SUPER_ADMIN_IDS:
            await update.message.reply_text("❌ Chỉ ADMIN mới có quyền này!")
            return
        
        all_key_stats = StatsManager.get_key_stats()
        
        if not all_key_stats:
            await update.message.reply_text("📭 Không có KEY nào trong hệ thống!")
            return
        
        # Group by status
        active_keys = [k for k in all_key_stats if k['status'] == 'ACTIVE' and float(k.get('days_left', 0)) > 0]
        expiring_keys = [k for k in active_keys if float(k.get('days_left', 0)) <= 3]
        expired_keys = [k for k in all_key_stats if k['status'] != 'ACTIVE' or float(k.get('days_left', 0)) <= 0]
        
        message = (
            "🗝️ *THỐNG KÊ KEY*\n"
            f"{UIFormatter.separator('━', 25)}\n"
            f"📊 *Tổng số KEY:* {len(all_key_stats)}\n"
            f"🟢 *Active:* {len(active_keys)}\n"
            f"⚠️ *Sắp hết hạn (≤3 ngày):* {len(expiring_keys)}\n"
            f"🔴 *Hết hạn/vô hiệu:* {len(expired_keys)}\n"
            f"{UIFormatter.separator('━', 25)}\n"
        )
        
        # Hiển thị KEY sắp hết hạn
        if expiring_keys:
            message += "*📌 KEY SẮP HẾT HẠN:*\n"
            for key in expiring_keys[:5]:  # Hiển thị 5 key đầu
                days_left = int(float(key.get('days_left', 0)))
                message += f"• `{key['key_value']}` - Còn {days_left} ngày\n"
        
        # Hiển thị KEY mới nhất
        if active_keys:
            message += f"\n*📌 KEY ACTIVE MỚI NHẤT:*\n"
            for key in active_keys[:3]:
                days_left = int(float(key.get('days_left', 0)))
                uid_count = key.get('uid_count', 0)
                message += (
                    f"• `{key['key_value']}`\n"
                    f"  👤 @{key.get('username', 'N/A')}\n"
                    f"  📊 {uid_count} UID | ⏳ {days_left} ngày\n"
                )
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=KeyboardManager.admin_panel()
        )
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Xử lý inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        is_admin = int(user_id) in SUPER_ADMIN_IDS
        
        callback_data = query.data
        
        if callback_data == "add_uid":
            await self.add_command(query.message, context)
        elif callback_data == "list_uids":
            await self.list_command(query.message, context)
        elif callback_data == "stats":
            await self.stats_command(query.message, context)
        elif callback_data == "my_key":
            await self.mykey_command(query.message, context)
        elif callback_data == "daily_report":
            await self.daily_report_command(query.message, context)
        elif callback_data == "admin_panel":
            if is_admin:
                await query.edit_message_text(
                    "👑 *ADMIN PANEL*",
                    parse_mode="Markdown",
                    reply_markup=KeyboardManager.admin_panel()
                )
            else:
                await query.edit_message_text("❌ Không có quyền truy cập!")
        elif callback_data == "admin_key_stats":
            await self.admin_key_stats_command(query.message, context)
        elif callback_data == "back_to_menu":
            await query.edit_message_text(
                "🏠 *Main Menu*",
                parse_mode="Markdown",
                reply_markup=KeyboardManager.main_menu(is_admin)
            )
        elif callback_data.startswith("note_"):
            uid_id = callback_data.split("_")[1]
            await query.message.reply_text(
                f"📝 Nhập ghi chú cho UID {uid_id}:\n"
                f"(Gửi tin nhắn với format: `{uid_id} | Nội dung ghi chú`)",
                parse_mode="Markdown"
            )
    
    # ... (các hàm khác giữ nguyên)

# ========== UPDATED NOTIFIER ==========
class Notifier:
    """Notifier với format mới"""
    
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)
        self.scheduler = None
    
    async def send_done_notification(self, chat_id: int, uid_data: Dict):
        """Gửi thông báo DONE với format mới"""
        try:
            message = UIFormatter.format_done_message(uid_data)
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            
            logging.info(f"✅ Sent DONE notification for UID: {uid_data['uid']}")
            
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")
    
    async def send_key_expiration_notifications(self):
        """Gửi thông báo KEY sắp hết hạn"""
        expiring_keys = KeyNotificationManager.check_expiring_keys()
        
        for key_data in expiring_keys['3_days']:
            try:
                message = UIFormatter.format_key_expiration_notification(key_data, 3)
                await self.bot.send_message(
                    chat_id=int(key_data['telegram_id']),
                    text=message,
                    parse_mode="Markdown"
                )
                KeyNotificationManager.mark_notification_sent(key_data['id'], '3_days')
                logging.info(f"Sent 3-day expiration notification for key: {key_data['key_value']}")
            except Exception as e:
                logging.error(f"Failed to send 3-day notification: {e}")
        
        for key_data in expiring_keys['1_day']:
            try:
                message = UIFormatter.format_key_expiration_notification(key_data, 1)
                await self.bot.send_message(
                    chat_id=int(key_data['telegram_id']),
                    text=message,
                    parse_mode="Markdown"
                )
                KeyNotificationManager.mark_notification_sent(key_data['id'], '1_day')
                logging.info(f"Sent 1-day expiration notification for key: {key_data['key_value']}")
            except Exception as e:
                logging.error(f"Failed to send 1-day notification: {e}")

# ========== MAIN APPLICATION ==========
def main():
    logging.info("Bot is starting with premium features...")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # add handlers ở đây
    # application.add_handler(...)

    # Scheduler mỗi 1 phút
    application.job_queue.run_repeating(
        scheduled_tasks,
        interval=60,
        first=5
    )

    application.run_polling()
    
    # Initialize database
    init_database()
    
    # Update daily stats
    StatsManager.update_daily_stats()
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Initialize handlers
    handlers = BotHandlers(BOT_TOKEN)
    
    # Add command handlers
    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("stats", handlers.stats_command))
    application.add_handler(CommandHandler("report_today", handlers.daily_report_command))
    application.add_handler(CommandHandler("mykey", handlers.mykey_command))
    application.add_handler(CommandHandler("key_stats", handlers.admin_key_stats_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(handlers.callback_handler))
    
    # Start scheduler
    notifier = Notifier(BOT_TOKEN)
    
    # Schedule tasks
    async def scheduled_tasks():
        """Run scheduled tasks"""
        while True:
            try:
                # Send key expiration notifications
                await notifier.send_key_expiration_notifications()
                
                # Update daily stats (once per day)
                if datetime.now().hour == 0:  # Midnight
                    StatsManager.update_daily_stats()
                
            except Exception as e:
                logging.error(f"Scheduled task error: {e}")
            
            # Wait 1 hour before next check
            await asyncio.sleep(3600)
    
    # Run bot
    logging.info("Bot is starting with premium features...")
    
    # Start scheduled tasks in background
    asyncio.create_task(scheduled_tasks())
    
    await application.run_polling()

if __name__ == "__main__":
    main()
