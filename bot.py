#!/usr/bin/env python3
import logging
import sqlite3
import datetime
import pandas as pd
import httpx
from io import BytesIO
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ==================== CẤU HÌNH ====================
TOKEN = "8388735235:AAHD31z_oB0JhhUzlFTu25ocGJv1xCbEXMk"
ADMIN_ID = 123456789  # <--- THAY ID CỦA BẠN VÀO ĐÂY
DB_FILE = "fb_keo_bot_v5_2.db"

# ==================== DATABASE ====================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS uids 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, customer_name TEXT, amount INTEGER, 
            status TEXT, added_by INTEGER, created_at TIMESTAMP, done_at TIMESTAMP, is_active BOOLEAN DEFAULT 1)''')
        conn.commit()

def db_query(q, p=(), fetch=False):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q, p)
        return cursor.fetchall() if fetch else conn.commit()

# ==================== LOGIC CHECK LIVE/DIE ====================
async def check_fb(uid):
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# ==================== CÁC LỆNH USER ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ **Bot FB Monitor Active!**\n\n📌 **Lệnh User:**\n/add_uid, /remove_uid, /list, /status, /stats_today\n\n🛠 **Lệnh Admin:**\n/admin_export", parse_mode="Markdown")

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        current = await check_fb(uid)
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, current, update.effective_user.id, datetime.datetime.now()))
        await update.message.reply_text(f"🚀 **Đã thêm:** `{uid}`\n📊 Trạng thái: **{current}**", parse_mode="Markdown")
    except:
        await update.message.reply_text("⚠️ `/add_uid <uid> <tên> <tiền>`")

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ `/remove_uid <uid>`")
    uid_rm = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid_rm, update.effective_user.id))
    await update.message.reply_text(f"🗑 Đã dừng theo dõi: `{uid_rm}`")

async def list_uids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (user_id,), fetch=True)
    if not rows: return await update.message.reply_text("Danh sách trống.")
    msg = "📋 **DANH SÁCH THEO DÕI:**\n"
    total = 0
    for r in rows:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} `{r['uid']}` | {r['customer_name']} | {r['amount']:,}đ\n"
        total += r['amount']
    msg += f"\n💰 **Tổng kèo:** {total:,}đ"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (user_id,), fetch=True)
    if not res: return await update.message.reply_text("Danh sách trống.")
    msg = "📊 **Trạng thái:**\n" + "\n".join([f"- {r['status']}: {r['c']} UID" for r in res])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (user_id,), fetch=True)
    await update.message.reply_text(f"💰 **Hôm nay:**\n✅ DONE: {res[0]['c']} kèo\nTổng: {res[0]['s'] or 0:,}đ")

# ==================== ADMIN COMMANDS ====================

async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = db_query("SELECT * FROM uids", fetch=True)
    df = pd.DataFrame([dict(r) for r in data])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='ChiTiet')
    output.seek(0)
    await update.message.reply_document(document=output, filename="BaoCao_FB_All.xlsx")

# ==================== JOB THEO DÕI TỰ ĐỘNG ====================

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            msg = (f"🔔 **CẬP NHẬT TRẠNG THÁI**\n🆔 `{row['uid']}` | {row['customer_name']}\n🔄: {row['status']} ➔ **{new_status}**")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode="Markdown")
            except: pass

# ==================== KHỞI CHẠY ====================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký Handler (Vị trí rất quan trọng)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("list", list_uids))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats_today", stats_today))
    app.add_handler(CommandHandler("admin_export", admin_export))
    
    # Chạy Job ngầm mỗi 60s
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT STARTED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
