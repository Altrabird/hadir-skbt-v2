"""
Hadir@SKBT v2 — Attendance tracking system for SK Bandar Tawau.
Flask backend with Google Sheets integration.
"""

import io
import datetime
import calendar
import logging
from zoneinfo import ZoneInfo
from functools import lru_cache

from flask import Flask, render_template, jsonify, request, Response
import gspread
import pandas as pd
import requests as http_requests
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import os

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-in-production")

SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1CiS8GhmhsDtOxZP0L3ZDmQDiA3DTbc1R0mQRQOfe1qY/edit#gid=237821847",
)
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")

TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")

# Sheet tab names
SHEET_STUDENTS = "Students"
SHEET_RMT = "RMT"
SHEET_ATTENDANCE = "Sheet1"

# Status constants
STATUS_PRESENT = "Present"
STATUS_ABSENT = "Absent"

# Session mapping — year prefix → session name
MORNING_YEARS = {"4", "5", "6"}   # Sesi Pagi
AFTERNOON_YEARS = {"1", "2", "3"} # Sesi Petang

# Secret code for manual Telegram trigger
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET", "hadirskbt")

# Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_PAGI = os.getenv("TELEGRAM_CHAT_PAGI", "")
TELEGRAM_CHAT_PETANG = os.getenv("TELEGRAM_CHAT_PETANG", "")

# Track last posted message IDs per session+date so we can delete & replace
# Key: "Pagi:2026-03-17" → message_id (per chat)
_telegram_msg_ids: dict[str, dict[str, int]] = {}  # {session:date: {chat_id: msg_id}}


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------
_gspread_client = None


def get_spreadsheet_client():
    """Return a cached gspread client."""
    global _gspread_client
    if _gspread_client is None:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        _gspread_client = gspread.authorize(creds)
    return _gspread_client


# Simple TTL cache for sheet data
_sheet_cache: dict = {}
_CACHE_TTL = 300  # seconds


def get_data_from_sheet(sheet_name: str) -> list[dict]:
    """Fetch all records from a worksheet, with a 5-minute cache."""
    now = datetime.datetime.now(tz=TIMEZONE).timestamp()
    key = sheet_name
    if key in _sheet_cache:
        data, ts = _sheet_cache[key]
        if now - ts < _CACHE_TTL:
            return data

    client = get_spreadsheet_client()
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    worksheet = (
        spreadsheet.sheet1 if sheet_name == SHEET_ATTENDANCE else spreadsheet.worksheet(sheet_name)
    )
    data = worksheet.get_all_records()
    _sheet_cache[key] = (data, now)
    return data


def invalidate_cache(sheet_name: str | None = None):
    """Clear cached sheet data."""
    if sheet_name:
        _sheet_cache.pop(sheet_name, None)
    else:
        _sheet_cache.clear()


def get_session(class_name: str) -> str:
    """Determine school session (Pagi / Petang) from the class name prefix."""
    if not class_name:
        return "Unknown"
    parts = str(class_name).strip().split()
    if not parts:
        return "Unknown"
    year = parts[0]
    if year in MORNING_YEARS:
        return "Pagi"
    if year in AFTERNOON_YEARS:
        return "Petang"
    return "Unknown"


# ---------------------------------------------------------------------------
# Notification control + settings persistence
# ---------------------------------------------------------------------------
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_settings.json")


def load_bot_settings() -> dict:
    """Load bot settings from file."""
    try:
        if os.path.exists(SETTINGS_FILE):
            import json
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"notifikasi": True, "admin_chat_id": ""}


def save_bot_settings(settings: dict):
    """Save bot settings to file."""
    import json
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)


def is_notifikasi_on() -> bool:
    """Check if scheduled notifications are enabled."""
    return load_bot_settings().get("notifikasi", True)


def get_admin_chat_id() -> str:
    """Get admin's personal chat ID for alerts."""
    return str(load_bot_settings().get("admin_chat_id", ""))


def is_school_day(date_str: str) -> bool:
    """Check if a date is a weekday (auto skip Sat & Sun)."""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() not in (5, 6)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

MALAY_DAYS = {
    "Monday": "Isnin", "Tuesday": "Selasa", "Wednesday": "Rabu",
    "Thursday": "Khamis", "Friday": "Jumaat", "Saturday": "Sabtu", "Sunday": "Ahad",
}
MALAY_MONTHS = [
    "", "Januari", "Februari", "Mac", "April", "Mei", "Jun",
    "Julai", "Ogos", "September", "Oktober", "November", "Disember",
]


def format_malay_date(date_str: str) -> str:
    """Convert '2026-03-17' to '17 Mac 2026 (Selasa)'."""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        day_name = MALAY_DAYS.get(dt.strftime("%A"), dt.strftime("%A"))
        return f"{dt.day} {MALAY_MONTHS[dt.month]} {dt.year} ({day_name})"
    except Exception:
        return date_str


def telegram_send(chat_id: str, text: str) -> int | None:
    """Send a message to a Telegram chat. Returns message_id."""
    if not TELEGRAM_TOKEN or not chat_id:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = http_requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        log.warning("Telegram send failed: %s", data)
    except Exception as e:
        log.warning("Telegram send error: %s", e)
    return None


def telegram_delete(chat_id: str, message_id: int):
    """Delete a message from a Telegram chat."""
    if not TELEGRAM_TOKEN or not chat_id or not message_id:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
        http_requests.post(url, json={
            "chat_id": chat_id,
            "message_id": message_id,
        }, timeout=10)
    except Exception:
        pass


