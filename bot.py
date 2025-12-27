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

# ==================== CẤU HÌNH (THAY TẠI ĐÂY) ====================
TOKEN = "8388735235:AAHkD03utv9sm5ZkSs3UepDy5Ps1zDRerKU"
ADMIN_ID = 5522878843  # ID Telegram của bạn
DB_FILE = "fb_keo_bot_v6.db"

# ==================== DATABASE LAYER ====================
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

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🔥 *HỆ THỐNG THEO DÕI UID FACEBOOK V6.0*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 *DANH SÁCH LỆNH CỦA BẠN:*\n\n"
        "🔹 `/add_uid <uid> <tên> <tiền>` : Thêm kèo mới\n"
        "🔹 `/list` : Xem danh sách đang theo dõi\n"
        "🔹 `/status` : Thống kê LIVE/DIE hiện tại\n"
        "🔹 `/remove_uid <uid>` : Xóa kèo khỏi hệ thống\n\n"
        "💰 *THỐNG KÊ DOANH THU:*\n"
        "🔹 `/stats_today` : Doanh thu hôm nay\n"
        "🔹 `/stats_week` : Doanh thu 7 ngày qua\n"
        "🔹 `/stats_month` : Doanh thu tháng này\n\n"
        "🛠 *DÀNH CHO ADMIN:*\n"
        "🔹 `/admin_export` : Xuất file Excel báo cáo"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 3:
            return await update.message.reply_text("⚠️ *Sai cú pháp!*\nHD: `/add_uid 1000xx An_Nguyen 500000`", parse_mode=ParseMode.MARKDOWN)
        
        uid, name, amount = context.args[0], context.args[1], int(context.args[2])
        current = await check_fb(uid)
        
        db_query("INSERT INTO uids (uid, customer_name, amount, status, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, name, amount, current, update.effective_user.id, datetime.datetime.now()))
        
        icon = "🟢" if current == "LIVE" else "🔴"
        await update.message.reply_text(f"✅ *THÊM KÈO THÀNH CÔNG*\n━━━━━━━━━━━━━━━━━━━━\n🆔 UID: `{uid}`\n👤 Khách: *{name}*\n💰 Tiền: *{amount:,}đ*\n📊 Trạng thái: {icon} *{current}*", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ *Lỗi:* {str(e)}")

async def list_uids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE added_by = ? AND is_active = 1", (update.effective_user.id,), fetch=True)
    if not rows: return await update.message.reply_text("📝 *Danh sách theo dõi trống!*", parse_mode=ParseMode.MARKDOWN)
    
    msg = "📋 *DANH SÁCH UID ĐANG THEO DÕI*\n━━━━━━━━━━━━━━━━━━━━\n"
    total = 0
    for r in rows:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} `{r['uid']}` | {r['customer_name']} | {r['amount']:,}đ\n"
        total += r['amount']
    msg += f"━━━━━━━━━━━━━━━━━━━━\n💰 *Tổng tiền kèo:* `{total:,}đ`"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT status, COUNT(*) as c FROM uids WHERE added_by = ? AND is_active = 1 GROUP BY status", (update.effective_user.id,), fetch=True)
    if not res: return await update.message.reply_text("📊 *Hiện tại chưa theo dõi UID nào.*", parse_mode=ParseMode.MARKDOWN)
    
    msg = "📊 *THỐNG KÊ TRẠNG THÁI HIỆN TẠI*\n━━━━━━━━━━━━━━━━━━━━\n"
    for r in res:
        icon = "🟢" if r['status'] == "LIVE" else "🔴"
        msg += f"{icon} {r['status']}: *{r['c']} UID*\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ HD: `/remove_uid <uid>`", parse_mode=ParseMode.MARKDOWN)
    uid_rm = context.args[0]
    db_query("UPDATE uids SET is_active = 0 WHERE uid = ? AND added_by = ?", (uid_rm, update.effective_user.id))
    await update.message.reply_text(f"🗑 *Đã ngừng theo dõi UID:* `{uid_rm}`", parse_mode=ParseMode.MARKDOWN)

# ==================== STATS DOANH THU ====================

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND date(done_at) = date('now')", (update.effective_user.id,), fetch=True)
    count = res[0]['c']
    total = res[0]['s'] or 0
    await update.message.reply_text(f"💰 *DOANH THU HÔM NAY*\n━━━━━━━━━━━━━━━━━━━━\n✅ Kèo DONE: *{count}*\n💸 Tổng thu: `{total:,}đ`", parse_mode="Markdown")

async def stats_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND done_at >= date('now', '-7 days')", (update.effective_user.id,), fetch=True)
    count = res[0]['c']
    total = res[0]['s'] or 0
    await update.message.reply_text(f"📅 *DOANH THU 7 NGÀY QUA*\n━━━━━━━━━━━━━━━━━━━━\n✅ Kèo DONE: *{count}*\n💸 Tổng thu: `{total:,}đ`", parse_mode="Markdown")

async def stats_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT COUNT(*) as c, SUM(amount) as s FROM uids WHERE added_by = ? AND status = 'LIVE' AND strftime('%m', done_at) = strftime('%m', 'now')", (update.effective_user.id,), fetch=True)
    count = res[0]['c']
    total = res[0]['s'] or 0
    await update.message.reply_text(f"📊 *DOANH THU THÁNG NÀY*\n━━━━━━━━━━━━━━━━━━━━\n✅ Kèo DONE: *{count}*\n💸 Tổng thu: `{total:,}đ`", parse_mode="Markdown")

async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = db_query("SELECT * FROM uids", fetch=True)
    df = pd.DataFrame([dict(r) for r in data])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Data_Keo')
    output.seek(0)
    await update.message.reply_document(document=output, filename=f"Bao_Cao_FB_Keo_{datetime.date.today()}.xlsx", caption="📊 *Báo cáo tổng hợp hệ thống*")

# ==================== AUTO MONITOR JOB ====================

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT * FROM uids WHERE is_active = 1", fetch=True)
    for row in rows:
        new_status = await check_fb(row['uid'])
        if new_status != row['status']:
            done_at = datetime.datetime.now() if new_status == "LIVE" else None
            db_query("UPDATE uids SET status = ?, done_at = ? WHERE id = ?", (new_status, done_at, row['id']))
            
            icon = "🟢 LIVE" if new_status == "LIVE" else "🔴 DIE"
            msg = (f"🔔 *CẬP NHẬT TRẠNG THÁI MỚI*\n━━━━━━━━━━━━━━━━━━━━\n"
                   f"🆔 UID: `{row['uid']}`\n"
                   f"👤 Khách: *{row['customer_name']}*\n"
                   f"🔄 Trạng thái: {row['status']} ➔ *{icon}*\n"
                   f"💰 Tiền kèo: *{row['amount']:,}đ*")
            try: await context.bot.send_message(chat_id=row['added_by'], text=msg, parse_mode=ParseMode.MARKDOWN)
            except: pass

# ==================== KHỞI CHẠY BOT ====================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Register Commands - Tách bạch rõ ràng
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("list", list_uids))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.add_handler(CommandHandler("stats_today", stats_today))
    app.add_handler(CommandHandler("stats_week", stats_week))
    app.add_handler(CommandHandler("stats_month", stats_month))
    app.add_handler(CommandHandler("admin_export", admin_export))
    
    # Chạy quét tự động mỗi 60s
    app.job_queue.run_repeating(monitor_job, interval=60, first=10)
    
    print("--- BOT STARTED V6.0 ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
