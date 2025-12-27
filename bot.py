import logging
import asyncio
import datetime
import secrets
import string
import pandas as pd
import httpx
from calendar import monthrange
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG
# ==========================================
TOKEN = "8388735235:AAEq-u7EuXROgfdBfucJYaQlAg9LlOfSO0k"
ADMIN_ID = 5522878843  # <--- THAY ID CỦA BẠN VÀO ĐÂY (Vào @userinfobot để lấy)
DB_URL = "sqlite:///fb_monitor_pro.db"

# ==========================================
# 2. DATABASE MODELS
# ==========================================
Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

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

# Cấu hình log để theo dõi trên VPS
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# 3. HÀM KIỂM TRA (HELPERS)
# ==========================================
def get_session():
    return SessionLocal()

def is_valid_user(user_id):
    if user_id == ADMIN_ID: return True
    db = get_session()
    key = db.query(UserKey).filter(UserKey.owner_id == user_id, UserKey.status == "active").first()
    valid = key and key.expire_at > datetime.datetime.now()
    db.close()
    return valid

async def check_fb_status_api(uid):
    """
    Logic check LIVE/DIE. Bạn có thể thay bằng API thật.
    Hiện tại mặc định trả về trạng thái ngẫu nhiên để test logic notify.
    """
    # GIẢ LẬP: Nếu UID kết thúc bằng số chẵn thì LIVE, lẻ thì DIE
    return "LIVE" if int(uid[-1]) % 2 == 0 else "DIE"

# ==========================================
# 4. CHỨC NĂNG THEO DÕI (JOB QUEUE)
# ==========================================
async def monitor_uids_job(context: ContextTypes.DEFAULT_TYPE):
    db = get_session()
    # Chỉ check những UID đang active và người thêm vẫn còn hạn Key
    uids = db.query(FacebookUID).filter(FacebookUID.is_active == True).all()
    
    for item in uids:
        if not is_valid_user(item.added_by):
            continue
            
        current_status = await check_fb_status_api(item.uid)
        
        # Chỉ thông báo khi ĐỔI trạng thái
        if item.last_status != current_status:
            old_status = item.last_status
            item.last_status = current_status
            
            # Nếu đổi sang LIVE -> DONE kèo
            if current_status == "LIVE":
                item.done_at = datetime.datetime.now()
                msg = (
                    f"✅ **DONE kèo:** `{item.uid}`\n"
                    f"👤 Khách hàng: {item.customer_name}\n"
                    f"📅 Ngày nhận: {item.created_at.strftime('%d/%m/%Y')}\n"
                    f"🏁 Ngày DONE: {item.done_at.strftime('%d/%m/%Y %H:%M')}\n"
                    f"💰 Số tiền: {item.price:,.0f} VND"
                )
            else: # Nếu đổi sang DIE
                msg = f"⚠️ **UID đổi trạng thái:** `{item.uid}`\n🔴 Trạng thái: **DIE**"

            try:
                await context.bot.send_message(chat_id=item.added_by, text=msg, parse_mode="Markdown")
                db.commit()
            except Exception as e:
                logger.error(f"Lỗi gửi tin nhắn cho {item.added_by}: {e}")
                
    db.close()

# ==========================================
# 5. LỆNH NGƯỜI DÙNG (USER COMMANDS)
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **FB Monitor System v2.0**\n\n"
        "Sử dụng các lệnh:\n"
        "👉 `/active <key>` - Kích hoạt bot\n"
        "👉 `/add_uid <uid> <tên> <số_tiền>` - Thêm kèo\n"
        "👉 `/status` - Xem trạng thái hiện tại\n"
        "👉 `/stats_today` - Doanh thu hôm nay\n"
        "👉 `/remove_uid <uid>` - Xóa kèo",
        parse_mode="Markdown"
    )

async def active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("⚠️ Nhập: `/active <key>`")
    db = get_session()
    key_obj = db.query(UserKey).filter(UserKey.key_code == context.args[0], UserKey.status == "active").first()
    
    if key_obj:
        if key_obj.owner_id and key_obj.owner_id != update.effective_user.id:
            return await update.message.reply_text("❌ Key này đã được người khác sử dụng.")
        key_obj.owner_id = update.effective_user.id
        db.commit()
        await update.message.reply_text("✅ Kích hoạt thành công!")
    else:
        await update.message.reply_text("❌ Key không tồn tại hoặc đã bị khóa.")
    db.close()