def get_chat_id_for_session(session: str) -> str:
    return TELEGRAM_CHAT_PAGI if session == "Pagi" else TELEGRAM_CHAT_PETANG


def build_session_summary(date_str: str, session: str, is_scheduled: bool = False) -> str:
    """Build a formatted attendance summary for a session (Pagi or Petang)."""
    records = get_data_from_sheet(SHEET_ATTENDANCE)
    students_data = get_data_from_sheet(SHEET_STUDENTS)
    rmt_data = get_data_from_sheet(SHEET_RMT)
    rmt_set = {str(r["NAME"]).strip().upper() for r in rmt_data}

    # Get all classes for this session
    all_classes = sorted({s["Class"] for s in students_data if get_session(s["Class"]) == session})

    # Students per class
    class_students = {}
    for s in students_data:
        cls = s["Class"]
        if get_session(cls) == session:
            class_students.setdefault(cls, []).append(s["Name"])

    # Parse attendance for today
    recorded_classes = {}  # class -> {present, absent, total, absent_names, time}
    if records:
        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        daily = df[df["DATE"].astype(str).str.contains(date_str)].copy()

        for cls in all_classes:
            cdf = daily[daily["CLASS"] == cls]
            if not cdf.empty:
                total = len(cdf)
                absent_count = int((cdf["STATUS"] == STATUS_ABSENT).sum())
                present_count = total - absent_count
                absent_names = []
                for _, row in cdf[cdf["STATUS"] == STATUS_ABSENT].iterrows():
                    name = row["NAME"]
                    rmt_tag = " [RMT]" if str(name).strip().upper() in rmt_set else ""
                    absent_names.append(f"{name}{rmt_tag}")
                try:
                    time_str = str(cdf["DATE"].iloc[0]).split(" ")[1]
                except Exception:
                    time_str = ""
                recorded_classes[cls] = {
                    "present": present_count,
                    "total": total,
                    "absent": absent_count,
                    "absent_names": absent_names,
                    "time": time_str,
                }

    pending_classes = [cls for cls in all_classes if cls not in recorded_classes]

    # Build message
    pretty_date = format_malay_date(date_str)
    session_label = "PAGI" if session == "Pagi" else "PETANG"

    lines = [
        f"<b>LAPORAN KEHADIRAN - SESI {session_label}</b>",
        f"Tarikh: {pretty_date}",
        "",
        "Link idMe: https://idme.moe.gov.my/login",
        "Link Hadir@SKBT: https://hadirskbt.altrabird.click",
        "─" * 28,
    ]

    if recorded_classes:
        lines.append("")
        lines.append("<b>TELAH DIREKOD:</b>")
        total_present = 0
        total_students = 0
        for cls in all_classes:
            if cls in recorded_classes:
                info = recorded_classes[cls]
                total_present += info["present"]
                total_students += info["total"]
                lines.append("")
                if info["absent"] == 0:
                    lines.append(f"<b>{cls}</b> - ({info['present']}/{info['total']}) - 100%")
                else:
                    absent_str = ", ".join(info["absent_names"])
                    lines.append(f"<b>{cls}</b> - ({info['present']}/{info['total']})")
                    lines.append(f"TH: {absent_str}")

    if pending_classes:
        lines.append("")
        lines.append("─" * 28)
        lines.append("<b>BELUM DIREKOD:</b>")
        for cls in pending_classes:
            count = len(class_students.get(cls, []))
            lines.append(f"- {cls} ({count} murid)")

    lines.append("")
    lines.append("─" * 28)

    # Totals
    if recorded_classes:
        total_p = sum(r["present"] for r in recorded_classes.values())
        total_s = sum(r["total"] for r in recorded_classes.values())
        rate = round(total_p / total_s * 100, 1) if total_s else 0
        lines.append(f"Jumlah Hadir: {total_p}/{total_s} ({rate}%)")
        lines.append(f"Kelas Direkod: {len(recorded_classes)}/{len(all_classes)}")
    else:
        lines.append("Tiada kelas yang direkod lagi.")

    # Compliment / reminder
    lines.append("")
    if is_scheduled:
        if recorded_classes:
            lines.append("Tahniah kepada guru kelas yang telah merekod kehadiran!")
        if pending_classes:
            lines.append("Sila rekod kehadiran bagi kelas yang belum direkod.")
    else:
        if pending_classes:
            remaining = len(pending_classes)
            lines.append(f"Menunggu {remaining} lagi kelas untuk direkod.")

    return "\n".join(lines)


def send_session_update(date_str: str, session: str, is_scheduled: bool = False):
    """Build summary, delete old message, send new one to the correct group."""
    chat_id = get_chat_id_for_session(session)
    if not chat_id:
        return

    msg_key = f"{session}:{date_str}"
    text = build_session_summary(date_str, session, is_scheduled=is_scheduled)

    # Delete old message if exists
    old = _telegram_msg_ids.get(msg_key, {})
    if chat_id in old:
        telegram_delete(chat_id, old[chat_id])

    # Send new message
    msg_id = telegram_send(chat_id, text)
    if msg_id:
        _telegram_msg_ids.setdefault(msg_key, {})[chat_id] = msg_id


