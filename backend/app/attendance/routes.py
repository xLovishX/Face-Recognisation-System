import base64
from datetime import datetime

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from app.attendance.reporting import (
    get_student_daily_summaries,
    get_student_percentage_summary
)
from app.attendance.slot_utils import (
    WEEKDAY_LABELS,
    enrich_slot_with_live_status,
    enrich_slots_with_live_status,
    get_active_slot,
    get_today_slots,
    has_configured_slots
)
from app.auth.dependencies import require_role
from app.database.connection import get_connection
from app.face_recognition.recognize_face import (
    mark_attendance,
    verify_blink_liveness
)

router = APIRouter(prefix="/attendance", tags=["Attendance"])


def _decode_base64_frame(image):
    if not isinstance(image, str) or "," not in image:
        raise ValueError("Invalid frame payload")

    image_data = image.split(",", 1)[1]
    img_bytes = base64.b64decode(image_data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        raise ValueError("Invalid frame")

    return frame


def _get_student_id_or_400(cursor, user_id):
    cursor.execute(
        "SELECT id FROM students WHERE user_id = %s",
        (user_id,)
    )
    student_row = cursor.fetchone()

    if not student_row:
        raise HTTPException(status_code=400, detail="Student record not found")

    return student_row[0]


@router.get("/percentage")
def attendance_percentage(current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    try:
        student_id = _get_student_id_or_400(cursor, user_id)
        summary = get_student_percentage_summary(cursor, student_id)

        return {
            "present_days": summary["present_days"],
            "scheduled_days": summary["scheduled_days"],
            "total_working_days": summary["scheduled_days"],
            "attendance_percentage": summary["attendance_percentage"]
        }
    finally:
        cursor.close()
        conn.close()


@router.get("/my")
def get_my_attendance(current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    try:
        student_id = _get_student_id_or_400(cursor, user_id)
        return get_student_daily_summaries(cursor, student_id)
    finally:
        cursor.close()
        conn.close()


@router.get("/slot-status")
def get_slot_status(current_user=Depends(require_role("student"))):
    conn = get_connection()
    cursor = conn.cursor()
    current_dt = datetime.now()

    try:
        active_slot = enrich_slot_with_live_status(
            cursor,
            get_active_slot(cursor, current_dt),
            current_dt
        )
        today_slots = enrich_slots_with_live_status(
            cursor,
            get_today_slots(cursor, current_dt),
            current_dt
        )

        return {
            "configured_slots": has_configured_slots(cursor),
            "current_day": WEEKDAY_LABELS[current_dt.weekday()],
            "active_slot": active_slot,
            "today_slots": today_slots,
            "attendance_open": (not active_slot) or bool(active_slot.get("attendance_open"))
        }
    finally:
        cursor.close()
        conn.close()


@router.post("/mark")
def mark_attendance_manual(current_user=Depends(require_role("student"))):
    raise HTTPException(
        status_code=403,
        detail="Manual attendance is disabled. Use face recognition."
    )


@router.post("/mark-face")
def mark_face(
    data: dict,
    current_user=Depends(require_role("student"))
):
    current_user_id = int(current_user["sub"])

    try:
        image_list = data["images"]

        if not isinstance(image_list, list) or len(image_list) < 3:
            raise ValueError("At least 3 frames are required")

        frames = [_decode_base64_frame(image) for image in image_list]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid liveness image data")

    user_id, recognition_error = verify_blink_liveness(frames)

    if not user_id:
        raise HTTPException(status_code=400, detail=recognition_error)

    if user_id != current_user_id:
        raise HTTPException(
            status_code=403,
            detail="Recognized face does not match the logged-in student"
        )

    success, attendance_error, slot_context = mark_attendance(user_id)

    if not success:
        raise HTTPException(status_code=400, detail=attendance_error)

    message = "Face recognized. Attendance marked successfully."

    if slot_context:
        message = (
            f"Face recognized. Attendance marked for {slot_context['title']} "
            f"({slot_context['time_range_display']})."
        )

    return {
        "message": message,
        "slot": slot_context
    }