async def add_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_valid_user(user_id): return await update.message.reply_text("❌ Key hết hạn.")
    
    try:
        uid, name, price = context.args[0], context.args[1], float(context.args[2])
        db = get_session()
        db.add(FacebookUID(uid=uid, customer_name=name, price=price, added_by=user_id))
        db.commit()
        db.close()
        await update.message.reply_text(f"🚀 Đã thêm UID `{uid}` vào hệ thống theo dõi.")
    except:
        await update.message.reply_text("⚠️ Cú pháp: `/add_uid <uid> <tên_khách> <số_tiền>`")

async def status_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_session()
    uids = db.query(FacebookUID).filter(FacebookUID.added_by == user_id, FacebookUID.is_active == True).all()
    live = [i for i in uids if i.last_status == "LIVE"]
    await update.message.reply_text(
        f"📊 **Trạng thái hiện tại:**\n"
        f"- Tổng UID: {len(uids)}\n"
        f"🟢 LIVE: {len(live)}\n"
        f"🔴 DIE: {len(uids) - len(live)}",
        parse_mode="Markdown"
    )
    db.close()

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_session()
    today = datetime.datetime.now().date()
    done = db.query(FacebookUID).filter(
        FacebookUID.added_by == user_id, 
        func.date(FacebookUID.done_at) == today
    ).all()
    total = sum(i.price for i in done)
    await update.message.reply_text(f"💰 **Hôm nay:**\n- Kèo DONE: {len(done)}\n- Doanh thu: {total:,.0f} VND")
    db.close()

# ==========================================
# 6. LỆNH ADMIN
# ==========================================
async def create_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        days = int(context.args[0])
        new_key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
        db = get_session()
        exp = datetime.datetime.now() + datetime.timedelta(days=days)
        db.add(UserKey(key_code=new_key, expire_at=exp))
        db.commit()
        db.close()
        await update.message.reply_text(f"🔑 Key: `{new_key}`\n⏳ Hạn: {days} ngày")
    except:
        await update.message.reply_text("⚠️ `/create_key <số_ngày>`")

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    db = get_session()
    uids = db.query(FacebookUID).all()
    
    # Sheet 1: Tổng kết
    df_sum = pd.DataFrame([{
        'Tổng UID': len(uids),
        'Kèo DONE': len([i for i in uids if i.done_at]),
        'Tổng Tiền': sum(i.price for i in uids if i.done_at)
    }])
    
    # Sheet 2: Chi tiết
    df_detail = pd.DataFrame([{
        'UID': i.uid, 'Khách': i.customer_name, 'Tiền': i.price,
        'Ngày Nhận': i.created_at, 'Ngày DONE': i.done_at, 'Status': i.last_status
    } for i in uids])

    file_name = "Bao_cao_he_thong.xlsx"
    with pd.ExcelWriter(file_name) as writer:
        df_sum.to_excel(writer, sheet_name='TongKet', index=False)
        df_detail.to_excel(writer, sheet_name='ChiTiet', index=False)
    
    await update.message.reply_document(document=open(file_name, 'rb'))
    db.close()

# ==========================================
# 7. KHỞI CHẠY (MAIN)
# ==========================================
def main():
    # Sử dụng defaults để bot hoạt động mượt hơn
    app = Application.builder().token(TOKEN).build()
    
    # Đăng ký Job lặp lại 1 phút/lần
    if app.job_queue:
        app.job_queue.run_repeating(monitor_uids_job, interval=60, first=10)
    
    # USER HANDLERS
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("active", active))
    app.add_handler(CommandHandler("add_uid", add_uid))
    app.add_handler(CommandHandler("status", status_now))
    app.add_handler(CommandHandler("stats_today", stats_today))
    
    # ADMIN HANDLERS
    app.add_handler(CommandHandler("create_key", create_key))
    app.add_handler(CommandHandler("export", export_excel))

    print("--- BOT ĐANG CHẠY ---")
    # run_polling tự động xử lý event loop chuẩn v20+
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
