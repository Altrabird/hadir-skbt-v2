"""
Hadir@SKBT v2 — Attendance tracking system for SK Bandar Tawau.
Flask backend with Google Sheets integration.
"""

import io
import base64
import datetime
import calendar
import hashlib
import hmac
import logging
import secrets
import threading
import contextlib
from zoneinfo import ZoneInfo
from functools import lru_cache

try:
    import fcntl  # Unix only — used for cross-process file lock
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

from flask import Flask, render_template, jsonify, request, Response, session
from werkzeug.middleware.proxy_fix import ProxyFix
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

# Behind nginx, Flask otherwise sees the plain-HTTP hop to gunicorn and builds
# links as http://127.0.0.1:5000/... — wrong in any message sent to parents.
# nginx already sets X-Forwarded-Proto / X-Forwarded-Host; trust one hop.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Definitive public address for links we hand out. Set this in .env — it is the
# only value that cannot be got wrong by a proxy misconfiguration.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Signed-cookie sessions carry the logged-in teacher. Set SESSION_COOKIE_SECURE=1
# in production (behind HTTPS); left off by default so local HTTP testing works.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "") == "1",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
)

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
SHEET_LOG = "REKOD LOG TETAPAN"
SHEET_SEBAB = "SEBAB"

# Absence reason ledger — append-only, never written into SHEET_ATTENDANCE
# (attendance rows for a date+class are deleted and rewritten on every submit,
#  so any reason stored there would be wiped by the next correction).
SEBAB_HEADER = [
    "DATE", "NAME", "CLASS", "KATEGORI", "SUB_KATEGORI", "CATATAN",
    "JENIS_BUKTI", "RUJUKAN_BUKTI", "STATUS_SAH", "DIREKOD_OLEH",
    "DIREKOD_PADA", "SUMBER",
]

# Where a ledger row came from. Parent submissions are claims, not proof —
# they always land as SAH_BELUM and a teacher verifies against the document.
SUMBER_GURU = "Guru"
SUMBER_WARIS = "Borang Waris"

# Absence reasons follow the KPM IDME / MOEIS-PELAJAR categories so the school
# can key the same values into MOEIS without re-mapping.
#   ponteng — counts toward the surat amaran ladder
#   waris   — a guardian may select it on the public form
SEBAB_KATEGORI = {
    "Aktiviti luar sekolah": {"ponteng": False, "waris": False, "sub": [
        "Wakil sekolah",
    ]},
    "Ancaman keselamatan": {"ponteng": False, "waris": True, "sub": [
        "Binatang liar/buas/berbisa", "Diculik", "Gangguan mistik/makluk halus",
        "Gangguan kumpulan kongsi gelap", "Kebakaran", "Pengganas/lanun",
        "Rusuhan di luar kawasan sekolah", "Ugutan daripada pihak luar",
        "Mangsa buli", "Mangsa seksual", "Tidak dapat dikesan/hilang",
    ]},
    "Bencana alam": {"ponteng": False, "waris": True, "sub": [
        "Jerebu", "Kemalangan", "Banjir", "Gempa bumi",
        "Hujan lebat/ribut taufan", "Pencemaran udara", "Kemarau",
        "Cuaca panas El Nino", "Pencemaran sisa kimia", "Pencemaran alam",
        "Tanah runtuh",
    ]},
    # School-imposed, so never a truancy warning — see notes in the UI.
    "Digantung sekolah": {"ponteng": False, "waris": False, "sub": [
        "Digantung sekolah",
    ]},
    "Masalah keluarga": {"ponteng": False, "waris": True, "sub": [
        "Bekerja", "Berpindah randah", "Perebutan hak penjagaan anak",
        "Mengikut keluarga bercuti/berkursus", "Menjaga/menguruskan ahli keluarga",
        "Menjaga ahli keluarga yang sakit", "Kematian ahli keluarga terdekat",
        "Kemiskinan/kesempitan hidup", "Masalah pengangkutan",
        "Menziarahi keluarga sakit", "Balik kampung", "Berpindah ke luar negara",
        "Krisis keluarga", "Lari dari rumah",
    ]},
    "Masalah peribadi": {"ponteng": False, "waris": True, "sub": [
        "Tekanan perasaan/trauma", "Kesakitan akibat haid/permulaan haid",
    ]},
    "PDPR": {"ponteng": False, "waris": False, "sub": [
        "Pembelajaran di rumah",
    ]},
    "Penggiliran peperiksaan": {"ponteng": False, "waris": False, "sub": [
        "Urusan peperiksaan",
    ]},
    "Kebenaran Pengetua/Guru Besar": {"ponteng": False, "waris": False, "sub": [
        "Haji/umrah/kegiatan agama", "Peperiksaaan/ujian selain KPM",
        "Pertandingan/aktiviti selain KPM", "Proses perpindahan sekolah",
        "Terlibat kes jenayah", "Terlibat kes trafik",
        "Urusan rasmi agensi kerajaan", "Latihan/ujian lesen memandu",
        "Tahanan pihak berkuasa", "Terlibat prosiding mahkamah",
        "Perlindungan jabatan kebajikan masyarakat", "Cuti semester",
        "Menjalani latihan industri",
    ]},
    "Ponteng": {"ponteng": True, "waris": False, "sub": [
        "Bangun lewat", "Malas ke sekolah", "Ketagihan gajet",
        "Tidak menyiapkan kerja sekolah", "Malas ke aktiviti kokurikulum",
    ]},
    "Masalah kesihatan": {"ponteng": False, "waris": True, "sub": [
        "Makluman ibu bapa penjaga", "Kecederaan/patah tulang",
        "Surat cuti sakit hospital/klinik", "Imunisasi rendah", "Demam",
        "Tantrum (MBK)", "Temujanji hospital/klinik",
        "Menjalani terapi/rawatan/kuarantin",
        "Tidak melepasi saringan kesihatan (MBK)",
        "Mendapatkan rawatan tradisional", "Kemurungan", "Batuk kokol",
        "Beguk", "Cacar air", "Chikungunya", "Covid 19 bergejala",
        "Covid 19 dengan kebenaran ibu bapa", "Covid 19 kuarantin",
        "Covid 19 penggiliran", "Denggi", "Swine Flue (H1N1)", "Hepatitis",
        "Hand, Foot and Mouth Disease (HFMD)", "Influenza",
        "Japanese Encephalitis", "Leptospirosis (penyakit kencing tikus)",
        "Kudis buta", "Malaria", "Mers COV", "Sars", "Taun", "Tibi",
        "Sakit Mistik", "Sakit mental", "Tekanan emosi",
    ]},
    "Sekolah dalam hospital": {"ponteng": False, "waris": False, "sub": [
        "Sekolah dalam hospital",
    ]},
}


def kategori_is_ponteng(kategori: str) -> bool:
    entry = SEBAB_KATEGORI.get(str(kategori).strip())
    return bool(entry and entry["ponteng"])


def sub_kategori_for(kategori: str) -> list[str]:
    entry = SEBAB_KATEGORI.get(str(kategori).strip())
    return list(entry["sub"]) if entry else []

# Verification states for a recorded reason
SAH_BELUM = "Belum Disahkan"
SAH_YA = "Sah"
SAH_TIDAK = "Tidak Sah"
SAH_OPTIONS = (SAH_BELUM, SAH_YA, SAH_TIDAK)

# Warning letters live in their own tab, NOT in SEBAB: build_sebab_index() keys
# on (date, pupil), so a letter row would be read back as a reason for whatever
# absence fell on its issue date.
SHEET_AMARAN = "AMARAN"
AMARAN_HEADER = [
    "TARIKH_KELUAR", "NAME", "CLASS", "TAHAP", "JUMLAH_PONTENG",
    "DIREKOD_OLEH", "CATATAN",
]

# Unexcused-absence count at which each escalation step is reached.
# Defaults follow the common 3/7/10/20 pattern — the school should confirm the
# figures against its current KPM circular and override them in settings.
AMARAN_DEFAULT_THRESHOLDS = {"1": 3, "2": 7, "3": 10, "4": 20}
AMARAN_LABELS = {
    1: "Surat Amaran Pertama",
    2: "Surat Amaran Kedua",
    3: "Surat Amaran Ketiga / Panggilan Waris",
    4: "Rujuk PK HEM",
}
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "SEKOLAH KEBANGSAAN BANDAR TAWAU")

# Proof types — Tier A: the school keeps the physical document, we record the
# reference to it. No file upload, no storage.
SEBAB_JENIS_BUKTI = [
    "Slip klinik / hospital",
    "Surat doktor (MC)",
    "Surat ibu bapa / waris",
    "Surat sekolah",
    "Panggilan telefon waris",
    "Tiada bukti",
]

# Settings password
# This repository is public, so the literal below is readable by anyone and must
# be treated as compromised. Set SETTINGS_PASSWORD in .env to override it; the
# fallback exists only so an un-updated deployment keeps working.
SETTINGS_PASSWORD = os.getenv("SETTINGS_PASSWORD", "xba3051")

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


# ---------------------------------------------------------------------------
# Teacher directory & access control
# ---------------------------------------------------------------------------
# Column A of SHEET_LOG holds teacher names (already the app's teacher list).
# Column B holds their class assignment:
#   "2 KRITIS"            -> guru kelas for that one class
#   "2 KRITIS, 3 KREATIF" -> more than one class
#   "SEMUA"               -> school-wide access (Guru Besar / PK HEM)
#   empty                 -> no access to absence records until assigned
TEACHER_ROLE_ALL = "SEMUA"
# Still parsed so a sheet that already carries it keeps working, but it no
# longer grants anything: the absence-and-reason register is open to every
# signed-in teacher, which is what the duty rotation actually needed.
TEACHER_ROLE_DUTY = "BERTUGAS"
_TEACHER_CACHE_KEY = "__teachers__"


def get_teacher_directory() -> dict[str, dict]:
    """Return {TEACHER_NAME_UPPER: {name, classes, is_admin}}, cached."""
    now = datetime.datetime.now(tz=TIMEZONE).timestamp()
    cached = _sheet_cache.get(_TEACHER_CACHE_KEY)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    client = get_spreadsheet_client()
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    ws = spreadsheet.worksheet(SHEET_LOG)
    rows = ws.get_values("A2:B1000")

    directory: dict[str, dict] = {}
    for row in rows:
        name = (row[0] if len(row) > 0 else "").strip()
        if not name:
            continue
        assignment = (row[1] if len(row) > 1 else "").strip()
        parts = [c.strip() for c in assignment.split(",") if c.strip()]
        is_duty = any(p.upper() == TEACHER_ROLE_DUTY for p in parts)
        parts = [p for p in parts if p.upper() != TEACHER_ROLE_DUTY]
        is_admin = any(p.upper() == TEACHER_ROLE_ALL for p in parts)
        classes = [] if is_admin else parts
        directory[name.upper()] = {
            "name": name,
            "classes": classes,
            "is_admin": is_admin,
            "is_duty": is_duty,
        }

    _sheet_cache[_TEACHER_CACHE_KEY] = (directory, now)
    return directory


def invalidate_teacher_cache():
    _sheet_cache.pop(_TEACHER_CACHE_KEY, None)


def current_teacher() -> dict | None:
    """Logged-in teacher, re-read from the directory so that changed or revoked
    assignments take effect without waiting for the session to expire."""
    name = session.get("teacher")
    if not name:
        return None
    return get_teacher_directory().get(str(name).strip().upper())


def require_teacher():
    """Return (info, None) when logged in, else (None, error_response)."""
    info = current_teacher()
    if info is None:
        return None, (jsonify({"error": "Sila log masuk untuk mengakses rekod ini."}), 401)
    return info, None


