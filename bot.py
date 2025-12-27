#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import pandas as pd
import httpx
import os
import threading
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "OK", 200
def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)
threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. DATABASE & CONFIG ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 # Thay ID của bạn
DB_FILE = "fb_pro_v10.db"

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS uids 
        (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, 
        status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')

# ==================== 3. STYLE & UI ELEMENTS ====================
class Style:
    B = "━━━━━━━━━━━━━━"
    STAR = "✨"
    ID = "🆔"
    USER = "👤"
    MONEY = "💰"
    TIME = "⏰"
    SUCCESS = "✅"
    INFO = "ℹ️"

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ THÊM KÈO"), KeyboardButton("📋 DANH SÁCH")],
        [KeyboardButton("📊 TRẠNG THÁI"), KeyboardButton("💰 DOANH THU")],
        [KeyboardButton("🗑 XÓA UID"), KeyboardButton("📖 HƯỚNG DẪN")]
    ], resize_keyboard=True)

# ==================== 4. LOGIC CHECK FB ====================
async def check_fb_status(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=7) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== 5. HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"{Style.STAR} *FB MONITOR V10.0 ULTRA*\n"
        f"`{Style.B}`\n"
        f"Chào mừng bạn đến với Dashboard quản lý kèo chuyên nghiệp\.\n\n"
        f"{Style.INFO} *Trạng thái:* `Online 24/7`\n"
        f"{Style.TIME} *Chu kỳ:* `60s/lần`\n\n"
        f"👉 _Vui lòng sử dụng Menu bên dưới_"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu())

async def add_uid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 3:
        return await update.message.reply_text(
            f"❌ *SAI CÚ PHÁP*\n\n"
            f"Hãy nhập: `/add_uid <UID> <Tên> <Tiền>`\n"
            f"_Ví dụ: /add_uid 1000123 An 500000_", 
            parse_mode=ParseMode.MARKDOWN
        )
    
    uid, name, amount = context.args[0], context.args[1], context.args[2]
    wait_msg = await update.message.reply_text(f"⏳ *Đang kiểm tra UID:* `{uid}`...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        current_status = await check_fb_status(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, int(amount), current_status, update.effective_user.id, datetime.datetime.now()))
        
        status_icon = "🟢" if current_status == "LIVE" else "🔴"
        result_text = (
            f"{Style.SUCCESS} *THÊM KÈO THÀNH CÔNG*\n"
            f"`{Style.B}`\n"
            f"{Style.ID} *UID:* `{uid}`\n"
            f"{Style.USER} *Khách:* `{name}`\n"
            f"{Style.MONEY} *Số tiền:* `{int(amount):,}đ`\n"
            f"{Style.STAR} *Trạng thái:* {status_icon} `{current_status}`\n"
            f"`{Style.B}`\n"
            f"🔎 _Hệ thống đang theo dõi biến động\.\.\._"
        )
        await wait_msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await wait_msg.edit_text(f"❌ *Lỗi hệ thống:* `{str(e)}`", parse_mode=ParseMode.MARKDOWN)

async def remove_uid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ Nhập: `/remove_uid <UID>`")
    uid = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid, update.effective_user.id))
    await update.message.reply_text(f"🗑 *Đã dừng theo dõi UID:* `{uid}`", parse_mode=ParseMode.MARKDOWN)

async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (update.effective_user.id,), fetch=True)
    if not rows: return await update.message.reply_text("📋 *Danh sách trống\!*", parse_mode=ParseMode.MARKDOWN_V2)
    
    msg = f"📋 *DANH SÁCH KÈO CỦA BẠN*\n`{Style.B}`\n"
    total = 0
    for r in rows:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} `{r['uid']}` \| {r['customer_name']} \| *{r['amount']:,}đ*\n"
        total += r['amount']
    msg += f"`{Style.B}`\n💰 *Tổng tiền:* `{total:,}đ`"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "➕ THÊM KÈO":
        await update.message.reply_text("📌 *HƯỚNG DẪN THÊM:*\nCopy dòng dưới, sửa thông tin rồi gửi:\n\n`/add_uid UID Tên SốTiền`", parse_mode=ParseMode.MARKDOWN)
    elif text == "📋 DANH SÁCH": await list_handler(update, context)
    elif text == "📊 TRẠNG THÁI": 
        res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (update.effective_user.id,), fetch=True)
        msg = f"📊 *THỐNG KÊ TRẠNG THÁI*\n`{Style.B}`\n"
        for r in res: msg += f"● {r['status']}: *{r['c']} UID*\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    elif text == "💰 DOANH THU":
        today = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (update.effective_user.id,), fetch=True)
        msg = f"💰 *DOANH THU*\n`{Style.B}`\n✅ *Hôm nay:* `{today[0]['s'] or 0:,}đ`\n📊 *Tổng kèo:* `{today[0]['c']}`"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    elif text == "🗑 XÓA UID":
        await update.message.reply_text("🗑 *XÓA KÈO:*\nGõ theo mẫu: `/remove_uid <UID>`", parse_mode=ParseMode.MARKDOWN)
    elif text == "📖 HƯỚNG DẪN": await start(update, context)

# ==================== 6. AUTO MONITOR ====================
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        new_status = await check_fb_status(row['uid'])
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            icon = "🟢 LIVE" if new_status == "LIVE" else "🔴 DIE"
            msg = (f"{Style.STAR} *CẬP NHẬT BIẾN ĐỘNG*\n`{Style.B}`\n🆔 `{row['uid']}` \| `{row['customer_name']}`\n🔄: {row['status']} ➔ *{icon}*\n💰 Tiền: *{row['amount']:,}đ*")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.MARKDOWN_V2)
            except: pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid_handler))
    app.add_handler(CommandHandler("remove_uid", remove_uid_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
