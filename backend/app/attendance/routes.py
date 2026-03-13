import base64
import numpy as np
import cv2

from fastapi import APIRouter, Depends, HTTPException

from backend.app.auth.dependencies import require_role
from backend.app.database.connection import get_connection

from backend.app.face_recognition.recognize_face import (
    verify_blink_liveness,
    mark_attendance
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


# ---------------------------
# Attendance Percentage
# ---------------------------
@router.get("/percentage")
def attendance_percentage(current_user=Depends(require_role("student"))):

    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM students WHERE user_id = %s",
        (user_id,)
    )
    student_row = cursor.fetchone()

    if not student_row:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Student record not found")

    student_id = student_row[0]

    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id = %s",
        (student_id,)
    )
    present_days = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance"
    )
    total_days = cursor.fetchone()[0]

    percentage = 0
    if total_days > 0:
        percentage = round((present_days / total_days) * 100, 2)

    cursor.close()
    conn.close()

    return {
        "present_days": present_days,
        "total_working_days": total_days,
        "attendance_percentage": percentage
    }


# ---------------------------
# Get My Attendance
# ---------------------------
@router.get("/my")
def get_my_attendance(current_user=Depends(require_role("student"))):

    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM students WHERE user_id = %s",
        (user_id,)
    )

    student_row = cursor.fetchone()

    if not student_row:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Student record not found")

    student_id = student_row[0]

    cursor.execute(
        "SELECT date, time FROM attendance WHERE student_id = %s ORDER BY date DESC",
        (student_id,)
    )

    records = cursor.fetchall()

    cursor.close()
    conn.close()

    return [{"date": str(r[0]), "time": str(r[1])} for r in records]


# ---------------------------
# Manual Attendance (Button)
# ---------------------------
@router.post("/mark")
def mark_attendance_manual(current_user=Depends(require_role("student"))):
    raise HTTPException(
        status_code=403,
        detail="Manual attendance is disabled. Use face recognition."
    )


# ---------------------------
# Face Recognition Attendance
# ---------------------------
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

    success, attendance_error = mark_attendance(user_id)

    if not success:
        raise HTTPException(
            status_code=400,
            detail=attendance_error
        )

    return {
        "message": "Face recognized. Attendance marked successfully."
    }
