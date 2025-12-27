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
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER (HEALTH CHECK) ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "TITANIUM_SYSTEM_ACTIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. CONFIG & DB ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 # <--- THAY ID CỦA BẠN VÀO ĐÂY
DB_FILE = "fb_pro_v19.db"

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

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

# ==================== 3. UI/UX DESIGN SYSTEM ====================
class UI:
    HEADER = "<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOTER = "\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>"
    L = "🟢 LIVE"
    D = "🔴 DIE"

def get_menu(user_id):
    # Menu riêng biệt cho Admin và User
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

# ==================== 4. NGHIỆP VỤ & PHÂN QUYỀN ====================
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
    uid = update.effective_user.id
    access = "ADMIN" if uid == ADMIN_ID else ("PREMIUM" if has_access(uid) else "CHƯA KÍCH HOẠT")
    
    msg = (f"{UI.HEADER}✨ <b>FB MONITOR PRO V19</b>\n{UI.DIV}"
           f"👤 ID: <code>{uid}</code>\n"
           f"🔰 Gói: <b>{access}</b>\n"
           f"🛰 System: <code>Online 24/7</code>\n{UI.FOOT}")
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(uid))

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id):
        return await update.message.reply_text("❌ <b>BẠN CHƯA CÓ KEY!</b>\nVui lòng liên hệ Admin để mua quyền sử dụng.", parse_mode=ParseMode.HTML)
    
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        msg_wait = await update.message.reply_text(f"⏳ Đang check UID <code>{uid}</code>...", parse_mode=ParseMode.HTML)
        
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, user_id, datetime.datetime.now()))
        
        icon = UI.L if status == "LIVE" else UI.D
        res = (f"✅ <b>THÊM KÈO THÀNH CÔNG</b>\n{UI.DIV}"
               f"🆔 UID: <code>{uid}</code>\n👤 Khách: <b>{name}</b>\n💰 Tiền: <code>{amount:,}đ</code>\n📊 Status: {icon}{UI.FOOT}")
        await msg_wait.edit_text(res, parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ <b>Sai cú pháp!</b>\nHD: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)

# ==================== 6. XỬ LÝ MENU & THỐNG KÊ ====================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id
    
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 <b>GỬI LỆNH THEO MẪU:</b>\n<code>/add_uid 1000123 An_Keo 500000</code>", parse_mode=ParseMode.HTML)
    
    elif txt == "📋 Danh Sách":
        if not has_access(uid): return
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (uid,), fetch=True)
        m = f"{UI.HEADER}📋 <b>DANH SÁCH THEO DÕI</b>\n{UI.DIV}"
        if not rows: m += "<i>(Trống)</i>"
        for r in rows:
            icon = UI.L if r['status'] == "LIVE" else UI.D
            m += f"{icon} <code>{r['uid']}</code> | {r['customer_name']}\n"
        await update.message.reply_text(m + UI.FOOT, parse_mode=ParseMode.HTML)

    elif txt == "💰 Doanh Thu":
        if not has_access(uid): return
        r = db_query("SELECT SUM(amount) as s, COUNT(*) as c FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (uid,), fetch=True)
        msg = f"💰 <b>KẾT QUẢ HÔM NAY</b>\n{UI.DIV}✅ DONE: <b>{r[0]['c']} kèo</b>\n💸 Thu về: <code>{r[0]['s'] or 0:,}đ</code>{UI.FOOT}"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif txt == "🔑 Tạo Key" and uid == ADMIN_ID:
        await update.message.reply_text("📌 Gõ lệnh: <code>/create_key SỐ_NGÀY</code>", parse_mode=ParseMode.HTML)

    elif txt == "📖 Hướng Dẫn":
        msg = (f"<b>📖 HƯỚNG DẪN SỬ DỤNG</b>\n{UI.DIV}"
               f"1. <b>Kích hoạt:</b> <code>/active PRO-XXXX</code>\n"
               f"2. <b>Thêm UID:</b> <code>/add_uid UID Tên Tiền</code>\n"
               f"3. <b>Xóa UID:</b> <code>/remove_uid UID</code>\n"
               f"{UI.DIV}💡 <i>Bot tự động báo khi có thay đổi trạng thái!</i>")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==================== 7. ADMIN KEY SYSTEM ====================
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = "PRO-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db_query("INSERT INTO keys (license_key, days) VALUES (?, ?)", (key, days))
        await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code>\n⏳ Hạn: <b>{days} ngày</b>", parse_mode=ParseMode.HTML)
    except: pass

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_input = context.args[0] if context.args else ""
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key_input,), fetch=True)
    if res:
        exp = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET used_by=?, expire_at=?, status='ACTIVE' WHERE license_key=?", (update.effective_user.id, exp, key_input))
        await update.message.reply_text(f"🎉 <b>KÍCH HOẠT THÀNH CÔNG!</b>\nHạn dùng: <code>{exp.strftime('%d/%m/%Y')}</code>", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ <b>Key sai hoặc đã sử dụng!</b>", parse_mode=ParseMode.HTML)

# ==================== 8. AUTO MONITOR JOB ====================
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        if not has_access(row['added_by']): continue
        new_s = await check_fb(row['uid'])
        if new_s != row['status']:
            db_query("UPDATE uids SET status=?, done_at=? WHERE id=?", (new_s, datetime.datetime.now() if new_s=="LIVE" else None, row['id']))
            icon = UI.L if new_s == "LIVE" else UI.D
            msg = (f"🔔 <b>THAY ĐỔI TRẠNG THÁI</b>\n{UI.DIV}"
                   f"🆔 UID: <code>{row['uid']}</code>\n👤 Khách: <b>{row['customer_name']}</b>\n"
                   f"🔄 Biến động: {row['status']} ➔ <b>{icon}</b>\n"
                   f"💰 Tiền kèo: <code>{row['amount']:,}đ</code>{UI.FOOT}")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.HTML)
            except: pass

# ==================== 9. MAIN RUNNER ====================
def main():
    init_db()
    # Loại bỏ Defaults lỗi, cấu hình trực tiếp trong builder
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- TITANIUM BOT STARTED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
