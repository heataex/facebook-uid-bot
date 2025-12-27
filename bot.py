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
import asyncio
import time
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, Defaults
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TimedOut, Conflict

# ==================== 1. WEB SERVER ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "SYSTEM_ONLINE", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    # Tắt log rác của Flask
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. CONFIG & DB ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 # <--- THAY ID CỦA BẠN TẠI ĐÂY
DB_FILE = "fb_pro_v17.db"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=30) as conn:
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

# ==================== 3. GIAO DIỆN (UI/UX) ====================
class UI:
    HEAD = "<b>┏━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOT = "\n<b>┗━━━━━━━━━━━━━━━━━━━┛</b>"
    L = "🟢 LIVE"
    D = "🔴 DIE"

def get_menu(user_id):
    if user_id == ADMIN_ID:
        return ReplyKeyboardMarkup([
            [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
            [KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")],
            [KeyboardButton("📖 Hướng Dẫn")]
        ], resize_keyboard=True)
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
        [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
        [KeyboardButton("📖 Hướng Dẫn")]
    ], resize_keyboard=True)

# ==================== 4. LOGIC NGHIỆP VỤ ====================
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

# ==================== 5. HANDLERS LỆNH ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    access = "Vĩnh Viễn" if user_id == ADMIN_ID else ("Đã kích hoạt" if has_access(user_id) else "Chưa kích hoạt")
    msg = (
        f"{UI.HEAD}✨ <b>FB MONITOR V17 PRO</b>\n{UI.DIV}"
        f"👤 ID: <code>{user_id}</code>\n"
        f"🔰 Quyền: <b>{access}</b>\n"
        f"🛰 System: <code>Online 24/7</code>\n{UI.FOOT}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"<b>📖 HƯỚNG DẪN SỬ DỤNG</b>\n{UI.DIV}"
        "1️⃣ <b>Kích hoạt:</b> <code>/active PRO-XXXX</code>\n\n"
        "2️⃣ <b>Thêm kèo:</b> <code>/add_uid UID Tên Tiền</code>\n"
        "<i>(VD: /add_uid 1000123 An_Keo 500000)</i>\n\n"
        "3️⃣ <b>Xóa kèo:</b> <code>/remove_uid UID</code>\n\n"
        "🔹 <i>Hệ thống tự quét 60s/lần và báo ngay khi có thay đổi!</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- THÊM KÈO ---
async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id):
        return await update.message.reply_text("❌ <b>Truy cập bị chặn!</b>\nBạn cần mua Key để sử dụng.", parse_mode=ParseMode.HTML)
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, user_id, datetime.datetime.now()))
        icon = UI.L if status == "LIVE" else UI.D
        msg = f"✅ <b>THÀNH CÔNG</b>\n{UI.DIV}🆔 UID: <code>{uid}</code>\n👤 Khách: <b>{name}</b>\n📊 Status: {icon}{UI.FOOT}"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ <b>Sai cú pháp!</b>\n<code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)

# --- XỬ LÝ MENU ---
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    if not has_access(user_id): return
    
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Gõ: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    elif txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = f"{UI.HEAD}📋 <b>DANH SÁCH THEO DÕI</b>\n{UI.DIV}"
        if not rows: m += "<i>(Trống)</i>"
        for r in rows: m += f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}\n"
        await update.message.reply_text(m + UI.FOOT, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        res = db_query("SELECT SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 <b>Lúa về hôm nay:</b>\n<code>{res[0]['s'] or 0:,}đ</code>", parse_mode=ParseMode.HTML)
    elif txt == "📖 Hướng Dẫn": await help_cmd(update, context)
    elif txt == "🔑 Tạo Key" and user_id == ADMIN_ID:
        await update.message.reply_text("📌 Gõ: <code>/create_key SỐ_NGÀY</code>", parse_mode=ParseMode.HTML)

# ==================== 6. ADMIN & MONITOR JOB ====================
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = "PRO-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db_query("INSERT INTO keys (license_key, days) VALUES (?, ?)", (key, days))
        await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code> ({days} ngày)", parse_mode=ParseMode.HTML)
    except: pass

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        if not has_access(row['added_by']): continue
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            db_query("UPDATE uids SET status=?, done_at=? WHERE id=?", (new_status, datetime.datetime.now() if new_status=="LIVE" else None, row['id']))
            icon = UI.L if new_status == "LIVE" else UI.D
            msg = (f"🔔 <b>THÔNG BÁO BIẾN ĐỘNG</b>\n{UI.DIV}🆔 <code>{row['uid']}</code>\n👤: {row['customer_name']}\n🔄: {row['status']} ➔ <b>{icon}</b>\n💰: {row['amount']:,}đ{UI.FOOT}")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.HTML)
            except: pass

# ==================== 7. RECOVERY RUNNER ====================
def main():
    init_db()
    # Tối ưu kết nối mạng
    defaults = Defaults(connect_timeout=20, read_timeout=20)
    app = Application.builder().token(TOKEN).defaults(defaults).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("active", lambda u,c: None)) # Cần viết thêm hàm active
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT STARTED V17 ---")
    
    while True:
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
        except (NetworkError, TimedOut, httpx.ReadError):
            time.sleep(5) # Nghỉ 5s rồi reconnect
            continue
        except Exception as e:
            logging.error(f"Lỗi chí mạng: {e}")
            time.sleep(5)
            continue

if __name__ == "__main__":
    main()