def allowed_classes_for(info: dict) -> list[str] | None:
    """Classes this teacher may see. None means every class."""
    return None if info["is_admin"] else list(info["classes"])


def class_denied(info: dict, class_name: str):
    """Return an error response if this teacher may not see the class."""
    if info["is_admin"] or class_name in info["classes"]:
        return None
    if not info["classes"]:
        return jsonify({
            "error": "Anda belum ditetapkan sebagai guru kelas. "
                     "Sila hubungi pentadbir sistem."
        }), 403
    return jsonify({
        "error": f"Anda hanya dibenarkan mengakses kelas: {', '.join(info['classes'])}."
    }), 403


def directory_has_admin() -> bool:
    return any(t["is_admin"] for t in get_teacher_directory().values())


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

    # Parse attendance for today — exact date match, dedup by name
    recorded_classes = {}  # class -> {present, absent, total, absent_names, time}
    if records:
        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
        daily = df[df["DATE_ONLY"] == date_str].copy()
        # Sort by DATE descending so dedup keeps the latest entry per (CLASS, NAME)
        daily = daily.sort_values("DATE", ascending=True)
        daily["NAME_KEY"] = daily["NAME"].astype(str).str.strip().str.upper()
        daily = daily.drop_duplicates(subset=["CLASS", "NAME_KEY"], keep="last")

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
                    time_str = str(cdf["DATE"].iloc[-1]).split(" ")[1]
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
# Scheduled morning reminder jobs
# ---------------------------------------------------------------------------
def build_morning_reminder(session: str) -> str:
    """Build the morning reminder text for a session."""
    today = datetime.datetime.now(tz=TIMEZONE)
    date_str = f"{today.day:02d} {MALAY_MONTHS[today.month]} {today.year}"
    day_name = MALAY_DAYS.get(today.strftime("%A"), today.strftime("%A"))
    session_label = "Sesi Pagi" if session == "Pagi" else "Sesi Petang"
    return (
        "📢 PEMAKLUMAN KEHADIRAN MURID\n\n"
        "Assalamualaikum dan selamat pagi.\n"
        f"Dimaklumkan bahawa guru waktu pertama {session_label} diminta untuk "
        "mengambil dan mengisi kehadiran murid pada hari ini seperti ketetapan berikut:\n"
        f"📆 Tarikh: {date_str}\n"
        f"🌤 Hari: {day_name}\n"
        "🔗 Sila gunakan pautan berikut untuk merekod kehadiran:\n\n"
        "👉 https://idme.moe.gov.my/login\n"
        "👉 https://hadirskbt.altrabird.click/\n\n"
        "Kerjasama semua guru amat dihargai bagi memastikan rekod kehadiran "
        "murid adalah tepat dan terkini.\nSekian, terima kasih."
    )


_SENT_MARKER_DIR = "/tmp"


def _job_already_fired_today(job_id: str) -> bool:
    """Check if a job already fired today by looking at a marker file.
    Cross-process safe — works across multiple Gunicorn workers.
    Returns True if already fired today (caller should skip).
    Otherwise creates the marker and returns False.
    """
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    marker = os.path.join(_SENT_MARKER_DIR, f"hadir_skbt_{job_id}_{today}.marker")
    if os.path.exists(marker):
        return True
    try:
        # Atomic create — fails if file exists (race-safe)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return False
    except FileExistsError:
        return True
    except Exception:
        # If we can't create the marker for any reason, allow the send (don't block)
        return False


def scheduled_pagi_reminder():
    """Post morning reminder to Pagi group at 6:30 AM."""
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    if not is_school_day(today) or not is_notifikasi_on():
        return
    if _job_already_fired_today("pagi_reminder"):
        log.info("Pagi reminder already sent today, skipping duplicate")
        return
    if TELEGRAM_CHAT_PAGI:
        telegram_send(TELEGRAM_CHAT_PAGI, build_morning_reminder("Pagi"))


def scheduled_petang_reminder():
    """Post morning reminder to Petang group at 11:45 AM."""
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    if not is_school_day(today) or not is_notifikasi_on():
        return
    if _job_already_fired_today("petang_reminder"):
        log.info("Petang reminder already sent today, skipping duplicate")
        return
    if TELEGRAM_CHAT_PETANG:
        telegram_send(TELEGRAM_CHAT_PETANG, build_morning_reminder("Petang"))


# ---------------------------------------------------------------------------
# Scheduled summary jobs
# ---------------------------------------------------------------------------
def scheduled_pagi_summary():
    """Post final Pagi summary at 10:00 AM."""
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    if not is_school_day(today) or not is_notifikasi_on():
        return
    if _job_already_fired_today("pagi_summary"):
        log.info("Pagi summary already sent today, skipping duplicate")
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
    if _job_already_fired_today("petang_summary"):
        log.info("Petang summary already sent today, skipping duplicate")
        return
    invalidate_cache()
    send_session_update(today, "Petang", is_scheduled=True)
    # Alert admin personally
    admin_id = get_admin_chat_id()
    if admin_id:
        telegram_send(admin_id, "Laporan Sesi Petang telah dihantar ke group.")


_scheduler_started = False
_scheduler_instance = None
_SCHEDULER_LOCK_FILE = "/tmp/hadir_skbt_scheduler.lock"


def _try_acquire_scheduler_lock():
    """Try to acquire an exclusive file lock for the scheduler.
    Returns the file handle if successful, None otherwise.
    Only ONE Gunicorn worker will succeed — others will fail and skip.
    The handle MUST be kept alive (never closed) for the lock to persist.
    """
    if not _HAS_FCNTL:
        return True  # On Windows/dev, just allow it
    try:
        f = open(_SCHEDULER_LOCK_FILE, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
        return f  # Keep handle alive — lock released only on process exit
    except (IOError, BlockingIOError):
        return None


def start_scheduler():
    """Start scheduler only once across ALL Gunicorn workers.
    Uses a file lock so only the first worker that acquires it runs the scheduler.
    """
    global _scheduler_started, _scheduler_instance
    if _scheduler_started:
        return

    lock_handle = _try_acquire_scheduler_lock()
    if lock_handle is None:
        log.info("Scheduler already running in another worker (PID lock held). Skipping.")
        _scheduler_started = True
        return

    _scheduler_started = True
    log.info("Scheduler starting in this worker (PID=%s)", os.getpid())

    scheduler = BackgroundScheduler(timezone="Asia/Kuala_Lumpur")
    if TELEGRAM_CHAT_PETANG:
        scheduler.add_job(scheduled_petang_summary, "cron", hour=15, minute=0, id="petang_summary")
        scheduler.add_job(scheduled_petang_reminder, "cron", hour=11, minute=45, id="petang_reminder")
    if TELEGRAM_CHAT_PAGI:
        scheduler.add_job(scheduled_pagi_summary, "cron", hour=10, minute=0, id="pagi_summary")
        scheduler.add_job(scheduled_pagi_reminder, "cron", hour=6, minute=30, id="pagi_reminder")
    scheduler.start()
    _scheduler_instance = (scheduler, lock_handle)  # Keep refs alive


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


@app.route("/sw.js")
def service_worker():
    """Serve service worker from root scope for PWA."""
    return app.send_static_file("sw.js")


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
# API: Settings Auth & Audit Log
# ---------------------------------------------------------------------------
@app.route("/api/settings/teachers")
def api_settings_teachers():
    """Return list of teacher names from the log tab (column A)."""
    try:
        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = spreadsheet.worksheet(SHEET_LOG)
        all_vals = ws.col_values(1)  # Column A only
        # Skip header row, filter empties
        teachers = [name.strip() for name in all_vals[1:] if name.strip()]
        teachers.sort()
        return jsonify(teachers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/login", methods=["POST"])
def api_settings_login():
    """Validate settings password. JSON: {teacher, password}"""
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "No JSON body"}), 400

    teacher = str(payload.get("teacher", "")).strip()
    password = str(payload.get("password", "")).strip()

    if not teacher:
        return jsonify({"error": "Sila pilih nama guru"}), 400
    if password != SETTINGS_PASSWORD:
        return jsonify({"error": "Kata laluan salah"}), 403

    try:
        directory = get_teacher_directory()
    except Exception as e:
        return jsonify({"error": f"Gagal membaca senarai guru: {e}"}), 500

    info = directory.get(teacher.upper())
    if info is None:
        return jsonify({"error": "Nama guru tidak dijumpai dalam senarai"}), 403

    session.permanent = True
    session["teacher"] = info["name"]

    return jsonify({
        "success": True,
        "teacher": info["name"],
        "classes": info["classes"],
        "is_admin": info["is_admin"],
        "is_duty": info.get("is_duty", False),
        "can_manage_teachers": info["is_admin"] or not directory_has_admin(),
    })


@app.route("/api/settings/logout", methods=["POST"])
def api_settings_logout():
    session.pop("teacher", None)
    return jsonify({"success": True})


@app.route("/api/settings/whoami")
def api_whoami():
    """Current session identity — lets the UI restore state after a reload."""
    info = current_teacher()
    if info is None:
        return jsonify({"logged_in": False})
    try:
        can_manage = info["is_admin"] or not directory_has_admin()
    except Exception:
        can_manage = info["is_admin"]
    return jsonify({
        "logged_in": True,
        "teacher": info["name"],
        "classes": info["classes"],
        "is_admin": info["is_admin"],
        "is_duty": info.get("is_duty", False),
        "can_manage_teachers": can_manage,
    })


