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
def health(): return "SYSTEM_ACTIVE", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CONFIG ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843 
DB_FILE = "fb_pro_v25.db"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. LOGIC QUYỀN TRUY CẬP ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

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

# ==================== 4. CÁC HÀM XỬ LÝ LỆNH (COMMANDS) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    access = has_access(user_id)
    if access:
        msg = f"<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n✨ <b>HỆ THỐNG FB PRO</b>\n<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n👤 ID: <code>{user_id}</code>\n🔰 Trạng thái: <b>Đã kích hoạt</b>\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>"
    else:
        msg = f"<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n⚠️ <b>YÊU CẦU KÍCH HOẠT</b>\n<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n👤 ID: <code>{user_id}</code>\n❌ Trạng thái: <b>Chưa có Key</b>\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>\n👉 Vui lòng nhấn nút bên dưới để nhập Key."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("📌 Cú pháp: <code>/active VIP-XXXXXXXX</code>", parse_mode=ParseMode.HTML)
    
    key = context.args[0].strip()
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key,), fetch=True)
    if res:
        expire = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET status='ACTIVE', used_by=?, expire_at=? WHERE license_key=?", (user_id, expire, key))
        await update.message.reply_text(f"🎉 <b>KÍCH HOẠT THÀNH CÔNG!</b>\nHết hạn: {expire.strftime('%d/%m/%Y')}", parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))
    else:
        await update.message.reply_text("❌ Key sai hoặc đã dùng.")

async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    days = int(context.args[0]) if context.args else 30
    key = "VIP-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    db_query("INSERT INTO keys (license_key, days) VALUES (?,?)", (key, days))
    await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code>", parse_mode=ParseMode.HTML)

# ==================== 5. XỬ LÝ MENU & NÚT BẤM (MESSAGES) ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text

    # Lệnh cho người dùng chưa kích hoạt
    if txt == "🔑 Nhập Key Kích Hoạt":
        return await update.message.reply_text("📌 Gõ lệnh: <code>/active MÃ_KEY_CỦA_BẠN</code>", parse_mode=ParseMode.HTML)

    # Chặn nếu không có quyền
    if not has_access(user_id):
        return await update.message.reply_text("🚫 <b>TRUY CẬP BỊ CHẶN</b>\nVui lòng kích hoạt Key để dùng chức năng này.", parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

    # Logic Menu khi đã có quyền
    if txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 Gõ mẫu: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    elif txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = "📋 <b>DANH SÁCH:</b>\n" + "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']}" for r in rows]) if rows else "Trống."
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        r = db_query("SELECT SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND is_active=1", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 Doanh thu: <b>{r[0]['s'] or 0:,}đ</b>", parse_mode=ParseMode.HTML)
    elif txt == "📜 List Key" and user_id == ADMIN_ID:
        keys = db_query("SELECT license_key, status FROM keys ORDER BY status DESC LIMIT 10", fetch=True)
        m = "🔑 <b>KEY GẦN ĐÂY:</b>\n" + "\n".join([f"• <code>{k['license_key']}</code> ({k['status']})" for k in keys])
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)

# ==================== 6. RUNNER ====================
def main():
    # Tự động init DB khi khởi động
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS keys (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')

    app = Application.builder().token(TOKEN).build()
    
    # ĐĂNG KÝ COMMAND (ƯU TIÊN CAO)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("add_uid", lambda u, c: None)) # Thêm hàm add_uid của bạn vào đây
    
    # ĐĂNG KÝ MESSAGE (XỬ LÝ NÚT BẤM) - SAU COMMAND
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("--- V25.3 FINAL READY ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
