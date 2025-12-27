#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import pandas as pd
import httpx
import os
import threading
import secrets
import string
from io import BytesIO
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER (HEALTH CHECK) ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Chạy Flask daemon để không chặn Bot
threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. CẤU HÌNH HỆ THỐNG ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 # <--- THAY ID CỦA BẠN
DB_FILE = "fb_keo_final_v9.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS uids 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, 
            status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS api_keys 
            (key TEXT PRIMARY KEY, expire_at TIMESTAMP, owner_id INTEGER, status TEXT DEFAULT 'ACTIVE')''')
        conn.commit()

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. GIAO DIỆN (UI/UX) ====================
def get_main_menu():
    keyboard = [
        [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
        [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
        [KeyboardButton("🗑 Xóa UID"), KeyboardButton("📖 Hướng Dẫn")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

class UI:
    HEADER = "┏━━━━━━━━━━━━━━━━━━━┓\n"
    FOOTER = "\n┗━━━━━━━━━━━━━━━━━━━┛"
    DIVIDER = "┣━━━━━━━━━━━━━━━━━━━┫\n"

# ==================== 4. LOGIC CHECK FACEBOOK ====================
async def check_fb(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== 5. HANDLERS LỆNH ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"{UI.HEADER}"
        "✨ **FB MONITOR PRO V9.0**\n"
        f"{UI.DIVIDER}"
        "🔹 Trạng thái: `Hoạt động 24/7`\n"
        "🔹 Chu kỳ quét: `60 giây/lần`\n\n"
        "💡 _Hệ thống tự động thông báo khi có thay đổi trạng thái UID!_"
        f"{UI.FOOTER}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 3:
            return await update.message.reply_text("⚠️ **Cú pháp đúng:**\n`/add_uid <UID> <Tên> <Số_Tiền>`", parse_mode=ParseMode.MARKDOWN)
        
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        current = await check_fb(uid)
        
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, current, update.effective_user.id, datetime.datetime.now()))
        
        icon = "🟢" if current == "LIVE" else "🔴"
        msg = (
            f"{UI.HEADER}"
            "✅ **THÊM KÈO THÀNH CÔNG**\n"
            f"{UI.DIVIDER}"
            f"🆔 UID: `{uid}`\n"
            f"👤 Khách: *{name}*\n"
            f"💰 Tiền: *{amount:,}đ*\n"
            f"📊 Gốc: {icon} *{current}*"
            f"{UI.FOOTER}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("❌ Lỗi dữ liệu! Tiền kèo phải là số.")

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ **Dùng:** `/remove_uid <UID>`", parse_mode=ParseMode.MARKDOWN)
    
    uid_target = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid_target, update.effective_user.id))
    await update.message.reply_text(f"🗑 **Đã xóa:** `{uid_target}` khỏi danh sách theo dõi.", parse_mode=ParseMode.MARKDOWN)

async def list_uids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (update.effective_user.id,), fetch=True)
    if not rows: return await update.message.reply_text("📋 **Danh sách trống!**", parse_mode=ParseMode.MARKDOWN)
    
    msg = f"{UI.HEADER}📋 **DANH SÁCH THEO DÕI**\n{UI.DIVIDER}"
    total = 0
    for r in rows:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} `{r['uid']}` | {r['customer_name']} | *{r['amount']:,}đ*\n"
        total += r['amount']
    msg += f"{UI.DIVIDER}💰 **TỔNG TIỀN:** `{total:,}đ`{UI.FOOTER}"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (update.effective_user.id,), fetch=True)
    if not res: return await update.message.reply_text("📊 **Chưa có dữ liệu.**", parse_mode=ParseMode.MARKDOWN)
    
    msg = f"{UI.HEADER}📊 **BÁO CÁO TRẠNG THÁI**\n{UI.DIVIDER}"
    for r in res:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} {r['status']}: *{r['c']} UID*\n"
    msg += f"{UI.FOOTER}"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def stats_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Thống kê hôm nay
    today = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (user_id,), fetch=True)
    # Thống kê tháng
    month = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND strftime('%m', done_at) = strftime('%m', 'now')", (user_id,), fetch=True)
    
    msg = (
        f"{UI.HEADER}💰 **BÁO CÁO DOANH THU**\n{UI.DIVIDER}"
        f"📅 **HÔM NAY:**\n✅ DONE: *{today[0]['c']}* | 💸 `{today[0]['s'] or 0:,}đ`\n\n"
        f"📅 **THÁNG NÀY:**\n✅ DONE: *{month[0]['c']}* | 💸 `{month[0]['s'] or 0:,}đ`"
        f"{UI.FOOTER}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ==================== 6. AUTO MONITOR JOB ====================
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            
            icon = "🟢 LIVE" if new_status == "LIVE" else "🔴 DIE"
            msg = (
                f"{UI.HEADER}🔔 **BIẾN ĐỘNG TRẠNG THÁI**\n{UI.DIVIDER}"
                f"🆔 UID: `{row['uid']}`\n"
                f"👤 Khách: *{row['customer_name']}*\n"
                f"🔄 Thay đổi: `{row['status']}` ➔ **{icon}**\n"
                f"💰 Tiền kèo: *{row['amount']:,}đ*"
                f"{UI.FOOTER}"
            )
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.MARKDOWN)
            except: pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Nhập lệnh: `/add_uid <UID> <Tên> <Tiền>`\n_Ví dụ: /add_uid 1000123 An 500000_", parse_mode=ParseMode.MARKDOWN)
    elif text == "📋 Danh Sách": await list_uids(update, context)
    elif text == "📊 Trạng Thái": await status_cmd(update, context)
    elif text == "💰 Doanh Thu": await stats_revenue(update, context)
    elif text == "🗑 Xóa UID":
        await update.message.reply_text("📌 Nhập lệnh: `/remove_uid <UID>`", parse_mode=ParseMode.MARKDOWN)
    elif text == "📖 Hướng Dẫn": await start(update, context)

# ==================== 7. CHƯƠNG TRÌNH CHÍNH ====================
def main():
    init_db()
    # Tăng thời gian timeout để tránh lỗi mạng VPS
    app = Application.builder().token(TOKEN).read_timeout(30).write_timeout(30).build()
    
    # Register Lệnh
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("list", list_uids))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats_today", stats_revenue))
    
    # Register Nút bấm
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Chạy quét tự động
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT ULTIMATE V9.0 IS RUNNING ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
