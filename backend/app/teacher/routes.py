from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.attendance.slot_utils import (
    WEEKDAY_LABELS,
    enrich_slot_with_live_status,
    enrich_slots_with_live_status,
    get_all_slots,
    get_active_slot,
    get_today_slots
)
from app.auth.dependencies import require_role
from app.database.connection import get_connection

router = APIRouter(prefix="/teacher", tags=["Teacher"])


@router.get("/slot-status")
def get_teacher_slot_status(current_user=Depends(require_role("teacher"))):
    teacher_user_id = int(current_user["sub"])
    conn = get_connection()
    cursor = conn.cursor()
    current_dt = datetime.now()

    try:
        active_slot = enrich_slot_with_live_status(
            cursor,
            get_active_slot(cursor, current_dt, teacher_user_id=teacher_user_id),
            current_dt
        )
        today_slots = enrich_slots_with_live_status(
            cursor,
            get_today_slots(cursor, current_dt, teacher_user_id=teacher_user_id),
            current_dt
        )
        assigned_slots = enrich_slots_with_live_status(
            cursor,
            get_all_slots(cursor, teacher_user_id=teacher_user_id),
            current_dt
        )

        return {
            "current_day": WEEKDAY_LABELS[current_dt.weekday()],
            "active_slot": active_slot,
            "today_slots": today_slots,
            "assigned_slots": assigned_slots
        }
    finally:
        cursor.close()
        conn.close()


@router.post("/confirm-active-slot")
def confirm_active_slot(current_user=Depends(require_role("teacher"))):
    teacher_user_id = int(current_user["sub"])
    conn = get_connection()
    cursor = conn.cursor()
    current_dt = datetime.now()

    try:
        active_slot = enrich_slot_with_live_status(
            cursor,
            get_active_slot(cursor, current_dt, teacher_user_id=teacher_user_id),
            current_dt
        )

        if not active_slot:
            raise HTTPException(
                status_code=400,
                detail="You do not have an active assigned class slot right now"
            )

        if active_slot.get("teacher_confirmed"):
            return {
                "message": "Class already confirmed for today",
                "slot": active_slot
            }

        cursor.execute(
            """
            INSERT INTO teacher_slot_confirmations (
                slot_id,
                teacher_user_id,
                session_date,
                confirmed_at
            )
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (slot_id, session_date)
            DO UPDATE SET
                teacher_user_id = EXCLUDED.teacher_user_id,
                confirmed_at = EXCLUDED.confirmed_at
            """,
            (active_slot["id"], teacher_user_id, current_dt.date())
        )
        conn.commit()

        refreshed_slot = enrich_slot_with_live_status(
            cursor,
            get_active_slot(cursor, current_dt, teacher_user_id=teacher_user_id),
            current_dt
        )

        return {
            "message": f"{active_slot['title']} confirmed. Students can now mark attendance.",
            "slot": refreshed_slot
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error confirming active slot:", exc)
        raise HTTPException(status_code=500, detail="Unable to confirm the active class slot")
    finally:
        cursor.close()
        conn.close()
