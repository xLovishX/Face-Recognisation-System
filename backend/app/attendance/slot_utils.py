from datetime import datetime


WEEKDAY_LABELS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
]

SLOT_SELECT_COLUMNS = """
    attendance_slots.id,
    attendance_slots.title,
    attendance_slots.day_of_week,
    attendance_slots.start_time,
    attendance_slots.end_time,
    attendance_slots.is_active,
    attendance_slots.effective_from,
    attendance_slots.teacher_user_id,
    users.name AS teacher_name
"""

SLOT_SELECT_FROM = f"""
    SELECT {SLOT_SELECT_COLUMNS}
    FROM attendance_slots
    LEFT JOIN users ON attendance_slots.teacher_user_id = users.id
"""


def format_time_value(time_value):
    if time_value is None:
        return ""

    normalized_value = time_value

    if isinstance(time_value, str):
        stripped_value = time_value.strip()

        for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
            try:
                normalized_value = datetime.strptime(
                    stripped_value.upper(),
                    time_format
                ).time()
                break
            except ValueError:
                continue
        else:
            return stripped_value

    return normalized_value.strftime("%I:%M %p")


def format_time_range(start_time, end_time):
    return f"{format_time_value(start_time)} - {format_time_value(end_time)}"


def serialize_slot_row(slot_row):
    (
        slot_id,
        title,
        day_of_week,
        start_time,
        end_time,
        is_active,
        effective_from,
        teacher_user_id,
        teacher_name
    ) = slot_row
    start_time_raw = start_time.strftime("%H:%M")
    end_time_raw = end_time.strftime("%H:%M")

    return {
        "id": slot_id,
        "title": title,
        "day_of_week": day_of_week,
        "day_name": WEEKDAY_LABELS[day_of_week],
        "start_time": start_time_raw,
        "end_time": end_time_raw,
        "start_time_display": format_time_value(start_time),
        "end_time_display": format_time_value(end_time),
        "time_range_display": format_time_range(start_time, end_time),
        "teacher_user_id": teacher_user_id,
        "teacher_name": teacher_name,
        "teacher_confirmation_required": bool(teacher_user_id),
        "is_active": is_active,
        "effective_from": str(effective_from)
    }


def serialize_slot_confirmation_row(confirmation_row):
    (
        confirmation_id,
        slot_id,
        teacher_user_id,
        teacher_name,
        session_date,
        confirmed_at
    ) = confirmation_row

    return {
        "id": confirmation_id,
        "slot_id": slot_id,
        "teacher_user_id": teacher_user_id,
        "teacher_name": teacher_name,
        "session_date": str(session_date),
        "confirmed_at": confirmed_at.isoformat(),
        "confirmed_at_display": format_time_value(confirmed_at.time())
    }


def get_slot_confirmation(cursor, slot_id, current_dt=None):
    current_dt = current_dt or datetime.now()

    cursor.execute("""
        SELECT
            teacher_slot_confirmations.id,
            teacher_slot_confirmations.slot_id,
            teacher_slot_confirmations.teacher_user_id,
            users.name,
            teacher_slot_confirmations.session_date,
            teacher_slot_confirmations.confirmed_at
        FROM teacher_slot_confirmations
        JOIN users ON teacher_slot_confirmations.teacher_user_id = users.id
        WHERE teacher_slot_confirmations.slot_id = %s
          AND teacher_slot_confirmations.session_date = %s
        LIMIT 1
    """, (slot_id, current_dt.date()))

    row = cursor.fetchone()
    return serialize_slot_confirmation_row(row) if row else None


def enrich_slot_with_live_status(cursor, slot, current_dt=None):
    if not slot:
        return None

    current_dt = current_dt or datetime.now()
    enriched_slot = dict(slot)
    confirmation = None

    if enriched_slot.get("teacher_user_id"):
        confirmation = get_slot_confirmation(cursor, enriched_slot["id"], current_dt)

    enriched_slot["teacher_confirmation"] = confirmation
    enriched_slot["teacher_confirmed"] = confirmation is not None
    enriched_slot["attendance_open"] = (
        not enriched_slot.get("teacher_confirmation_required")
        or enriched_slot["teacher_confirmed"]
    )

    return enriched_slot


def enrich_slots_with_live_status(cursor, slots, current_dt=None):
    return [
        enrich_slot_with_live_status(cursor, slot, current_dt)
        for slot in slots
    ]


def get_all_slots(cursor, teacher_user_id=None):
    query = f"""
        {SLOT_SELECT_FROM}
        WHERE 1 = 1
    """
    params = []

    if teacher_user_id is not None:
        query += " AND attendance_slots.teacher_user_id = %s"
        params.append(teacher_user_id)

    query += """
        ORDER BY day_of_week, start_time, title
    """

    cursor.execute(query, tuple(params))

    return [serialize_slot_row(row) for row in cursor.fetchall()]


def get_today_slots(cursor, current_dt=None, teacher_user_id=None):
    current_dt = current_dt or datetime.now()
    query = f"""
        {SLOT_SELECT_FROM}
        WHERE attendance_slots.is_active = TRUE
          AND attendance_slots.effective_from <= %s
          AND attendance_slots.day_of_week = %s
    """
    params = [current_dt.date(), current_dt.weekday()]

    if teacher_user_id is not None:
        query += " AND attendance_slots.teacher_user_id = %s"
        params.append(teacher_user_id)

    query += """
        ORDER BY start_time, title
    """

    cursor.execute(query, tuple(params))

    return [serialize_slot_row(row) for row in cursor.fetchall()]


def get_active_slot(cursor, current_dt=None, teacher_user_id=None):
    current_dt = current_dt or datetime.now()
    current_time = current_dt.time()
    query = f"""
        {SLOT_SELECT_FROM}
        WHERE attendance_slots.is_active = TRUE
          AND attendance_slots.effective_from <= %s
          AND attendance_slots.day_of_week = %s
          AND attendance_slots.start_time <= %s
          AND attendance_slots.end_time > %s
    """
    params = [current_dt.date(), current_dt.weekday(), current_time, current_time]

    if teacher_user_id is not None:
        query += " AND attendance_slots.teacher_user_id = %s"
        params.append(teacher_user_id)

    query += """
        ORDER BY start_time, title
        LIMIT 1
    """

    cursor.execute(query, tuple(params))

    row = cursor.fetchone()
    return serialize_slot_row(row) if row else None


def has_configured_slots(cursor):
    cursor.execute(
        "SELECT COUNT(*) FROM attendance_slots WHERE is_active = TRUE"
    )

    return cursor.fetchone()[0] > 0
