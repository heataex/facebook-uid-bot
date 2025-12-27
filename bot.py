#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import os
import threading
import secrets
import string
import httpx
import asyncio
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
DB_FILE = "fb_diamond_v25.db"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== 3. UI/UX DESIGN ====================
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

# ==================== 4. PHÂN QUYỀN ====================
def has_access(user_id):
    if user_id == ADMIN_ID: return True
    res = db_query("SELECT expire_at FROM keys WHERE used_by = ? AND status = 'ACTIVE'", (user_id,), fetch=True)
    if not res: return False
    expire = datetime.datetime.strptime(res[0]['expire_at'], '%Y-%m-%d %H:%M:%S.%f')
    return expire > datetime.datetime.now()

async def check_fb_status(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=7) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== 5. CÁC HÀM XỬ LÝ LỆNH (COMMANDS) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = f"{UI.HEADER}✨ <b>FB MONITOR V25 PRO</b>\n{UI.DIV}Hệ thống đã sẵn sàng phản hồi\!\nVui lòng chọn chức năng bên dưới\.{UI.FOOTER}"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_menu(user_id))

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): return await update.message.reply_text("❌ Bạn cần mua Key để sử dụng.")
    
    if len(context.args) < 3:
        return await update.message.reply_text("⚠️ Cú pháp: <code>/add_uid UID Tên Tiền</code>", parse_mode=ParseMode.HTML)
    
    uid, name, amount = context.args[0], context.args[1], context.args[2]
    wait_msg = await update.message.reply_text(f"⏳ Đang kiểm tra UID <code>{uid}</code>...", parse_mode=ParseMode.HTML)
    
    status = await check_fb_status(uid)
    db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?,?,?,?,?,?)",
             (uid, name, int(amount), status, user_id, datetime.datetime.now()))
    
    icon = "🟢" if status == "LIVE" else "🔴"
    res = (f"✅ <b>THÊM KÈO THÀNH CÔNG</b>\n{UI.DIV}"
           f"🆔 UID: <code>{uid}</code>\n👤 Khách: <b>{name}</b>\n💰 Tiền: <code>{int(amount):,}đ</code>\n📊 Gốc: <b>{icon} {status}</b>{UI.FOOTER}")
    await wait_msg.edit_text(res, parse_mode=ParseMode.HTML)

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_access(user_id): return
    if not context.args: return await update.message.reply_text("⚠️ Cú pháp: <code>/remove_uid 123,456</code>")
    
    uids = "".join(context.args).split(",")
    count = 0
    for u in uids:
        u = u.strip()
        db_query("UPDATE uids SET is_active=0 WHERE uid=? AND added_by=?", (u, user_id))
        count += 1
    await update.message.reply_text(f"🗑 <b>Đã xóa {count} UID thành công!</b>", parse_mode=ParseMode.HTML)

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ Gõ: <code>/active MÃ_KEY</code>", parse_mode=ParseMode.HTML)
    key = context.args[0]
    res = db_query("SELECT * FROM keys WHERE license_key=? AND status='UNUSED'", (key,), fetch=True)
    if res:
        expire = datetime.datetime.now() + datetime.timedelta(days=res[0]['days'])
        db_query("UPDATE keys SET status='ACTIVE', used_by=?, expire_at=? WHERE license_key=?", (update.effective_user.id, expire, key))
        await update.message.reply_text(f"🎉 <b>Kích hoạt thành công!</b>\nHạn dùng: {expire.strftime('%d/%m/%Y')}")
    else: await update.message.reply_text("❌ Key không hợp lệ.")

async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    days = int(context.args[0]) if context.args else 30
    key = "VIP-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    db_query("INSERT INTO keys (license_key, days) VALUES (?,?)", (key, days))
    await update.message.reply_text(f"🎫 <b>KEY MỚI:</b> <code>{key}</code>", parse_mode=ParseMode.HTML)

# ==================== 6. XỬ LÝ MENU CLICK (MESSAGES) ====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    
    if txt == "📋 Danh Sách":
        if not has_access(user_id): return
        rows = db_query("SELECT * FROM uids WHERE added_by=? AND is_active=1", (user_id,), fetch=True)
        m = f"{UI.HEADER}📋 <b>DANH SÁCH</b>\n{UI.DIV}"
        m += "\n".join([f"• <code>{r['uid']}</code> | {r['customer_name']} | {r['status']}" for r in rows]) if rows else "<i>(Trống)</i>"
        await update.message.reply_text(m + UI.FOOTER, parse_mode=ParseMode.HTML)
        
    elif txt == "💰 Doanh Thu":
        if not has_access(user_id): return
        r = db_query("SELECT SUM(amount) as s, COUNT(*) as c FROM uids WHERE added_by=? AND status='LIVE' AND is_active=1 AND date(done_at)=date('now')", (user_id,), fetch=True)
        await update.message.reply_text(f"💰 <b>Hôm nay:</b> <code>{r[0]['s'] or 0:,}đ</code> ({r[0]['c']} kèo)", parse_mode=ParseMode.HTML)

    elif txt == "📊 Trạng Thái":
        if not has_access(user_id): return
        res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by=? AND is_active=1 GROUP BY status", (user_id,), fetch=True)
        msg = "📊 <b>THỐNG KÊ:</b>\n" + "\n".join([f"● {r['status']}: {r['c']}" for r in res])
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif txt == "📜 List Key" and user_id == ADMIN_ID:
        keys = db_query("SELECT license_key, status FROM keys ORDER BY status DESC LIMIT 10", fetch=True)
        m = "🔑 <b>DANH SÁCH KEY:</b>\n" + "\n".join([f"• <code>{k['license_key']}</code> ({k['status']})" for k in keys])
        await update.message.reply_text(m, parse_mode=ParseMode.HTML)
    
    elif txt in ["➕ Thêm Kèo", "🗑 Xóa UID", "🔑 Kích Hoạt", "🔑 Tạo Key"]:
        reminders = {
            "➕ Thêm Kèo": "📌 Gõ mẫu: <code>/add_uid UID Tên Tiền</code>",
            "🗑 Xóa UID": "📌 Gõ mẫu: <code>/remove_uid 123,456</code>",
            "🔑 Kích Hoạt": "📌 Gõ mẫu: <code>/active VIP-XXXX</code>",
            "🔑 Tạo Key": "📌 Gõ mẫu: <code>/create_key 30</code>"
        }
        await update.message.reply_text(reminders[txt], parse_mode=ParseMode.HTML)

# ==================== 7. CHẠY BOT ====================
def main():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS uids (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS keys (license_key TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, expire_at TIMESTAMP, status TEXT DEFAULT 'UNUSED')''')

    app = Application.builder().token(TOKEN).build()
    
    # LUÔN ĐĂNG KÝ COMMAND TRƯỚC
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("create_key", create_key))
    
    # ĐĂNG KÝ MESSAGE HANDLER SAU VỚI BỘ LỌC KHÔNG PHẢI LỆNH
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("--- SYSTEM READY V25 ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