# ---------------------------------------------------------------------------
# Scheduled summary jobs
# ---------------------------------------------------------------------------
def scheduled_pagi_summary():
    """Post final Pagi summary at 10:00 AM."""
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    if not is_school_day(today) or not is_notifikasi_on():
        return
    invalidate_cache()
    send_session_update(today, "Pagi", is_scheduled=True)
    # Alert admin personally
    admin_id = get_admin_chat_id()
    if admin_id:
        telegram_send(admin_id, "Laporan Sesi Pagi telah dihantar ke group.")


def scheduled_petang_summary():
    """Post final Petang summary at 3:00 PM."""
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    if not is_school_day(today) or not is_notifikasi_on():
        return
    invalidate_cache()
    send_session_update(today, "Petang", is_scheduled=True)
    # Alert admin personally
    admin_id = get_admin_chat_id()
    if admin_id:
        telegram_send(admin_id, "Laporan Sesi Petang telah dihantar ke group.")


_scheduler_started = False


def start_scheduler():
    """Start scheduler only once (avoid duplicate in Gunicorn workers)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    scheduler = BackgroundScheduler(timezone="Asia/Kuala_Lumpur")
    if TELEGRAM_CHAT_PETANG:
        scheduler.add_job(scheduled_petang_summary, "cron", hour=15, minute=0, id="petang_summary")
    if TELEGRAM_CHAT_PAGI:
        scheduler.add_job(scheduled_pagi_summary, "cron", hour=10, minute=0, id="pagi_summary")
    scheduler.start()


# ---------------------------------------------------------------------------
# Telegram webhook — receive messages from Telegram
# ---------------------------------------------------------------------------
@app.route(f"/api/telegram/webhook/{TELEGRAM_SECRET}", methods=["POST"])
def telegram_webhook():
    """Handle incoming Telegram messages.
    Users can type:
        hadirskbt         → summary keseluruhan
        hadirskbt petang  → summary petang only
        hadirskbt pagi    → summary pagi only
    """
    try:
        data = request.get_json()
        if not data:
            return "ok", 200

        msg = data.get("message", {})
        text = (msg.get("text") or "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text.startswith("hadirskbt"):
            return "ok", 200

        today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
        parts = text.split()

        # Show chat ID for setup
        if len(parts) >= 2 and parts[1] == "chatid":
            chat_title = msg.get("chat", {}).get("title", "Private")
            telegram_send(chat_id, f"Chat ID: <code>{chat_id}</code>\nNama: {chat_title}\n\nGunakan ID ini untuk setting .env di VPS.")
            return "ok", 200

        # Notification control commands
        if len(parts) >= 2 and parts[1] == "notifikasi":
            settings = load_bot_settings()
            if len(parts) >= 3:
                if parts[2] == "on":
                    settings["notifikasi"] = True
                    # Save sender's personal chat for alerts
                    sender_id = str(msg.get("from", {}).get("id", ""))
                    if sender_id:
                        settings["admin_chat_id"] = sender_id
                    save_bot_settings(settings)
                    telegram_send(chat_id, "Notifikasi auto DIHIDUPKAN.\nBot akan hantar laporan pada jam 3:00 PM (hari persekolahan).")
                elif parts[2] == "off":
                    settings["notifikasi"] = False
                    save_bot_settings(settings)
                    telegram_send(chat_id, "Notifikasi auto DIMATIKAN.\nBot tidak akan hantar laporan automatik sehingga dihidupkan semula.")
                elif parts[2] == "status":
                    status = "HIDUP" if settings.get("notifikasi", True) else "MATI"
                    admin_id = settings.get("admin_chat_id", "")
                    admin_status = "Ditetapkan" if admin_id else "Belum ditetapkan"
                    telegram_send(chat_id, f"Status notifikasi: <b>{status}</b>\nAlert peribadi: {admin_status}\n\nSabtu & Ahad auto skip.")
                else:
                    telegram_send(chat_id, "Format:\nhadirskbt notifikasi on\nhadirskbt notifikasi off\nhadirskbt notifikasi status")
            else:
                telegram_send(chat_id, "Format:\nhadirskbt notifikasi on\nhadirskbt notifikasi off\nhadirskbt notifikasi status")
            return "ok", 200

        # Help command
        if len(parts) >= 2 and parts[1] == "help":
            help_text = (
                "<b>ARAHAN BOT HADIR@SKBT:</b>\n"
                "\n"
                "<b>Laporan:</b>\n"
                "hadirskbt - Laporan semua sesi\n"
                "hadirskbt petang - Laporan Petang\n"
                "hadirskbt pagi - Laporan Pagi\n"
                "\n"
                "<b>Kawalan:</b>\n"
                "hadirskbt notifikasi on - Hidupkan auto\n"
                "hadirskbt notifikasi off - Matikan auto\n"
                "hadirskbt notifikasi status - Semak status\n"
                "hadirskbt help - Senarai arahan"
            )
            telegram_send(chat_id, help_text)
            return "ok", 200

        # Summary commands
        invalidate_cache()

        if len(parts) >= 2 and parts[1] in ("petang", "pagi"):
            session = parts[1].capitalize()
            summary = build_session_summary(today, session)
            telegram_send(chat_id, summary)
        else:
            # Send both sessions
            for session in ["Pagi", "Petang"]:
                summary = build_session_summary(today, session)
                telegram_send(chat_id, summary)

    except Exception as e:
        log.warning("Webhook error: %s", e)

    return "ok", 200


def setup_telegram_webhook():
    """Register webhook URL with Telegram."""
    if not TELEGRAM_TOKEN:
        return
    webhook_url = f"https://hadirskbt.altrabird.click/api/telegram/webhook/{TELEGRAM_SECRET}"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
        resp = http_requests.post(url, json={"url": webhook_url}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            log.info("Telegram webhook set: %s", webhook_url)
        else:
            log.warning("Webhook setup failed: %s", data)
    except Exception as e:
        log.warning("Webhook setup error: %s", e)


_webhook_set = False


def init_telegram():
    """Initialize Telegram webhook and scheduler (called once)."""
    global _webhook_set
    if _webhook_set:
        return
    _webhook_set = True
    setup_telegram_webhook()
    start_scheduler()


# ---------------------------------------------------------------------------
# Initialize Telegram on first request (avoids issues with Gunicorn workers)
# ---------------------------------------------------------------------------
@app.before_request
def before_first_request():
    init_telegram()


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: Classes
# ---------------------------------------------------------------------------
@app.route("/api/classes")
def api_classes():
    """Return sorted list of all classes."""
    try:
        students = get_data_from_sheet(SHEET_STUDENTS)
        classes = sorted({row["Class"] for row in students})
        return jsonify(classes)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Students for a class (with RMT flag)
# ---------------------------------------------------------------------------
@app.route("/api/students/<class_name>")
def api_students(class_name: str):
    """Return students for a class, flagged if they are in the RMT programme."""
    try:
        students = get_data_from_sheet(SHEET_STUDENTS)
        rmt_data = get_data_from_sheet(SHEET_RMT)
        rmt_set = {str(r["NAME"]).strip().upper() for r in rmt_data}

        result = []
        for row in students:
            if row["Class"] == class_name:
                name = row["Name"]
                result.append({
                    "name": name,
                    "is_rmt": str(name).strip().upper() in rmt_set,
                })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Get attendance for a date (optionally filtered by class)
# ---------------------------------------------------------------------------
@app.route("/api/attendance/<date_str>")
@app.route("/api/attendance/<date_str>/<class_name>")
def api_attendance(date_str: str, class_name: str | None = None):
    """Return attendance records for a given date, optionally filtered by class."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        if not records:
            return jsonify([])

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        mask = df["DATE"].astype(str).str.contains(date_str)
        if class_name:
            mask = mask & (df["CLASS"] == class_name)
        filtered = df[mask]

        result = []
        for _, row in filtered.iterrows():
            result.append({
                "date": str(row["DATE"]),
                "name": row["NAME"],
                "class": row["CLASS"],
                "status": row["STATUS"],
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Submit attendance
# ---------------------------------------------------------------------------
@app.route("/api/attendance", methods=["POST"])
def api_submit_attendance():
    """Submit attendance for a class. Expects JSON:
    {
        "date": "2026-03-29",
        "class": "4 BESTARI",
        "students": [
            {"name": "ALI", "status": "Present"},
            {"name": "ABU", "status": "Absent"}
        ]
    }
    """
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"error": "No JSON body"}), 400

        target_date = payload["date"]
        target_class = payload["class"]
        student_list = payload["students"]

        now = datetime.datetime.now(tz=TIMEZONE)
        timestamp = f"{target_date} {now.strftime('%H:%M:%S')}"

        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        attendance_sheet = spreadsheet.sheet1

        # 1. Read all existing data
        all_records = attendance_sheet.get_all_records()
        df_all = pd.DataFrame(all_records)

        if not df_all.empty:
            df_all.columns = [c.upper() for c in df_all.columns]
            condition = (
                df_all["DATE"].astype(str).str.contains(target_date)
            ) & (df_all["CLASS"] == target_class)
            df_clean = df_all[~condition].copy()
        else:
            df_clean = pd.DataFrame(columns=["DATE", "NAME", "CLASS", "STATUS"])

        # 2. Build new rows
        new_rows = []
        absent_count = 0
        for s in student_list:
            status = s["status"]
            if status == STATUS_ABSENT:
                absent_count += 1
            new_rows.append({
                "DATE": timestamp,
                "NAME": s["name"],
                "CLASS": target_class,
                "STATUS": status,
            })

        df_new = pd.DataFrame(new_rows)
        df_final = pd.concat([df_clean, df_new], ignore_index=True)
        df_final = df_final[["DATE", "NAME", "CLASS", "STATUS"]]

        # 3. Write back
        attendance_sheet.clear()
        attendance_sheet.update(
            [df_final.columns.values.tolist()] + df_final.values.tolist()
        )

        invalidate_cache(SHEET_ATTENDANCE)

        # Send Telegram update only if recording for today and it's a school day
        today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
        if target_date == today and is_school_day(today):
            session = get_session(target_class)
            try:
                send_session_update(target_date, session)
            except Exception as e:
                log.warning("Telegram update failed: %s", e)

        return jsonify({
            "success": True,
            "absent_count": absent_count,
            "total": len(student_list),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Dashboard metrics
# ---------------------------------------------------------------------------
@app.route("/api/dashboard/<date_str>")
def api_dashboard(date_str: str):
    """Return aggregated dashboard data for a date."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        students = get_data_from_sheet(SHEET_STUDENTS)
        rmt_data = get_data_from_sheet(SHEET_RMT)

        all_classes = sorted({row["Class"] for row in students})
        rmt_set = {str(r["NAME"]).strip().upper() for r in rmt_data}

        if not records:
            return jsonify({
                "has_data": False,
                "all_classes": list(all_classes),
            })

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        daily = df[df["DATE"].astype(str).str.contains(date_str)].copy()

        if daily.empty:
            return jsonify({
                "has_data": False,
                "all_classes": list(all_classes),
            })

        # Add session & RMT flags
        class_session = {s["Class"]: get_session(s["Class"]) for s in students}
        daily["SESSION"] = daily["CLASS"].map(class_session).fillna("Unknown")
        daily["IS_RMT"] = daily["NAME"].apply(
            lambda x: str(x).strip().upper() in rmt_set
        )

        total = len(daily)
        absent = int((daily["STATUS"] == STATUS_ABSENT).sum())
        present = total - absent

        def session_metrics(session_name):
            s = daily[daily["SESSION"] == session_name]
            t = len(s)
            p = int((s["STATUS"] == STATUS_PRESENT).sum())
            a = t - p
            return {
                "total": t,
                "present": p,
                "absent": a,
                "present_pct": round(p / t * 100, 1) if t else 0,
                "absent_pct": round(a / t * 100, 1) if t else 0,
            }

        # RMT metrics
        rmt_df = daily[daily["IS_RMT"]]
        rmt_pagi = rmt_df[rmt_df["SESSION"] == "Pagi"]
        rmt_petang = rmt_df[rmt_df["SESSION"] == "Petang"]
        rmt_pagi_present = int((rmt_pagi["STATUS"] == STATUS_PRESENT).sum())
        rmt_petang_present = int((rmt_petang["STATUS"] == STATUS_PRESENT).sum())
        rmt_total_marked = len(rmt_df)

        # Per-class summary
        class_summary = []
        for cls in all_classes:
            cdf = daily[daily["CLASS"] == cls]
            session = get_session(cls)
            if not cdf.empty:
                n_absent = int((cdf["STATUS"] == STATUS_ABSENT).sum())
                n_present = len(cdf) - n_absent
                t = len(cdf)
                absent_names = cdf[cdf["STATUS"] == STATUS_ABSENT]["NAME"].tolist()
                try:
                    last_update = str(cdf["DATE"].iloc[0]).split(" ")[1]
                except Exception:
                    last_update = "Updated"
                class_summary.append({
                    "class": cls,
                    "session": session,
                    "status": "updated",
                    "time": last_update,
                    "present": n_present,
                    "present_pct": round(n_present / t * 100, 1) if t else 0,
                    "absent": n_absent,
                    "absent_pct": round(n_absent / t * 100, 1) if t else 0,
                    "total": t,
                    "absent_names": absent_names,
                })
            else:
                class_summary.append({
                    "class": cls,
                    "session": session,
                    "status": "pending",
                    "time": "-",
                    "present": 0,
                    "present_pct": 0,
                    "absent": 0,
                    "absent_pct": 0,
                    "total": 0,
                    "absent_names": [],
                })

        return jsonify({
            "has_data": True,
            "total_marked": total,
            "total_present": present,
            "total_absent": absent,
            "attendance_rate": round(present / total * 100, 1) if total else 0,
            "classes_updated": int(daily["CLASS"].nunique()),
            "total_classes": len(all_classes),
            "pagi": session_metrics("Pagi"),
            "petang": session_metrics("Petang"),
            "rmt": {
                "pagi_present": rmt_pagi_present,
                "pagi_total": len(rmt_pagi),
                "petang_present": rmt_petang_present,
                "petang_total": len(rmt_petang),
                "total_present": rmt_pagi_present + rmt_petang_present,
                "total_marked": rmt_total_marked,
                "coverage_pct": round(
                    (rmt_pagi_present + rmt_petang_present) / rmt_total_marked * 100, 1
                ) if rmt_total_marked else 0,
            },
            "class_summary": class_summary,
            "all_classes": list(all_classes),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Summary — Students list grouped by class
# ---------------------------------------------------------------------------
@app.route("/api/summary/students-list")
def api_summary_students_list():
    """Return all students grouped by class for sidebar dropdowns."""
    try:
        students = get_data_from_sheet(SHEET_STUDENTS)
        grouped: dict[str, list[str]] = {}
        for row in students:
            cls = row["Class"]
            name = row["Name"]
            grouped.setdefault(cls, []).append(name)

        result = []
        for cls in sorted(grouped.keys()):
            result.append({"name": cls, "students": sorted(grouped[cls])})
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Summary — Individual student history
# ---------------------------------------------------------------------------
@app.route("/api/summary/student/<student_name>")
def api_summary_student(student_name: str):
    """Return attendance summary for an individual student across all dates."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        students = get_data_from_sheet(SHEET_STUDENTS)

        # Find student's class
        student_class = ""
        for s in students:
            if s["Name"] == student_name:
                student_class = s["Class"]
                break

        if not records:
            return jsonify({
                "name": student_name,
                "class": student_class,
                "total_days": 0,
                "present": 0,
                "absent": 0,
                "rate": 0,
                "absent_dates": [],
            })

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]

        # Filter for this student
        student_df = df[df["NAME"] == student_name].copy()

        if student_df.empty:
            return jsonify({
                "name": student_name,
                "class": student_class,
                "total_days": 0,
                "present": 0,
                "absent": 0,
                "rate": 0,
                "absent_dates": [],
            })

        # Extract date part (YYYY-MM-DD) from timestamp
        student_df["DATE_ONLY"] = student_df["DATE"].astype(str).str[:10]

        # Deduplicate: keep last entry per date (in case of re-submissions)
        student_df = student_df.drop_duplicates(subset=["DATE_ONLY"], keep="last")

        total = len(student_df)
        absent_count = int((student_df["STATUS"] == STATUS_ABSENT).sum())
        present_count = total - absent_count
        rate = round(present_count / total * 100, 1) if total else 0

        absent_dates = sorted(
            student_df[student_df["STATUS"] == STATUS_ABSENT]["DATE_ONLY"].tolist(),
            reverse=True,
        )

        return jsonify({
            "name": student_name,
            "class": student_class,
            "total_days": total,
            "present": present_count,
            "absent": absent_count,
            "rate": rate,
            "absent_dates": absent_dates,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Summary — Class history
# ---------------------------------------------------------------------------
@app.route("/api/summary/class/<class_name>")
def api_summary_class(class_name: str):
    """Return attendance summary for a class with per-student breakdown."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        students = get_data_from_sheet(SHEET_STUDENTS)

        session = get_session(class_name)
        class_students = sorted([s["Name"] for s in students if s["Class"] == class_name])

        if not records:
            return jsonify({
                "class": class_name,
                "session": session,
                "total_days": 0,
                "avg_rate": 0,
                "student_summary": [],
            })

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        class_df = df[df["CLASS"] == class_name].copy()

        if class_df.empty:
            return jsonify({
                "class": class_name,
                "session": session,
                "total_days": 0,
                "avg_rate": 0,
                "student_summary": [],
            })

        # Extract date part
        class_df["DATE_ONLY"] = class_df["DATE"].astype(str).str[:10]
        total_days = class_df["DATE_ONLY"].nunique()

        # Per-student breakdown
        student_summary = []
        rates = []
        for name in class_students:
            sdf = class_df[class_df["NAME"] == name].drop_duplicates(
                subset=["DATE_ONLY"], keep="last"
            )
            s_total = len(sdf)
            s_absent = int((sdf["STATUS"] == STATUS_ABSENT).sum())
            s_present = s_total - s_absent
            s_rate = round(s_present / s_total * 100, 1) if s_total else 0
            rates.append(s_rate)
            student_summary.append({
                "name": name,
                "total_days": s_total,
                "present": s_present,
                "absent": s_absent,
                "rate": s_rate,
            })

        # Sort by rate ascending (worst attendance first)
        student_summary.sort(key=lambda x: x["rate"])

        avg_rate = round(sum(rates) / len(rates), 1) if rates else 0

        return jsonify({
            "class": class_name,
            "session": session,
            "total_days": total_days,
            "avg_rate": avg_rate,
            "total_students": len(class_students),
            "student_summary": student_summary,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: CSV export
# ---------------------------------------------------------------------------
@app.route("/api/export/<date_str>")
@app.route("/api/export/<date_str>/<class_name>")
def api_export(date_str: str, class_name: str | None = None):
    """Download a CSV of absent students for a date, optionally filtered by class."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        if not records:
            return Response("No data", status=404)

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        mask = df["DATE"].astype(str).str.contains(date_str)
        if class_name:
            mask = mask & (df["CLASS"] == class_name)

        filtered = df[mask].copy()
        absent = filtered[filtered["STATUS"] == STATUS_ABSENT][["DATE", "NAME", "CLASS"]]

        buf = io.StringIO()
        absent.to_csv(buf, index=False)
        filename = f"Absent_{class_name or 'All'}_{date_str}.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return Response(str(e), status=500)


# ---------------------------------------------------------------------------
# API: Monthly RMT Summary
# ---------------------------------------------------------------------------
def _get_col(row: dict, *candidates: str) -> str:
    """Get value from dict trying multiple key names (case-insensitive)."""
    lower_map = {k.lower(): k for k in row}
    for c in candidates:
        real_key = lower_map.get(c.lower())
        if real_key is not None:
            return str(row[real_key]).strip()
    return ""


def build_rmt_monthly_data(year_month: str) -> dict:
    """Build monthly RMT attendance data for all classes.
    year_month format: '2026-03'
    """
    year, month = int(year_month[:4]), int(year_month[5:7])

    students_data = get_data_from_sheet(SHEET_STUDENTS)
    rmt_data = get_data_from_sheet(SHEET_RMT)
    records = get_data_from_sheet(SHEET_ATTENDANCE)

    # Build RMT name set — case-insensitive, handle any column name
    rmt_set = set()
    for r in rmt_data:
        name = _get_col(r, "NAME", "Name", "name", "Nama")
        if name:
            rmt_set.add(name.upper())

    # Build list of RMT students with their class and session
    rmt_students = []
    for s in students_data:
        sname = _get_col(s, "Name", "NAME", "name", "Nama")
        sclass = _get_col(s, "Class", "CLASS", "class", "Kelas")
        if sname.upper() in rmt_set:
            rmt_students.append({
                "name": sname,
                "class": sclass,
                "session": get_session(sclass),
            })

    # Get school days (weekdays) in the month
    today = datetime.datetime.now(tz=TIMEZONE)
    _, days_in_month = calendar.monthrange(year, month)
    school_days = []
    for day in range(1, days_in_month + 1):
        dt = datetime.date(year, month, day)
        if dt.weekday() < 5:  # Mon-Fri
            if year == today.year and month == today.month and day > today.day:
                break  # Don't include future dates
            school_days.append(dt.strftime("%Y-%m-%d"))

    # Parse attendance for the month — use UPPER keys for case-insensitive matching
    attendance_map = {}  # {(NAME_UPPER, date): status}
    recorded_class_dates = set()  # {(class_upper, date)}
    if records:
        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
        monthly = df[df["DATE_ONLY"].str.startswith(year_month)].copy()
        monthly["NAME_UPPER"] = monthly["NAME"].astype(str).str.strip().str.upper()
        monthly["CLASS_UPPER"] = monthly["CLASS"].astype(str).str.strip().str.upper()
        # Deduplicate: keep last entry per student per date
        monthly = monthly.drop_duplicates(subset=["NAME_UPPER", "DATE_ONLY"], keep="last")
        for _, row in monthly.iterrows():
            attendance_map[(row["NAME_UPPER"], row["DATE_ONLY"])] = row["STATUS"]
            recorded_class_dates.add((row["CLASS_UPPER"], row["DATE_ONLY"]))

    # Build per-class, per-student data
    classes_data = {}
    for student in rmt_students:
        cls = student["class"]
        cls_upper = cls.upper()
        name = student["name"]
        name_upper = name.upper()
        if cls not in classes_data:
            classes_data[cls] = {
                "class": cls,
                "session": student["session"],
                "students": [],
            }

        daily = []
        present = 0
        absent = 0
        total_days = 0
        for d in school_days:
            if (cls_upper, d) in recorded_class_dates:
                total_days += 1
                status = attendance_map.get((name_upper, d), STATUS_ABSENT)
                if status == STATUS_PRESENT:
                    present += 1
                    daily.append({"date": d, "status": "H"})
                else:
                    absent += 1
                    daily.append({"date": d, "status": "TH"})
            else:
                daily.append({"date": d, "status": "-"})

        rate = round(present / total_days * 100, 1) if total_days > 0 else 0
        classes_data[cls]["students"].append({
            "name": name,
            "present": present,
            "absent": absent,
            "total_days": total_days,
            "rate": rate,
            "daily": daily,
        })

    # Sort classes and compute totals
    classes_list = sorted(classes_data.values(), key=lambda c: c["class"])
    all_rates = []
    total_rmt = 0
    for cls in classes_list:
        cls["students"].sort(key=lambda s: s["rate"])
        total_rmt += len(cls["students"])
        for s in cls["students"]:
            if s["total_days"] > 0:
                all_rates.append(s["rate"])

    avg_rate = round(sum(all_rates) / len(all_rates), 1) if all_rates else 0

    return {
        "month": year_month,
        "year": year,
        "month_num": month,
        "classes": classes_list,
        "school_days": school_days,
        "totals": {
            "total_rmt": total_rmt,
            "avg_rate": avg_rate,
        },
    }


@app.route("/api/summary/rmt/<year_month>")
def api_summary_rmt(year_month: str):
    """Return monthly RMT attendance summary."""
    try:
        data = build_rmt_monthly_data(year_month)
        logging.info("RMT summary %s: %d classes, %d students, %d school_days",
                      year_month, len(data["classes"]), data["totals"]["total_rmt"],
                      len(data["school_days"]))
        return jsonify(data)
    except Exception as e:
        logging.exception("RMT summary error for %s", year_month)
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/rmt/<year_month>")
@app.route("/api/export/rmt/<year_month>/<class_name>")
def api_export_rmt(year_month: str, class_name: str | None = None):
    """Download Excel file of monthly RMT attendance."""
    try:
        data = build_rmt_monthly_data(year_month)
        classes = data["classes"]
        school_days = data["school_days"]
        short_days = [d[5:] for d in school_days]  # MM-DD format for column headers

        if class_name:
            classes = [c for c in classes if c["class"] == class_name]

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            # Ringkasan (Summary) sheet
            summary_rows = []
            bil = 0
            for cls in classes:
                for s in cls["students"]:
                    bil += 1
                    summary_rows.append({
                        "Bil": bil,
                        "Nama": s["name"],
                        "Kelas": cls["class"],
                        "Sesi": cls["session"],
                        "Hari Rekod": s["total_days"],
                        "Hadir": s["present"],
                        "Tidak Hadir": s["absent"],
                        "Kadar (%)": s["rate"],
                    })

            if summary_rows:
                df_summary = pd.DataFrame(summary_rows)
                df_summary.to_excel(writer, sheet_name="Ringkasan", index=False)

            # Per-class daily detail sheets
            for cls in classes:
                sheet_name = cls["class"][:28]  # Excel 31 char limit
                rows = []
                for i, s in enumerate(cls["students"], 1):
                    row = {"Bil": i, "Nama": s["name"]}
                    for j, d in enumerate(s["daily"]):
                        row[short_days[j]] = d["status"]
                    row["Hadir"] = s["present"]
                    row["T/Hadir"] = s["absent"]
                    row["Kadar (%)"] = s["rate"]
                    rows.append(row)

                if rows:
                    df_detail = pd.DataFrame(rows)
                    df_detail.to_excel(writer, sheet_name=sheet_name, index=False)

        buf.seek(0)
        fname = f"RMT_{class_name or 'Semua'}_{year_month}.xlsx"
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    except Exception as e:
        logging.exception("RMT export error for %s / %s", year_month, class_name)
        return Response(str(e), status=500)


# ---------------------------------------------------------------------------
# API: Telegram — get recent chat IDs (helper for setup)
# ---------------------------------------------------------------------------
@app.route("/api/telegram/updates")
def api_telegram_updates():
    """Fetch recent bot updates to find group chat IDs."""
    if not TELEGRAM_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN not set"}), 500
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        resp = http_requests.get(url, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            return jsonify({"error": "Failed to get updates", "detail": data}), 500

        chats = {}
        for update in data.get("result", []):
            msg = update.get("message") or update.get("my_chat_member", {}).get("chat")
            if msg:
                chat = msg.get("chat") or msg
                chat_id = chat.get("id")
                title = chat.get("title", chat.get("first_name", "Unknown"))
                chat_type = chat.get("type", "unknown")
                if chat_id:
                    chats[str(chat_id)] = {"id": chat_id, "title": title, "type": chat_type}

        return jsonify({"chats": list(chats.values())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/telegram/<secret>")
def telegram_trigger_page(secret: str):
    """Simple UI page to trigger Telegram summary with session selection."""
    if secret != TELEGRAM_SECRET:
        return "Kod rahsia salah.", 403
    return '''<!DOCTYPE html>
<html lang="ms">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hantar Laporan Telegram</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
</head>
<body class="bg-slate-100 min-h-screen flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-lg p-6 w-full max-w-sm space-y-4">
        <div class="text-center">
            <i class="fa-solid fa-paper-plane text-3xl text-blue-500 mb-2"></i>
            <h1 class="text-lg font-bold text-gray-800">Hantar Laporan ke Telegram</h1>
            <p class="text-xs text-gray-400 mt-1">Pilih sesi untuk dihantar</p>
        </div>
        <div class="space-y-2">
            <button onclick="send('petang')" class="w-full py-3 rounded-xl font-bold text-sm text-white bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 transition shadow-md active:scale-[0.98]">
                <i class="fa-solid fa-moon mr-2"></i>Sesi Petang (Thn 1-3)
            </button>
            <button onclick="send('pagi')" class="w-full py-3 rounded-xl font-bold text-sm text-white bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 transition shadow-md active:scale-[0.98]">
                <i class="fa-solid fa-sun mr-2"></i>Sesi Pagi (Thn 4-6)
            </button>
            <button onclick="send('')" class="w-full py-3 rounded-xl font-bold text-sm text-white bg-gradient-to-r from-blue-600 to-indigo-700 hover:from-blue-700 hover:to-indigo-800 transition shadow-md active:scale-[0.98]">
                <i class="fa-solid fa-paper-plane mr-2"></i>Keseluruhan (Semua Sesi)
            </button>
        </div>
        <div id="result" class="hidden text-center text-sm font-semibold py-2 rounded-lg"></div>
    </div>
    <script>
    async function send(session) {
        const res = document.getElementById("result");
        res.className = "text-center text-sm font-semibold py-2 rounded-lg bg-blue-50 text-blue-600";
        res.textContent = "Menghantar...";
        res.classList.remove("hidden");
        try {
            const url = session
                ? "/api/telegram/send/''' + secret + '''/" + session
                : "/api/telegram/send/''' + secret + '''";
            const r = await fetch(url);
            const d = await r.json();
            if (d.success) {
                res.className = "text-center text-sm font-semibold py-2 rounded-lg bg-emerald-50 text-emerald-600";
                res.textContent = "Berjaya dihantar ke Telegram!";
            } else {
                res.className = "text-center text-sm font-semibold py-2 rounded-lg bg-red-50 text-red-600";
                res.textContent = d.error || "Gagal menghantar.";
            }
        } catch(e) {
            res.className = "text-center text-sm font-semibold py-2 rounded-lg bg-red-50 text-red-600";
            res.textContent = "Ralat: " + e.message;
        }
    }
    </script>
</body>
</html>'''


@app.route("/api/telegram/send/<secret>")
@app.route("/api/telegram/send/<secret>/<session>")
def api_telegram_send(secret: str, session: str | None = None):
    """Manually trigger sending summary to Telegram with secret code.
    Usage:
        /api/telegram/send/skbt2026          → send both sessions
        /api/telegram/send/skbt2026/petang   → send petang only
        /api/telegram/send/skbt2026/pagi     → send pagi only
    """
    if secret != TELEGRAM_SECRET:
        return jsonify({"error": "Kod rahsia salah"}), 403

    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    invalidate_cache()
    results = {}

    sessions_to_send = []
    if session:
        s = session.capitalize()
        if s in ("Pagi", "Petang"):
            sessions_to_send = [s]
        else:
            return jsonify({"error": "Session mesti 'pagi' atau 'petang'"}), 400
    else:
        sessions_to_send = ["Pagi", "Petang"]

    for s in sessions_to_send:
        chat_id = get_chat_id_for_session(s)
        if chat_id:
            send_session_update(today, s, is_scheduled=False)
            results[s] = {"sent": True, "chat_id": chat_id}
        else:
            results[s] = {"sent": False, "error": f"Chat ID not configured"}

    return jsonify({"success": True, "date": today, "results": results})


@app.route("/api/telegram/test")
def api_telegram_test():
    """Send a test message to both groups."""
    results = {}
    for label, chat_id in [("Pagi", TELEGRAM_CHAT_PAGI), ("Petang", TELEGRAM_CHAT_PETANG)]:
        if chat_id:
            msg_id = telegram_send(chat_id, f"Bot Hadir@SKBT berjaya disambung ke kumpulan Sesi {label}!")
            results[label] = {"chat_id": chat_id, "sent": msg_id is not None, "message_id": msg_id}
        else:
            results[label] = {"chat_id": "", "sent": False, "error": f"TELEGRAM_CHAT_{label.upper()} not set"}
    return jsonify(results)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
