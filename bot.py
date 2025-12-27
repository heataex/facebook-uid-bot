#!/usr/bin/env python3
"""
FB KÈO BOT - Telegram Bot theo dõi trạng thái UID Facebook
Version 4.0 - Update tracking dài hạn & báo cáo nâng cao
"""

import asyncio
import logging
import sqlite3
import secrets
import string
import json
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from io import BytesIO

from telegram import (
    Update, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    InputFile
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    ContextTypes, 
    filters
)
from telegram.constants import ParseMode

# ==================== CẤU HÌNH ====================
BOT_TOKEN = "8388735235:AAEdJPWlZxo9Vm5rVIYGsFIeJ44wWTuT3D0"
CHECK_INTERVAL = 60  # Giây
DB_FILE = "fb_keo_bot.db"
DEFAULT_UID_LIMIT = 10
DEFAULT_EXPIRE_DAYS = 30
ADMIN_ID = 5522878843  # Thay bằng ID ADMIN thực tế

# ==================== DATABASE UPDATE ====================
def init_database():
    """Khởi tạo database SQLite với các bảng mới"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Bảng users (có sẵn)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            role TEXT DEFAULT 'USER',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng api_keys
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            user_id INTEGER,
            uid_limit INTEGER DEFAULT 10,
            expired_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE',
            note TEXT,
            assigned_to INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (assigned_to) REFERENCES users (id)
        )
    ''')
    
    # Bảng uids - THÊM TRƯỜNG MỚI: long_tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            user_id INTEGER,
            key_id INTEGER,
            status TEXT DEFAULT 'DIE',
            tracking_status TEXT DEFAULT 'ACTIVE',
            customer_name TEXT,
            amount INTEGER DEFAULT 1000000,
            note TEXT,
            received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            first_live_date TIMESTAMP,
            done_date TIMESTAMP,
            last_checked TIMESTAMP,
            removed_at TIMESTAMP,
            long_tracking BOOLEAN DEFAULT 1,  -- MỚI: Theo dõi dài hạn
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (key_id) REFERENCES api_keys (id)
        )
    ''')
    
    # Bảng transactions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid_id INTEGER,
            user_id INTEGER,
            amount INTEGER,
            transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            period TEXT,  -- daily, weekly, monthly
            FOREIGN KEY (uid_id) REFERENCES uids (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng key_logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS key_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (key_id) REFERENCES api_keys (id),
            FOREIGN KEY (admin_id) REFERENCES users (id)
        )
    ''')
    
    # ==================== BẢNG MỚI ====================
    # Bảng uid_logs - Ghi log thay đổi trạng thái UID
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uid_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid_id INTEGER,
            old_status TEXT,
            new_status TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            details TEXT,
            FOREIGN KEY (uid_id) REFERENCES uids (id)
        )
    ''')
    
    # Bảng status_history - Lịch sử trạng thái hàng ngày
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            total_uids INTEGER,
            live_uids INTEGER,
            die_uids INTEGER,
            paused_uids INTEGER,
            record_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Bảng revenue_logs - Log doanh thu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS revenue_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            period TEXT,  -- daily, weekly, monthly
            record_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Index cho performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_uid_logs_uid ON uid_logs(uid_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status_history_date ON status_history(record_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_revenue_period ON revenue_logs(period, record_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_uids_long_tracking ON uids(long_tracking)')
    
    conn.commit()
    conn.close()

# ==================== MODELS UPDATE ====================
@dataclass
class User:
    id: int
    telegram_id: int
    username: str
    role: str
    
@dataclass
class APIKey:
    id: int
    key: str
    user_id: int
    uid_limit: int
    expired_at: datetime
    status: str
    note: str
    assigned_to: Optional[int]
    
@dataclass
class UID:
    id: int
    uid: str
    user_id: int
    key_id: int
    status: str
    tracking_status: str
    customer_name: str
    amount: int
    note: str
    received_date: datetime
    first_live_date: Optional[datetime]
    done_date: Optional[datetime]
    last_checked: datetime
    removed_at: Optional[datetime]
    long_tracking: bool

# ==================== DATABASE HELPER UPDATE ====================
class Database:
    @staticmethod
    def get_conn():
        return sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
    
    @staticmethod
    def dict_factory(cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
    
    # ==================== USER ====================
    @staticmethod
    def get_user(telegram_id: int) -> Optional[User]:
        conn = Database.get_conn()
        conn.row_factory = Database.dict_factory
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return User(**row)
        return None
    
    @staticmethod
    def is_admin(telegram_id: int) -> bool:
        user = Database.get_user(telegram_id)
        return user and user.role == "ADMIN" if user else False
    
    # ==================== STATUS HIỆN TẠI ====================
    @staticmethod
    def get_current_status(user_id: int = None, is_admin: bool = False) -> Dict:
        """Lấy trạng thái HIỆN TẠI (không tính DONE)"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        if is_admin and user_id is None:
            # ADMIN xem toàn hệ thống
            cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'LIVE' THEN 1 ELSE 0 END) as live,
                    SUM(CASE WHEN status = 'DIE' THEN 1 ELSE 0 END) as die,
                    SUM(CASE WHEN tracking_status = 'PAUSED' THEN 1 ELSE 0 END) as paused
                FROM uids 
                WHERE tracking_status IN ('ACTIVE', 'PAUSED')
            ''')
        elif user_id:
            # USER xem cá nhân
            cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'LIVE' THEN 1 ELSE 0 END) as live,
                    SUM(CASE WHEN status = 'DIE' THEN 1 ELSE 0 END) as die,
                    SUM(CASE WHEN tracking_status = 'PAUSED' THEN 1 ELSE 0 END) as paused
                FROM uids 
                WHERE user_id = ? AND tracking_status IN ('ACTIVE', 'PAUSED')
            ''', (user_id,))
        else:
            return {"total": 0, "live": 0, "die": 0, "paused": 0}
        
        row = cursor.fetchone()
        conn.close()
        
        return {
            "total": row[0] or 0,
            "live": row[1] or 0,
            "die": row[2] or 0,
            "paused": row[3] or 0
        }
    
    # ==================== STATS THEO THỜI GIAN ====================
    @staticmethod
    def get_stats_period(user_id: int = None, period: str = "today", is_admin: bool = False) -> Dict:
        """Lấy thống kê DONE theo thời gian"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        # Xác định thời gian
        now = datetime.now()
        if period == "today":
            date_condition = "DATE(u.done_date) = DATE('now')"
            period_text = "Hôm nay"
        elif period == "week":
            date_condition = "strftime('%Y-%W', u.done_date) = strftime('%Y-%W', 'now')"
            period_text = "Tuần này"
        elif period == "month":
            date_condition = "strftime('%Y-%m', u.done_date) = strftime('%Y-%m', 'now')"
            period_text = "Tháng này"
        else:
            date_condition = "DATE(u.done_date) = DATE('now')"
            period_text = "Hôm nay"
        
        if is_admin and user_id is None:
            # ADMIN xem toàn hệ thống
            cursor.execute(f'''
                SELECT 
                    COUNT(DISTINCT u.id) as done_count,
                    COALESCE(SUM(t.amount), 0) as total_amount,
                    GROUP_CONCAT(DISTINCT u.uid) as done_uids
                FROM uids u
                LEFT JOIN transactions t ON u.id = t.uid_id
                WHERE u.status = 'LIVE' 
                AND u.done_date IS NOT NULL
                AND {date_condition}
            ''')
        elif user_id:
            # USER xem cá nhân
            cursor.execute(f'''
                SELECT 
                    COUNT(DISTINCT u.id) as done_count,
                    COALESCE(SUM(t.amount), 0) as total_amount,
                    GROUP_CONCAT(DISTINCT u.uid) as done_uids
                FROM uids u
                LEFT JOIN transactions t ON u.id = t.uid_id
                WHERE u.user_id = ? 
                AND u.status = 'LIVE' 
                AND u.done_date IS NOT NULL
                AND {date_condition}
            ''', (user_id,))
        else:
            return {
                "period": period_text,
                "done_count": 0,
                "total_amount": 0,
                "done_uids": []
            }
        
        row = cursor.fetchone()
        conn.close()
        
        done_uids = row[2].split(',') if row[2] else []
        
        return {
            "period": period_text,
            "done_count": row[0] or 0,
            "total_amount": row[1] or 0,
            "done_uids": done_uids[:10],  # Chỉ lấy 10 UID đầu
            "total_done_uids": len(done_uids)
        }
    
    # ==================== LƯU LOG THAY ĐỔI TRẠNG THÁI ====================
    @staticmethod
    def log_uid_status_change(uid_id: int, old_status: str, new_status: str, details: str = ""):
        """Ghi log thay đổi trạng thái UID"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO uid_logs (uid_id, old_status, new_status, details)
            VALUES (?, ?, ?, ?)
        ''', (uid_id, old_status, new_status, details))
        
        conn.commit()
        conn.close()
    
    # ==================== LƯU LỊCH SỬ TRẠNG THÁI HÀNG NGÀY ====================
    @staticmethod
    def save_daily_status(user_id: int):
        """Lưu trạng thái hàng ngày của user"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        # Lấy trạng thái hiện tại
        status = Database.get_current_status(user_id)
        
        # Kiểm tra đã lưu hôm nay chưa
        cursor.execute('''
            SELECT COUNT(*) FROM status_history 
            WHERE user_id = ? AND record_date = DATE('now')
        ''', (user_id,))
        
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO status_history 
                (user_id, total_uids, live_uids, die_uids, paused_uids)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, status['total'], status['live'], status['die'], status['paused']))
        
        conn.commit()
        conn.close()
    
    # ==================== LƯU DOANH THU ====================
    @staticmethod
    def save_revenue_log(user_id: int, amount: int, period: str):
        """Lưu log doanh thu"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO revenue_logs (user_id, amount, period)
            VALUES (?, ?, ?)
        ''', (user_id, amount, period))
        
        conn.commit()
        conn.close()
    
    # ==================== UID LONG TRACKING ====================
    @staticmethod
    def update_uid_status_long_tracking(uid_id: int, new_status: str):
        """Cập nhật trạng thái UID với tracking dài hạn"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        # Lấy thông tin cũ
        cursor.execute("SELECT status, first_live_date FROM uids WHERE id = ?", (uid_id,))
        old_status, first_live_date = cursor.fetchone()
        
        now = datetime.now()
        
        if new_status == "LIVE" and old_status == "DIE":
            # DIE -> LIVE: Đây là DONE
            done_date = now
            if not first_live_date:
                first_live_date = now
        else:
            done_date = None
        
        # Cập nhật
        cursor.execute('''
            UPDATE uids 
            SET status = ?, last_checked = ?, done_date = ?, first_live_date = ?
            WHERE id = ?
        ''', (new_status, now, done_date, first_live_date, uid_id))
        
        # Nếu DONE (DIE -> LIVE), thêm vào transactions
        if new_status == "LIVE" and old_status == "DIE":
            cursor.execute("SELECT amount, user_id FROM uids WHERE id = ?", (uid_id,))
            amount, user_id = cursor.fetchone()
            
            cursor.execute('''
                INSERT INTO transactions (uid_id, user_id, amount, transaction_date)
                VALUES (?, ?, ?, ?)
            ''', (uid_id, user_id, amount, now))
            
            # Lưu log doanh thu
            Database.save_revenue_log(user_id, amount, "daily")
        
        # Log thay đổi trạng thái
        Database.log_uid_status_change(
            uid_id, 
            old_status, 
            new_status,
            f"Auto check at {now}"
        )
        
        conn.commit()
        conn.close()
        
        # Trả về thông tin để gửi notify
        return {
            "uid_id": uid_id,
            "old_status": old_status,
            "new_status": new_status,
            "is_done": (old_status == "DIE" and new_status == "LIVE")
        }
    
    # ==================== LẤY DỮ LIỆU BÁO CÁO THÁNG ====================
    @staticmethod
    def get_monthly_report_data(month: int = None, year: int = None):
        """Lấy dữ liệu báo cáo tháng"""
        conn = Database.get_conn()
        conn.row_factory = Database.dict_factory
        cursor = conn.cursor()
        
        if month is None:
            month = datetime.now().month
        if year is None:
            year = datetime.now().year
        
        # Tổng kết
        cursor.execute('''
            SELECT 
                COUNT(DISTINCT u.id) as total_uids,
                SUM(CASE WHEN u.status = 'LIVE' AND strftime('%Y-%m', u.done_date) = ? THEN 1 ELSE 0 END) as total_done,
                COALESCE(SUM(CASE WHEN strftime('%Y-%m', u.done_date) = ? THEN t.amount ELSE 0 END), 0) as total_revenue
            FROM uids u
            LEFT JOIN transactions t ON u.id = t.uid_id
            WHERE strftime('%Y-%m', u.received_date) <= ?
        ''', (f"{year}-{month:02d}", f"{year}-{month:02d}", f"{year}-{month:02d}"))
        
        summary = cursor.fetchone()
        
        # Chi tiết kèo DONE
        cursor.execute('''
            SELECT 
                u.uid,
                u.customer_name,
                u.amount,
                u.received_date,
                u.done_date,
                k.key,
                us.username
            FROM uids u
            LEFT JOIN api_keys k ON u.key_id = k.id
            LEFT JOIN users us ON u.user_id = us.id
            WHERE u.status = 'LIVE' 
            AND strftime('%Y-%m', u.done_date) = ?
            ORDER BY u.done_date DESC
        ''', (f"{year}-{month:02d}",))
        
        details = cursor.fetchall()
        
        # Thống kê theo user
        cursor.execute('''
            SELECT 
                us.username,
                COUNT(DISTINCT u.id) as uids_count,
                SUM(CASE WHEN u.status = 'LIVE' AND strftime('%Y-%m', u.done_date) = ? THEN 1 ELSE 0 END) as done_count,
                COALESCE(SUM(CASE WHEN strftime('%Y-%m', u.done_date) = ? THEN u.amount ELSE 0 END), 0) as revenue
            FROM users us
            LEFT JOIN uids u ON us.id = u.user_id
            WHERE us.role = 'USER'
            GROUP BY us.id
            ORDER BY revenue DESC
        ''', (f"{year}-{month:02d}", f"{year}-{month:02d}"))
        
        users_stats = cursor.fetchall()
        
        conn.close()
        
        return {
            "summary": summary,
            "details": details,
            "users_stats": users_stats,
            "month": month,
            "year": year
        }

# ==================== EXCEL REPORT GENERATOR ====================
class ExcelReport:
    @staticmethod
    def generate_monthly_report(month: int, year: int) -> BytesIO:
        """Tạo file Excel báo cáo tháng"""
        # Lấy dữ liệu
        data = Database.get_monthly_report_data(month, year)
        
        # Tạo workbook
        wb = Workbook()
        
        # ========== SHEET 1: TỔNG KẾT ==========
        ws1 = wb.active
        ws1.title = "Tổng kết"
        
        # Header style
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # Data style
        data_font = Font(size=11)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Tiêu đề
        ws1.merge_cells('A1:E1')
        ws1['A1'] = f"BÁO CÁO TỔNG KẾT THÁNG {month}/{year}"
        ws1['A1'].font = Font(size=14, bold=True, color="366092")
        ws1['A1'].alignment = Alignment(horizontal="center")
        
        # Tổng kết
        summary_data = [
            ["CHỈ SỐ", "GIÁ TRỊ", "GHI CHÚ"],
            ["Tổng số UID", data['summary']['total_uids'], "Tất cả UID đang theo dõi"],
            ["Tổng kèo DONE", data['summary']['total_done'], f"Tháng {month}/{year}"],
            ["Tổng doanh thu", f"{data['summary']['total_revenue']:,}đ", f"Tháng {month}/{year}"],
            ["Tỷ lệ DONE", f"{(data['summary']['total_done']/data['summary']['total_uids']*100):.1f}%" if data['summary']['total_uids'] > 0 else "0%", "DONE/Tổng UID"]
        ]
        
        # Write summary
        for row_idx, row in enumerate(summary_data, start=3):
            for col_idx, value in enumerate(row, start=1):
                cell = ws1.cell(row=row_idx, column=col_idx, value=value)
                cell.border = border
                if row_idx == 3:  # Header
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = header_alignment
                else:
                    cell.font = data_font
        
        # Auto size columns
        for col in ws1.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws1.column_dimensions[column].width = adjusted_width
        
        # ========== SHEET 2: CHI TIẾT KÈO DONE ==========
        ws2 = wb.create_sheet(title="Chi tiết kèo DONE")
        
        # Header
        headers = ["UID", "Khách hàng", "Số tiền", "Ngày nhận", "Ngày DONE", "KEY", "User"]
        for col_idx, header in enumerate(headers, start=1):
            cell = ws2.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = border
        
        # Data
        for row_idx, detail in enumerate(data['details'], start=2):
            ws2.cell(row=row_idx, column=1, value=detail['uid']).border = border
            ws2.cell(row=row_idx, column=2, value=detail['customer_name']).border = border
            ws2.cell(row=row_idx, column=3, value=detail['amount']).border = border
            ws2.cell(row=row_idx, column=4, value=detail['received_date']).border = border
            ws2.cell(row=row_idx, column=5, value=detail['done_date']).border = border
            ws2.cell(row=row_idx, column=6, value=detail['key'][:12] + "..." if detail['key'] else "").border = border
            ws2.cell(row=row_idx, column=7, value=detail['username']).border = border
        
        # Auto size columns
        for col in ws2.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 30)
            ws2.column_dimensions[column].width = adjusted_width
        
        # ========== SHEET 3: THỐNG KÊ USER ==========
        ws3 = wb.create_sheet(title="Thống kê User")
        
        # Header
        user_headers = ["Username", "Số UID", "Kèo DONE", "Doanh thu", "Tỷ lệ DONE"]
        for col_idx, header in enumerate(user_headers, start=1):
            cell = ws3.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = border
        
        # Data
        for row_idx, user_stat in enumerate(data['users_stats'], start=2):
            ws3.cell(row=row_idx, column=1, value=user_stat['username']).border = border
            ws3.cell(row=row_idx, column=2, value=user_stat['uids_count']).border = border
            ws3.cell(row=row_idx, column=3, value=user_stat['done_count']).border = border
            ws3.cell(row=row_idx, column=4, value=user_stat['revenue']).border = border
            ratio = (user_stat['done_count'] / user_stat['uids_count'] * 100) if user_stat['uids_count'] > 0 else 0
            ws3.cell(row=row_idx, column=5, value=f"{ratio:.1f}%").border = border
        
        # Auto size columns
        for col in ws3.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 20)
            ws3.column_dimensions[column].width = adjusted_width
        
        # Save to BytesIO
        excel_file = BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)
        
        return excel_file

# ==================== UI FORMATTER UPDATE ====================
class UIFormatter:
    @staticmethod
    def format_status(status: Dict) -> str:
        """Format message trạng thái HIỆN TẠI"""
        return f"""
