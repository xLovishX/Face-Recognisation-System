import base64
import json
import secrets
import string
from datetime import datetime

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from backend.app.auth.dependencies import require_role
from backend.app.auth.security import hash_password
from backend.app.database.connection import get_connection
from backend.app.face_recognition.recognize_face import (
    extract_face_encoding_for_registration
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def _decode_base64_frame(image):
    if not isinstance(image, str) or "," not in image:
        raise HTTPException(status_code=400, detail="Invalid image data")

    image_data = image.split(",", 1)[1]
    img_bytes = base64.b64decode(image_data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        raise HTTPException(status_code=400, detail="Invalid image data")

    return frame


def _generate_temporary_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.post("/register-student")
def register_student(
    data: dict,
    current_user=Depends(require_role("admin"))
):
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()

    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    frame = _decode_base64_frame(data["image"])

    encoding, recognition_error = extract_face_encoding_for_registration(frame)

    if recognition_error:
        raise HTTPException(status_code=400, detail=recognition_error)

    encoding = encoding.tolist()
    temporary_password = str(data.get("password", "")).strip() or _generate_temporary_password()
    password_hash = hash_password(temporary_password)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()

        if existing_user:
            raise HTTPException(status_code=400, detail="A user with this email already exists")

        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, role)
            VALUES (%s, %s, %s, 'student')
            RETURNING id
            """,
            (name, email, password_hash)
        )

        user_id = cursor.fetchone()[0]

        cursor.execute(
            "INSERT INTO students (user_id, encoding) VALUES (%s, %s)",
            (user_id, json.dumps(encoding))
        )

        conn.commit()
        return {
            "message": "Student registered successfully",
            "temporary_password": temporary_password
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error registering student:", exc)
        raise HTTPException(status_code=500, detail="Unable to register student")
    finally:
        cursor.close()
        conn.close()

@router.get("/analytics")
def admin_analytics(admin=Depends(require_role("admin"))):

    conn = get_connection()
    cursor = conn.cursor()

    # Total students
    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    # Total attendance records
    cursor.execute("SELECT COUNT(*) FROM attendance")
    total_attendance_records = cursor.fetchone()[0]

    # Today's attendance
    today = datetime.now().date()
    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE date = %s",
        (today,)
    )
    today_attendance = cursor.fetchone()[0]

    # Attendance percentage per student
    cursor.execute("""
        SELECT users.name, COUNT(attendance.id) AS present_days
        FROM students
        JOIN users ON students.user_id = users.id
        LEFT JOIN attendance ON attendance.student_id = students.id
        GROUP BY users.name
    """)

    student_data = cursor.fetchall()

    cursor.execute("SELECT COUNT(DISTINCT date) FROM attendance")
    total_working_days = cursor.fetchone()[0]

    analytics = []

    for name, present_days in student_data:
        percentage = 0
        if total_working_days > 0:
            percentage = round((present_days / total_working_days) * 100, 2)

        analytics.append({
            "student_name": name,
            "present_days": present_days,
            "attendance_percentage": percentage
        })

    cursor.close()
    conn.close()

    return {
        "total_students": total_students,
        "total_attendance_records": total_attendance_records,
        "today_attendance": today_attendance,
        "student_analytics": analytics
    }
@router.get("/stats")
def admin_stats(current_user=Depends(require_role("admin"))):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance WHERE date = CURRENT_DATE")
    today_attendance = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT date) FROM attendance")
    total_days = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance")
    total_present = cursor.fetchone()[0]

    percentage = 0
    if total_days > 0 and total_students > 0:
        percentage = round((total_present / (total_students * total_days)) * 100, 2)

    cursor.close()
    conn.close()

    return {
        "total_students": total_students,
        "today_attendance": today_attendance,
        "attendance_percentage": percentage
    }    
@router.get("/students")
def get_students(current_user=Depends(require_role("admin"))):

    conn = get_connection()
    cursor = conn.cursor()

    # Get all students with their user info
    cursor.execute("""
        SELECT users.id, users.name, users.email
        FROM students
        JOIN users ON students.user_id = users.id
        ORDER BY users.name
    """)
    students = cursor.fetchall()

    results = []

    # total working days in system
    cursor.execute("SELECT COUNT(DISTINCT date) FROM attendance")
    total_days = cursor.fetchone()[0] or 0

    for user_id, name, email in students:

        # convert user → student id
        cursor.execute(
            "SELECT id FROM students WHERE user_id = %s",
            (user_id,)
        )
        student_id = cursor.fetchone()[0]

        # present days
        cursor.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id = %s",
            (student_id,)
        )
        present_days = cursor.fetchone()[0]

        percentage = 0
        if total_days > 0:
            percentage = round((present_days / total_days) * 100, 2)

        results.append({
            "name": name,
            "email": email,
            "attendance_percentage": percentage
        })

    cursor.close()
    conn.close()

    return results    
@router.get("/attendance-logs")
def attendance_logs(current_user=Depends(require_role("admin"))):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT users.name, attendance.date, attendance.time
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        JOIN users ON students.user_id = users.id
        ORDER BY attendance.date DESC, attendance.time DESC
    """)

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    logs = []

    for name, date, time in rows:
        logs.append({
            "name": name,
            "date": str(date),
            "time": str(time)
        })

    return logs
