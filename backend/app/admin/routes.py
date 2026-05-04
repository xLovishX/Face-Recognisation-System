import base64
import json
import secrets
import string
from datetime import datetime

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from app.attendance.reporting import (
    get_admin_stats_summary,
    get_student_percentage_summary
)
from app.attendance.slot_utils import format_time_value, get_all_slots
from app.auth.dependencies import require_role
from app.auth.security import hash_password
from app.database.connection import get_connection
from app.face_recognition.recognize_face import (
    clear_encodings_cache,
    extract_face_encoding_for_registration,
    extract_face_encoding_from_frames_for_registration
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


def _decode_registration_frames(data):
    images = data.get("images")

    if isinstance(images, list) and images:
        return [_decode_base64_frame(image) for image in images]

    image = data.get("image")

    if image:
        return [_decode_base64_frame(image)]

    raise HTTPException(status_code=400, detail="At least one registration image is required")


def _parse_slot_time(value, field_name):
    normalized_value = str(value).strip()

    for time_format in ("%H:%M", "%I:%M %p"):
        try:
            return datetime.strptime(normalized_value.upper(), time_format).time()
        except ValueError:
            continue

    raise HTTPException(
        status_code=400,
        detail=f"{field_name} must be in HH:MM or HH:MM AM/PM format"
    )


def _parse_slot_payload(data):
    title = str(data.get("title", "")).strip()

    if not title:
        raise HTTPException(status_code=400, detail="Slot title is required")

    try:
        day_of_week = int(data.get("day_of_week"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Day of week is required") from exc

    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(status_code=400, detail="Day of week must be between 0 and 6")

    start_time = _parse_slot_time(data.get("start_time"), "Start time")
    end_time = _parse_slot_time(data.get("end_time"), "End time")

    teacher_user_id = data.get("teacher_user_id")

    if teacher_user_id in ("", None):
        teacher_user_id = None
    else:
        try:
            teacher_user_id = int(teacher_user_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Teacher selection is invalid") from exc

    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    return title, day_of_week, start_time, end_time, teacher_user_id


def _validate_teacher_user_id(cursor, teacher_user_id):
    if teacher_user_id is None:
        return None

    cursor.execute(
        """
        SELECT id, name
        FROM users
        WHERE id = %s
          AND role = 'teacher'
        """,
        (teacher_user_id,)
    )
    teacher_row = cursor.fetchone()

    if not teacher_row:
        raise HTTPException(status_code=400, detail="Selected teacher was not found")

    return {
        "id": teacher_row[0],
        "name": teacher_row[1]
    }


def _delete_user_with_role(cursor, user_id, role):
    cursor.execute(
        """
        DELETE FROM users
        WHERE id = %s
          AND role = %s
        RETURNING id, name, email
        """,
        (user_id, role)
    )
    deleted_user = cursor.fetchone()

    if not deleted_user:
        role_label = "Teacher" if role == "teacher" else "Student"
        raise HTTPException(status_code=404, detail=f"{role_label} not found")

    return {
        "id": deleted_user[0],
        "name": deleted_user[1],
        "email": deleted_user[2]
    }


@router.post("/register-student")
def register_student(
    data: dict,
    current_user=Depends(require_role("admin"))
):
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()

    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    frames = _decode_registration_frames(data)

    if len(frames) >= 3:
        encoding, recognition_error = extract_face_encoding_from_frames_for_registration(frames)
    else:
        encoding, recognition_error = extract_face_encoding_for_registration(frames[0])

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
        clear_encodings_cache()
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


@router.get("/teachers")
def get_teachers(current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                users.id,
                users.name,
                users.email,
                COUNT(attendance_slots.id) AS assigned_slot_count
            FROM users
            LEFT JOIN attendance_slots
              ON attendance_slots.teacher_user_id = users.id
            WHERE users.role = 'teacher'
            GROUP BY users.id, users.name, users.email
            ORDER BY users.name
        """)

        return [
            {
                "id": teacher_id,
                "name": name,
                "email": email,
                "assigned_slot_count": assigned_slot_count
            }
            for teacher_id, name, email, assigned_slot_count in cursor.fetchall()
        ]
    finally:
        cursor.close()
        conn.close()


@router.post("/teachers")
def create_teacher(data: dict, current_user=Depends(require_role("admin"))):
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()

    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    temporary_password = str(data.get("password", "")).strip() or _generate_temporary_password()
    password_hash = hash_password(temporary_password)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="A user with this email already exists")

        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, role)
            VALUES (%s, %s, %s, 'teacher')
            RETURNING id
            """,
            (name, email, password_hash)
        )
        teacher_user_id = cursor.fetchone()[0]
        conn.commit()

        return {
            "message": "Teacher created successfully",
            "temporary_password": temporary_password,
            "teacher": {
                "id": teacher_user_id,
                "name": name,
                "email": email,
                "assigned_slot_count": 0
            }
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error creating teacher:", exc)
        raise HTTPException(status_code=500, detail="Unable to create teacher")
    finally:
        cursor.close()
        conn.close()


@router.delete("/teachers/{teacher_user_id}")
def delete_teacher(teacher_user_id: int, current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        deleted_teacher = _delete_user_with_role(cursor, teacher_user_id, "teacher")
        conn.commit()

        return {
            "message": "Teacher deleted successfully",
            "teacher": deleted_teacher
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error deleting teacher:", exc)
        raise HTTPException(status_code=500, detail="Unable to delete teacher")
    finally:
        cursor.close()
        conn.close()


@router.get("/attendance-slots")
def get_attendance_slots(current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        return get_all_slots(cursor)
    finally:
        cursor.close()
        conn.close()


@router.post("/attendance-slots")
def create_attendance_slot(data: dict, current_user=Depends(require_role("admin"))):
    title, day_of_week, start_time, end_time, teacher_user_id = _parse_slot_payload(data)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        _validate_teacher_user_id(cursor, teacher_user_id)

        cursor.execute("""
            SELECT id
            FROM attendance_slots
            WHERE is_active = TRUE
              AND day_of_week = %s
              AND (
                (%s < end_time AND %s > start_time)
                OR (%s = start_time AND %s = end_time)
              )
        """, (day_of_week, start_time, end_time, start_time, end_time))

        if cursor.fetchone():
            raise HTTPException(
                status_code=400,
                detail="This slot overlaps an existing active slot"
            )

        cursor.execute("""
            INSERT INTO attendance_slots (
                title,
                day_of_week,
                start_time,
                end_time,
                teacher_user_id,
                is_active
            )
            VALUES (%s, %s, %s, %s, %s, TRUE)
        """, (title, day_of_week, start_time, end_time, teacher_user_id))

        conn.commit()
        return {"message": "Attendance slot created successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error creating attendance slot:", exc)
        raise HTTPException(status_code=500, detail="Unable to create attendance slot")
    finally:
        cursor.close()
        conn.close()


@router.put("/attendance-slots/{slot_id}/teacher")
def update_attendance_slot_teacher(
    slot_id: int,
    data: dict,
    current_user=Depends(require_role("admin"))
):
    teacher_user_id = data.get("teacher_user_id")

    if teacher_user_id in ("", None):
        teacher_user_id = None
    else:
        try:
            teacher_user_id = int(teacher_user_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Teacher selection is invalid") from exc

    conn = get_connection()
    cursor = conn.cursor()

    try:
        _validate_teacher_user_id(cursor, teacher_user_id)

        cursor.execute(
            """
            UPDATE attendance_slots
            SET teacher_user_id = %s
            WHERE id = %s
            RETURNING id
            """,
            (teacher_user_id, slot_id)
        )
        updated_row = cursor.fetchone()

        if not updated_row:
            raise HTTPException(status_code=404, detail="Attendance slot not found")

        conn.commit()
        return {"message": "Slot teacher updated successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error updating slot teacher:", exc)
        raise HTTPException(status_code=500, detail="Unable to update slot teacher")
    finally:
        cursor.close()
        conn.close()


@router.delete("/attendance-slots/{slot_id}")
def delete_attendance_slot(slot_id: int, current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT COUNT(*) FROM attendance WHERE slot_id = %s",
            (slot_id,)
        )
        linked_attendance_count = cursor.fetchone()[0]

        if linked_attendance_count > 0:
            raise HTTPException(
                status_code=400,
                detail="This class slot already has attendance history and cannot be deleted"
            )

        cursor.execute(
            "DELETE FROM attendance_slots WHERE id = %s RETURNING id",
            (slot_id,)
        )

        deleted_row = cursor.fetchone()

        if not deleted_row:
            raise HTTPException(status_code=404, detail="Attendance slot not found")

        conn.commit()
        return {"message": "Attendance slot deleted successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error deleting attendance slot:", exc)
        raise HTTPException(status_code=500, detail="Unable to delete attendance slot")
    finally:
        cursor.close()
        conn.close()


@router.get("/analytics")
def admin_analytics(admin=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance")
    total_attendance_records = cursor.fetchone()[0]

    today = datetime.now().date()
    cursor.execute(
        "SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date = %s",
        (today,)
    )
    today_attendance = cursor.fetchone()[0]

    cursor.execute("""
        SELECT students.id, users.name
        FROM students
        JOIN users ON students.user_id = users.id
        ORDER BY users.name
    """)
    student_rows = cursor.fetchall()

    analytics = []

    for student_id, name in student_rows:
        summary = get_student_percentage_summary(cursor, student_id)
        analytics.append({
            "student_name": name,
            "present_days": summary["present_days"],
            "scheduled_days": summary["scheduled_days"],
            "attendance_percentage": summary["attendance_percentage"]
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

    try:
        return get_admin_stats_summary(cursor)
    finally:
        cursor.close()
        conn.close()


@router.get("/students")
def get_students(current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT users.id, students.id, users.name, users.email
        FROM students
        JOIN users ON students.user_id = users.id
        ORDER BY users.name
    """)
    students = cursor.fetchall()

    results = []

    for user_id, student_id, name, email in students:
        summary = get_student_percentage_summary(cursor, student_id)
        results.append({
            "id": user_id,
            "name": name,
            "email": email,
            "present_days": summary["present_days"],
            "scheduled_days": summary["scheduled_days"],
            "attendance_percentage": summary["attendance_percentage"]
        })

    cursor.close()
    conn.close()

    return results


@router.delete("/students/{student_user_id}")
def delete_student(student_user_id: int, current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        deleted_student = _delete_user_with_role(cursor, student_user_id, "student")
        conn.commit()
        clear_encodings_cache()

        return {
            "message": "Student deleted successfully",
            "student": deleted_student
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error deleting student:", exc)
        raise HTTPException(status_code=500, detail="Unable to delete student")
    finally:
        cursor.close()
        conn.close()


@router.get("/attendance-logs")
def attendance_logs(current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            users.name,
            attendance.date,
            attendance.time,
            attendance_slots.title,
            CASE
                WHEN attendance.slot_id IS NULL THEN 'legacy'
                ELSE 'slot'
            END AS entry_type
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        JOIN users ON students.user_id = users.id
        LEFT JOIN attendance_slots ON attendance.slot_id = attendance_slots.id
        ORDER BY attendance.date DESC, attendance.time DESC
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    logs = []

    for name, date, time, slot_title, entry_type in rows:
        logs.append({
            "name": name,
            "date": str(date),
            "time": time.strftime("%H:%M") if time else "--:--",
            "time_display": format_time_value(time) if time else "--",
            "slot_title": slot_title or "Legacy Daily Mark",
            "entry_type": entry_type
        })

    return logs


@router.delete("/attendance-logs")
def clear_attendance_logs(current_user=Depends(require_role("admin"))):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM attendance")
        log_count = cursor.fetchone()[0]

        cursor.execute("DELETE FROM attendance")
        conn.commit()

        return {
            "message": "Attendance logs cleared successfully",
            "deleted_count": log_count
        }
    except Exception as exc:
        conn.rollback()
        print("Error clearing attendance logs:", exc)
        raise HTTPException(status_code=500, detail="Unable to clear attendance logs")
    finally:
        cursor.close()
        conn.close()
