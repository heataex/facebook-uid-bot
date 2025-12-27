#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import pandas as pd
import httpx
from io import BytesIO
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ==================== CẤU HÌNH ====================
TOKEN = "8388735235:AAFw4kiurkE6AtrwAHxC4aG0uaJdAEyRHus"
ADMIN_ID = 5522878843  # <--- PHẢI THAY ID CỦA BẠN VÀO ĐÂY
DB_FILE = "fb_keo_bot_v5.db"

# ==================== DATABASE ====================
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

# ==================== LOGIC CHECK ====================
async def check_fb(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            # 302 là LIVE, còn lại (thường là 200 về ảnh lỗi) là DIE
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== JOB THEO DÕI TỰ ĐỘNG ====================
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        new_status = await check_fb(row['uid'])
        # Nếu trạng thái vừa check khác trạng thái lưu trong DB -> THÔNG BÁO
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            
            status_emoji = "🟢 LIVE" if new_status == "LIVE" else "🔴 DIE"
            msg = (f"🔔 **THAY ĐỔI TRẠNG THÁI**\n"
                   f"🆔 UID: `{row['uid']}`\n"
                   f"👤 Khách: {row['customer_name']}\n"
                   f"🔄 Trạng thái mới: **{status_emoji}**\n"
                   f"💰 Tiền kèo: {row['amount']:,}đ")
            
            try:
                await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Không thể gửi tin cho {row['added_by']}: {e}")

# ==================== HANDLERS (Từng lệnh một) ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot đã sẵn sàng!\nLệnh: /add_uid, /status, /remove_uid, /stats_today")

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        current = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, current, update.effective_user.id, datetime.datetime.now()))
        await update.message.reply_text(f"🚀 Đã thêm UID `{uid}`\nTrạng thái hiện tại: **{current}**")
    except:
        await update.message.reply_text("⚠️ Sai cú pháp! Dùng: `/add_uid <uid> <tên> <tiền>`")

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ Dùng: `/remove_uid <uid>`")
    uid_rm = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid_rm, update.effective_user.id))
    await update.message.reply_text(f"🗑 Đã dừng theo dõi UID: `{uid_rm}`")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (user_id,), fetch=True)
    if not res:
        return await update.message.reply_text("Hiện không theo dõi UID nào.")
    
    msg = "📊 **Trạng thái hiện tại:**\n"
    for r in res:
        msg += f"- {r['status']}: {r['c']} UID\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (user_id,), fetch=True)
    await update.message.reply_text(f"💰 **Hôm nay:**\n✅ DONE: {res[0]['c']} kèo\nTổng: {res[0]['s'] or 0:,}đ")

# ==================== MAIN RUNNER ====================
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký từng lệnh một cách tường minh
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats_today", stats_today))
    
    # Chạy quét tự động mỗi 60 giây
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT STARTED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