📊 *TRẠNG THÁI HIỆN TẠI*
━━━━━━━━━━━━━━━━━━
• 📈 Tổng UID: *{status['total']}*
• 🟢 LIVE: *{status['live']}*
• 🔴 DIE: *{status['die']}*
• ⏸️ PAUSED: *{status['paused']}*
━━━━━━━━━━━━━━━━━━
*Lưu ý:* Chỉ tính UID đang theo dõi (ACTIVE/PAUSED)
        """.strip()
    
    @staticmethod
    def format_stats(stats: Dict) -> str:
        """Format message thống kê DONE theo thời gian"""
        period = stats.get('period', 'Hôm nay')
        
        return f"""
📈 *THỐNG KÊ {period.upper()}*
━━━━━━━━━━━━━━━━━━
• ✅ Kèo DONE: *{stats['done_count']}*
• 💰 Tổng tiền: *{stats['total_amount']:,}đ*
• 🆔 UID DONE: *{stats['total_done_uids']}*
━━━━━━━━━━━━━━━━━━
*UID DONE gần nhất:*
{', '.join([f'`{uid}`' for uid in stats['done_uids']]) if stats['done_uids'] else 'Chưa có'}
━━━━━━━━━━━━━━━━━━
        """.strip()
    
    @staticmethod
    def format_time_period_menu() -> str:
        """Menu chọn thời gian cho stats"""
        return """
