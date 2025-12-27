import logging
import asyncio
import datetime
import pandas as pd
import httpx
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, func
from sqlalchemy.orm import sessionmaker, declarative_base

# --- CONFIG ---
TOKEN = "8388735235:AAEdJPWlZxo9Vm5rVIYGsFIeJ44wWTuT3D0"
ADMIN_ID = 5522878843 # Thay bằng ID thật của bạn
DB_URL = "sqlite:///fb_monitor.db"

Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

# --- MODELS ---
class UserKey(Base):
    __tablename__ = 'keys'
    id = Column(Integer, primary_key=True)
    key_code = Column(String, unique=True)
    owner_id = Column(Integer, unique=True, nullable=True)
    expire_at = Column(DateTime)
    status = Column(String, default="active") # active, banned

class FacebookUID(Base):
    __tablename__ = 'uids'
    id = Column(Integer, primary_key=True)
    uid = Column(String)
    customer_name = Column(String)
    price = Column(Float)
    last_status = Column(String, default="DIE")
    created_at = Column(DateTime, default=datetime.datetime.now)
    done_at = Column(DateTime, nullable=True)
    added_by = Column(Integer)
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(engine)

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)

# --- CORE LOGIC ---
def is_valid_user(user_id):
    if user_id == ADMIN_ID: return True
    db = SessionLocal()
    key = db.query(UserKey).filter(UserKey.owner_id == user_id, UserKey.status == "active").first()
    valid = key and key.expire_at > datetime.datetime.now()
    db.close()
    return valid

# --- JOB: CHECK UID ---
async def check_uids_job(context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    active_uids = db.query(FacebookUID).filter(FacebookUID.is_active == True).all()
    
    async with httpx.AsyncClient() as client:
        for item in active_uids:
            # Giả lập check qua 1 service hoặc graph api
            # Ở đây tôi dùng placeholder logic
            current_status = "LIVE" # Logic check thực tế của bạn ở đây
            
            if item.last_status != current_status:
                old_status = item.last_status
                item.last_status = current_status
                
                if current_status == "LIVE":
                    item.done_at = datetime.datetime.now()
                    msg = (
                        f"✅ **DONE kèo:** `{item.uid}`\n"
                        f"👤 Khách: {item.customer_name}\n"
                        f"📅 Nhận: {item.created_at.strftime('%d/%m/%Y')}\n"
                        f"💰 Tiền: {item.price:,.0f}"
                    )
                    await context.bot.send_message(chat_id=item.added_by, text=msg, parse_mode="Markdown")
                db.commit()
    db.close()

# --- HANDLERS ---
async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_valid_user(user_id):
        return await update.message.reply_text("❌ Bạn không có Key hoặc Key hết hạn.")
    
    try:
        uid, name, price = context.args[0], context.args[1], float(context.args[2])
        db = SessionLocal()
        new_item = FacebookUID(uid=uid, customer_name=name, price=price, added_by=user_id)
        db.add(new_item)
        db.commit()
        db.close()
        await update.message.reply_text(f"🚀 Đã thêm UID: {uid}")
    except:
        await update.message.reply_text("⚠️ HD: /add_uid <uid> <tên> <tiền>")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = SessionLocal()
    items = db.query(FacebookUID).filter(FacebookUID.added_by == user_id, FacebookUID.is_active == True).all()
    live = len([i for i in items if i.last_status == "LIVE"])
    await update.message.reply_text(f"📊 Đang theo dõi: {len(items)}\n🟢 LIVE: {live}\n🔴 DIE: {len(items)-live}")
    db.close()

# --- ADMIN: EXCEL REPORT ---
async def export_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    db = SessionLocal()
    data = db.query(FacebookUID).all()
    
    df_detail = pd.DataFrame([{
        'UID': i.uid, 'Khách': i.customer_name, 'Tiền': i.price, 
        'Ngày Nhận': i.created_at, 'Ngày Done': i.done_at, 'Status': i.last_status
    } for i in data])

    file_name = "Bao_Cao_Doanh_Thu.xlsx"
    with pd.ExcelWriter(file_name) as writer:
        df_detail.to_excel(writer, sheet_name='Chi Tiet', index=False)
        # Sheet 1: Tổng kết
        summary = pd.DataFrame([{
            'Tổng UID': len(data),
            'Tổng Doanh Thu': df_detail['Tiền'].sum()
        }])
        summary.to_excel(writer, sheet_name='Tong Ket', index=False)

    await update.message.reply_document(document=open(file_name, 'rb'))
    db.close()

def main():
    app = Application.builder().token(TOKEN).build()
    
    # Job check mỗi 60 giây
    app.job_queue.run_repeating(check_uids_job, interval=60, first=10)
    
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("export", export_report))
    
    print("Bot is starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
