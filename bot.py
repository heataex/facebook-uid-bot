#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import pandas as pd
import httpx
import os
import threading
from io import BytesIO
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER (HEALTH CHECK) ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. CẤU HÌNH BOT & DB ====================
TOKEN = "8388735235:AAHvdU9ClwMaCU3v4DNWVikd6VtkfpGWAUM"
ADMIN_ID = 5522878843 # Thay ID của bạn
DB_FILE = "fb_keo_pro_v8.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS uids 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, 
            status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.commit()

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. MENU NÚT BẤM (UX) ====================
def get_main_menu():
    keyboard = [
        [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
        [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
        [KeyboardButton("❓ Hướng Dẫn")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== 4. LOGIC CHECK FB ====================
async def check_fb(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== 5. HANDLERS (GIAO DIỆN MỚI) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🔥 *HỆ THỐNG FB MONITOR V8.0 PRO*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Chế độ: *Auto Monitoring 24/7*\n"
        "🚀 Chu kỳ quét: *60 giây/lần*\n\n"
        "👉 _Vui lòng chọn chức năng dưới Menu!_"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, update.effective_user.id, datetime.datetime.now()))
        
        icon = "🟢" if status == "LIVE" else "🔴"
        msg = (
            "✅ *THÊM KÈO THÀNH CÔNG*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 *UID:* `{uid}`\n"
            f"👤 *Khách:* {name}\n"
            f"💰 *Tiền:* {amount:,}đ\n"
            f"📊 *Trạng thái:* {icon} {status}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔎 Bot đã bắt đầu theo dõi biến động..."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("⚠️ *Sai cú pháp!* Ví dụ:\n`/add_uid 1000123 An_Nguyen 500000`", parse_mode=ParseMode.MARKDOWN)

# --- FIX: Hàm xóa UID bị thiếu dẫn đến lỗi ---
async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ Nhập UID cần xóa. Ví dụ: `/remove_uid 1000123`", parse_mode=ParseMode.MARKDOWN)
    
    uid_target = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid_target, update.effective_user.id))
    await update.message.reply_text(f"🗑 *Đã ngừng theo dõi UID:* `{uid_target}`", parse_mode=ParseMode.MARKDOWN)

async def list_uids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (update.effective_user.id,), fetch=True)
    if not rows: return await update.message.reply_text("📝 *Danh sách theo dõi trống!*", parse_mode=ParseMode.MARKDOWN)
    
    msg = "📋 *KÈO ĐANG THEO DÕI*\n━━━━━━━━━━━━━━━━━━━━\n"
    total = 0
    for r in rows:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} `{r['uid']}` \| *{r['customer_name']}* \| *{r['amount']:,}đ*\n"
        total += r['amount']
    msg += f"━━━━━━━━━━━━━━━━━━━━\n💰 *Tổng cộng:* `{total:,} VNĐ`"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (update.effective_user.id,), fetch=True)
    if not res: return await update.message.reply_text("📊 *Chưa có dữ liệu theo dõi.*", parse_mode=ParseMode.MARKDOWN)
    
    msg = "📊 *TỔNG KẾT TRẠNG THÁI*\n━━━━━━━━━━━━━━━━━━━━\n"
    for r in res:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} {r['status']}: *{r['c']} UID*\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (update.effective_user.id,), fetch=True)
    count, total = res[0]['c'], res[0]['s'] or 0
    msg = (
        "💰 *BÁO CÁO DOANH THU HÔM NAY*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Số kèo DONE: *{count}*\n"
        f"💸 Tiền thu về: `{total:,}đ`"
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
                "🔔 *CẬP NHẬT TRẠNG THÁI MỚI*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 *UID:* `{row['uid']}`\n"
                f"👤 *Khách:* {row['customer_name']}\n"
                f"🔄 *Biến động:* {row['status']} ➔ *{icon}*\n"
                f"💰 *Tiền kèo:* {row['amount']:,}đ\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.MARKDOWN)
            except: pass

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Nhập lệnh: `/add_uid <UID> <Tên> <Tiền>`\n_VD: /add_uid 1000123 An 500000_", parse_mode=ParseMode.MARKDOWN)
    elif text == "📋 Danh Sách": await list_uids(update, context)
    elif text == "📊 Trạng Thái": await status_cmd(update, context)
    elif text == "💰 Doanh Thu": await stats_today(update, context)
    elif text == "❓ Hướng Dẫn":
        await update.message.reply_text("📖 *HƯỚNG DẪN:*\n- `/add_uid`: Thêm kèo\n- `/remove_uid`: Xóa kèo\n- `/list`: Xem danh sách", parse_mode=ParseMode.MARKDOWN)

# ==================== 7. KHỞI CHẠY BOT ====================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    
    # Job
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT STARTED V8.0 ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