@app.route("/api/settings/guru")
def api_list_guru():
    """Teacher list with class assignments. Requires login."""
    info, err = require_teacher()
    if err:
        return err
    try:
        directory = get_teacher_directory()
        students = get_data_from_sheet(SHEET_STUDENTS)
        all_classes = sorted({row["Class"] for row in students})
        return jsonify({
            "teachers": [
                {
                    "name": t["name"],
                    "classes": t["classes"],
                    "is_admin": t["is_admin"],
                    "is_duty": t.get("is_duty", False),
                }
                for t in sorted(directory.values(), key=lambda x: x["name"])
            ],
            "all_classes": all_classes,
            "can_manage": info["is_admin"] or not directory_has_admin(),
            "has_admin": directory_has_admin(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/guru", methods=["POST"])
def api_set_guru():
    """Assign classes to a teacher. JSON: {teacher, classes: [...] | "SEMUA"}

    Only an admin may reassign. Bootstrap exception: while no admin exists yet,
    any logged-in teacher may set assignments — otherwise a fresh install could
    never appoint its first admin. Assign one immediately.
    """
    actor, err = require_teacher()
    if err:
        return err
    try:
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Data permintaan tidak sah"}), 400

        if not actor["is_admin"] and directory_has_admin():
            return jsonify({
                "error": "Hanya pentadbir (SEMUA) boleh menetapkan guru kelas."
            }), 403

        target = str(payload.get("teacher", "")).strip()
        if not target:
            return jsonify({"error": "Sila pilih nama guru"}), 400

        directory = get_teacher_directory()
        entry = directory.get(target.upper())
        if entry is None:
            return jsonify({"error": "Nama guru tidak dijumpai dalam senarai"}), 404

        raw = payload.get("classes")
        if isinstance(raw, str) and raw.strip().upper() == TEACHER_ROLE_ALL:
            assignment = TEACHER_ROLE_ALL
        else:
            if not isinstance(raw, list):
                return jsonify({"error": "Senarai kelas tidak sah"}), 400
            students = get_data_from_sheet(SHEET_STUDENTS)
            valid = {row["Class"] for row in students}
            chosen = []
            for c in raw:
                c = str(c).strip()
                if not c:
                    continue
                if c not in valid:
                    return jsonify({"error": f"Kelas tidak sah: {c}"}), 400
                if c not in chosen:
                    chosen.append(c)
            assignment = ", ".join(chosen)


        # Locate the teacher's row in column A and write column B beside it
        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = spreadsheet.worksheet(SHEET_LOG)
        names = ws.col_values(1)
        row_idx = None
        for i, name in enumerate(names[1:], start=2):
            if name.strip().upper() == target.upper():
                row_idx = i
                break
        if row_idx is None:
            return jsonify({"error": "Nama guru tidak dijumpai dalam senarai"}), 404

        ws.update(f"B{row_idx}", [[assignment]])
        invalidate_teacher_cache()
        write_settings_log(
            actor["name"], "Tetapan Guru Kelas",
            f"{entry['name']} → {assignment or '(tiada)'}",
        )
        return jsonify({"success": True, "teacher": entry["name"], "assignment": assignment})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def write_settings_log(teacher: str, action: str, details: str):
    """Write an audit log entry to the REKOD LOG TETAPAN sheet (columns C-F)."""
    try:
        now = datetime.datetime.now(tz=TIMEZONE)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = spreadsheet.worksheet(SHEET_LOG)

        # Find next empty row in column C (log area)
        log_col = ws.col_values(3)  # Column C = TARIKH
        next_row = len(log_col) + 1

        ws.update(f"C{next_row}:F{next_row}", [[timestamp, teacher, action, details]])
        log.info("Settings log: %s | %s | %s | %s", timestamp, teacher, action, details)
    except Exception as e:
        log.warning("Failed to write settings log: %s", e)


# ---------------------------------------------------------------------------
# API: Notification Toggle (Telegram on/off)
# ---------------------------------------------------------------------------
@app.route("/api/settings/notifikasi", methods=["GET"])
def api_get_notifikasi():
    """Return current notification status."""
    try:
        settings = load_bot_settings()
        return jsonify({
            "notifikasi": bool(settings.get("notifikasi", True)),
            "admin_chat_id": str(settings.get("admin_chat_id", "")),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/notifikasi", methods=["POST"])
def api_set_notifikasi():
    """Toggle notification on/off. JSON: {enabled: bool, teacher: str}"""
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"error": "No JSON body"}), 400

        enabled = bool(payload.get("enabled", True))
        teacher = str(payload.get("teacher", "")).strip()

        settings = load_bot_settings()
        settings["notifikasi"] = enabled
        save_bot_settings(settings)

        # Log to audit sheet
        action = "Hidupkan Notifikasi" if enabled else "Matikan Notifikasi"
        write_settings_log(teacher, action, "Telegram auto-update + peringatan")

        # Notify admin via Telegram
        admin_id = get_admin_chat_id()
        if admin_id:
            status = "HIDUP" if enabled else "MATI"
            telegram_send(admin_id, f"Notifikasi auto telah ditukar ke <b>{status}</b> oleh {teacher} melalui Hadir@SKBT.")

        return jsonify({"success": True, "notifikasi": enabled})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Student Management (Add / Remove / Update RMT)
# ---------------------------------------------------------------------------
_student_mgmt_lock = threading.Lock()


@app.route("/api/students/add", methods=["POST"])
def api_add_student():
    """Add a new student. JSON: {name, class, is_rmt, teacher}"""
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"error": "No JSON body"}), 400

        name = str(payload.get("name", "")).strip().upper()
        cls = str(payload.get("class", "")).strip()
        is_rmt = bool(payload.get("is_rmt", False))

        if not name or not cls:
            return jsonify({"error": "Nama dan kelas diperlukan"}), 400

        with _student_mgmt_lock:
            client = get_spreadsheet_client()
            spreadsheet = client.open_by_url(SPREADSHEET_URL)

            # Check for duplicate in Students sheet
            students_ws = spreadsheet.worksheet(SHEET_STUDENTS)
            existing = students_ws.get_all_records()
            for row in existing:
                if str(row["Name"]).strip().upper() == name and str(row["Class"]).strip() == cls:
                    return jsonify({"error": f"Murid '{name}' sudah wujud dalam kelas {cls}"}), 409

            # Add to Students sheet
            students_ws.append_row([name, cls], value_input_option="RAW")

            # Add to RMT sheet if is_rmt
            if is_rmt:
                rmt_ws = spreadsheet.worksheet(SHEET_RMT)
                rmt_ws.append_row([name], value_input_option="RAW")

        invalidate_cache(SHEET_STUDENTS)
        invalidate_cache(SHEET_RMT)

        teacher = str(payload.get("teacher", "")).strip()
        rmt_label = " [RMT]" if is_rmt else ""
        write_settings_log(teacher, "Tambah Murid", f"{name} → {cls}{rmt_label}")

        return jsonify({"success": True, "name": name, "class": cls, "is_rmt": is_rmt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/students/remove", methods=["POST"])
def api_remove_student():
    """Remove a student from Students sheet (and RMT if exists).
    JSON: {name, class}
    Attendance records are NOT deleted — only the student list entry.
    """
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"error": "No JSON body"}), 400

        name = str(payload.get("name", "")).strip().upper()
        cls = str(payload.get("class", "")).strip()

        if not name or not cls:
            return jsonify({"error": "Nama dan kelas diperlukan"}), 400

        with _student_mgmt_lock:
            client = get_spreadsheet_client()
            spreadsheet = client.open_by_url(SPREADSHEET_URL)

            # Remove from Students sheet
            students_ws = spreadsheet.worksheet(SHEET_STUDENTS)
            all_vals = students_ws.get_all_values()
            rows_to_delete = []
            for i, row in enumerate(all_vals[1:], start=2):
                if len(row) >= 2 and str(row[0]).strip().upper() == name and str(row[1]).strip() == cls:
                    rows_to_delete.append(i)
            for idx in reversed(rows_to_delete):
                students_ws.delete_rows(idx)

            # Also remove from RMT sheet if present
            rmt_ws = spreadsheet.worksheet(SHEET_RMT)
            rmt_vals = rmt_ws.get_all_values()
            rmt_to_delete = []
            for i, row in enumerate(rmt_vals[1:], start=2):
                if len(row) >= 1 and str(row[0]).strip().upper() == name:
                    rmt_to_delete.append(i)
            for idx in reversed(rmt_to_delete):
                rmt_ws.delete_rows(idx)

        invalidate_cache(SHEET_STUDENTS)
        invalidate_cache(SHEET_RMT)

        teacher = str(payload.get("teacher", "")).strip()
        write_settings_log(teacher, "Buang Murid", f"{name} dari {cls}")

        return jsonify({"success": True, "name": name, "class": cls})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/students/update-rmt", methods=["POST"])
def api_update_rmt():
    """Toggle RMT status for a student.
    JSON: {name, is_rmt}
    If is_rmt=true → add to RMT sheet (if not already there)
    If is_rmt=false → remove from RMT sheet
    """
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"error": "No JSON body"}), 400

        name = str(payload.get("name", "")).strip().upper()
        is_rmt = bool(payload.get("is_rmt", False))

        if not name:
            return jsonify({"error": "Nama diperlukan"}), 400

        with _student_mgmt_lock:
            client = get_spreadsheet_client()
            spreadsheet = client.open_by_url(SPREADSHEET_URL)
            rmt_ws = spreadsheet.worksheet(SHEET_RMT)
            rmt_vals = rmt_ws.get_all_values()

            # Check if name already in RMT
            existing_rows = []
            for i, row in enumerate(rmt_vals[1:], start=2):
                if len(row) >= 1 and str(row[0]).strip().upper() == name:
                    existing_rows.append(i)

            if is_rmt and not existing_rows:
                # Add to RMT
                rmt_ws.append_row([name], value_input_option="RAW")
            elif not is_rmt and existing_rows:
                # Remove from RMT
                for idx in reversed(existing_rows):
                    rmt_ws.delete_rows(idx)

        invalidate_cache(SHEET_RMT)

        teacher = str(payload.get("teacher", "")).strip()
        action = "Set RMT" if is_rmt else "Buang RMT"
        write_settings_log(teacher, action, name)

        return jsonify({"success": True, "name": name, "is_rmt": is_rmt})
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
        df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
        mask = df["DATE_ONLY"] == date_str
        if class_name:
            mask = mask & (df["CLASS"] == class_name)
        filtered = df[mask].copy()

        # Dedup: keep latest per (CLASS, NAME) so the form shows one row per student
        if not filtered.empty:
            filtered = filtered.sort_values("DATE", ascending=True)
            filtered["NAME_KEY"] = filtered["NAME"].astype(str).str.strip().str.upper()
            filtered = filtered.drop_duplicates(subset=["CLASS", "NAME_KEY"], keep="last")

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
_submit_lock = threading.Lock()
_LOCK_FILE_PATH = "/tmp/hadir_skbt_submit.lock"


