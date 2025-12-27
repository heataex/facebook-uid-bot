#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import os
import threading
import secrets
import string
import httpx
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "ONLINE", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CONFIG ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 
DB_FILE = "fb_pro_v26.db"

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. NGHIỆP VỤ ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

async def check_fb_status(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

def get_menu(user_id):
    if has_access(user_id):
        btns = [[KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
                [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
                [KeyboardButton("🗑 Xóa UID")]]
        if user_id == ADMIN_ID:
            btns.append([KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")])
    else:
        btns = [[KeyboardButton("🔑 Nhập Key Kích Hoạt")]]
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# ==================== 4. LỆNH ADMIN ====================

async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0]) if context.args else 30
        key = "VIP-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db_query("INSERT INTO keys (license_key, days) VALUES (?,?)", (key, days))
        await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code> ({days} ngày)", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi: <code>{e}</code>", parse_mode=ParseMode.HTML)

async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    keys = db_query("SELECT license_key, status FROM keys ORDER BY rowid DESC LIMIT 10", fetch=True)
    m = "🔑 <b>10 KEY GẦN NHẤT:</b>\n"
    for k in keys: m += f"• <code>{k['license_key']}</code> ({k['status']})\n"
    await update.message.reply_text(m, parse_mode=ParseMode.HTML)

# ==================== 5. LỆNH USER ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = "✨ <b>HỆ THỐNG FB MONITOR V26</b>\n"
    if not has_access(user_id):
        msg += f"❌ Trạng thái: <b>Chưa kích hoạt</b>\n👉 Vui lòng nhập Key để sử dụng."
    else:
        msg += f"✅ Trạng thái: <b>Premium Active</b>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("📌 Nhập: <code>/active VIP-XXXX</code>", parse_mode=ParseMode.HTML)
    key = context.args[0].strip()
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key,), fetch=True)
    if res:
        expire = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET status='ACTIVE', used_by=?, expire_at=? WHERE license_key=?", (user_id, expire, key))
        await update.message.reply_text(f"🎉 Kích hoạt thành công!\nHạn dùng: {expire.strftime('%d/%m/%Y')}", reply_markup=get_menu(user_id))
    else:
        await update.message.reply_text("❌ Key sai hoặc đã dùng.")

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): return
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        status = await check_fb_status(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?,?,?,?,?,?)",
                 (uid, name, amount, status, user_id, datetime.datetime.now()))
        await update.message.reply_text(f"✅ Đã thêm <code>{uid}</code>\nTrạng thái gốc: <b>{status}</b>", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ Gõ: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)

# ==================== 6. XỬ LÝ TIN NHẮN (MESSAGE HANDLER) ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id

    if txt == "🔑 Nhập Key Kích Hoạt":
        await update.message.reply_text("📌 Gõ lệnh: <code>/active MÃ_KEY</code>", parse_mode=ParseMode.HTML)
    elif txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Gõ: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    elif txt == "📋 Danh Sách" and has_access(user_id):
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = "📋 <b>DANH SÁCH:</b>\n" + "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']}" for r in rows]) if rows else "Trống."
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    elif txt == "🔑 Tạo Key" and user_id == ADMIN_ID:
        await create_key(update, context)
    elif txt == "📜 List Key" and user_id == ADMIN_ID:
        await list_keys(update, context)

# ==================== 7. CHẠY HỆ THỐNG ====================

def main():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS keys (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')

    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký Command trước
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("list_keys", list_keys))
    
    # MessageHandler xử lý các nút bấm (Nhưng không chặn Command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("--- V26.0 SUPER STABLE IS READY ---")
    # Tắt drop_pending_updates để không mất tin nhắn lúc khởi động
    app.run_polling()

if __name__ == "__main__":
    main()
