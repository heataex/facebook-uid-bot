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
import time
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, Defaults
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER (HEALTH CHECK) ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "Hệ thống đang chạy ổn định", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CONFIG & DATABASE ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843  # <--- THAY ID TELEGRAM CỦA BẠN VÀO ĐÂY
DB_FILE = "fb_business_v13.db"

class UI:
    B = "━━━━━━━━━━━━━━"
    STAR = "✨"
    ID = "🆔"
    USER = "👤"
    MONEY = "💰"
    EXP = "⏳"
    DONE = "✅"
    ERROR = "❌"

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS uids 
        (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, 
        status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
    db_query('''CREATE TABLE IF NOT EXISTS keys 
        (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')

# ==================== 3. AUTHENTICATION ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expiry = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expiry > datetime.datetime.now()

def main_menu(user_id):
    keyboard = [
        [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
        [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("🔑 Quản Lý Key"), KeyboardButton("📄 Xuất Excel")])
    keyboard.append([KeyboardButton("📖 Hướng Dẫn")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== 4. LOGIC CHECK FB ====================
async def check_fb(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=5) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== 5. HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    access = "Vĩnh viễn (Admin)" if user_id == ADMIN_ID else "Chưa kích hoạt"
    if not is_admin(user_id) and has_access(user_id):
        res = db_query("SELECT expire_at FROM keys WHERE used_by = ?", (user_id,), fetch=True)
        access = res[0]['expire_at'].split(".")[0]

    msg = (
        f"{UI.STAR} *FB MONITOR ULTIMATE V13*\n"
        f"`{UI.B}`\n"
        f"👤 *Tài khoản:* `{user_id}`\n"
        f"{UI.EXP} *Hạn dùng:* `{access}`\n"
        f"`{UI.B}`\n"
        f"👉 _Vui lòng chọn chức năng bên dưới menu\._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu(user_id))

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id):
        return await update.message.reply_text(f"{UI.ERROR} *Truy cập bị chặn\!\nBạn cần mua Key để sử dụng chức năng này\.*", parse_mode=ParseMode.MARKDOWN_V2)
    
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, user_id, datetime.datetime.now()))
        
        icon = "🟢" if status == "LIVE" else "🔴"
        msg = (
            f"{UI.DONE} *THÊM KÈO THÀNH CÔNG*\n"
            f"`{UI.B}`\n"
            f"{UI.ID} UID: `{uid}`\n"
            f"{UI.USER} Khách: *{name}*\n"
            f"{UI.MONEY} Giá: `{amount:,}đ`\n"
            f"📊 Status: {icon} `{status}`\n"
            f"`{UI.B}`"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text(f"{UI.ERROR} *Sai cú pháp\!\nSử dụng:* `/add_uid <UID> <Tên> <SốTiền>`", parse_mode=ParseMode.MARKDOWN_V2)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Nhập lệnh theo mẫu:\n`/add_uid 1000123 An_Nguyen 500000`", parse_mode=ParseMode.MARKDOWN)
    elif txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (user_id,), fetch=True)
        if not rows: return await update.message.reply_text("📋 Danh sách trống.")
        msg = "📋 *DANH SÁCH THEO DÕI*\n"
        for r in rows:
            icon = "🟢" if r['status'] == "LIVE" else "🔴"
            msg += f"{icon} `{r['uid']}` \| {r['customer_name']} \| *{r['amount']:,}đ*\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    elif txt == "💰 Doanh Thu":
        res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 *KẾT QUẢ HÔM NAY*\n`{UI.B}`\n✅ DONE: `{res[0]['c']}` kèo\n💵 Thu nhập: `{res[0]['s'] or 0:,}đ`", parse_mode=ParseMode.MARKDOWN_V2)
    elif txt == "📖 Hướng Dẫn":
        await start(update, context)
    elif txt == "🔑 Quản Lý Key" and user_id == ADMIN_ID:
        await update.message.reply_text("🛠 *LỆNH ADMIN:*\n- `/create_key <ngày>`: Tạo key mới\n- `/list_keys`: Xem các key đã cấp", parse_mode=ParseMode.MARKDOWN)

# ==================== 6. ADMIN - KEY MANAGEMENT ====================
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = "PRO-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        db_query("INSERT INTO keys (license_key, days) VALUES (?, ?)", (key, days))
        await update.message.reply_text(f"🎫 *KEY MỚI:* `{key}`\n⏳ Hạn dùng: `{days}` ngày")
    except: pass

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    key_input = context.args[0] if context.args else ""
    res = db_query("SELECT * FROM keys WHERE license_key = ? AND status = 'UNUSED'", (key_input,), fetch=True)
    if res:
        expire_at = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET used_by = ?, expire_at = ?, status = 'ACTIVE' WHERE license_key = ?", (user_id, expire_at, key_input))
        await update.message.reply_text(f"🎉 Kích hoạt thành công!\nHạn dùng: {expire_at}")
    else:
        await update.message.reply_text("❌ Key sai hoặc đã dùng!")

# ==================== 7. AUTO MONITORING ====================
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        # Check hạn dùng user trước khi notify
        if not has_access(row['added_by']): continue
        
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            icon = "🟢 LIVE" if new_status == "LIVE" else "🔴 DIE"
            msg = (
                f"🔔 *CẬP NHẬT TRẠNG THÁI*\n"
                f"`{UI.B}`\n"
                f"🆔 UID: `{row['uid']}`\n"
                f"👤 Khách: {row['customer_name']}\n"
                f"🔄: {row['status']} ➔ *{icon}*\n"
                f"💰 Tiền: *{row['amount']:,}đ*"
            )
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.MARKDOWN_V2)
            except: pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
