import logging
import asyncio
import datetime
import pandas as pd
import httpx
from typing import List
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func

# --- CONFIGURATION ---
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_ID = 123456789  # Thay ID của bạn vào đây
DB_URL = "sqlite:///fb_monitor.db"

# --- DATABASE SETUP ---
Base = declarative_base()

class UserKey(Base):
    __tablename__ = 'keys'
    id = Column(Integer, primary_key=True)
    key_code = Column(String, unique=True)
    owner_id = Column(Integer) # Telegram User ID
    created_at = Column(DateTime, default=datetime.datetime.now)
    expire_at = Column(DateTime)
    status = Column(String, default="active") # active, banned, expired

class FacebookUID(Base):
    __tablename__ = 'uids'
    id = Column(Integer, primary_key=True)
    uid = Column(String)
    customer_name = Column(String)
    price = Column(Float)
    last_status = Column(String) # LIVE, DIE
    created_at = Column(DateTime, default=datetime.datetime.now)
    done_at = Column(DateTime, nullable=True)
    added_by = Column(Integer) # Telegram User ID
    is_active = Column(Boolean, default=True)

engine = create_engine(DB_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- HELPERS ---
def check_key_valid(user_id):
    session = Session()
    key = session.query(UserKey).filter(UserKey.owner_id == user_id, UserKey.status == "active").first()
    if key and key.expire_at > datetime.datetime.now():
        session.close()
        return True
    session.close()
    return False

async def check_fb_status(uid: str) -> str:
    """
    Logic check UID Facebook. 
    Thay thế URL API thực tế của bạn vào đây.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Giả lập call API check UID
            # r = await client.get(f"https://graph.facebook.com/{uid}/picture?type=normal")
            # return "LIVE" if r.status_code == 200 else "DIE"
            return "LIVE" # Mockup
    except Exception:
        return "DIE"

# --- JOBS ---
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    uids = session.query(FacebookUID).filter(FacebookUID.is_active == True).all()
    
    for item in uids:
        # Check key của người sở hữu UID còn hạn không
        if not check_key_valid(item.added_by):
            continue

        current_status = await check_fb_status(item.uid)
        
        if item.last_status and item.last_status != current_status:
            item.last_status = current_status
            if current_status == "LIVE": # Giả định DONE khi từ DIE sang LIVE hoặc ngược lại tùy logic bạn
                item.done_at = datetime.datetime.now()
                
                msg = (
                    f"✅ **DONE kèo:** `{item.uid}`\n"
                    f"👤 Khách hàng: {item.customer_name}\n"
                    f"📅 Ngày nhận: {item.created_at.strftime('%d/%m/%Y')}\n"
                    f"🏁 Ngày DONE: {item.done_at.strftime('%d/%m/%Y')}\n"
                    f"💰 Số tiền: {item.price:,.0f} VND"
                )
                await context.bot.send_message(chat_id=item.added_by, text=msg, parse_mode="Markdown")
            
            session.commit()
    session.close()

async def monthly_report_job(context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    now = datetime.datetime.now()
    # Logic lấy data tháng vừa qua và xuất Excel
    # (Phần này sẽ tạo file .xlsx bằng pandas và gửi cho ADMIN_ID)
    file_path = f"report_{now.strftime('%m_%Y')}.xlsx"
    # ... code pandas to excel ...
    await context.bot.send_document(chat_id=ADMIN_ID, document=open(file_path, 'rb'), caption=f"Báo cáo tháng {now.month}")
    session.close()

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Hệ thống theo dõi UID Facebook Professional.\nVui lòng nhập KEY để sử dụng.")

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_key_valid(user_id):
        return await update.message.reply_text("❌ Key hết hạn hoặc không tồn tại.")
    
    try:
        # Format: /add_uid <uid> <tên> <tiền>
        data = context.args
        uid, name, price = data[0], data[1], float(data[2])
        
        session = Session()
        new_uid = FacebookUID(uid=uid, customer_name=name, price=price, added_by=user_id, last_status="DIE")
        session.add(new_uid)
        session.commit()
        session.close()
        await update.message.reply_text(f"🚀 Đã thêm UID {uid} vào hệ thống theo dõi.")
    except Exception as e:
        await update.message.reply_text("⚠️ Sai cú pháp: /add_uid <uid> <tên_khách> <số_tiền>")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = Session()
    total = session.query(FacebookUID).filter(FacebookUID.added_by == user_id, FacebookUID.is_active == True).count()
    live = session.query(FacebookUID).filter(FacebookUID.added_by == user_id, FacebookUID.is_active == True, FacebookUID.last_status == "LIVE").count()
    die = total - live
    
    await update.message.reply_text(f"📊 **Trạng thái hiện tại:**\n- Tổng: {total}\n- LIVE: {live}\n- DIE: {die}", parse_mode="Markdown")
    session.close()

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = Session()
    today = datetime.datetime.now().date()
    done_today = session.query(FacebookUID).filter(
        FacebookUID.added_by == user_id, 
        func.date(FacebookUID.done_at) == today
    ).all()
    
    total_revenue = sum(item.price for item in done_today)
    await update.message.reply_text(f"💰 **Hôm nay:**\n- Kèo DONE: {len(done_today)}\n- Doanh thu: {total_revenue:,.0f} VND", parse_mode="Markdown")
    session.close()

# --- ADMIN COMMANDS ---
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        new_key_code = f"KEY-{datetime.datetime.now().timestamp()}"
        session = Session()
        new_key = UserKey(key_code=new_key_code, expire_at=datetime.datetime.now() + datetime.timedelta(days=days))
        session.add(new_key)
        session.commit()
        await update.message.reply_text(f"🔑 Đã tạo Key: `{new_key_code}` ({days} ngày)", parse_mode="Markdown")
        session.close()
    except:
        await update.message.reply_text("Cú pháp: /create_key <số_ngày>")

# --- MAIN RUNNER ---
def main():
    application = Application.builder().token(TOKEN).build()

    # Job Queue
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_job, interval=60, first=10) # Mỗi 60s
    
    # Register Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_uid", add_uid))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("stats_today", stats_today))
    application.add_handler(CommandHandler("create_key", create_key))
    # ... thêm các command còn lại tương tự ...

    # RUN POLLING - KHÔNG DÙNG await/asyncio.run() ở đây theo chuẩn PTB v20
    print("Bot is running...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
