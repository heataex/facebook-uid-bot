import logging
import asyncio
import datetime
import secrets
import string
import pandas as pd
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, func
from sqlalchemy.orm import sessionmaker, declarative_base

# --- CONFIG ---
TOKEN = "8388735235:AAG0jG2-gNmkaxUG7rcF4D3vqAfaoOiOjEk"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN VÀO ĐÂY
DB_URL = "sqlite:///fb_monitor_final.db"

Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

class FacebookUID(Base):
    __tablename__ = 'uids'
    id = Column(Integer, primary_key=True)
    uid = Column(String)
    customer_name = Column(String)
    price = Column(Float)
    last_status = Column(String)
    is_active = Column(Boolean, default=True)
    added_by = Column(Integer)
    created_at = Column(DateTime, default=datetime.datetime.now)
    done_at = Column(DateTime, nullable=True)

Base.metadata.create_all(engine)

# --- LOGIC CHECK THẬT ---
async def check_fb_status_api(uid: str) -> str:
    url = f"https://graph.facebook.com/{uid}/picture?type=normal"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            r = await client.get(url)
            # Nếu LIVE sẽ redirect (302) tới ảnh cá nhân
            return "LIVE" if r.status_code == 302 else "DIE"
    except: return "DIE"

# --- COMMANDS ---
async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid, name, price = context.args[0], context.args[1], float(context.args[2])
        # Check trạng thái lúc thêm
        current = await check_fb_status_api(uid)
        
        db = SessionLocal()
        db.add(FacebookUID(uid=uid, customer_name=name, price=price, 
                           last_status=current, added_by=update.effective_user.id))
        db.commit()
        db.close()
        await update.message.reply_text(f"🚀 Thêm thành công! Trạng thái hiện tại: **{current}**", parse_mode="Markdown")
    except:
        await update.message.reply_text("⚠️ `/add_uid <uid> <tên> <tiền>`")

async def list_uids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    uids = db.query(FacebookUID).filter(FacebookUID.added_by == update.effective_user.id, FacebookUID.is_active == True).all()
    if not uids: return await update.message.reply_text("Danh sách trống.")
    
    msg = "📋 **Danh sách UID đang theo dõi:**\n"
    for i in uids:
        msg += f"- `{i.uid}` | {i.customer_name} | {i.last_status}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")
    db.close()

async def remove_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    uid_rm = context.args[0]
    db = SessionLocal()
    item = db.query(FacebookUID).filter(FacebookUID.uid == uid_rm, FacebookUID.added_by == update.effective_user.id).first()
    if item:
        item.is_active = False
        db.commit()
        await update.message.reply_text(f"🗑 Đã xóa UID `{uid_rm}`")
    db.close()

# --- MONITOR JOB ---
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    uids = db.query(FacebookUID).filter(FacebookUID.is_active == True).all()
    for item in uids:
        new_status = await check_fb_status_api(item.uid)
        if new_status != item.last_status:
            item.last_status = new_status
            item.done_at = datetime.datetime.now()
            msg = f"🔔 **THÔNG BÁO THAY ĐỔI**\n🆔 UID: `{item.uid}`\n👤 Khách: {item.customer_name}\n🔄 Trạng thái mới: **{new_status}**"
            await context.bot.send_message(chat_id=item.added_by, text=msg, parse_mode="Markdown")
            db.commit()
    db.close()

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(monitor_job, interval=60)
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("list", list_uids))
    app.add_handler(CommandHandler("remove_uid", remove_uid))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
