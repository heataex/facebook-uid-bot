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

# ==================== 1. WEB SERVER ====================
web_app = Flask(__name__)
@web_app.route("/")
def health(): return "SYSTEM_ACTIVE", 200
threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()

# ==================== 2. CONFIG & DB ====================
TOKEN = "8388735235:AAGvuqNIoCvcDpy7T7TGPMWYX8CATm83Jp4"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN
DB_FILE = "fb_pro_v23.db"

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

# ==================== 3. UI/UX DESIGN SYSTEM ====================
class UI:
    HEADER = "<b>┏━━━━━━━━━━━━━━━━━━━━┓</b>\n"
    DIV = "<b>┣━━━━━━━━━━━━━━━━━━━━┫</b>\n"
    FOOTER = "\n<b>┗━━━━━━━━━━━━━━━━━━━━┛</b>"
    STAR = "✨"

def get_menu(user_id):
    btns = [[KeyboardButton("➕ Thêm Kèo"), KeyboardButton("📋 Danh Sách")],
            [KeyboardButton("📊 Trạng Thái"), KeyboardButton("💰 Doanh Thu")],
            [KeyboardButton("🗑 Xóa UID"), KeyboardButton("📖 Hướng Dẫn")]]
    if user_id == ADMIN_ID:
        btns.append([KeyboardButton("🔑 Tạo Key"), KeyboardButton("📜 List Key")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# ==================== 4. PHÂN QUYỀN & TIỆN ÍCH ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

# ==================== 5. NÂNG CẤP LỆNH REMOVE_UID (XÓA NHIỀU) ====================

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id):
        return await update.message.reply_text("❌ Bạn chưa kích hoạt bản quyền.")

    if not context.args:
        return await update.message.reply_text(
            f"<b>⚠️ THIẾU THÔNG TIN</b>\n{UI.DIV}"
            f"• Xóa 1 UID: <code>/remove_uid 1000123</code>\n"
            f"• Xóa nhiều: <code>/remove_uid 123,456,789</code>\n"
            f"{UI.DIV}<i>💡 Các UID cách nhau bởi dấu phẩy (,)</i>", 
            parse_mode=ParseMode.HTML
        )

    # Xử lý chuỗi UID đầu vào (hỗ trợ cả dấu cách sau dấu phẩy)
    raw_input = "".join(context.args)
    uids_to_remove = [u.strip() for u in raw_input.split(",") if u.strip()]
    
    success_list = []
    fail_list = []

    for uid in uids_to_remove:
        check = db_query("SELECT customer_name FROM uids WHERE uid = ? AND added_by = ? AND is_active = 1", (uid, user_id), fetch=True)
        if check:
            db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid, user_id))
            success_list.append(f"<code>{uid}</code> ({check[0]['customer_name']})")
        else:
            fail_list.append(f"<code>{uid}</code>")

    # Tạo Output thông báo chuyên nghiệp
    msg = f"🗑 <b>KẾT QUẢ DỌN DẸP</b>\n{UI.DIV}"
    if success_list:
        msg += f"✅ <b>Đã xóa ({len(success_list)}):</b>\n" + "\n".join(success_list) + "\n"
    if fail_list:
        if success_list: msg += f"{UI.DIV}"
        msg += f"❌ <b>Không tìm thấy ({len(fail_list)}):</b>\n" + "\n".join(fail_list)
    msg += UI.FOOTER

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==================== 6. HANDLERS LỆNH KHÁC ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    access = "ADMIN" if user_id == ADMIN_ID else ("PREMIUM" if has_access(user_id) else "FREE")
    msg = (f"{UI.HEADER}{UI.STAR} <b>FB MONITOR V23 PRO</b>\n{UI.DIV}"
           f"👤 User ID: <code>{user_id}</code>\n"
           f"🔰 Quyền hạn: <b>{access}</b>\n"
           f"🛰 Trạng thái: <code>Stable Online</code>\n{UI.FOOTER}")
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def handle_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    
    if txt == "🗑 Xóa UID":
        msg = (f"<b>🗑 HƯỚNG DẪN XÓA UID</b>\n{UI.DIV}"
               f"Để xóa một hoặc nhiều UID cùng lúc, hãy sử dụng lệnh:\n\n"
               f"👉 <code>/remove_uid UID1,UID2,UID3</code>\n\n"
               f"<i>Ví dụ: /remove_uid 1000123,1000456</i>")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    elif txt == "➕ Thêm Kèo":
        await update.message.reply_text("📌 <b>LỆNH THÊM KÈO:</b>\n<code>/add_uid UID Tên_Khách Tiền</code>", parse_mode=ParseMode.HTML)
    elif txt == "📋 Danh Sách":
        if not has_access(user_id): return
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = f"{UI.HEADER}📋 <b>DANH SÁCH KÈO</b>\n{UI.DIV}"
        if not rows: m += "<i>(Trống)</i>"
        for r in rows: m += f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}\n"
        await update.message.reply_text(m + UI.FOOTER, parse_mode=ParseMode.HTML)
    elif txt == "💰 Doanh Thu":
        if not has_access(user_id): return
        r = db_query("SELECT SUM(amount) as s, COUNT(*) as c FROM uids WHERE added_by=? AND status='LIVE' AND date(done_at)=date('now')", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 <b>DOANH THU HÔM NAY</b>\n{UI.DIV}✅ DONE: <b>{r[0]['c']}</b>\n💸 Thu nhập: <code>{r[0]['s'] or 0:,}đ</code>", parse_mode=ParseMode.HTML)
    elif txt == "📊 Trạng Thái":
        if not has_access(user_id): return
        res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by=? AND is_active=1 GROUP BY status", (user_id,), fetch=True)
        m = "📊 <b>TRẠNG THÁI HIỆN TẠI</b>\n"
        for r in res: m += f"● {r['status']}: <b>{r['c']} UID</b>\n"
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    elif txt == "📖 Hướng Dẫn":
        await start(update, context)

# ==================== 7. RUNNER ====================
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký lệnh Slash
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("add_uid", lambda u,c: None)) # (Tương tự bản cũ)
    app.add_handler(CommandHandler("active", lambda u,c: None)) # (Tương tự bản cũ)
    
    # Đăng ký MessageHandler cho nút bấm
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_click))
    
    print("--- V23.0 DIAMOND DEPLOYED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
