"""
Hadir@SKBT v2 — Attendance tracking system for SK Bandar Tawau.
Flask backend with Google Sheets integration.
"""

import io
import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache

from flask import Flask, render_template, jsonify, request, Response
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
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
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
