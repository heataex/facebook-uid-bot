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
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "SYSTEM_ONLINE", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CONFIG & DB ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN TẠI ĐÂY
DB_FILE = "fb_pro_v16.db"

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

# ==================== 3. UI/UX DESIGN (ADMIN vs USER) ====================
class UI:
    HEADER = "<b>┏━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOTER = "\n<b>┗━━━━━━━━━━━━━━━━━━━┛</b>"

def get_menu(user_id):
    if user_id == ADMIN_ID:
        return ReplyKeyboardMarkup([
            [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái Hệ Thống"), KeyboardButton("💰 Doanh Thu")],
            [KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")],
            [KeyboardButton("📖 Hướng Dẫn")]
        ], resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup([
            [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
            [KeyboardButton("📖 Hướng Dẫn")]
        ], resize_keyboard=True)

# ==================== 4. AUTH & CHECK ====================
async def check_fb(uid):
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=5) as client:
            r = await client.get(f"https://graph.facebook.com/{uid}/picture?type=normal")
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

# ==================== 5. HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    status_text = "Quản trị viên" if user_id == ADMIN_ID else ("Thành viên Premium" if has_access(user_id) else "Chưa kích hoạt")
    
    msg = (
        f"{UI.HEADER}"
        f"✨ <b>FB MONITOR PRO V16</b>\n"
        f"{UI.DIV}"
        f"👤 ID: <code>{user_id}</code>\n"
        f"🔰 Quyền hạn: <b>{status_text}</b>\n"
        f"🛰 Hệ thống: <b>Online 24/7</b>\n"
        f"{UI.FOOTER}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def handle_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid_id = update.effective_user.id

    # --- NHÓM LỆNH CHUNG ---
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Gõ lệnh theo mẫu:\n<code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    
    elif txt == "📖 Hướng Dẫn":
        msg = (
            f"<b>📖 HƯỚNG DẪN SỬ DỤNG</b>\n"
            f"{UI.DIV}"
            f"1️⃣ <b>Kích hoạt:</b> <code>/active KEY</code>\n"
            f"2️⃣ <b>Thêm kèo:</b> <code>/add_uid UID Tên Tiền</code>\n"
            f"3️⃣ <b>Xóa kèo:</b> <code>/remove_uid UID</code>\n"
            f"{UI.DIV}"
            f"💡 <i>Bot tự động báo khi UID chuyển trạng thái!</i>"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    # --- NHÓM LỆNH USER (CẦN ACCESS) ---
    elif txt in ["📋 Danh Sách", "📊 Trạng Thái", "💰 Doanh Thu"]:
        if not has_access(uid_id):
            return await update.message.reply_text("❌ <b>Truy cập bị chặn!</b>\nVui lòng kích hoạt Key.", parse_mode=ParseMode.HTML)
        
        if txt == "📋 Danh Sách":
            rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (uid_id,), fetch=True)
            m = f"{UI.HEADER}📋 <b>KÈO THEO DÕI</b>\n{UI.DIV}"
            if not rows: m += "<i>(Trống)</i>"
            for r in rows: m += f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}\n"
            await update.message.reply_text(m + UI.FOOTER, parse_mode=ParseMode.HTML)
            
        elif txt == "💰 Doanh Thu":
            r = db_query("SELECT SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (uid_id,), fetch=True)
            await update.message.reply_text(f"💰 <b>Lúa về hôm nay:</b>\n<code>{r[0]['s'] or 0:,}đ</code>", parse_mode=ParseMode.HTML)

    # --- NHÓM LỆNH ADMIN ---
    elif uid_id == ADMIN_ID:
        if txt == "🔑 Tạo Key":
            await update.message.reply_text("📌 Gõ lệnh: <code>/create_key SỐ_NGÀY</code>", parse_mode=ParseMode.HTML)
        elif txt == "📜 List Key":
            keys = db_query("SELECT * FROM keys ORDER BY status DESC LIMIT 10", fetch=True)
            m = f"{UI.HEADER}📜 <b>DANH SÁCH KEY</b>\n{UI.DIV}"
            for k in keys: m += f"🔑 <code>{k['license_key']}</code> ({k['days']}d) - {k['status']}\n"
            await update.message.reply_text(m + UI.FOOTER, parse_mode=ParseMode.HTML)

# ==================== 6. CÁC LỆNH SLASH (COMMANDS) ====================

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_access(update.effective_user.id): return
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, update.effective_user.id, datetime.datetime.now()))
        await update.message.reply_text(f"✅ <b>Đã thêm thành công!</b>\nUID: <code>{uid}</code>\nStatus: <b>{status}</b>", parse_mode=ParseMode.HTML)
    except: await update.message.reply_text("❌ Sai cú pháp!")

async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = "PRO-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db_query("INSERT INTO keys (license_key, days) VALUES (?, ?)", (key, days))
        await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code> ({days} ngày)", parse_mode=ParseMode.HTML)
    except: pass

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_input = context.args[0] if context.args else ""
    res = db_query("SELECT * FROM keys WHERE license_key = ? AND status = 'UNUSED'", (key_input,), fetch=True)
    if res:
        exp = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET used_by=?, expire_at=?, status='ACTIVE' WHERE license_key=?", (update.effective_user.id, exp, key_input))
        await update.message.reply_text(f"🎉 <b>Kích hoạt Premium thành công!</b>\nHết hạn: {exp}", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Key không hợp lệ!")

# ==================== 7. MONITOR JOB & MAIN ====================

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        if not has_access(row['added_by']): continue
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            db_query("UPDATE uids SET status=?, done_at=? WHERE id=?", (new_status, datetime.datetime.now() if new_status=="LIVE" else None, row['id']))
            msg = (f"🔔 <b>BIẾN ĐỘNG TRẠNG THÁI</b>\n{UI.DIV}🆔 <code>{row['uid']}</code>\n👤 Khách: {row['customer_name']}\n🔄: {row['status']} ➔ <b>{new_status}</b>\n💰: {row['amount']:,}đ{UI.FOOTER}")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.HTML)
            except: pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("active", active_key))
    
    # Xử lý nút bấm Menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_commands))
    
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
