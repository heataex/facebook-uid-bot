import logging
import asyncio
import datetime
import pandas as pd
import httpx
import os
import secrets
import string
from calendar import monthrange
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ======================== 1. CẤU HÌNH ========================
TOKEN = "8388735235:AAEdJPWlZxo9Vm5rVIYGsFIeJ44wWTuT3D0" # Token của bạn
ADMIN_ID = 123456789 # THAY BẰNG ID TELEGRAM CỦA BẠN
DB_URL = "sqlite:///fb_monitor.db"

# ======================== 2. DATABASE SETUP ========================
Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

class UserKey(Base):
    __tablename__ = 'keys'
    id = Column(Integer, primary_key=True)
    key_code = Column(String, unique=True)
    owner_id = Column(Integer, unique=True, nullable=True) # ID Telegram người dùng
    expire_at = Column(DateTime)
    status = Column(String, default="active")

class FacebookUID(Base):
    __tablename__ = 'uids'
    id = Column(Integer, primary_key=True)
    uid = Column(String)
    customer_name = Column(String)
    price = Column(Float)
    last_status = Column(String, default="DIE")
    created_at = Column(DateTime, default=datetime.datetime.now)
    done_at = Column(DateTime, nullable=True)
    added_by = Column(Integer) # ID Telegram người thêm
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(engine)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# ======================== 3. HÀM BỔ TRỢ (HELPERS) ========================
def is_valid_user(user_id):
    if user_id == ADMIN_ID: return True
    db = SessionLocal()
    key = db.query(UserKey).filter(UserKey.owner_id == user_id, UserKey.status == "active").first()
    valid = key and key.expire_at > datetime.datetime.now()
    db.close()
    return valid

async def check_fb_status_api(uid):
    """Thay logic check LIVE/DIE thực tế của bạn ở đây"""
    return "LIVE" # Giả lập trả về LIVE

# ======================== 4. CÁC CÔNG VIỆC CHẠY NGẦM (JOBS) ========================
async def check_uids_job(context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    active_uids = db.query(FacebookUID).filter(FacebookUID.is_active == True).all()
    
    for item in active_uids:
        if not is_valid_user(item.added_by): continue
        
        current_status = await check_fb_status_api(item.uid)
        
        if item.last_status != current_status:
            old_status = item.last_status
            item.last_status = current_status
            
            if current_status == "LIVE":
                item.done_at = datetime.datetime.now()
                msg = (
                    f"✅ **DONE kèo:** `{item.uid}`\n"
                    f"👤 Khách: {item.customer_name}\n"
                    f"📅 Nhận: {item.created_at.strftime('%d/%m/%Y')}\n"
                    f"💰 Tiền: {item.price:,.0f} VND"
                )
                try:
                    await context.bot.send_message(chat_id=item.added_by, text=msg, parse_mode="Markdown")
                except: pass
            db.commit()
    db.close()

async def auto_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now()
    if now.day != monthrange(now.year, now.month)[1]: return
    # (Logic xuất Excel tương tự hàm export bên dưới)
    pass

# ======================== 5. LỆNH NGƯỜI DÙNG (USER COMMANDS) ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Bot Monitor FB 24/7\nSử dụng /active <key> để bắt đầu.")

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args: return await update.message.reply_text("⚠️ Nhập: /active <key>")
    
    db = SessionLocal()
    key_obj = db.query(UserKey).filter(UserKey.key_code == context.args[0], UserKey.status == "active").first()
    if key_obj:
        key_obj.owner_id = user_id
        db.commit()
        await update.message.reply_text("✅ Kích hoạt thành công!")
    else:
        await update.message.reply_text("❌ Key sai hoặc đã dùng.")
    db.close()

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_valid_user(user_id): return await update.message.reply_text("❌ Hết hạn Key.")
    try:
        uid, name, price = context.args[0], context.args[1], float(context.args[2])
        db = SessionLocal()
        db.add(FacebookUID(uid=uid, customer_name=name, price=price, added_by=user_id))
        db.commit()
        db.close()
        await update.message.reply_text(f"🚀 Đã thêm UID {uid}")
    except:
        await update.message.reply_text("⚠️ HD: /add_uid <uid> <tên> <tiền>")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = SessionLocal()
    uids = db.query(FacebookUID).filter(FacebookUID.added_by == user_id, FacebookUID.is_active == True).all()
    live = len([i for i in uids if i.last_status == "LIVE"])
    await update.message.reply_text(f"📊 Đang chạy: {len(uids)}\n🟢 LIVE: {live}\n🔴 DIE: {len(uids)-live}")
    db.close()

# ======================== 6. LỆNH ADMIN (ADMIN COMMANDS) ========================
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
        db = SessionLocal()
        db.add(UserKey(key_code=key, expire_at=datetime.datetime.now() + datetime.timedelta(days=days)))
        db.commit()
        db.close()
        await update.message.reply_text(f"🔑 Key mới: `{key}` ({days} ngày)", parse_mode="Markdown")
    except:
        await update.message.reply_text("⚠️ /create_key <ngày>")

async def export_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    db = SessionLocal()
    data = db.query(FacebookUID).all()
    df = pd.DataFrame([{'UID': i.uid, 'Khách': i.customer_name, 'Tiền': i.price, 'Status': i.last_status} for i in data])
    df.to_excel("report.xlsx", index=False)
    await update.message.reply_document(document=open("report.xlsx", 'rb'))
    db.close()

# ======================== 7. KHỞI CHẠY (MAIN) ========================
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký Job lặp lại 60s/lần
    if app.job_queue:
        app.job_queue.run_repeating(check_uids_job, interval=60, first=10)
    
    # Đăng ký các câu lệnh
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("export", export_report))
    
    print("--- BOT ĐANG CHẠY ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
