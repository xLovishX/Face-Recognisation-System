from datetime import datetime

from backend.app.attendance.slot_utils import format_time_value, has_configured_slots


def get_scheduled_dates(cursor, end_date=None):
    end_date = end_date or datetime.now().date()

    cursor.execute("""
        SELECT DISTINCT generated_date::date AS scheduled_date
        FROM attendance_slots slot
        CROSS JOIN generate_series(
            slot.effective_from,
            %s::date,
            INTERVAL '1 day'
        ) AS generated_date
        WHERE slot.is_active = TRUE
          AND slot.day_of_week = (EXTRACT(ISODOW FROM generated_date)::int - 1)
        ORDER BY scheduled_date
    """, (end_date,))

    return [row[0] for row in cursor.fetchall()]


def get_scheduled_slot_count_for_date(cursor, date_value):
    cursor.execute("""
        SELECT COUNT(*)
        FROM attendance_slots
        WHERE is_active = TRUE
          AND effective_from <= %s
          AND day_of_week = %s
    """, (date_value, date_value.weekday()))

    return cursor.fetchone()[0]


def get_student_percentage_summary(cursor, student_id):
    if not has_configured_slots(cursor):
        cursor.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance WHERE student_id = %s",
            (student_id,)
        )
        present_days = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT date) FROM attendance")
        scheduled_days = cursor.fetchone()[0]

        percentage = 0
        if scheduled_days > 0:
            percentage = round((present_days / scheduled_days) * 100, 2)

        return {
            "present_days": present_days,
            "scheduled_days": scheduled_days,
            "attendance_percentage": percentage
        }

    scheduled_dates = get_scheduled_dates(cursor)
    scheduled_date_set = set(scheduled_dates)

    cursor.execute(
        "SELECT DISTINCT date FROM attendance WHERE student_id = %s",
        (student_id,)
    )
    present_dates = {row[0] for row in cursor.fetchall()}

    present_days = len(present_dates & scheduled_date_set)
    scheduled_days = len(scheduled_dates)

    percentage = 0
    if scheduled_days > 0:
        percentage = round((present_days / scheduled_days) * 100, 2)

    return {
        "present_days": present_days,
        "scheduled_days": scheduled_days,
        "attendance_percentage": percentage
    }


def get_student_daily_summaries(cursor, student_id):
    cursor.execute("""
        SELECT
            date,
            COUNT(*) FILTER (WHERE slot_id IS NOT NULL) AS slot_marks,
            COUNT(*) FILTER (WHERE slot_id IS NULL) AS legacy_marks,
            MAX(time) AS last_marked_time
        FROM attendance
        WHERE student_id = %s
        GROUP BY date
        ORDER BY date DESC
    """, (student_id,))

    summaries = []

    for date_value, slot_marks, legacy_marks, last_marked_time in cursor.fetchall():
        slot_marks = slot_marks or 0
        legacy_marks = legacy_marks or 0

        if slot_marks > 0:
            scheduled_slots = get_scheduled_slot_count_for_date(cursor, date_value)
            status = "full" if scheduled_slots > 0 and slot_marks >= scheduled_slots else "partial"
            marked_slots = slot_marks
        else:
            scheduled_slots = 0
            status = "legacy"
            marked_slots = legacy_marks

        summaries.append({
            "date": str(date_value),
            "marked_slots": marked_slots,
            "scheduled_slots": scheduled_slots,
            "last_marked_time": last_marked_time.strftime("%H:%M") if last_marked_time else "--:--",
            "last_marked_time_display": format_time_value(last_marked_time) if last_marked_time else "--",
            "status": status
        })

    return summaries


def get_admin_stats_summary(cursor):
    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    today = datetime.now().date()

    cursor.execute(
        "SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date = %s",
        (today,)
    )
    today_attendance = cursor.fetchone()[0]

    if not has_configured_slots(cursor):
        cursor.execute("SELECT COUNT(DISTINCT date) FROM attendance")
        scheduled_days = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM attendance")
        total_present = cursor.fetchone()[0]

        percentage = 0
        if scheduled_days > 0 and total_students > 0:
            percentage = round((total_present / (total_students * scheduled_days)) * 100, 2)

        return {
            "total_students": total_students,
            "today_attendance": today_attendance,
            "attendance_percentage": percentage,
            "scheduled_days": scheduled_days
        }

    scheduled_dates = get_scheduled_dates(cursor)
    scheduled_date_set = set(scheduled_dates)
    scheduled_days = len(scheduled_dates)

    cursor.execute("SELECT DISTINCT student_id, date FROM attendance")
    present_student_days = {
        (student_id, date_value)
        for student_id, date_value in cursor.fetchall()
        if date_value in scheduled_date_set
    }

    percentage = 0
    if scheduled_days > 0 and total_students > 0:
        percentage = round(
            (len(present_student_days) / (total_students * scheduled_days)) * 100,
            2
        )

    return {
        "total_students": total_students,
        "today_attendance": today_attendance,
        "attendance_percentage": percentage,
        "scheduled_days": scheduled_days
    }
