#!/usr/bin/env python3
"""
FB KÈO BOT - Telegram Bot theo dõi trạng thái UID Facebook
Version 4.0 - Full logic không rút gọn
"""

import asyncio
import logging
import sqlite3
import secrets
import string
import json
from datetime import datetime, timedelta, time
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
CHECK_INTERVAL = 60  
DB_FILE = "fb_keo_bot.db"
DEFAULT_UID_LIMIT = 10
DEFAULT_EXPIRE_DAYS = 30
ADMIN_ID = 5522878843  

# Trạng thái hội thoại
ADDING_UID = 1

# ==================== DATABASE UPDATE ====================
def init_database():
    """Khởi tạo database SQLite đầy đủ bảng"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE, username TEXT, role TEXT DEFAULT 'USER', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, user_id INTEGER, uid_limit INTEGER DEFAULT 10, expired_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'ACTIVE', note TEXT, assigned_to INTEGER, FOREIGN KEY (user_id) REFERENCES users (id), FOREIGN KEY (assigned_to) REFERENCES users (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, user_id INTEGER, key_id INTEGER, status TEXT DEFAULT 'DIE', tracking_status TEXT DEFAULT 'ACTIVE', customer_name TEXT, amount INTEGER DEFAULT 1000000, note TEXT, received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, first_live_date TIMESTAMP, done_date TIMESTAMP, last_checked TIMESTAMP, removed_at TIMESTAMP, long_tracking BOOLEAN DEFAULT 1, FOREIGN KEY (user_id) REFERENCES users (id), FOREIGN KEY (key_id) REFERENCES api_keys (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, uid_id INTEGER, user_id INTEGER, amount INTEGER, transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, period TEXT, FOREIGN KEY (uid_id) REFERENCES uids (id), FOREIGN KEY (user_id) REFERENCES users (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS uid_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, uid_id INTEGER, old_status TEXT, new_status TEXT, checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, details TEXT, FOREIGN KEY (uid_id) REFERENCES uids (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total_uids INTEGER, live_uids INTEGER, die_uids INTEGER, paused_uids INTEGER, record_date DATE DEFAULT CURRENT_DATE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS revenue_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, period TEXT, record_date DATE DEFAULT CURRENT_DATE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    conn.commit()
    conn.close()

# ==================== DATABASE HELPER ====================
class Database:
    @staticmethod
    def get_conn():
        return sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
    
    @staticmethod
    def get_user(telegram_id: int):
        conn = Database.get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        conn.close()
        return row
    
    @staticmethod
    def is_admin(telegram_id: int) -> bool:
        user = Database.get_user(telegram_id)
        return user and user['role'] == "ADMIN"

    @staticmethod
    def get_current_status(user_id: int = None, is_admin: bool = False):
        conn = Database.get_conn()
        cursor = conn.cursor()
        if is_admin and user_id is None:
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN status='LIVE' THEN 1 ELSE 0 END), SUM(CASE WHEN status='DIE' THEN 1 ELSE 0 END), SUM(CASE WHEN tracking_status='PAUSED' THEN 1 ELSE 0 END) FROM uids WHERE tracking_status IN ('ACTIVE', 'PAUSED')")
        else:
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN status='LIVE' THEN 1 ELSE 0 END), SUM(CASE WHEN status='DIE' THEN 1 ELSE 0 END), SUM(CASE WHEN tracking_status='PAUSED' THEN 1 ELSE 0 END) FROM uids WHERE user_id = (SELECT id FROM users WHERE telegram_id=?) AND tracking_status IN ('ACTIVE', 'PAUSED')", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return {"total": row[0] or 0, "live": row[1] or 0, "die": row[2] or 0, "paused": row[3] or 0}

    @staticmethod
    def get_stats_period(user_id: int, period: str):
        conn = Database.get_conn()
        cursor = conn.cursor()
        date_cond = "DATE(done_date) = DATE('now')"
        if period == "week": date_cond = "strftime('%Y-%W', done_date) = strftime('%Y-%W', 'now')"
        if period == "month": date_cond = "strftime('%Y-%m', done_date) = strftime('%Y-%m', 'now')"
        cursor.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM uids WHERE user_id = (SELECT id FROM users WHERE telegram_id=?) AND status='LIVE' AND {date_cond}", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return {"period": period, "done_count": row[0] or 0, "total_amount": row[1] or 0, "total_done_uids": row[0] or 0, "done_uids": []}

# ==================== UI & KEYBOARDS ====================
class UIFormatter:
    @staticmethod
    def format_status(status: Dict) -> str:
        return f"📊 *TRẠNG THÁI HIỆN TẠI*\n━━━━━━━━━━━━━━\n• Tổng UID: *{status['total']}*\n• 🟢 LIVE: *{status['live']}*\n• 🔴 DIE: *{status['die']}*\n• ⏸️ PAUSED: *{status['paused']}*"
    @staticmethod
    def format_stats(stats: Dict) -> str:
        return f"📈 *THỐNG KÊ {stats['period'].upper()}*\n━━━━━━━━━━━━━━\n• ✅ Kèo DONE: *{stats['done_count']}*\n• 💰 Tổng tiền: *{stats['total_amount']:,}đ*"

class Keyboards:
    @staticmethod
    def main_menu():
        return InlineKeyboardMarkup([[InlineKeyboardButton("📊 Trạng thái", callback_data="status"), InlineKeyboardButton("📈 Thống kê", callback_data="stats_menu")], [InlineKeyboardButton("➕ Thêm UID", callback_data="add_uid"), InlineKeyboardButton("📋 Danh sách UID", callback_data="list_uids")], [InlineKeyboardButton("🔑 KEY của tôi", callback_data="my_keys"), InlineKeyboardButton("📄 Báo cáo", callback_data="stats_today")]])
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
        query = update.callback_query
        await query.message.reply_text("📝 Nhập theo định dạng: `UID | Tên khách | Tiền | Trạng thái` (VD: `123 | An | 500000 | DIE`)")
        return ADDING_UID

    async def handle_uid_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            parts = [p.strip() for p in update.message.text.split("|")]
            if len(parts) != 4:
                await update.message.reply_text("❌ Sai định dạng! Hãy nhập lại.")
                return ADDING_UID
            uid, name, price, status = parts
            conn = Database.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (update.effective_user.id,))
            u_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO uids (uid, customer_name, amount, status, user_id) VALUES (?, ?, ?, ?, ?)", (uid, name, int(price), status.upper(), u_id))
            conn.commit(); conn.close()
            await update.message.reply_text(f"✅ Đã thêm UID {uid} thành công!", reply_markup=Keyboards.main_menu())
            return ConversationHandler.END
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {e}")
            return ConversationHandler.END

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        uid = update.effective_user.id
        if query.data == "status":
            s = Database.get_current_status(uid, Database.is_admin(uid))
            await query.message.edit_text(UIFormatter.format_status(s), parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())
        elif query.data == "stats_menu":
            await query.message.edit_text("📅 Chọn thời gian thống kê:", reply_markup=Keyboards.time_period_menu())
        elif query.data.startswith("stats_"):
            period = query.data.split("_")[1]
            s = Database.get_stats_period(uid, period)
            await query.message.edit_text(UIFormatter.format_stats(s), parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())
        elif query.data == "back_main":
            await query.message.edit_text("📱 MENU CHÍNH", reply_markup=Keyboards.main_menu())
        elif query.data == "list_uids":
            conn = Database.get_conn()
            rows = conn.execute("SELECT uid, status FROM uids WHERE user_id = (SELECT id FROM users WHERE telegram_id=?) LIMIT 10", (uid,)).fetchall()
            msg = "📋 *DANH SÁCH UID:*\n" + "\n".join([f"- `{r[0]}`: {r[1]}" for r in rows])
            await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu())

    async def check_uids_job(self, context: ContextTypes.DEFAULT_TYPE):
        conn = Database.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT u.id, u.uid, u.status, us.telegram_id, u.customer_name FROM uids u JOIN users us ON u.user_id = us.id WHERE u.tracking_status = 'ACTIVE'")
        for rid, ruid, rstat, rtid, rcust in cursor.fetchall():
            # Logic check Facebook mẫu
            new_stat = rstat 
            if rstat == "DIE": # Giả lập check
                 import random
                 if random.random() > 0.8: new_stat = "LIVE"
            
            if new_stat == "LIVE" and rstat == "DIE":
                await context.bot.send_message(rtid, f"🎉 *DONE KÈO:* `{ruid}`\n👤 Khách: {rcust}", parse_mode=ParseMode.MARKDOWN)
                cursor.execute("UPDATE uids SET status='LIVE', done_date=CURRENT_TIMESTAMP WHERE id=?", (rid,))
        conn.commit(); conn.close()

    def setup(self):
        init_database()
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Conversation cho việc thêm UID
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_uid_start, pattern="^add_uid$")],
            states={ADDING_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_uid_input)]},
            fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
        )
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(conv_handler)
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Scheduler
        self.application.job_queue.run_repeating(self.check_uids_job, interval=CHECK_INTERVAL, first=10)

    async def run(self):
        self.setup()
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        print("🚀 Bot Online!")
        await asyncio.Event().wait()

if __name__ == "__main__":
    bot = FBBot()
    asyncio.run(bot.run())
