#!/usr/bin/env python3
import asyncio
import logging
import sqlite3
import secrets
import string
import datetime
from datetime import datetime, timedelta
import pandas as pd
from io import BytesIO
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
import httpx # Cần thiết để check UID thật

# ==================== CẤU HÌNH ====================
BOT_TOKEN = "8388735235:AAG0jG2-gNmkaxUG7rcF4D3vqAfaoOiOjEk"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN VÀO ĐÂY
DB_FILE = "fb_keo_bot_v4.db"
CHECK_INTERVAL = 60 

# ==================== DATABASE LAYER ====================
def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Bảng users
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE,
        username TEXT, role TEXT DEFAULT 'USER', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Bảng api_keys
    cursor.execute('''CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, user_id INTEGER,
        expired_at TIMESTAMP, status TEXT DEFAULT 'ACTIVE')''')
    # Bảng uids (Tối ưu hóa theo yêu cầu long_tracking)
    cursor.execute('''CREATE TABLE IF NOT EXISTS uids (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, user_id INTEGER,
        status TEXT DEFAULT 'DIE', tracking_status TEXT DEFAULT 'ACTIVE',
        customer_name TEXT, amount INTEGER, received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        done_date TIMESTAMP, last_checked TIMESTAMP, long_tracking BOOLEAN DEFAULT 1)''')
    
    # Admin mặc định
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id, role) VALUES (?, 'ADMIN')", (ADMIN_ID,))
    conn.commit()
    conn.close()

class Database:
    @staticmethod
    def execute(query, params=(), fetchone=False, fetchall=False):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            conn.commit()
        except Exception as e:
            logging.error(f"DB Error: {e}")
        finally:
            conn.close()

# ==================== LOGIC CHECK THẬT ====================
async def check_fb_status_real(uid: str) -> str:
    """Check LIVE/DIE thật qua Facebook Graph"""
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except:
        return "DIE"

# ==================== UI & KEYBOARDS ====================
class Keyboards:
    @staticmethod
    def main_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Trạng thái", callback_data="status"),
             InlineKeyboardButton("📈 Thống kê", callback_data="stats_menu")],
            [InlineKeyboardButton("📄 Xuất Excel (Admin)", callback_data="export_admin")]
        ])

# ==================== BOT LOGIC ====================
class FBBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()

    # --- Handlers ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 *FB KÈO BOT V4.1* đã sẵn sàng.\nHệ thống theo dõi UID tự động 24/7.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=Keyboards.main_menu()
        )

    async def add_uid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            uid, name, amount = context.args[0], context.args[1], int(context.args[2])
            user_id = update.effective_user.id
            
            # Check trạng thái ban đầu
            status = await check_fb_status_real(uid)
            
            Database.execute(
                "INSERT INTO uids (uid, customer_name, amount, user_id, status) VALUES (?, ?, ?, ?, ?)",
                (uid, name, amount, user_id, status)
            )
            await update.message.reply_text(f"✅ Đã thêm UID `{uid}`\nTrạng thái gốc: *{status}*", parse_mode=ParseMode.MARKDOWN)
        except:
            await update.message.reply_text("⚠️ Cú pháp: `/add_uid <uid> <tên> <tiền>`")

    # --- Jobs (Scheduler) ---
    async def monitor_job(self, context: ContextTypes.DEFAULT_TYPE):
        uids = Database.execute("SELECT * FROM uids WHERE tracking_status = 'ACTIVE'", fetchall=True)
        for row in uids:
            new_status = await check_fb_status_real(row['uid'])
            
            if new_status != row['status']:
                Database.execute(
                    "UPDATE uids SET status = ?, last_checked = ?, done_date = ? WHERE id = ?",
                    (new_status, datetime.now(), datetime.now() if new_status == "LIVE" else None, row['id'])
                )
                
                # Thông báo cho User
                msg = (f"🔔 *THAY ĐỔI TRẠNG THÁI*\n🆔 UID: `{row['uid']}`\n👤 Khách: {row['customer_name']}\n"
                       f"🔄: {row['status']} ➔ *{new_status}*")
                if new_status == "LIVE":
                    msg += f"\n💰 Tiền kèo: *{row['amount']:,}đ*"
                
                try:
                    await context.bot.send_message(chat_id=row['user_id'], text=msg, parse_mode=ParseMode.MARKDOWN)
                except: pass

    # --- Excel Export ---
    async def export_excel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if update.effective_user.id != ADMIN_ID:
            return await query.answer("❌ Bạn không có quyền Admin.")
        
        await query.answer("⏳ Đang tạo báo cáo...")
        data = Database.execute("SELECT * FROM uids", fetchall=True)
        
        df = pd.DataFrame([dict(row) for row in data])
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ChiTiet')
        
        output.seek(0)
        await context.bot.send_document(
            chat_id=update.effective_user.id,
            document=output,
            filename=f"BaoCao_FB_{datetime.now().strftime('%Y%m%d')}.xlsx"
        )

    # --- Runner ---
    def run(self):
        init_database()
        
        # Add Handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("add_uid", self.add_uid))
        self.app.add_handler(CallbackQueryHandler(self.export_excel, pattern="export_admin"))
        
        # Job Queue
        self.app.job_queue.run_repeating(self.monitor_job, interval=CHECK_INTERVAL, first=10)
        
        print("🤖 Bot Version 4.1 is running...")
        self.app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot = FBBot()
    bot.run()
