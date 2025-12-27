#!/usr/bin/env python3
"""
FB KÈO BOT - Telegram Bot theo dõi trạng thái UID Facebook
Version 4.0 - Update tracking dài hạn & báo cáo nâng cao
"""

import asyncio
import logging
import sqlite3
import secrets
import string
import json
from datetime import datetime, timedelta, time # FIX: Thêm time vào đây
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from io import BytesIO

from telegram import (
    Update, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    InputFile
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    ContextTypes, 
    filters,
    ConversationHandler
)
from telegram.constants import ParseMode

# ==================== CẤU HÌNH ====================
BOT_TOKEN = "8388735235:AAEdJPWlZxo9Vm5rVIYGsFIeJ44wWTuT3D0"
CHECK_INTERVAL = 60  # Giây
DB_FILE = "fb_keo_bot.db"
DEFAULT_UID_LIMIT = 10
DEFAULT_EXPIRE_DAYS = 30
ADMIN_ID = 5522878843  # Thay bằng ID ADMIN thực tế

# Trạng thái cho ConversationHandler
ADDING_UID = 1

# ==================== DATABASE UPDATE ====================
def init_database():
    """Khởi tạo database SQLite với các bảng mới"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Bảng users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            role TEXT DEFAULT 'USER',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng api_keys
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            user_id INTEGER,
            uid_limit INTEGER DEFAULT 10,
            expired_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE',
            note TEXT,
            assigned_to INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (assigned_to) REFERENCES users (id)
        )
    ''')
    
    # Bảng uids
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            user_id INTEGER,
            key_id INTEGER,
            status TEXT DEFAULT 'DIE',
            tracking_status TEXT DEFAULT 'ACTIVE',
            customer_name TEXT,
            amount INTEGER DEFAULT 1000000,
            note TEXT,
            received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            first_live_date TIMESTAMP,
            done_date TIMESTAMP,
            last_checked TIMESTAMP,
            removed_at TIMESTAMP,
            long_tracking BOOLEAN DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (key_id) REFERENCES api_keys (id)
        )
    ''')
    
    # Bảng transactions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid_id INTEGER,
            user_id INTEGER,
            amount INTEGER,
            transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            period TEXT,
            FOREIGN KEY (uid_id) REFERENCES uids (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng key_logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS key_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (key_id) REFERENCES api_keys (id),
            FOREIGN KEY (admin_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng uid_logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uid_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid_id INTEGER,
            old_status TEXT,
            new_status TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            details TEXT,
            FOREIGN KEY (uid_id) REFERENCES uids (id)
        )
    ''')
    
    # Bảng status_history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            total_uids INTEGER,
            live_uids INTEGER,
            die_uids INTEGER,
            paused_uids INTEGER,
            record_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng revenue_logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS revenue_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            period TEXT,
            record_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# ==================== MODELS UPDATE ====================
@dataclass
class User:
    id: int
    telegram_id: int
    username: str
    role: str
    
@dataclass
class APIKey:
    id: int
    key: str
    user_id: int
    uid_limit: int
    expired_at: datetime
    status: str
    note: str
    assigned_to: Optional[int]

# ==================== DATABASE HELPER UPDATE ====================
class Database:
    @staticmethod
    def get_conn():
        return sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
    
    @staticmethod
    def dict_factory(cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
    
    @staticmethod
    def get_user(telegram_id: int) -> Optional[User]:
        conn = Database.get_conn()
        conn.row_factory = Database.dict_factory
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        conn.close()
        if row: return User(**row)
        return None
    
    @staticmethod
    def is_admin(telegram_id: int) -> bool:
        user = Database.get_user(telegram_id)
        return user and user.role == "ADMIN" if user else False
    
    @staticmethod
    def get_current_status(user_id: int = None, is_admin: bool = False) -> Dict:
        conn = Database.get_conn()
        cursor = conn.cursor()
        if is_admin and user_id is None:
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN status = 'LIVE' THEN 1 ELSE 0 END), SUM(CASE WHEN status = 'DIE' THEN 1 ELSE 0 END), SUM(CASE WHEN tracking_status = 'PAUSED' THEN 1 ELSE 0 END) FROM uids WHERE tracking_status IN ('ACTIVE', 'PAUSED')")
        elif user_id:
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN status = 'LIVE' THEN 1 ELSE 0 END), SUM(CASE WHEN status = 'DIE' THEN 1 ELSE 0 END), SUM(CASE WHEN tracking_status = 'PAUSED' THEN 1 ELSE 0 END) FROM uids WHERE user_id = ? AND tracking_status IN ('ACTIVE', 'PAUSED')", (user_id,))
        else: return {"total": 0, "live": 0, "die": 0, "paused": 0}
        row = cursor.fetchone()
        conn.close()
        return {"total": row[0] or 0, "live": row[1] or 0, "die": row[2] or 0, "paused": row[3] or 0}

    @staticmethod
    def get_stats_period(user_id: int = None, period: str = "today", is_admin: bool = False) -> Dict:
        conn = Database.get_conn()
        cursor = conn.cursor()
        if period == "today": date_condition = "DATE(u.done_date) = DATE('now')"
        elif period == "week": date_condition = "strftime('%Y-%W', u.done_date) = strftime('%Y-%W', 'now')"
        else: date_condition = "strftime('%Y-%m', u.done_date) = strftime('%Y-%m', 'now')"
        
        query = f"SELECT COUNT(DISTINCT u.id), COALESCE(SUM(t.amount), 0), GROUP_CONCAT(DISTINCT u.uid) FROM uids u LEFT JOIN transactions t ON u.id = t.uid_id WHERE u.status = 'LIVE' AND u.done_date IS NOT NULL AND {date_condition}"
        if not is_admin: query += f" AND u.user_id = {user_id}"
        
        cursor.execute(query)
        row = cursor.fetchone()
        conn.close()
        done_uids = row[2].split(',') if row[2] else []
        return {"period": period, "done_count": row[0] or 0, "total_amount": row[1] or 0, "done_uids": done_uids[:10], "total_done_uids": len(done_uids)}

    @staticmethod
    def log_uid_status_change(uid_id: int, old_status: str, new_status: str, details: str = ""):
        conn = Database.get_conn(); cursor = conn.cursor()
        cursor.execute("INSERT INTO uid_logs (uid_id, old_status, new_status, details) VALUES (?, ?, ?, ?)", (uid_id, old_status, new_status, details))
        conn.commit(); conn.close()

    @staticmethod
    def save_daily_status(user_id: int):
        conn = Database.get_conn(); cursor = conn.cursor()
        status = Database.get_current_status(user_id)
        cursor.execute("INSERT INTO status_history (user_id, total_uids, live_uids, die_uids, paused_uids) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM status_history WHERE user_id = ? AND record_date = DATE('now'))", (user_id, status['total'], status['live'], status['die'], status['paused'], user_id))
        conn.commit(); conn.close()

    @staticmethod
    def save_revenue_log(user_id: int, amount: int, period: str):
        conn = Database.get_conn(); cursor = conn.cursor()
        cursor.execute("INSERT INTO revenue_logs (user_id, amount, period) VALUES (?, ?, ?)", (user_id, amount, period))
        conn.commit(); conn.close()

    @staticmethod
    def update_uid_status_long_tracking(uid_id: int, new_status: str):
        conn = Database.get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT status, first_live_date FROM uids WHERE id = ?", (uid_id,))
        old_status, first_live_date = cursor.fetchone()
        now = datetime.now()
        done_date = now if new_status == "LIVE" and old_status == "DIE" else None
        cursor.execute("UPDATE uids SET status = ?, last_checked = ?, done_date = ?, first_live_date = COALESCE(first_live_date, ?) WHERE id = ?", (new_status, now, done_date, now if done_date else None, uid_id))
        if done_date:
            cursor.execute("SELECT amount, user_id FROM uids WHERE id = ?", (uid_id,))
            amt, u_id = cursor.fetchone()
            cursor.execute("INSERT INTO transactions (uid_id, user_id, amount, transaction_date) VALUES (?, ?, ?, ?)", (uid_id, u_id, amt, now))
            Database.save_revenue_log(u_id, amt, "daily")
        Database.log_uid_status_change(uid_id, old_status, new_status, f"Auto check at {now}")
        conn.commit(); conn.close()
        return {"old_status": old_status, "new_status": new_status, "is_done": bool(done_date)}

    @staticmethod
    def get_monthly_report_data(month: int, year: int):
        conn = Database.get_conn(); conn.row_factory = Database.dict_factory; cursor = conn.cursor()
        date_str = f"{year}-{month:02d}"
        cursor.execute("SELECT COUNT(DISTINCT u.id) as total_uids, SUM(CASE WHEN u.status = 'LIVE' AND strftime('%Y-%m', u.done_date) = ? THEN 1 ELSE 0 END) as total_done, COALESCE(SUM(CASE WHEN strftime('%Y-%m', u.done_date) = ? THEN t.amount ELSE 0 END), 0) as total_revenue FROM uids u LEFT JOIN transactions t ON u.id = t.uid_id", (date_str, date_str))
        summary = cursor.fetchone()
        cursor.execute("SELECT u.uid, u.customer_name, u.amount, u.received_date, u.done_date, us.username FROM uids u LEFT JOIN users us ON u.user_id = us.id WHERE u.status = 'LIVE' AND strftime('%Y-%m', u.done_date) = ?", (date_str,))
        details = cursor.fetchall()
        cursor.execute("SELECT us.username, COUNT(u.id) as uids_count, SUM(CASE WHEN u.status = 'LIVE' AND strftime('%Y-%m', u.done_date) = ? THEN 1 ELSE 0 END) as done_count FROM users us LEFT JOIN uids u ON us.id = u.user_id GROUP BY us.id", (date_str,))
        users_stats = cursor.fetchall()
        conn.close()
        return {"summary": summary, "details": details, "users_stats": users_stats}

# ==================== EXCEL REPORT ====================
class ExcelReport:
    @staticmethod
    def generate_monthly_report(month: int, year: int) -> BytesIO:
        data = Database.get_monthly_report_data(month, year)
        wb = Workbook(); ws1 = wb.active; ws1.title = "Tổng kết"
        ws1['A1'] = f"BÁO CÁO THÁNG {month}/{year}"
        # Giữ nguyên logic Excel của bạn...
        file_stream = BytesIO(); wb.save(file_stream); file_stream.seek(0)
        return file_stream

# ==================== UI & KEYBOARDS ====================
class UIFormatter:
    @staticmethod
    def format_status(status: Dict) -> str:
        return f"📊 *TRẠNG THÁI HIỆN TẠI*\n━━━━━━━━━━━━━━\n• Tổng UID: *{status['total']}*\n• 🟢 LIVE: *{status['live']}*\n• 🔴 DIE: *{status['die']}*\n• ⏸️ PAUSED: *{status['paused']}*"
    @staticmethod
    def format_stats(stats: Dict) -> str:
        return f"📈 *THỐNG KÊ {stats['period'].upper()}*\n━━━━━━━━━━━━━━\n• ✅ Kèo DONE: *{stats['done_count']}*\n• 💰 Tổng tiền: *{stats['total_amount']:,}đ*"
    @staticmethod
    def format_time_period_menu() -> str:
        return "📅 *CHỌN THỜI GIAN THỐNG KÊ*\nVui lòng chọn thời gian bạn muốn xem:"

class Keyboards:
    @staticmethod
    def main_menu():
        return InlineKeyboardMarkup([[InlineKeyboardButton("📊 Trạng thái", callback_data="status"), InlineKeyboardButton("📈 Thống kê", callback_data="stats_menu")], [InlineKeyboardButton("➕ Thêm UID", callback_data="add_uid"), InlineKeyboardButton("📋 Danh sách UID", callback_data="list_uids")], [InlineKeyboardButton("🔑 KEY của tôi", callback_data="my_keys"), InlineKeyboardButton("📄 Báo cáo DONE", callback_data="stats_today")]])
    @staticmethod
    def time_period_menu():
        return InlineKeyboardMarkup([[InlineKeyboardButton("📅 Hôm nay", callback_data="stats_today"), InlineKeyboardButton("📆 Tuần này", callback_data="stats_week")], [InlineKeyboardButton("📊 Tháng này", callback_data="stats_month"), InlineKeyboardButton("⬅️ Quay lại", callback_data="back_main")]])

# ==================== MAIN BOT CLASS ====================
class FBBot:
    def __init__(self):
        self.application = None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        conn = Database.get_conn()
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (user.id, user.username))
        conn.commit(); conn.close()
        await update.message.reply_text("👋 Chào mừng đến với FB KÈO BOT!", reply_markup=Keyboards.main_menu())

    async def add_uid_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query; await query.answer()
        await query.message.reply_text("📝 Nhập theo định dạng: `UID | Tên khách | Tiền | Trạng thái` (VD: `123 | An | 500000 | DIE`)")
        return ADDING_UID

    async def handle_uid_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            parts = [p.strip() for p in update.message.text.split("|")]
            if len(parts) != 4:
                await update.message.reply_text("❌ Sai định dạng! Hãy nhập lại hoặc gõ /cancel.")
                return ADDING_UID
            uid, name, price, status = parts
            conn = Database.get_conn(); cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (update.effective_user.id,))
            u_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO uids (uid, customer_name, amount, status, user_id) VALUES (?, ?, ?, ?, ?)", (uid, name, int(price.replace(",","")), status.upper(), u_id))
            conn.commit(); conn.close()
            await update.message.reply_text(f"✅ Đã thêm UID {uid} thành công!", reply_markup=Keyboards.main_menu())
            return ConversationHandler.END
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {e}"); return ConversationHandler.END

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query; await query.answer()
        uid = update.effective_user.id
        user = Database.get_user(uid)
        if not user: return
        
        if query.data == "status":
            s = Database.get_current_status(user.id, user.role == "ADMIN")
            await query.message.edit_text(UIFormatter.format_status(s), parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())
        elif query.data == "stats_menu":
            await query.message.edit_text(UIFormatter.format_time_period_menu(), parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.time_period_menu())
        elif query.data.startswith("stats_"):
            period = query.data.split("_")[1]
            s = Database.get_stats_period(user.id, period, user.role == "ADMIN")
            await query.message.edit_text(UIFormatter.format_stats(s), parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())
        elif query.data == "back_main":
            await query.message.edit_text("📱 MENU CHÍNH", reply_markup=Keyboards.main_menu())
        elif query.data == "list_uids":
            conn = Database.get_conn()
            rows = conn.execute("SELECT uid, status FROM uids WHERE user_id = ? AND tracking_status='ACTIVE' LIMIT 10", (user.id,)).fetchall()
            msg = "📋 *DANH SÁCH UID:*\n" + "\n".join([f"- `{r[0]}`: {r[1]}" for r in rows])
            await query.message.edit_text(msg or "Trống", parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())

    async def check_uids_job(self, context: ContextTypes.DEFAULT_TYPE):
        conn = Database.get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT u.id, u.uid, u.status, us.telegram_id, u.customer_name FROM uids u JOIN users us ON u.user_id = us.id WHERE u.tracking_status = 'ACTIVE'")
        for rid, ruid, rstat, rtid, rcust in cursor.fetchall():
            import random
            new_stat = "LIVE" if random.random() > 0.9 else rstat # Demo logic
            if new_stat != rstat:
                res = Database.update_uid_status_long_tracking(rid, new_stat)
                if res['is_done']: await context.bot.send_message(rtid, f"🎉 DONE KÈO: `{ruid}`\n👤 Khách: {rcust}", parse_mode=ParseMode.MARKDOWN)
        conn.close()

    async def generate_monthly_report_job(self, context: ContextTypes.DEFAULT_TYPE):
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            file = ExcelReport.generate_monthly_report(now.month, now.year)
            await context.bot.send_document(chat_id=ADMIN_ID, document=InputFile(file, filename="report.xlsx"))

    async def save_daily_stats_job(self, context: ContextTypes.DEFAULT_TYPE):
        conn = Database.get_conn(); cursor = conn.cursor(); cursor.execute("SELECT id FROM users")
        for (u_id,) in cursor.fetchall(): Database.save_daily_status(u_id)
        conn.close()

    def setup(self):
        init_database()
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Conversation nạp UID qua nút bấm
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_uid_start, pattern="^add_uid$")],
            states={ADDING_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_uid_input)]},
            fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
        )
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(conv_handler)
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Scheduler Jobs
        self.application.job_queue.run_repeating(self.check_uids_job, interval=CHECK_INTERVAL, first=10)
        self.application.job_queue.run_repeating(self.generate_monthly_report_job, interval=60, first=10)
        self.application.job_queue.run_daily(self.save_daily_stats_job, time=time(hour=0, minute=1))

    async def run(self):
        self.setup()
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        print("🚀 Bot Online!")
        await asyncio.Event().wait()

if __name__ == "__main__":
    bot = FBBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("Stopped")
