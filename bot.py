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
DB_FILE = "fb_pro_v24.db"

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. LOGIC CHECK FB ====================
async def check_fb_live(uid):
    """Hàm kiểm tra trạng thái Facebook ngay lập tức"""
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            # Facebook trả về 302 Redirect là Live, 404 hoặc khác là Die
            return "LIVE" if r.status_code == 302 else "DIE"
    except Exception:
        return "DIE"

# ==================== 4. UI/UX DESIGN ====================
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

def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

# ==================== 5. HANDLER NÂNG CẤP (QUAN TRỌNG) ====================

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): 
        return await update.message.reply_text("❌ Bạn cần mua Key để sử dụng.")
    
    try:
        if len(context.args) < 3:
            return await update.message.reply_text("⚠️ Cú pháp: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
        
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        
        # Gửi tin nhắn chờ để người dùng không cảm thấy bot bị treo
        wait_msg = await update.message.reply_text(f"⏳ Đang kiểm tra trạng thái gốc của <code>{uid}</code>...", parse_mode=ParseMode.HTML)
        
        # LẤY TRẠNG THÁI BAN ĐẦU
        initial_status = await check_fb_live(uid)
        
        # LƯU VÀO DATABASE VỚI TRẠNG THÁI THỰC
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?,?,?,?,?,?)",
                 (uid, name, amount, initial_status, user_id, datetime.datetime.now()))
        
        icon = "🟢 LIVE" if initial_status == "LIVE" else "🔴 DIE"
        
        result_msg = (
            f"{UI.HEADER}✅ <b>THÊM KÈO THÀNH CÔNG</b>\n{UI.DIV}"
            f"🆔 UID: <code>{uid}</code>\n"
            f"👤 Khách: <b>{name}</b>\n"
            f"💰 Tiền: <code>{amount:,}đ</code>\n"
            f"📊 Gốc: <b>{icon}</b>\n{UI.DIV}"
            f"🔎 Bot đã bắt đầu theo dõi biến động!"
            f"{UI.FOOTER}"
        )
        await wait_msg.edit_text(result_msg, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logging.error(f"Lỗi thêm UID: {e}")
        await update.message.reply_text("❌ Lỗi hệ thống hoặc sai định dạng tiền.")

# ==================== 6. CÁC HÀM CÒN LẠI (GIỮ NGUYÊN) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"{UI.HEADER}<b>🤖 FB MONITOR V24.2</b>\n{UI.DIV}Hệ thống check trạng thái gốc khi thêm!\nChọn chức năng bên dưới.{UI.FOOTER}", 
                                  parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    if txt == "📋 Danh Sách":
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        msg = f"📋 <b>DANH SÁCH:</b>\n" + "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}" for r in rows]) if rows else "Trống."
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        r = db_query("SELECT SUM(amount) as s FROM uids WHERE added_by=? AND status='LIVE' AND is_active=1", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 Doanh thu (LIVE): <b>{r[0]['s'] or 0:,}đ</b>", parse_mode=ParseMode.HTML)
    elif txt == "📖 Hướng Dẫn":
        await start(update, context)

def main():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS keys (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    
    print("--- V24.2 ULTIMATE DEPLOYED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