@contextlib.contextmanager
def submit_lock():
    """Acquire both thread-level and process-level (file) lock.
    Works across multiple Gunicorn workers on the same machine.
    """
    with _submit_lock:
        if _HAS_FCNTL:
            try:
                f = open(_LOCK_FILE_PATH, "w")
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    f.close()
            except Exception as e:
                log.warning("File lock failed, falling back to thread lock only: %s", e)
                yield
        else:
            yield


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
    Uses lock + targeted row deletion to prevent race conditions.
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

        # Build new rows first (outside lock) — dedup by name (last status wins)
        new_rows_map = {}  # NAME_KEY -> (name, status)
        for s in student_list:
            name_key = str(s["name"]).strip().upper()
            new_rows_map[name_key] = (s["name"], s["status"])

        new_rows = []
        absent_count = 0
        for name, status in new_rows_map.values():
            if status == STATUS_ABSENT:
                absent_count += 1
            new_rows.append([timestamp, name, target_class, status])

        # Lock to prevent concurrent read-delete-write race condition
        # Uses file lock — works across Gunicorn workers
        with submit_lock():
            client = get_spreadsheet_client()
            spreadsheet = client.open_by_url(SPREADSHEET_URL)
            attendance_sheet = spreadsheet.sheet1

            # 1. Read all cell values (row 1 = header)
            all_values = attendance_sheet.get_all_values()
            if not all_values:
                attendance_sheet.update("A1", [["DATE", "NAME", "CLASS", "STATUS"]])
                all_values = [["DATE", "NAME", "CLASS", "STATUS"]]

            header = all_values[0]

            # 2. Find rows to delete (matching date AND class) — use exact match
            rows_to_delete = []
            for i, row in enumerate(all_values[1:], start=2):  # 1-indexed, skip header
                if len(row) >= 4:
                    row_date = str(row[0])[:10]   # Extract YYYY-MM-DD from "YYYY-MM-DD HH:MM:SS"
                    row_class = str(row[2])
                    if row_date == target_date and row_class == target_class:
                        rows_to_delete.append(i)

            # 3. Delete old rows in reverse order (bottom-up to preserve indices)
            for row_idx in reversed(rows_to_delete):
                attendance_sheet.delete_rows(row_idx)

            # 4. Append new rows at the end
            attendance_sheet.append_rows(new_rows, value_input_option="RAW")

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
        df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
        daily = df[df["DATE_ONLY"] == date_str].copy()

        if daily.empty:
            return jsonify({
                "has_data": False,
                "all_classes": list(all_classes),
            })

        # Dedup: keep latest entry per (CLASS, NAME) if duplicates exist
        daily = daily.sort_values("DATE", ascending=True)
        daily["NAME_KEY"] = daily["NAME"].astype(str).str.strip().str.upper()
        daily = daily.drop_duplicates(subset=["CLASS", "NAME_KEY"], keep="last")

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
                    last_update = str(cdf["DATE"].iloc[-1]).split(" ")[1]
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

        # Enrolled per year from Students tab (source of truth)
        # Build {year: [{name, class}, ...]}
        enrolled_by_year = {y: [] for y in ("1", "2", "3", "4", "5", "6")}
        for s in students:
            cls = str(s["Class"]).strip()
            parts = cls.split()
            if parts and parts[0] in enrolled_by_year:
                enrolled_by_year[parts[0]].append({
                    "name": str(s["Name"]).strip(),
                    "class": cls,
                })

        # Build set of (class, name_upper) that have been marked today
        marked_keys = set()
        for _, row in daily.iterrows():
            marked_keys.add((str(row["CLASS"]).strip(), str(row["NAME"]).strip().upper()))

        # Per-year summary — use enrolled total (so it matches Students tab)
        by_year = []
        for y in ("1", "2", "3", "4", "5", "6"):
            enrolled_list = enrolled_by_year[y]
            enrolled = len(enrolled_list)
            if enrolled == 0:
                continue
            # Match by first token (robust to whitespace edge cases)
            year_df = daily[daily["CLASS"].astype(str).str.split().str[0] == y]
            p = int((year_df["STATUS"] == STATUS_PRESENT).sum())
            a = int((year_df["STATUS"] == STATUS_ABSENT).sum())
            marked = p + a

            # Find unmarked students grouped by class
            not_marked_by_class = {}
            for st in enrolled_list:
                key = (st["class"], st["name"].upper())
                if key not in marked_keys:
                    not_marked_by_class.setdefault(st["class"], []).append(st["name"])

            not_marked_classes = [
                {"class": cls, "students": sorted(names), "count": len(names)}
                for cls, names in sorted(not_marked_by_class.items())
            ]
            not_marked_count = sum(c["count"] for c in not_marked_classes)
            session = "Petang" if y in ("1", "2", "3") else "Pagi"
            by_year.append({
                "year": y,
                "label": f"Tahun {y}",
                "session": session,
                "present": p,
                "absent": a,
                "marked": marked,
                "not_marked": not_marked_count,
                "not_marked_classes": not_marked_classes,
                "total": enrolled,
                "pct": round(p / enrolled * 100, 1) if enrolled else 0,
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
            "by_year": by_year,
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
# Absence reason ledger (SEBAB) — read helpers
# ---------------------------------------------------------------------------
def get_sebab_records() -> list[dict]:
    """Fetch the SEBAB ledger.

    Tolerates the tab not existing yet: the report then simply shows every
    absence as 'Tiada Sebab'. The empty result is cached like any other sheet
    so a missing tab doesn't cost an API call on every request.
    """
    try:
        return get_data_from_sheet(SHEET_SEBAB)
    except gspread.WorksheetNotFound:
        now = datetime.datetime.now(tz=TIMEZONE).timestamp()
        _sheet_cache[SHEET_SEBAB] = ([], now)
        return []


def build_sebab_index() -> dict[tuple[str, str], dict]:
    """Return the latest reason entry per (date, pupil).

    The ledger is append-only — a correction is a new row, so the last matching
    row wins and the earlier ones remain as history.
    """
    index: dict[tuple[str, str], dict] = {}
    for row in get_sebab_records():
        date_only = _get_col(row, "DATE", "TARIKH")[:10]
        name = _get_col(row, "NAME", "NAMA")
        if not date_only or not name:
            continue
        index[(date_only, name.strip().upper())] = {
            "kategori": _get_col(row, "KATEGORI"),
            "sub_kategori": _get_col(row, "SUB_KATEGORI"),
            "catatan": _get_col(row, "CATATAN"),
            "jenis_bukti": _get_col(row, "JENIS_BUKTI"),
            "rujukan_bukti": _get_col(row, "RUJUKAN_BUKTI"),
            "status_sah": _get_col(row, "STATUS_SAH") or SAH_BELUM,
            "direkod_oleh": _get_col(row, "DIREKOD_OLEH"),
            "direkod_pada": _get_col(row, "DIREKOD_PADA"),
            "sumber": _get_col(row, "SUMBER") or SUMBER_GURU,
        }
    return index


def resolve_sebab_status(entry: dict | None) -> str:
    """Collapse a ledger entry into one of four report states."""
    if entry is None:
        return "tiada_sebab"
    status = entry.get("status_sah") or SAH_BELUM
    if status == SAH_YA:
        return "sah"
    if status == SAH_TIDAK:
        return "tidak_sah"
    return "belum_disahkan"


def counts_as_ponteng(status: str, entry: dict | None) -> bool:
    """Does this absence count toward the surat amaran ladder?

    Only a teacher-verified excuse clears a day. An unverified reason — including
    one a guardian submitted through the public form — keeps counting, otherwise
    anyone could reduce a pupil's truancy tally just by typing a reason.

    A confirmed "Ponteng" is verified *and* truancy, so the IDME category
    overrides verification in the other direction.
    """
    if entry and kategori_is_ponteng(entry.get("kategori", "")):
        return True
    return status != "sah"


def _valid_date(value: str) -> bool:
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def build_ketidakhadiran(class_name: str, date_from: str, date_to: str) -> dict:
    """Build the per-class absence record joining attendance with the reason ledger."""
    students = get_data_from_sheet(SHEET_STUDENTS)
    session = get_session(class_name)
    enrolled = {str(s["Name"]).strip().upper() for s in students if s["Class"] == class_name}

    empty = {
        "class": class_name,
        "session": session,
        "from": date_from,
        "to": date_to,
        "total_absences": 0,
        "total_pupils": 0,
        "school_days": 0,
        "counts": {"tiada_sebab": 0, "belum_disahkan": 0, "sah": 0, "tidak_sah": 0},
        "total_ponteng": 0,
        "pupils": [],
    }

    records = get_data_from_sheet(SHEET_ATTENDANCE)
    if not records:
        return empty

    df = pd.DataFrame(records)
    df.columns = [c.upper() for c in df.columns]
    class_df = df[df["CLASS"] == class_name].copy()
    if class_df.empty:
        return empty

    class_df["DATE_ONLY"] = class_df["DATE"].astype(str).str[:10]
    if date_from:
        class_df = class_df[class_df["DATE_ONLY"] >= date_from]
    if date_to:
        class_df = class_df[class_df["DATE_ONLY"] <= date_to]
    if class_df.empty:
        return empty

    # Dedup re-submissions: latest row per pupil per day wins
    class_df = class_df.sort_values("DATE", ascending=True)
    class_df["NAME_KEY"] = class_df["NAME"].astype(str).str.strip().str.upper()
    class_df = class_df.drop_duplicates(subset=["NAME_KEY", "DATE_ONLY"], keep="last")

    school_days = int(class_df["DATE_ONLY"].nunique())
    absent_df = class_df[class_df["STATUS"] == STATUS_ABSENT]
    if absent_df.empty:
        empty["school_days"] = school_days
        return empty

    sebab_index = build_sebab_index()
    counts = {"tiada_sebab": 0, "belum_disahkan": 0, "sah": 0, "tidak_sah": 0}
    by_pupil: dict[str, dict] = {}

    for _, row in absent_df.iterrows():
        name = str(row["NAME"]).strip()
        name_key = str(row["NAME_KEY"])
        date_only = str(row["DATE_ONLY"])
        entry = sebab_index.get((date_only, name_key))
        status = resolve_sebab_status(entry)
        counts[status] += 1

        pupil = by_pupil.setdefault(name_key, {
            "name": name,
            "still_enrolled": name_key in enrolled,
            "total": 0,
            "ponteng": 0,
            "counts": {"tiada_sebab": 0, "belum_disahkan": 0, "sah": 0, "tidak_sah": 0},
            "records": [],
        })
        pupil["total"] += 1
        pupil["counts"][status] += 1
        is_ponteng = counts_as_ponteng(status, entry)
        if is_ponteng:
            pupil["ponteng"] += 1

        pupil["records"].append({
            "date": date_only,
            "date_label": format_malay_date(date_only),
            "status": status,
            "kira_ponteng": is_ponteng,
            "kategori": entry["kategori"] if entry else "",
            "sub_kategori": entry["sub_kategori"] if entry else "",
            "catatan": entry["catatan"] if entry else "",
            "jenis_bukti": entry["jenis_bukti"] if entry else "",
            "rujukan_bukti": entry["rujukan_bukti"] if entry else "",
            "status_sah": entry["status_sah"] if entry else "",
            "direkod_oleh": entry["direkod_oleh"] if entry else "",
            "direkod_pada": entry["direkod_pada"] if entry else "",
            "sumber": entry["sumber"] if entry else "",
        })

    pupils = list(by_pupil.values())
    for p in pupils:
        p["records"].sort(key=lambda r: r["date"], reverse=True)
    # Worst first: most unexcused, then most absences, then name
    pupils.sort(key=lambda p: (-p["ponteng"], -p["total"], p["name"]))

    return {
        "class": class_name,
        "session": session,
        "from": date_from,
        "to": date_to,
        "total_absences": int(len(absent_df)),
        "total_pupils": len(pupils),
        "school_days": school_days,
        "counts": counts,
        # Not derivable from `counts`: an unverified reason still counts, and a
        # verified "Ponteng" counts despite being verified.
        "total_ponteng": sum(p["ponteng"] for p in pupils),
        "pupils": pupils,
    }


# ---------------------------------------------------------------------------
# API: Rekod Ketidakhadiran by class
# ---------------------------------------------------------------------------
@app.route("/api/kelas/<class_name>/ketidakhadiran")
def api_kelas_ketidakhadiran(class_name: str):
    """Absence record for one class, joined with the reason ledger.

    Optional query params: ?from=YYYY-MM-DD&to=YYYY-MM-DD (default = all history).
    Restricted to the classes assigned to the logged-in teacher.
    """
    info, err = require_teacher()
    if err:
        return err
    denied = class_denied(info, class_name)
    if denied:
        return denied
    try:
        date_from = str(request.args.get("from", "")).strip()
        date_to = str(request.args.get("to", "")).strip()
        for label, value in (("from", date_from), ("to", date_to)):
            if value and not _valid_date(value):
                return jsonify({"error": f"Tarikh '{label}' tidak sah (guna YYYY-MM-DD)"}), 400
        if date_from and date_to and date_from > date_to:
            return jsonify({"error": "Tarikh mula selepas tarikh tamat"}), 400

        return jsonify(build_ketidakhadiran(class_name, date_from, date_to))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def build_rekod_sebab(date_from: str, date_to: str,
                      class_name: str | None = None,
                      status_filter: str | None = None) -> dict:
    """The consolidated absence-and-reason register, school-wide.

    One row per pupil per absent day, carrying whatever reason has been
    recorded — by a guardian through the public form, or by the class teacher —
    and whether the class teacher has accepted it.
    """
    records = get_data_from_sheet(SHEET_ATTENDANCE)
    sebab_index = build_sebab_index()

    seen: set[tuple[str, str, str]] = set()
    rows = []
    counts = {"tiada_sebab": 0, "belum_disahkan": 0, "sah": 0, "tidak_sah": 0}
    daripada_waris = 0

    for r in records:
        if str(r.get("STATUS", "")).strip() != STATUS_ABSENT:
            continue
        date_only = str(r.get("DATE", ""))[:10]
        if not date_only:
            continue
        if date_from and date_only < date_from:
            continue
        if date_to and date_only > date_to:
            continue
        cls = str(r.get("CLASS", "")).strip()
        if class_name and cls != class_name:
            continue
        name = str(r.get("NAME", "")).strip()
        key = (date_only, cls, name.upper())
        if key in seen:
            continue
        seen.add(key)

        entry = sebab_index.get((date_only, name.upper()))
        status = resolve_sebab_status(entry)
        counts[status] += 1
        sumber = entry["sumber"] if entry else ""
        if sumber == SUMBER_WARIS:
            daripada_waris += 1

        rows.append({
            "date": date_only,
            "date_label": format_malay_date(date_only),
            "name": name,
            "class": cls,
            "session": get_session(cls),
            "status": status,
            "kira_ponteng": counts_as_ponteng(status, entry),
            "kategori": entry["kategori"] if entry else "",
            "sub_kategori": entry["sub_kategori"] if entry else "",
            "catatan": entry["catatan"] if entry else "",
            "jenis_bukti": entry["jenis_bukti"] if entry else "",
            "rujukan_bukti": entry["rujukan_bukti"] if entry else "",
            "direkod_oleh": entry["direkod_oleh"] if entry else "",
            "direkod_pada": entry["direkod_pada"] if entry else "",
            "sumber": sumber,
        })

    total = len(rows)
    if status_filter:
        rows = [x for x in rows if x["status"] == status_filter]

    # Newest day first; within a day the pupils with no reason lead, since
    # those are the ones an intervention session is for.
    order = {"tiada_sebab": 0, "belum_disahkan": 1, "tidak_sah": 2, "sah": 3}
    rows.sort(key=lambda x: (x["date"], -order.get(x["status"], 9),
                             x["class"], x["name"]), reverse=True)

    return {
        "from": date_from,
        "to": date_to,
        "class": class_name or "",
        "status": status_filter or "",
        "total_absences": total,
        "shown": len(rows),
        "counts": counts,
        "daripada_waris": daripada_waris,
        "perlu_intervensi": counts["tiada_sebab"],
        "rows": rows,
    }


def _rekod_args():
    """Shared query parsing. Returns (params, error_message)."""
    date_from = str(request.args.get("from", "")).strip()
    date_to = str(request.args.get("to", "")).strip()
    for label, value in (("from", date_from), ("to", date_to)):
        if value and not _valid_date(value):
            return None, f"Tarikh '{label}' tidak sah (guna YYYY-MM-DD)"
    if date_from and date_to and date_from > date_to:
        return None, "Tarikh mula selepas tarikh tamat"

    status = str(request.args.get("status", "")).strip()
    if status and status not in ("tiada_sebab", "belum_disahkan", "sah", "tidak_sah"):
        return None, "Status tidak sah"

    return {
        "date_from": date_from,
        "date_to": date_to,
        "class_name": str(request.args.get("class", "")).strip() or None,
        "status_filter": status or None,
    }, None


@app.route("/api/rekod-sebab")
def api_rekod_sebab():
    """Absence-and-reason register. Any signed-in teacher sees the whole school:
    the duty teacher rotates, so gating this behind a per-teacher setting only
    created work without protecting anything the staff room does not know."""
    info, err = require_teacher()
    if err:
        return err
    try:
        params, msg = _rekod_args()
        if msg:
            return jsonify({"error": msg}), 400
        return jsonify(build_rekod_sebab(**params))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/rekod-sebab")
def api_export_rekod_sebab():
    """CSV of the register, honouring the same filters as the on-screen list."""
    info, err = require_teacher()
    if err:
        return Response("Sila log masuk.", status=401)
    try:
        params, msg = _rekod_args()
        if msg:
            return Response(msg, status=400)
        data = build_rekod_sebab(**params)
        if not data["rows"]:
            return Response("Tiada rekod bagi tapisan ini", status=404)

        label = {"tiada_sebab": "Tiada Sebab", "belum_disahkan": SAH_BELUM,
                 "sah": SAH_YA, "tidak_sah": SAH_TIDAK}
        out = []
        for i, x in enumerate(data["rows"], 1):
            out.append({
                "Bil": i,
                "Tarikh": x["date"],
                "Hari": x["date_label"],
                "Nama": x["name"],
                "Kelas": x["class"],
                "Sesi": x["session"],
                "Status Sebab": label.get(x["status"], x["status"]),
                "Perlu Intervensi": "Ya" if x["status"] == "tiada_sebab" else "Tidak",
                "Dikira Ponteng": "Ya" if x["kira_ponteng"] else "Tidak",
                "Kategori": x["kategori"],
                "Sub Alasan": x["sub_kategori"],
                "Catatan": x["catatan"],
                "Jenis Bukti": x["jenis_bukti"],
                "Rujukan Bukti": x["rujukan_bukti"],
                "Direkod Oleh": x["direkod_oleh"],
                "Direkod Pada": x["direkod_pada"],
                "Sumber": x["sumber"],
            })
        buf = io.StringIO()
        pd.DataFrame(out).to_csv(buf, index=False)
        span = f"{params['date_from'] or 'awal'}_{params['date_to'] or 'terkini'}"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="Rekod_Sebab_{span}.csv"'},
        )
    except Exception as e:
        return Response(str(e), status=500)


@app.route("/api/kelas/senarai-saya")
def api_kelas_saya():
    """Classes the logged-in teacher may access — drives the UI dropdowns."""
    info, err = require_teacher()
    if err:
        return err
    try:
        if info["is_admin"]:
            students = get_data_from_sheet(SHEET_STUDENTS)
            classes = sorted({row["Class"] for row in students})
        else:
            classes = list(info["classes"])
        return jsonify({
            "teacher": info["name"],
            "is_admin": info["is_admin"],
            "classes": classes,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Borang Waris — public reason form for parents
# ---------------------------------------------------------------------------
# The link is signed with SECRET_KEY, so nothing needs storing. It carries the
# class, the month it was issued, and a per-class serial the school can bump to
# kill a leaked link.
BORANG_MAX_PER_HOUR = 40           # submissions per token per hour, per worker
BORANG_TEMPOH_HARI = 90            # how far back the form offers absences
_borang_hits: dict[str, list[float]] = {}

# Parents pick from the excused reasons only — "no reason" is a finding the
# school records, not something a guardian submits about their own child.
SEBAB_KATEGORI_WARIS = {
    k: v["sub"] for k, v in SEBAB_KATEGORI.items() if v["waris"]
}


def get_borang_siri(class_name: str) -> int:
    """Per-class link serial. Bumping it invalidates every old link."""
    settings = load_bot_settings()
    try:
        return int((settings.get("borang_siri") or {}).get(class_name, 1))
    except (TypeError, ValueError):
        return 1


def bump_borang_siri(class_name: str) -> int:
    settings = load_bot_settings()
    siri = settings.setdefault("borang_siri", {})
    try:
        current = int(siri.get(class_name, 1))
    except (TypeError, ValueError):
        current = 1
    siri[class_name] = current + 1
    save_bot_settings(settings)
    return current + 1


def _borang_sign(payload: str) -> str:
    key = str(app.config["SECRET_KEY"]).encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:12]


def _borang_periods() -> tuple[str, str]:
    """Current month and the previous one, so a link shared at month end keeps
    working for a few days."""
    now = datetime.datetime.now(tz=TIMEZONE)
    prev = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    return now.strftime("%Y-%m"), prev


def make_borang_token(class_name: str, period: str | None = None,
                      date_str: str = "") -> str:
    """Signed link body: class + serial + the date the message was about.

    The date is bound into the token so the form can be pinned to exactly the
    day the teacher announced; without it a guardian is shown every outstanding
    absence in the class. The period is folded into the signature rather than
    carried in the body, to keep the token short.
    """
    period = period or _borang_periods()[0]
    siri = get_borang_siri(class_name)
    body = f"{class_name}|{siri}|{date_str}"
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{encoded}.{_borang_sign(f'{class_name}|{period}|{siri}|{date_str}')}"


def verify_borang_token(token: str) -> tuple[str, str] | None:
    """Return (class_name, date) if valid, else None. date is "" for a
    class-wide link — the shape issued before dates were bound in."""
    try:
        encoded, sig = str(token).split(".", 1)
        padding = "=" * (-len(encoded) % 4)
        parts = base64.urlsafe_b64decode(encoded + padding).decode().split("|")
        class_name, siri = parts[0], parts[1]
        date_str = parts[2] if len(parts) > 2 else ""
    except Exception:
        return None

    if int(siri) != get_borang_siri(class_name):
        return None

    # The period is not in the URL, so try each one the link may have been
    # issued in; a match proves both the signature and the period.
    for period in _borang_periods():
        if hmac.compare_digest(sig, _borang_sign(f"{class_name}|{period}|{siri}|{date_str}")):
            return class_name, date_str
    return None


def borang_base() -> str:
    """Public origin for links we hand out."""
    return PUBLIC_BASE_URL or request.url_root.rstrip("/")


def borang_url(token: str) -> str:
    """Long signed form URL — used internally and as a fallback."""
    return f"{borang_base()}/b/{token}"


# --- Short links ----------------------------------------------------------
# Own-domain shortener. A third-party service (bit.ly, forms.gle) would add an
# external dependency, leak which classes are being messaged, and — worse for a
# school — make the link look like the shortened URLs used in scams. A code on
# the school's own domain is both shorter and more trustworthy.
# Alphabet excludes 0/O/1/I/L so a parent can retype it without ambiguity.
BORANG_KOD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BORANG_KOD_LENGTH = 6


def _borang_kod_map() -> dict:
    return load_bot_settings().get("borang_kod") or {}


def get_borang_kod(class_name: str, date_str: str = "") -> str:
    """Short code for one class on one date, creating it if needed.

    One code per (class, date) so each message the teacher sends points at the
    day it is about. Codes are tied to the class's serial, so 'Jana Pautan
    Baharu' retires them along with the old token.
    """
    siri = get_borang_siri(class_name)
    settings = load_bot_settings()
    kod_map = settings.setdefault("borang_kod", {})

    for kod, meta in kod_map.items():
        if (meta.get("class") == class_name
                and str(meta.get("date", "")) == date_str
                and int(meta.get("siri", 0)) == siri):
            return kod

    # Retire this class's codes from earlier serials, and drop codes issued long
    # enough ago to be finished with, so the settings file stays small.
    # Pruning must key on when the CODE was issued, not on the absence date — a
    # teacher may legitimately send a message about a day months in the past,
    # and that fresh link must not be swept away by the next one minted.
    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    cutoff = (datetime.datetime.now(tz=TIMEZONE)
              - datetime.timedelta(days=BORANG_TEMPOH_HARI)).strftime("%Y-%m-%d")
    for kod in [
        k for k, m in kod_map.items()
        if (m.get("class") == class_name and int(m.get("siri", 0)) != siri)
        or (m.get("created") and str(m["created"]) < cutoff)
    ]:
        kod_map.pop(kod, None)

    while True:
        kod = "".join(secrets.choice(BORANG_KOD_ALPHABET) for _ in range(BORANG_KOD_LENGTH))
        if kod not in kod_map:
            break
    kod_map[kod] = {"class": class_name, "date": date_str, "siri": siri,
                    "created": today}
    save_bot_settings(settings)
    return kod


def resolve_borang_kod(kod: str) -> tuple[str, str] | None:
    """(class, date) for a short code, or None if unknown or retired."""
    meta = _borang_kod_map().get(str(kod).strip().upper())
    if not meta:
        return None
    class_name = meta.get("class", "")
    if int(meta.get("siri", 0)) != get_borang_siri(class_name):
        return None
    return class_name, str(meta.get("date", ""))


def borang_short_url(class_name: str, date_str: str = "") -> str:
    return f"{borang_base()}/s/{get_borang_kod(class_name, date_str)}"


def _borang_rate_ok(token: str) -> bool:
    now = datetime.datetime.now(tz=TIMEZONE).timestamp()
    hits = [t for t in _borang_hits.get(token, []) if now - t < 3600]
    if len(hits) >= BORANG_MAX_PER_HOUR:
        _borang_hits[token] = hits
        return False
    hits.append(now)
    _borang_hits[token] = hits
    return True


@app.route("/s/<kod>")
def page_borang_pendek(kod: str):
    """Short link shared with parents. Resolves to the class, then renders the
    same form — the long signed token stays server-side."""
    resolved = resolve_borang_kod(kod)
    if resolved is None:
        return render_template("borang_sebab.html", invalid=True,
                               school=SCHOOL_NAME, class_name="", token="")
    class_name, date_str = resolved
    return page_borang_waris(make_borang_token(class_name, date_str=date_str))


@app.route("/b/<token>")
@app.route("/borang/<token>")   # long form kept working for links already shared
def page_borang_waris(token: str):
    """Public form for guardians. No login — the signed link is the credential."""
    resolved = verify_borang_token(token)
    if resolved is None:
        return render_template("borang_sebab.html", invalid=True,
                               school=SCHOOL_NAME, class_name="", token=token)
    class_name, date_str = resolved
    return render_template(
        "borang_sebab.html",
        invalid=False,
        school=SCHOOL_NAME,
        class_name=class_name,
        token=token,
        tarikh=date_str,
        tarikh_label=format_malay_date(date_str) if date_str else "",
        kategori=SEBAB_KATEGORI_WARIS,
        jenis_bukti=SEBAB_JENIS_BUKTI,
    )


@app.route("/api/borang/<token>/pilihan")
def api_borang_pilihan(token: str):
    """Guardian-selectable categories and their sub-reasons."""
    if verify_borang_token(token) is None:
        return jsonify({"error": "Pautan tidak sah atau telah tamat tempoh."}), 403
    return jsonify({"kategori": SEBAB_KATEGORI_WARIS})


def _borang_token_scope(token: str):
    """(class, pinned_date) for a token, or None."""
    return verify_borang_token(token)


@app.route("/api/borang/<token>/murid")
def api_borang_murid(token: str):
    """Only the pupils who actually have an absence still needing a reason,
    each with the exact dates.

    The names are already listed in the message sent to the class group, so
    withholding them here protected nothing while forcing a guardian to hunt
    through the whole roster. Dates a teacher has already decided on are left
    out — a guardian cannot change those anyway.
    """
    resolved = verify_borang_token(token)
    if resolved is None:
        return jsonify({"error": "Pautan tidak sah atau telah tamat tempoh."}), 403
    class_name, pinned_date = resolved
    try:
        cutoff = (datetime.datetime.now(tz=TIMEZONE)
                  - datetime.timedelta(days=BORANG_TEMPOH_HARI)).strftime("%Y-%m-%d")
        sebab_index = build_sebab_index()

        by_pupil: dict[str, set] = {}
        for r in get_data_from_sheet(SHEET_ATTENDANCE):
            if str(r.get("CLASS", "")).strip() != class_name:
                continue
            if str(r.get("STATUS", "")).strip() != STATUS_ABSENT:
                continue
            date_only = str(r.get("DATE", ""))[:10]
            if not date_only:
                continue
            # A dated link shows exactly the day the teacher announced — the
            # rolling window must not hide it, however long ago that was.
            if pinned_date:
                if date_only != pinned_date:
                    continue
            elif date_only < cutoff:
                continue
            name = str(r.get("NAME", "")).strip()
            status = resolve_sebab_status(sebab_index.get((date_only, name.upper())))
            if status in ("sah", "tidak_sah"):
                continue
            by_pupil.setdefault(name, set()).add(date_only)

        students = [
            {
                "name": name,
                "dates": [
                    {"date": d, "label": format_malay_date(d)}
                    for d in sorted(dates, reverse=True)
                ],
            }
            for name, dates in sorted(by_pupil.items())
        ]
        return jsonify({"class": class_name, "students": students})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/borang/<token>", methods=["POST"])
def api_borang_submit(token: str):
    """Guardian submits a reason. Always lands as 'Belum Disahkan'."""
    resolved = verify_borang_token(token)
    if resolved is None:
        return jsonify({"error": "Pautan tidak sah atau telah tamat tempoh."}), 403
    class_name, pinned_date = resolved
    if not _borang_rate_ok(token):
        return jsonify({
            "error": "Terlalu banyak hantaran buat masa ini. Sila cuba sebentar lagi."
        }), 429
    try:
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Data permintaan tidak sah"}), 400

        name = str(payload.get("name", "")).strip()
        date_str = str(payload.get("date", "")).strip()[:10]
        penghantar = str(payload.get("penghantar", "")).strip()
        kategori = str(payload.get("kategori", "")).strip()
        jenis_bukti = str(payload.get("jenis_bukti", "")).strip()
        catatan = str(payload.get("catatan", "")).strip()[:300]

        if not name:
            return jsonify({"error": "Sila pilih nama murid."}), 400
        if not penghantar:
            return jsonify({"error": "Sila isikan nama anda (ibu bapa/penjaga)."}), 400
        if not _valid_date(date_str):
            return jsonify({"error": "Tarikh tidak sah."}), 400
        # A dated link may only be used for its own date
        if pinned_date and date_str != pinned_date:
            return jsonify({
                "error": f"Pautan ini hanya untuk {format_malay_date(pinned_date)}."
            }), 400
        if kategori not in SEBAB_KATEGORI_WARIS:
            return jsonify({"error": "Sila pilih kategori sebab."}), 400
        subs = SEBAB_KATEGORI_WARIS[kategori]
        sub_kategori = str(payload.get("sub_kategori", "")).strip()
        if not sub_kategori and len(subs) == 1:
            sub_kategori = subs[0]
        if sub_kategori not in subs:
            return jsonify({"error": "Sila pilih sub-alasan yang berkaitan."}), 400
        if jenis_bukti and jenis_bukti not in SEBAB_JENIS_BUKTI:
            return jsonify({"error": "Jenis bukti tidak sah."}), 400

        students = get_data_from_sheet(SHEET_STUDENTS)
        roster = {str(s["Name"]).strip().upper() for s in students
                  if str(s["Class"]).strip() == class_name}
        name_key = name.upper()
        if name_key not in roster:
            return jsonify({"error": "Nama murid tidak dijumpai dalam kelas ini."}), 400

        # The date must really be an absence. The message stays neutral either
        # way so nobody can probe this form to learn who was absent.
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        is_absent = any(
            str(r.get("DATE", ""))[:10] == date_str
            and str(r.get("NAME", "")).strip().upper() == name_key
            and str(r.get("CLASS", "")).strip() == class_name
            and str(r.get("STATUS", "")).strip() == STATUS_ABSENT
            for r in records
        )
        if not is_absent:
            return jsonify({
                "error": "Tiada rekod ketidakhadiran bagi murid dan tarikh tersebut. "
                         "Sila semak tarikh, atau hubungi guru kelas."
            }), 400

        # A guardian must never be able to undo a teacher's verification —
        # the ledger is latest-wins, so this has to be blocked explicitly.
        existing = build_sebab_index().get((date_str, name_key))
        if existing and existing["status_sah"] in (SAH_YA, SAH_TIDAK):
            return jsonify({
                "error": "Sebab bagi tarikh ini telah disahkan oleh guru kelas. "
                         "Sila hubungi guru kelas jika perlu pembetulan."
            }), 409

        now = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        row = [date_str, name, class_name, kategori, sub_kategori, catatan,
               jenis_bukti, "", SAH_BELUM, f"WARIS: {penghantar}", now,
               SUMBER_WARIS]

        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = ensure_sebab_worksheet(spreadsheet)
        ws.append_rows([row], value_input_option="RAW")
        invalidate_cache(SHEET_SEBAB)

        return jsonify({
            "success": True,
            "message": "Terima kasih. Maklumat anda telah dihantar kepada guru kelas "
                       "untuk pengesahan. Sila kemukakan surat atau slip klinik asal "
                       "kepada guru kelas.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kelas/<class_name>/borang")
def api_kelas_borang(class_name: str):
    """Link + ready-made WhatsApp message for a class. Optional ?date=YYYY-MM-DD."""
    info, err = require_teacher()
    if err:
        return err
    denied = class_denied(info, class_name)
    if denied:
        return denied
    try:
        date_str = str(request.args.get("date", "")).strip()
        if date_str and not _valid_date(date_str):
            return jsonify({"error": "Tarikh tidak sah"}), 400

        records = get_data_from_sheet(SHEET_ATTENDANCE)

        # One pass: absentee names per date, and which dates were marked at all
        by_date: dict[str, list[str]] = {}
        marked_dates: set[str] = set()
        for r in records:
            if str(r.get("CLASS", "")).strip() != class_name:
                continue
            d = str(r.get("DATE", ""))[:10]
            if not d:
                continue
            marked_dates.add(d)
            if str(r.get("STATUS", "")).strip() == STATUS_ABSENT:
                by_date.setdefault(d, []).append(str(r.get("NAME", "")).strip())

        # Dates that actually have absences — newest first, for the quick picker
        dates_with_absences = [
            {
                "date": d,
                "label": format_malay_date(d),
                "count": len(sorted(set(names))),
            }
            for d, names in sorted(by_date.items(), reverse=True)
        ][:60]

        # Default to the most recent date that has absences. Defaulting to
        # "today" lands on weekends and school holidays, where there is no
        # session at all and the message would be meaningless.
        if not date_str:
            if dates_with_absences:
                date_str = dates_with_absences[0]["date"]
            elif marked_dates:
                date_str = max(marked_dates)
            else:
                date_str = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")

        absent = sorted(set(by_date.get(date_str, [])))
        recorded = date_str in marked_dates

        # Link is bound to this date, so the form opens on exactly these pupils
        url = borang_short_url(class_name, date_str)
        tajuk = f"*Kehadiran {class_name} — {format_malay_date(date_str)}*"

        if absent:
            senarai = "\n".join(f"{i}. {n}" for i, n in enumerate(absent, 1))
            mesej = (
                f"{tajuk}\n\n"
                f"Tidak hadir ({len(absent)}):\n{senarai}\n\n"
                f"Sila isi sebab: {url}\n"
                f"Surat/slip asal serahkan kepada guru kelas. Terima kasih."
            )
        elif recorded:
            mesej = f"{tajuk}\n\nSemua murid hadir. Terima kasih."
        else:
            mesej = (
                f"{tajuk}\n\n"
                f"Tiada rekod kehadiran bagi tarikh ini (cuti atau belum diisi)."
            )

        return jsonify({
            "class": class_name,
            "date": date_str,
            "date_label": format_malay_date(date_str),
            "absent": absent,
            "absent_count": len(absent),
            "recorded": recorded,
            "dates": dates_with_absences,
            "url": url,
            "message": mesej,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kelas/<class_name>/borang/jana-semula", methods=["POST"])
def api_kelas_borang_reset(class_name: str):
    """Invalidate every existing link for this class and issue a new one."""
    info, err = require_teacher()
    if err:
        return err
    denied = class_denied(info, class_name)
    if denied:
        return denied
    try:
        siri = bump_borang_siri(class_name)
        kod = get_borang_kod(class_name)   # retires the old code, mints a new one
        write_settings_log(info["name"], "Jana Semula Pautan Borang",
                           f"{class_name} — siri {siri} — kod {kod}")
        return jsonify({"success": True, "siri": siri, "kod": kod})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sebab/options")
def api_sebab_options():
    """Dropdown options for the reason form — kept server-side so the stored
    values stay aggregatable."""
    return jsonify({
        "kategori": [
            {
                "label": k,
                "kira_ponteng": v["ponteng"],
                "waris": v["waris"],
                "sub": v["sub"],
            }
            for k, v in SEBAB_KATEGORI.items()
        ],
        "jenis_bukti": SEBAB_JENIS_BUKTI,
        "status_sah": list(SAH_OPTIONS),
    })


_sebab_header_checked = False


def ensure_sebab_worksheet(spreadsheet):
    """Return the SEBAB worksheet, creating it with its header if absent."""
    global _sebab_header_checked
    try:
        ws = spreadsheet.worksheet(SHEET_SEBAB)
        # A tab created before SUMBER existed would have a short header; widen
        # it once per process so the new column is labelled.
        if not _sebab_header_checked:
            _sebab_header_checked = True
            try:
                header = ws.row_values(1)
                if len(header) < len(SEBAB_HEADER):
                    ws.update("A1", [SEBAB_HEADER])
                    log.info("Extended %s header to %d columns",
                             SHEET_SEBAB, len(SEBAB_HEADER))
            except Exception as e:
                log.warning("Could not verify %s header: %s", SHEET_SEBAB, e)
        return ws
    except gspread.WorksheetNotFound:
        try:
            ws = spreadsheet.add_worksheet(
                title=SHEET_SEBAB, rows=2000, cols=len(SEBAB_HEADER)
            )
            ws.update("A1", [SEBAB_HEADER])
            log.info("Created %s worksheet", SHEET_SEBAB)
            return ws
        except Exception:
            # Another worker won the race — re-fetch rather than fail
            return spreadsheet.worksheet(SHEET_SEBAB)


@app.route("/api/sebab", methods=["POST"])
def api_record_sebab():
    """Record the reason for one or more absences of a single pupil.

    JSON: {class, name, dates: [...], kategori, catatan,
           jenis_bukti, rujukan_bukti, status_sah}

    Requires a logged-in teacher assigned to the class. Append-only:
    re-recording the same date adds a new row and the latest wins, so
    corrections keep their history. Never writes to SHEET_ATTENDANCE.
    """
    info, err = require_teacher()
    if err:
        return err
    try:
        # silent=True so malformed JSON returns a clean 400, not a 500
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Data permintaan tidak sah"}), 400

        # Identity comes from the session, never the request body — otherwise a
        # teacher could file a record under a colleague's name.
        teacher = info["name"]

        cls = str(payload.get("class", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not cls or not name:
            return jsonify({"error": "Nama murid dan kelas diperlukan"}), 400

        denied = class_denied(info, cls)
        if denied:
            return denied

        dates = payload.get("dates") or []
        if not isinstance(dates, list) or not dates:
            return jsonify({"error": "Sila pilih sekurang-kurangnya satu tarikh"}), 400
        dates = [str(d).strip()[:10] for d in dates]
        bad = [d for d in dates if not _valid_date(d)]
        if bad:
            return jsonify({"error": f"Tarikh tidak sah: {', '.join(bad)}"}), 400
        dates = sorted(set(dates))

        kategori = str(payload.get("kategori", "")).strip()
        if kategori not in SEBAB_KATEGORI:
            return jsonify({"error": "Kategori sebab tidak sah"}), 400

        subs = sub_kategori_for(kategori)
        sub_kategori = str(payload.get("sub_kategori", "")).strip()
        if not sub_kategori and len(subs) == 1:
            sub_kategori = subs[0]          # only one option — fill it in
        if sub_kategori not in subs:
            return jsonify({
                "error": f"Sila pilih sub-alasan bagi kategori '{kategori}'"
            }), 400

        status_sah = str(payload.get("status_sah", "")).strip() or SAH_BELUM
        if status_sah not in SAH_OPTIONS:
            return jsonify({"error": "Status pengesahan tidak sah"}), 400

        jenis_bukti = str(payload.get("jenis_bukti", "")).strip()
        if jenis_bukti and jenis_bukti not in SEBAB_JENIS_BUKTI:
            return jsonify({"error": "Jenis bukti tidak sah"}), 400

        catatan = str(payload.get("catatan", "")).strip()
        rujukan = str(payload.get("rujukan_bukti", "")).strip()

        # A reason must attach to a real absence — guards against typos
        # creating orphan ledger rows nothing will ever read.
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        name_key = name.upper()
        absent_dates = {
            str(r.get("DATE", ""))[:10]
            for r in records
            if str(r.get("NAME", "")).strip().upper() == name_key
            and str(r.get("CLASS", "")).strip() == cls
            and str(r.get("STATUS", "")).strip() == STATUS_ABSENT
        }
        not_absent = [d for d in dates if d not in absent_dates]
        if not_absent:
            return jsonify({
                "error": "Tarikh berikut bukan rekod tidak hadir bagi murid ini: "
                         + ", ".join(not_absent)
            }), 400

        now = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        new_rows = [
            [d, name, cls, kategori, sub_kategori, catatan, jenis_bukti,
             rujukan, status_sah, teacher, now, SUMBER_GURU]
            for d in dates
        ]

        # Append-only, so no delete-rewrite race — no lock needed here.
        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = ensure_sebab_worksheet(spreadsheet)
        ws.append_rows(new_rows, value_input_option="RAW")

        invalidate_cache(SHEET_SEBAB)
        write_settings_log(
            teacher, "Rekod Sebab",
            f"{name} ({cls}) — {len(dates)} tarikh — {kategori} [{status_sah}]",
        )

        return jsonify({"success": True, "recorded": len(dates), "dates": dates})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Surat Amaran — escalation ladder
# ---------------------------------------------------------------------------
def get_amaran_thresholds() -> dict[str, int]:
    """Return {tahap: ponteng_count}, school-configurable via bot_settings.json."""
    settings = load_bot_settings()
    raw = settings.get("amaran_thresholds") or {}
    out = dict(AMARAN_DEFAULT_THRESHOLDS)
    for tahap in out:
        try:
            val = int(raw[tahap])
            if val > 0:
                out[tahap] = val
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_amaran_records() -> list[dict]:
    """Fetch issued-letter records, tolerating the tab not existing yet."""
    try:
        return get_data_from_sheet(SHEET_AMARAN)
    except gspread.WorksheetNotFound:
        now = datetime.datetime.now(tz=TIMEZONE).timestamp()
        _sheet_cache[SHEET_AMARAN] = ([], now)
        return []


def build_amaran_index() -> dict[tuple[str, str], dict]:
    """Highest warning level already issued per (class, pupil)."""
    index: dict[tuple[str, str], dict] = {}
    for row in get_amaran_records():
        name = _get_col(row, "NAME", "NAMA")
        cls = _get_col(row, "CLASS", "KELAS")
        if not name or not cls:
            continue
        try:
            tahap = int(_get_col(row, "TAHAP") or 0)
        except ValueError:
            continue
        key = (cls, name.strip().upper())
        prev = index.get(key)
        if prev is None or tahap > prev["tahap"]:
            index[key] = {
                "tahap": tahap,
                "tarikh": _get_col(row, "TARIKH_KELUAR")[:10],
                "oleh": _get_col(row, "DIREKOD_OLEH"),
            }
    return index


def tahap_for_ponteng(ponteng: int, thresholds: dict[str, int]) -> int:
    """Highest escalation level reached by this many unexcused absences."""
    reached = 0
    for tahap_str, minimum in thresholds.items():
        if ponteng >= minimum:
            reached = max(reached, int(tahap_str))
    return reached


def build_amaran_senarai(date_from: str, date_to: str,
                         class_name: str | None = None,
                         allowed: list[str] | None = None) -> dict:
    """Pupils who have reached a warning threshold.

    Single pass over the attendance data — building this per class would rescan
    the whole sheet once per class.
    """
    thresholds = get_amaran_thresholds()
    students = get_data_from_sheet(SHEET_STUDENTS)
    enrolled = {
        (str(s["Class"]).strip(), str(s["Name"]).strip().upper())
        for s in students
    }

    result = {
        "from": date_from,
        "to": date_to,
        "class": class_name or "",
        "thresholds": thresholds,
        "labels": {str(k): v for k, v in AMARAN_LABELS.items()},
        "pupils": [],
        "perlu_tindakan": 0,
    }

    records = get_data_from_sheet(SHEET_ATTENDANCE)
    if not records:
        return result

    df = pd.DataFrame(records)
    df.columns = [c.upper() for c in df.columns]
    if class_name:
        df = df[df["CLASS"] == class_name]
    elif allowed is not None:
        # No class chosen: fall back to everything this teacher may see
        df = df[df["CLASS"].isin(allowed)]
    if df.empty:
        return result

    df = df.copy()
    df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
    if date_from:
        df = df[df["DATE_ONLY"] >= date_from]
    if date_to:
        df = df[df["DATE_ONLY"] <= date_to]
    if df.empty:
        return result

    df = df.sort_values("DATE", ascending=True)
    df["NAME_KEY"] = df["NAME"].astype(str).str.strip().str.upper()
    df = df.drop_duplicates(subset=["CLASS", "NAME_KEY", "DATE_ONLY"], keep="last")
    absent_df = df[df["STATUS"] == STATUS_ABSENT]
    if absent_df.empty:
        return result

    sebab_index = build_sebab_index()
    amaran_index = build_amaran_index()

    tally: dict[tuple[str, str], dict] = {}
    for _, row in absent_df.iterrows():
        cls = str(row["CLASS"]).strip()
        name_key = str(row["NAME_KEY"])
        date_only = str(row["DATE_ONLY"])
        sebab_entry = sebab_index.get((date_only, name_key))
        status = resolve_sebab_status(sebab_entry)

        entry = tally.setdefault((cls, name_key), {
            "name": str(row["NAME"]).strip(),
            "class": cls,
            "total_absences": 0,
            "ponteng": 0,
            "ponteng_dates": [],
        })
        entry["total_absences"] += 1
        if counts_as_ponteng(status, sebab_entry):
            entry["ponteng"] += 1
            entry["ponteng_dates"].append(date_only)

    pupils = []
    for (cls, name_key), entry in tally.items():
        tahap_layak = tahap_for_ponteng(entry["ponteng"], thresholds)
        if tahap_layak == 0:
            continue
        issued = amaran_index.get((cls, name_key))
        tahap_issued = issued["tahap"] if issued else 0
        entry.update({
            "still_enrolled": (cls, name_key) in enrolled,
            "tahap_layak": tahap_layak,
            "tahap_label": AMARAN_LABELS.get(tahap_layak, ""),
            "tahap_dikeluarkan": tahap_issued,
            "amaran_terakhir": issued["tarikh"] if issued else "",
            "amaran_oleh": issued["oleh"] if issued else "",
            "perlu_tindakan": tahap_layak > tahap_issued,
            "ponteng_dates": sorted(entry["ponteng_dates"], reverse=True),
        })
        pupils.append(entry)

    # Most urgent first: needs action, then highest level, then most absences
    pupils.sort(key=lambda p: (not p["perlu_tindakan"], -p["tahap_layak"], -p["ponteng"], p["name"]))
    result["pupils"] = pupils
    result["perlu_tindakan"] = sum(1 for p in pupils if p["perlu_tindakan"])
    return result


@app.route("/api/amaran/senarai")
def api_amaran_senarai():
    """Pupils who have reached a warning threshold. Optional ?class=&from=&to=

    Without ?class=, returns every class the logged-in teacher may see.
    """
    info, err = require_teacher()
    if err:
        return err
    try:
        date_from = str(request.args.get("from", "")).strip()
        date_to = str(request.args.get("to", "")).strip()
        class_name = str(request.args.get("class", "")).strip() or None
        for label, value in (("from", date_from), ("to", date_to)):
            if value and not _valid_date(value):
                return jsonify({"error": f"Tarikh '{label}' tidak sah (guna YYYY-MM-DD)"}), 400
        if date_from and date_to and date_from > date_to:
            return jsonify({"error": "Tarikh mula selepas tarikh tamat"}), 400
        if class_name:
            denied = class_denied(info, class_name)
            if denied:
                return denied
        return jsonify(build_amaran_senarai(
            date_from, date_to, class_name, allowed_classes_for(info)
        ))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/amaran/tetapan", methods=["GET"])
def api_get_amaran_tetapan():
    """Current escalation thresholds."""
    info, err = require_teacher()
    if err:
        return err
    try:
        return jsonify({
            "thresholds": get_amaran_thresholds(),
            "labels": {str(k): v for k, v in AMARAN_LABELS.items()},
            "defaults": AMARAN_DEFAULT_THRESHOLDS,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/amaran/tetapan", methods=["POST"])
def api_set_amaran_tetapan():
    """Update thresholds. JSON: {thresholds: {"1": 3, ...}}. Admin only —
    the ladder is school-wide policy, not a per-class setting."""
    info, err = require_teacher()
    if err:
        return err
    try:
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Data permintaan tidak sah"}), 400
        if not info["is_admin"]:
            return jsonify({
                "error": "Hanya pentadbir (SEMUA) boleh menukar paras amaran."
            }), 403
        teacher = info["name"]

        raw = payload.get("thresholds") or {}
        cleaned: dict[str, int] = {}
        for tahap in AMARAN_DEFAULT_THRESHOLDS:
            try:
                val = int(raw[tahap])
            except (KeyError, TypeError, ValueError):
                return jsonify({"error": f"Nilai tahap {tahap} tidak sah"}), 400
            if val < 1 or val > 365:
                return jsonify({"error": f"Nilai tahap {tahap} mesti antara 1 dan 365"}), 400
            cleaned[tahap] = val

        ordered = [cleaned[t] for t in sorted(cleaned, key=int)]
        if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            return jsonify({"error": "Nilai tahap mesti menaik (tahap 1 < 2 < 3 < 4)"}), 400

        settings = load_bot_settings()
        settings["amaran_thresholds"] = cleaned
        save_bot_settings(settings)
        write_settings_log(
            teacher, "Tetapan Amaran",
            " / ".join(f"T{t}={cleaned[t]}" for t in sorted(cleaned, key=int)),
        )
        return jsonify({"success": True, "thresholds": cleaned})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def ensure_amaran_worksheet(spreadsheet):
    """Return the AMARAN worksheet, creating it with its header if absent."""
    try:
        return spreadsheet.worksheet(SHEET_AMARAN)
    except gspread.WorksheetNotFound:
        try:
            ws = spreadsheet.add_worksheet(
                title=SHEET_AMARAN, rows=2000, cols=len(AMARAN_HEADER)
            )
            ws.update("A1", [AMARAN_HEADER])
            log.info("Created %s worksheet", SHEET_AMARAN)
            return ws
        except Exception:
            return spreadsheet.worksheet(SHEET_AMARAN)


@app.route("/api/amaran/rekod", methods=["POST"])
def api_record_amaran():
    """Record that a warning letter was issued.

    JSON: {class, name, tahap, jumlah_ponteng, catatan}
    Append-only; the highest level recorded wins.
    """
    info, err = require_teacher()
    if err:
        return err
    try:
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Data permintaan tidak sah"}), 400

        teacher = info["name"]
        cls = str(payload.get("class", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not cls or not name:
            return jsonify({"error": "Nama murid dan kelas diperlukan"}), 400

        denied = class_denied(info, cls)
        if denied:
            return denied

        try:
            tahap = int(payload.get("tahap", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Tahap amaran tidak sah"}), 400
        if tahap not in AMARAN_LABELS:
            return jsonify({"error": "Tahap amaran tidak sah"}), 400

        try:
            jumlah = int(payload.get("jumlah_ponteng", 0))
        except (TypeError, ValueError):
            jumlah = 0

        # Don't let a lower level overwrite a higher one already on file
        existing = build_amaran_index().get((cls, name.upper()))
        if existing and existing["tahap"] >= tahap:
            return jsonify({
                "error": f"{AMARAN_LABELS[existing['tahap']]} sudah direkodkan "
                         f"pada {existing['tarikh']} oleh {existing['oleh']}."
            }), 409

        today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
        row = [today, name, cls, tahap, jumlah, teacher,
               str(payload.get("catatan", "")).strip()]

        client = get_spreadsheet_client()
        spreadsheet = client.open_by_url(SPREADSHEET_URL)
        ws = ensure_amaran_worksheet(spreadsheet)
        ws.append_rows([row], value_input_option="RAW")

        invalidate_cache(SHEET_AMARAN)
        write_settings_log(
            teacher, "Surat Amaran",
            f"{name} ({cls}) — {AMARAN_LABELS[tahap]} — {jumlah} hari ponteng",
        )
        return jsonify({"success": True, "tahap": tahap, "tarikh": today})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/surat-amaran/<class_name>/<student_name>")
def page_surat_amaran(class_name: str, student_name: str):
    """Printable warning letter. Rendered as HTML — print to PDF from the browser."""
    info, err = require_teacher()
    if err:
        return Response("Sila log masuk untuk menjana surat amaran.", status=401)
    if class_denied(info, class_name):
        return Response("Anda tidak dibenarkan mengakses kelas ini.", status=403)
    try:
        tahap = int(request.args.get("tahap", 1))
    except (TypeError, ValueError):
        tahap = 1
    if tahap not in AMARAN_LABELS:
        tahap = 1

    date_from = str(request.args.get("from", "")).strip()
    date_to = str(request.args.get("to", "")).strip()
    for value in (date_from, date_to):
        if value and not _valid_date(value):
            return Response("Tarikh tidak sah", status=400)

    data = build_amaran_senarai(date_from, date_to, class_name)
    pupil = next(
        (p for p in data["pupils"] if p["name"].strip().upper() == student_name.strip().upper()),
        None,
    )
    if pupil is None:
        return Response(
            "Murid ini belum mencapai mana-mana tahap amaran bagi tempoh ini.",
            status=404,
        )

    today = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    return render_template(
        "surat_amaran.html",
        school=SCHOOL_NAME,
        pupil=pupil,
        tahap=tahap,
        tahap_label=AMARAN_LABELS[tahap],
        session=get_session(class_name),
        today_label=format_malay_date(today),
        dates=[format_malay_date(d) for d in pupil["ponteng_dates"]],
        threshold=data["thresholds"].get(str(tahap), 0),
    )


@app.route("/api/export/ketidakhadiran/<class_name>")
def api_export_ketidakhadiran(class_name: str):
    """Download the class absence record as CSV, for sharing with the class."""
    info, err = require_teacher()
    if err:
        return Response("Sila log masuk untuk memuat turun rekod.", status=401)
    if class_denied(info, class_name):
        return Response("Anda tidak dibenarkan mengakses kelas ini.", status=403)
    try:
        date_from = str(request.args.get("from", "")).strip()
        date_to = str(request.args.get("to", "")).strip()
        for value in (date_from, date_to):
            if value and not _valid_date(value):
                return Response("Tarikh tidak sah", status=400)

        data = build_ketidakhadiran(class_name, date_from, date_to)
        if not data["pupils"]:
            return Response("Tiada rekod ketidakhadiran", status=404)

        status_label = {
            "tiada_sebab": "Tiada Sebab",
            "belum_disahkan": SAH_BELUM,
            "sah": SAH_YA,
            "tidak_sah": SAH_TIDAK,
        }

        rows = []
        bil = 0
        for pupil in data["pupils"]:
            for rec in sorted(pupil["records"], key=lambda r: r["date"]):
                bil += 1
                rows.append({
                    "Bil": bil,
                    "Nama": pupil["name"],
                    "Kelas": class_name,
                    "Tarikh": rec["date"],
                    "Hari": rec["date_label"],
                    "Status Sebab": status_label.get(rec["status"], rec["status"]),
                    "Dikira Ponteng": "Ya" if rec.get("kira_ponteng") else "Tidak",
                    "Kategori": rec["kategori"],
                    "Sub Alasan": rec.get("sub_kategori", ""),
                    "Catatan": rec["catatan"],
                    "Jenis Bukti": rec["jenis_bukti"],
                    "Rujukan Bukti": rec["rujukan_bukti"],
                    "Direkod Oleh": rec["direkod_oleh"],
                    "Sumber": rec.get("sumber", ""),
                })

        buf = io.StringIO()
        pd.DataFrame(rows).to_csv(buf, index=False)
        span = f"{date_from or 'awal'}_{date_to or 'terkini'}"
        filename = f"Ketidakhadiran_{class_name}_{span}.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return Response(str(e), status=500)


# ---------------------------------------------------------------------------
# API: CSV export
# ---------------------------------------------------------------------------
@app.route("/api/export/<date_str>")
@app.route("/api/export/<date_str>/<class_name>")
def api_export(date_str: str, class_name: str | None = None):
    """Download a CSV of full attendance list for a date, optionally filtered by class."""
    try:
        records = get_data_from_sheet(SHEET_ATTENDANCE)
        if not records:
            return Response("No data", status=404)

        df = pd.DataFrame(records)
        df.columns = [c.upper() for c in df.columns]
        df["DATE_ONLY"] = df["DATE"].astype(str).str[:10]
        mask = df["DATE_ONLY"] == date_str
        if class_name:
            mask = mask & (df["CLASS"] == class_name)

        filtered = df[mask].copy()
        if filtered.empty:
            return Response("No data for this date", status=404)

        # Build clean export with Bil, Nama, Kelas, Status, Sesi
        filtered = filtered.sort_values(["CLASS", "NAME"])
        rows = []
        bil = 0
        for _, row in filtered.iterrows():
            bil += 1
            status_label = "Hadir" if row["STATUS"] == STATUS_PRESENT else "Tidak Hadir"
            rows.append({
                "Bil": bil,
                "Nama": row["NAME"],
                "Kelas": row["CLASS"],
                "Status": status_label,
                "Sesi": get_session(row["CLASS"]),
            })

        export_df = pd.DataFrame(rows)
        buf = io.StringIO()
        export_df.to_csv(buf, index=False)
        label = class_name or "Semua"
        filename = f"Kehadiran_{label}_{date_str}.csv"
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
        /api/telegram/send/skbt2026                    → send today, both sessions
        /api/telegram/send/skbt2026/petang             → send today, petang only
        /api/telegram/send/skbt2026?date=2026-04-30    → send specific date
    """
    if secret != TELEGRAM_SECRET:
        return jsonify({"error": "Kod rahsia salah"}), 403

    # Optional date param — defaults to today
    date_str = request.args.get("date", "").strip()
    if not date_str:
        date_str = datetime.datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
    # Validate date format
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Format tarikh mesti YYYY-MM-DD"}), 400

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
            send_session_update(date_str, s, is_scheduled=False)
            results[s] = {"sent": True, "chat_id": chat_id}
        else:
            results[s] = {"sent": False, "error": "Chat ID not configured"}

    return jsonify({"success": True, "date": date_str, "results": results})


@app.route("/api/telegram/reminder/<secret>")
@app.route("/api/telegram/reminder/<secret>/<session>")
def api_telegram_reminder(secret: str, session: str | None = None):
    """Manually trigger morning reminder to Telegram group(s).
    Usage:
        /api/telegram/reminder/skbt2026          → kedua-dua sesi
        /api/telegram/reminder/skbt2026/pagi     → sesi pagi sahaja
        /api/telegram/reminder/skbt2026/petang   → sesi petang sahaja
    """
    if secret != TELEGRAM_SECRET:
        return jsonify({"error": "Kod rahsia salah"}), 403

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
        chat_id = TELEGRAM_CHAT_PAGI if s == "Pagi" else TELEGRAM_CHAT_PETANG
        if chat_id:
            msg_id = telegram_send(chat_id, build_morning_reminder(s))
            results[s] = {"sent": msg_id is not None, "chat_id": chat_id, "message_id": msg_id}
        else:
            results[s] = {"sent": False, "error": "Chat ID not configured"}

    return jsonify({"success": True, "results": results})


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
