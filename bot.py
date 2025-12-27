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
def health(): return "DIAMOND_SYSTEM_ACTIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ==================== 2. CONFIG & DB ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN
DB_FILE = "fb_pro_v20.db"

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

# ==================== 3. UI/UX DESIGN ====================
class UI:
    HEADER = "<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOTER = "\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>"

def get_menu(user_id):
    btns = [[KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")]]
    if user_id == ADMIN_ID:
        btns.append([KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")])
    btns.append([KeyboardButton("📖 Hướng Dẫn")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# ==================== 4. PHÂN QUYỀN ====================
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

# ==================== 5. XỬ LÝ LỆNH SLASH (/) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    access = "ADMIN" if user_id == ADMIN_ID else ("PREMIUM" if has_access(user_id) else "CHƯA KÍCH HOẠT")
    msg = (f"{UI.HEADER}✨ <b>FB MONITOR PRO V20</b>\n{UI.DIV}"
           f"👤 ID: <code>{user_id}</code>\n🔰 Gói: <b>{access}</b>\n{UI.FOOT}")
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id):
        return await update.message.reply_text("❌ Bạn cần kích hoạt Key để dùng lệnh này.")
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, status, user_id, datetime.datetime.now()))
        icon = "🟢" if status == "LIVE" else "🔴"
        await update.message.reply_text(f"✅ <b>Thành công!</b>\nUID: <code>{uid}</code>\nStatus: {icon} {status}", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ <b>Sai cú pháp!</b>\nHD: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_access(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("⚠️ Gõ: <code>/remove_uid UID</code>", parse_mode=ParseMode.HTML)
    db_query("UPDATE uids SET is_active=0 WHERE uid=? AND added_by=?", (context.args[0], update.effective_user.id))
    await update.message.reply_text(f"🗑 Đã xóa UID: <code>{context.args[0]}</code>", parse_mode=ParseMode.HTML)

async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = "VIP-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db_query("INSERT INTO keys (license_key, days) VALUES (?, ?)", (key, days))
        await update.message.reply_text(f"🎫 <b>KEY:</b> <code>{key}</code> ({days} ngày)", parse_mode=ParseMode.HTML)
    except: pass

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ Gõ: <code>/active KEY</code>")
    key_input = context.args[0]
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key_input,), fetch=True)
    if res:
        exp = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET used_by=?, expire_at=?, status='ACTIVE' WHERE license_key=?", (update.effective_user.id, exp, key_input))
        await update.message.reply_text(f"🎉 <b>Kích hoạt thành công!</b>\nHạn: {exp.strftime('%d/%m/%Y')}", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Key sai hoặc đã dùng!")

# ==================== 6. XỬ LÝ NÚT BẤM (TEXT) ====================

async def handle_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 <b>GỬI LỆNH THEO MẪU:</b>\n<code>/add_uid 1000123 An_Keo 500000</code>", parse_mode=ParseMode.HTML)
    elif txt == "📖 Hướng Dẫn":
        msg = (f"<b>📖 HƯỚNG DẪN</b>\n{UI.DIV}"
               f"1. Kích hoạt: <code>/active VIP-XXXX</code>\n"
               f"2. Thêm UID: <code>/add_uid UID Tên Tiền</code>\n"
               f"3. Xóa UID: <code>/remove_uid UID</code>\n"
               f"{UI.DIV}💡 Bot báo ngay khi UID đổi trạng thái!")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    # Các lệnh cần quyền Premium
    if not has_access(user_id):
        if txt in ["📋 Danh Sách", "📊 Trạng Thái", "💰 Doanh Thu"]:
            return await update.message.reply_text("❌ Bạn cần có Key để dùng tính năng này.")
        return

    if txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = f"{UI.HEADER}📋 <b>DANH SÁCH</b>\n{UI.DIV}"
        if not rows: m += "<i>(Trống)</i>"
        for r in rows: m += f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}\n"
        await update.message.reply_text(m + UI.FOOT, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        r = db_query("SELECT SUM(amount) as s, COUNT(*) as c FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 <b>Hôm nay:</b> <code>{r[0]['s'] or 0:,}đ</code> ({r[0]['c']} kèo)", parse_mode=ParseMode.HTML)
    elif txt == "📊 Trạng Thái":
        res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by=? AND is_active=1 GROUP BY status", (user_id,), fetch=True)
        m = f"📊 <b>TRẠNG THÁI</b>\n"
        for r in res: m += f"● {r['status']}: <b>{r['c']} UID</b>\n"
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    elif txt == "📜 List Key" and user_id == ADMIN_ID:
        keys = db_query("SELECT license_key, status FROM keys ORDER BY status DESC LIMIT 10", fetch=True)
        m = "🔑 <b>DANH SÁCH KEY:</b>\n"
        for k in keys: m += f"• <code>{k['license_key']}</code> ({k['status']})\n"
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)

# ==================== 7. AUTO MONITOR & MAIN ====================

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        if not has_access(row['added_by']): continue
        new_s = await check_fb(row['uid'])
        if new_s != row['status']:
            db_query("UPDATE uids SET status=?, done_at=? WHERE id=?", (new_s, datetime.datetime.now() if new_s=="LIVE" else None, row['id']))
            msg = (f"🔔 <b>CẬP NHẬT BIẾN ĐỘNG</b>\n{UI.DIV}"
                   f"🆔 <code>{row['uid']}</code> | {row['customer_name']}\n"
                   f"🔄 {row['status']} ➔ <b>{new_s}</b>\n"
                   f"💰 Tiền: {row['amount']:,}đ{UI.FOOT}")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.HTML)
            except: pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # 1. Đăng ký lệnh Slash trước (Ưu tiên cao nhất)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    
    # 2. Đăng ký MessageHandler cho Nút bấm (Lọc command để tránh xung đột)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_click))
    
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    print("--- TITANIUM BOT STARTED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