📅 *CHỌN THỜI GIAN THỐNG KÊ*
━━━━━━━━━━━━━━━━━━
Vui lòng chọn thời gian bạn muốn xem thống kê:
        """.strip()

# ==================== KEYBOARDS UPDATE ====================
class Keyboards:
    @staticmethod
    def time_period_menu():
        keyboard = [
            [
                InlineKeyboardButton("📅 Hôm nay", callback_data="stats_today"),
                InlineKeyboardButton("📆 Tuần này", callback_data="stats_week")
            ],
            [
                InlineKeyboardButton("📊 Tháng này", callback_data="stats_month"),
                InlineKeyboardButton("⬅️ Quay lại", callback_data="back_main")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def main_menu():
        keyboard = [
            [
                InlineKeyboardButton("📊 Trạng thái", callback_data="status"),
                InlineKeyboardButton("📈 Thống kê", callback_data="stats_menu")
            ],
            [
                InlineKeyboardButton("➕ Thêm UID", callback_data="add_uid"),
                InlineKeyboardButton("📋 Danh sách UID", callback_data="list_uids")
            ],
            [
                InlineKeyboardButton("🔑 KEY của tôi", callback_data="my_keys"),
                InlineKeyboardButton("📄 Báo cáo DONE", callback_data="report_today")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

# ==================== MAIN BOT CLASS UPDATE ====================
class FBBot:
    def __init__(self):
        self.application = None
        self.user_sessions = {}
        self.checker_running = True
    
    # ==================== COMMAND HANDLERS ====================
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /status - Hiển thị trạng thái HIỆN TẠI"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User không tồn tại")
            return
        
        is_admin = user.role == "ADMIN"
        
        # Lấy trạng thái hiện tại
        if is_admin and context.args and context.args[0] == "all":
            status = Database.get_current_status(is_admin=True)
        elif is_admin:
            # ADMIN mặc định xem toàn hệ thống
            status = Database.get_current_status(is_admin=True)
        else:
            status = Database.get_current_status(user.id)
        
        await update.message.reply_text(
            UIFormatter.format_status(status),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /stats - Hiển thị menu chọn thời gian"""
        await update.message.reply_text(
            UIFormatter.format_time_period_menu(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.time_period_menu()
        )
    
    async def stats_today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /stats_today - Thống kê hôm nay"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User không tồn tại")
            return
        
        is_admin = user.role == "ADMIN"
        
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="today",
            is_admin=is_admin
        )
        
        await update.message.reply_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def stats_week_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /stats_week - Thống kê tuần này"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User không tồn tại")
            return
        
        is_admin = user.role == "ADMIN"
        
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="week",
            is_admin=is_admin
        )
        
        await update.message.reply_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def stats_month_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /stats_month - Thống kê tháng này"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User không tồn tại")
            return
        
        is_admin = user.role == "ADMIN"
        
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="month",
            is_admin=is_admin
        )
        
        await update.message.reply_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    # ==================== CALLBACK HANDLERS ====================
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Xử lý callback từ inline keyboard"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data == "status":
            await self.show_status(update, context)
        elif data == "stats_menu":
            await self.show_stats_menu(update, context)
        elif data == "stats_today":
            await self.show_stats_today(update, context)
        elif data == "stats_week":
            await self.show_stats_week(update, context)
        elif data == "stats_month":
            await self.show_stats_month(update, context)
        elif data == "back_main":
            await self.show_main_menu(update, context)
        elif data == "add_uid":
            # Gọi hàm bắt đầu ConversationHandler thêm UID
            await self.add_uid_start(update, context)
        elif data == "list_uids":
            # Gọi hàm hiển thị danh sách UID
            await self.show_uid_list(update, context)


    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị trạng thái qua callback"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            return
        
        is_admin = user.role == "ADMIN"
        status = Database.get_current_status(user.id if not is_admin else None, is_admin)
        
        await update.callback_query.message.edit_text(
            UIFormatter.format_status(status),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def show_stats_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị menu stats qua callback"""
        await update.callback_query.message.edit_text(
            UIFormatter.format_time_period_menu(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.time_period_menu()
        )
    
    async def show_stats_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị stats hôm nay qua callback"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            return
        
        is_admin = user.role == "ADMIN"
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="today",
            is_admin=is_admin
        )
        
        await update.callback_query.message.edit_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def show_stats_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị stats tuần này qua callback"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            return
        
        is_admin = user.role == "ADMIN"
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="week",
            is_admin=is_admin
        )
        
        await update.callback_query.message.edit_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def show_stats_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị stats tháng này qua callback"""
        user_id = update.effective_user.id
        user = Database.get_user(user_id)
        
        if not user:
            return
        
        is_admin = user.role == "ADMIN"
        stats = Database.get_stats_period(
            user_id=user.id if not is_admin else None,
            period="month",
            is_admin=is_admin
        )
        
        await update.callback_query.message.edit_text(
            UIFormatter.format_stats(stats),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hiển thị menu chính"""
        await update.callback_query.message.edit_text(
            "📱 *MENU CHÍNH*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=Keyboards.main_menu()
        )
    
    # ==================== SCHEDULER UPDATE ====================
    async def check_uids_long_tracking(self, context: ContextTypes.DEFAULT_TYPE):
        """Check UID với tracking dài hạn"""
        if not self.checker_running:
            return
        
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        # Chỉ lấy UID có tracking_status = 'ACTIVE' và long_tracking = 1
        cursor.execute('''
            SELECT u.id, u.uid, u.status, u.user_id, us.telegram_id
            FROM uids u
            JOIN users us ON u.user_id = us.id
            WHERE u.tracking_status = 'ACTIVE' 
            AND u.long_tracking = 1
            AND u.removed_at IS NULL
        ''')
        
        uids = cursor.fetchall()
        conn.close()
        
        for uid_id, uid_str, current_status, user_id, telegram_id in uids:
            # TODO: Thực hiện check UID thực tế với Facebook API
            new_status = await self.check_facebook_uid(uid_str)
            
            if new_status != current_status:
                # Cập nhật với tracking dài hạn
                result = Database.update_uid_status_long_tracking(uid_id, new_status)
                
                # Gửi notify nếu có thay đổi
                if result and result["is_done"]:
                    await self.send_done_notification(uid_id, telegram_id, context)
                elif result:
                    await self.send_status_change_notification(
                        uid_id, telegram_id, 
                        result["old_status"], result["new_status"], 
                        context
                    )
        
        # Lưu trạng thái hàng ngày cho tất cả user
        await self.save_all_users_daily_status()
    
    async def check_facebook_uid(self, uid: str) -> str:
        """Check trạng thái UID trên Facebook - LOGIC MẪU"""
        import random
        return "LIVE" if random.random() > 0.3 else "DIE"
    
    async def send_done_notification(self, uid_id: int, telegram_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Gửi thông báo khi UID DONE"""
        conn = Database.get_conn()
        conn.row_factory = Database.dict_factory
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT u.* FROM uids u WHERE u.id = ?
        ''', (uid_id,))
        
        uid_data = cursor.fetchone()
        conn.close()
        
        if uid_data:
            uid_data['received_date'] = datetime.fromisoformat(uid_data['received_date']) if uid_data['received_date'] else None
            uid_data['done_date'] = datetime.fromisoformat(uid_data['done_date']) if uid_data['done_date'] else None
            
            done_msg = f"""
━━━━━━━━━━━━━━━━━━
✅ *DONE KÈO FACEBOOK*
━━━━━━━━━━━━━━━━━━
👤 *Khách hàng:* {uid_data['customer_name']}
🆔 *UID:* `{uid_data['uid']}`
💰 *Số tiền:* {uid_data['amount']:,}đ

📅 *Ngày nhận:* {uid_data['received_date'].strftime('%d/%m/%Y %H:%M')}
🎉 *Ngày DONE:* {datetime.now().strftime('%d/%m/%Y %H:%M')}
━━━━━━━━━━━━━━━━━━
            """.strip()
            
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=done_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                print(f"Error sending done notification: {e}")
    
    async def send_status_change_notification(self, uid_id: int, telegram_id: int, 
                                            old_status: str, new_status: str, 
                                            context: ContextTypes.DEFAULT_TYPE):
        """Gửi thông báo khi UID thay đổi trạng thái (không phải DONE)"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT uid, customer_name FROM uids WHERE id = ?", (uid_id,))
        uid_data = cursor.fetchone()
        conn.close()
        
        if uid_data:
            uid_str, customer_name = uid_data
            
            emoji_old = "🟢" if old_status == "LIVE" else "🔴"
            emoji_new = "🟢" if new_status == "LIVE" else "🔴"
            
            status_msg = f"""
🔄 *THAY ĐỔI TRẠNG THÁI UID*
━━━━━━━━━━━━━━━━━━
👤 Khách hàng: {customer_name}
🆔 UID: `{uid_str}`

{emoji_old} Trạng thái cũ: {old_status}
{emoji_new} Trạng thái mới: {new_status}

⏰ Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M')}
━━━━━━━━━━━━━━━━━━
            """.strip()
            
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=status_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                print(f"Error sending status change notification: {e}")
    
    async def save_all_users_daily_status(self):
        """Lưu trạng thái hàng ngày cho tất cả user"""
        conn = Database.get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall()
        conn.close()
        
        for (user_id,) in users:
            Database.save_daily_status(user_id)
    
    # ==================== MONTHLY REPORT SCHEDULER ====================
    async def generate_monthly_report(self, context: ContextTypes.DEFAULT_TYPE):
        """Tự động tạo và gửi báo cáo tháng"""
        now = datetime.now()
        
        # Kiểm tra có phải là 23:59 ngày cuối tháng không
        if now.hour == 23 and now.minute == 59:
            # Tính tháng trước (vì báo cáo cho tháng vừa kết thúc)
            if now.month == 1:
                report_month = 12
                report_year = now.year - 1
            else:
                report_month = now.month - 1
                report_year = now.year
            
            # Tạo file Excel
            excel_file = ExcelReport.generate_monthly_report(report_month, report_year)
            
            # Gửi cho ADMIN
            try:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=InputFile(
                        excel_file, 
                        filename=f"report_{report_month:02d}_{report_year}.xlsx"
                    ),
                    caption=f"📊 *BÁO CÁO TỔNG KẾT THÁNG {report_month}/{report_year}*\n\n"
                            f"Được tạo tự động lúc: {now.strftime('%d/%m/%Y %H:%M')}",
                    parse_mode=ParseMode.MARKDOWN
                )
                print(f"✅ Đã gửi báo cáo tháng {report_month}/{report_year} cho ADMIN")
            except Exception as e:
                print(f"❌ Lỗi gửi báo cáo tháng: {e}")
    
    async def manual_monthly_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /report_month - Tạo báo cáo tháng thủ công (ADMIN ONLY)"""
        user_id = update.effective_user.id
        
        if not Database.is_admin(user_id):
            await update.message.reply_text("❌ Chỉ ADMIN mới có quyền này")
            return
        
        # Lấy tháng từ args, mặc định là tháng trước
        now = datetime.now()
        if context.args and len(context.args) >= 2:
            try:
                report_month = int(context.args[0])
                report_year = int(context.args[1])
            except ValueError:
                await update.message.reply_text("⚠️ Sử dụng: `/report_month <tháng> <năm>`")
                return
        else:
            if now.month == 1:
                report_month = 12
                report_year = now.year - 1
            else:
                report_month = now.month - 1
                report_year = now.year
        
        await update.message.reply_text(
            f"⏳ Đang tạo báo cáo tháng {report_month}/{report_year}..."
        )
        
        # Tạo file Excel
        excel_file = ExcelReport.generate_monthly_report(report_month, report_year)
        
        # Gửi file
        await update.message.reply_document(
            document=InputFile(
                excel_file, 
                filename=f"report_{report_month:02d}_{report_year}.xlsx"
            ),
            caption=f"📊 *BÁO CÁO TỔNG KẾT THÁNG {report_month}/{report_year}*\n\n"
                    f"Được tạo thủ công bởi ADMIN",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # ==================== SETUP ====================
    def setup_handlers(self):
        """Thiết lập các command handler mới"""
        # Command mới
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("stats_today", self.stats_today_command))
        self.application.add_handler(CommandHandler("stats_week", self.stats_week_command))
        self.application.add_handler(CommandHandler("stats_month", self.stats_month_command))
        self.application.add_handler(CommandHandler("report_month", self.manual_monthly_report))
        
        # Callback handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Command cũ (giữ lại)
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
    
    def setup_scheduler(self):
        """Thiết lập scheduler mới"""
        job_queue = self.application.job_queue
        
        # Check UID với tracking dài hạn mỗi phút
        job_queue.run_repeating(self.check_uids_long_tracking, interval=CHECK_INTERVAL, first=10)
        
        # Tạo báo cáo tháng tự động lúc 23:59 ngày cuối tháng
        # Chạy mỗi phút để kiểm tra thời điểm
        job_queue.run_repeating(self.generate_monthly_report, interval=60, first=10)
        
        # Lưu trạng thái hàng ngày lúc 00:01 mỗi ngày
        job_queue.run_daily(
            self.save_all_users_daily_status,
            time=time(hour=0, minute=1),
            days=(0, 1, 2, 3, 4, 5, 6)
        )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /start cơ bản"""
        await update.message.reply_text(
            "👋 Chào mừng đến với FB KÈO BOT\n\n"
            "Sử dụng /help để xem danh sách lệnh",
            reply_markup=Keyboards.main_menu()
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /help cập nhật"""
        help_text = """
📚 *DANH SÁCH LỆNH MỚI*

*TRẠNG THÁI & THỐNG KÊ:*
• /status - Trạng thái UID hiện tại
• /stats - Menu thống kê theo thời gian
• /stats_today - Thống kê DONE hôm nay
• /stats_week - Thống kê DONE tuần này
• /stats_month - Thống kê DONE tháng này

*BÁO CÁO (ADMIN):*
• /report_month - Xuất báo cáo Excel (thủ công)

*LỆNH KHÁC:*
• /start - Bắt đầu bot
• /help - Xem danh sách lệnh
        """.strip()
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def run(self):
        """Chạy bot"""
        # Khởi tạo database
        init_database()
        
        # Tạo application
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Thiết lập handlers
        self.setup_handlers()
        
        # Thiết lập scheduler
        self.setup_scheduler()
        
        # Chạy bot
        await self.application.initialize()
        await self.application.start()
        print("🤖 Bot đang chạy với tracking dài hạn...")
        
        # Giữ bot chạy
        await self.application.updater.start_polling()
        await asyncio.Event().wait()

# ==================== MAIN ====================
if __name__ == "__main__":
    bot = FBBot()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nếu đang chạy trên Server Railway
            loop.create_task(bot.run())
        else:
            # Nếu chạy trên máy tính cá nhân
            loop.run_until_complete(bot.run())
    except RuntimeError:
        asyncio.run(bot.run())
