#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import os
import threading
import secrets
import string
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==================== 1. WEB SERVER (Để treo 24/7) ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "SYSTEM_ACTIVE", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CẤU HÌNH & DATABASE ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 # <--- ĐÃ CẬP NHẬT THEO LOG CỦA BẠN
DB_FILE = "fb_pro_v24.db"

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

# ==================== 3. GIAO DIỆN UI/UX ====================
class UI:
    HEADER = "<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOTER = "\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>"

def get_menu(user_id):
    btns = [[KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
            [KeyboardButton("🗑 Xóa UID"), KeyboardButton("🔑 Kích Hoạt")]]
    if user_id == ADMIN_ID:
        btns.append([KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# ==================== 4. LOGIC PHÂN QUYỀN ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

# ==================== 5. CHI TIẾT CÁC HÀM LỆNH ====================

# --- Lệnh Start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = f"{UI.HEADER}<b>🤖 FB MONITOR V24 PRO</b>\n{UI.DIV}Chào mừng bạn! Vui lòng sử dụng menu bên dưới.{UI.FOOTER}"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

# --- Lệnh Thêm UID ---
async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): return await update.message.reply_text("❌ Bạn cần Key để dùng.")
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?,?,?,?,?,?)",
                 (uid, name, amount, "DIE", user_id, datetime.datetime.now()))
        await update.message.reply_text(f"✅ Đã thêm: <code>{uid}</code>\n👤 Khách: {name}\n💰 Tiền: {amount:,}đ", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ Sai cú pháp! Ví dụ: <code>/add_uid 123 NguyenVanA 500000</code>", parse_mode=ParseMode.HTML)

# --- Lệnh Xóa UID (Hỗ trợ nhiều UID) ---
async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): return
    if not context.args:
        return await update.message.reply_text("⚠️ Cú pháp: <code>/remove_uid UID1,UID2</code>", parse_mode=ParseMode.HTML)
    
    uids = "".join(context.args).split(",")
    success, fail = [], []
    for uid in uids:
        uid = uid.strip()
        check = db_query("SELECT customer_name FROM uids WHERE uid=? AND added_by=? AND is_active=1", (uid, user_id), fetch=True)
        if check:
            db_query("UPDATE uids SET is_active=0 WHERE uid=? AND added_by=?", (uid, user_id))
            success.append(f"{uid} ({check[0]['customer_name']})")
        else: fail.append(uid)
    
    msg = f"<b>🗑 KẾT QUẢ XÓA:</b>\n✅ Thành công: {len(success)}\n❌ Thất bại: {len(fail)}"
    await update.message.reply_text(msg)

# --- Quản lý Key (Cho Admin) ---
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    days = int(context.args[0]) if context.args else 30
    new_key = f"VIP-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))}"
    db_query("INSERT INTO keys (license_key, days) VALUES (?,?)", (new_key, days))
    await update.message.reply_text(f"🔑 Key mới ({days} ngày):\n<code>{new_key}</code>", parse_mode=ParseMode.HTML)

# --- Kích hoạt Key (Cho User) ---
async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ Nhập: <code>/active VIP-XYZ</code>", parse_mode=ParseMode.HTML)
    key = context.args[0]
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key,), fetch=True)
    if res:
        expire = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET status='ACTIVE', used_by=?, expire_at=? WHERE license_key=?", (update.effective_user.id, expire, key))
        await update.message.reply_text(f"✅ Kích hoạt thành công!\nHạn dùng: {expire.strftime('%d/%m/%Y')}")
    else:
        await update.message.reply_text("❌ Key sai hoặc đã sử dụng.")

# --- Xử lý Menu ---
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    if txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = f"📋 <b>DANH SÁCH:</b>\n" + "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']}" for r in rows]) if rows else "Trống."
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        r = db_query("SELECT SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND is_active=1", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 Tổng thu: <b>{r[0]['s'] or 0:,}đ</b>", parse_mode=ParseMode.HTML)
    elif txt == "🗑 Xóa UID":
        await update.message.reply_text("Sử dụng lệnh: <code>/remove_uid UID1,UID2</code>", parse_mode=ParseMode.HTML)
    elif txt == "➕ Thêm Kèo":
        await update.message.reply_text("Sử dụng lệnh: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    elif txt == "🔑 Kích Hoạt":
        await update.message.reply_text("Sử dụng lệnh: <code>/active MÃ_KEY</code>", parse_mode=ParseMode.HTML)

# ==================== 6. CHẠY BOT ====================
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("active", active_key))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    
    print("--- V24.0 ULTIMATE IS RUNNING ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
