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
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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

# ==================== 3. KIỂM TRA QUYỀN ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

# ==================== 4. GIAO DIỆN MENU ====================
def get_menu(user_id):
    # Nếu là Admin hoặc có quyền
    if has_access(user_id):
        btns = [[KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
                [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
                [KeyboardButton("🗑 Xóa UID")]]
        if user_id == ADMIN_ID:
            btns.append([KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")])
        return ReplyKeyboardMarkup(btns, resize_keyboard=True)
    else:
        # Nếu chưa kích hoạt: Chỉ hiện đúng 1 nút hướng dẫn nhập Key
        return ReplyKeyboardMarkup([[KeyboardButton("🔑 Nhập Key Kích Hoạt")]], resize_keyboard=True)

# ==================== 5. XỬ LÝ LỆNH /START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if has_access(user_id):
        role = "ADMIN" if user_id == ADMIN_ID else "PREMIUM"
        msg = f"✅ Chào mừng quay lại! Bạn đang dùng quyền <b>{role}</b>."
    else:
        msg = (f"⚠️ <b>THÔNG BÁO HỆ THỐNG</b>\n"
               f"Tài khoản của bạn <code>{user_id}</code> chưa được kích hoạt.\n\n"
               f"Vui lòng liên hệ Admin để mua Key và nhấn nút bên dưới để nhập mã.")
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

# ==================== 6. XỬ LÝ KÍCH HOẠT ====================
async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("📌 Vui lòng nhập mã theo cú pháp: <code>/active VIP-XXXXXXXX</code>", parse_mode=ParseMode.HTML)
    
    key = context.args[0].strip()
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key,), fetch=True)
    
    if res:
        days = res[0]['days']
        expire = datetime.datetime.now() + datetime.timedelta(days=days)
        db_query("UPDATE keys SET status='ACTIVE', used_by=?, expire_at=? WHERE license_key=?", (user_id, expire, key))
        
        await update.message.reply_text(
            f"🎉 <b>KÍCH HOẠT THÀNH CÔNG!</b>\n"
            f"Hạn dùng: 30 ngày ({expire.strftime('%d/%m/%Y')})\n"
            f"Bây giờ bạn có thể sử dụng đầy đủ chức năng.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu(user_id) # Cập nhật lại Menu ngay lập tức
        )
    else:
        await update.message.reply_text("❌ Mã Key không tồn tại hoặc đã được sử dụng.")

# ==================== 7. CHẶN TRUY CẬP TRÁI PHÉP ====================
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text

    # 1. Xử lý nút bấm "Nhập Key Kích Hoạt"
    if txt == "🔑 Nhập Key Kích Hoạt":
        return await update.message.reply_text("📌 Bạn hãy gõ: <code>/active MÃ_CỦA_BẠN</code>", parse_mode=ParseMode.HTML)

    # 2. Kiểm tra quyền cho tất cả các hành động khác
    if not has_access(user_id):
        return await update.message.reply_text(
            "🚫 <b>TRUY CẬP BỊ CHẶN</b>\nBạn cần kích hoạt Key để sử dụng Bot.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu(user_id)
        )

    # 3. Nếu có quyền thì xử lý Menu như bình thường (Giữ nguyên logic cũ của bạn)
    if txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = "📋 <b>DANH SÁCH:</b>\n" + "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}" for r in rows]) if rows else "Trống."
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    # ... (Các logic Menu khác giữ nguyên)

# ==================== 8. CHẠY BOT ====================
def main():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS keys (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("active", active_key))
    
    # Các lệnh Admin vẫn giữ nguyên
    app.add_handler(CommandHandler("create_key", lambda u, c: create_key(u, c) if u.effective_user.id == ADMIN_ID else None))
    
    # Xử lý tất cả tin nhắn và nút bấm qua bộ lọc kiểm tra quyền
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
