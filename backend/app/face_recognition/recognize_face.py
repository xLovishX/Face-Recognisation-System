import cv2
import face_recognition
import json
import numpy as np
from datetime import datetime
from threading import Lock
from backend.app.attendance.slot_utils import (
    enrich_slot_with_live_status,
    format_time_range,
    get_active_slot,
    get_today_slots,
    has_configured_slots
)
from backend.app.database.connection import get_connection

EYE_CLOSED_THRESHOLD = 0.21
EYE_OPEN_THRESHOLD = 0.24
BLINK_MIN_EAR_DROP = 0.03
MIN_VALID_LIVENESS_FRAMES = 3
MIN_REGISTRATION_FRAMES = 3
MATCH_DISTANCE_THRESHOLD = 0.6
MAX_FRAME_WIDTH = 960
ENCODING_CACHE_TTL_SECONDS = 60
_ENCODINGS_CACHE = {
    "loaded_at": None,
    "known_ids": [],
    "known_names": [],
    "known_encodings": []
}
_ENCODINGS_CACHE_LOCK = Lock()


def clear_encodings_cache():
    with _ENCODINGS_CACHE_LOCK:
        _ENCODINGS_CACHE["loaded_at"] = None
        _ENCODINGS_CACHE["known_ids"] = []
        _ENCODINGS_CACHE["known_names"] = []
        _ENCODINGS_CACHE["known_encodings"] = []


def load_encodings(force_refresh=False):
    with _ENCODINGS_CACHE_LOCK:
        loaded_at = _ENCODINGS_CACHE["loaded_at"]

        if (
            not force_refresh and
            loaded_at and
            (datetime.now() - loaded_at).total_seconds() < ENCODING_CACHE_TTL_SECONDS
        ):
            return (
                _ENCODINGS_CACHE["known_ids"],
                _ENCODINGS_CACHE["known_names"],
                _ENCODINGS_CACHE["known_encodings"]
            )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT users.id, users.name, students.encoding
        FROM students
        JOIN users ON students.user_id = users.id
    """)

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    if not rows:
        with _ENCODINGS_CACHE_LOCK:
            _ENCODINGS_CACHE["loaded_at"] = datetime.now()
            _ENCODINGS_CACHE["known_ids"] = []
            _ENCODINGS_CACHE["known_names"] = []
            _ENCODINGS_CACHE["known_encodings"] = []
        print("No registered faces found.")
        return [], [], []

    known_ids = []
    known_names = []
    known_encodings = []

    for user_id, name, encoding_str in rows:
        if not encoding_str:
            continue

        try:
            parsed_encoding = json.loads(encoding_str)
        except (TypeError, json.JSONDecodeError):
            continue

        known_ids.append(user_id)
        known_names.append(name)
        known_encodings.append(np.asarray(parsed_encoding, dtype=np.float64))

    with _ENCODINGS_CACHE_LOCK:
        _ENCODINGS_CACHE["loaded_at"] = datetime.now()
        _ENCODINGS_CACHE["known_ids"] = known_ids
        _ENCODINGS_CACHE["known_names"] = known_names
        _ENCODINGS_CACHE["known_encodings"] = known_encodings

    return known_ids, known_names, known_encodings


def _normalize_frame(frame):
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return None

    normalized_frame = frame

    if len(normalized_frame.shape) == 2:
        normalized_frame = cv2.cvtColor(normalized_frame, cv2.COLOR_GRAY2BGR)

    if len(normalized_frame.shape) != 3 or normalized_frame.shape[2] != 3:
        return None

    height, width = normalized_frame.shape[:2]
    if width > MAX_FRAME_WIDTH:
        scale = MAX_FRAME_WIDTH / float(width)
        normalized_frame = cv2.resize(
            normalized_frame,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA
        )

    return normalized_frame


def _enhance_frame(frame):
    lab_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab_frame)
    enhanced_l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_channel)
    enhanced_lab = cv2.merge((enhanced_l, a_channel, b_channel))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)


def _frame_variants(frame):
    normalized_frame = _normalize_frame(frame)

    if normalized_frame is None:
        return

    yield normalized_frame

    enhanced_frame = _enhance_frame(normalized_frame)
    if not np.array_equal(enhanced_frame, normalized_frame):
        yield enhanced_frame


def _extract_face_encoding(frame):
    for frame_variant in _frame_variants(frame):
        rgb_frame = cv2.cvtColor(frame_variant, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(
            rgb_frame,
            number_of_times_to_upsample=1,
            model="hog"
        )

        if not face_locations:
            continue

        if len(face_locations) > 1:
            return None, None, "Multiple faces detected"

        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        if not face_encodings:
            continue

        return face_encodings[0], face_locations[0], None

    return None, None, "No clear single face detected"


def _extract_face_data(frame):
    for frame_variant in _frame_variants(frame):
        rgb_frame = cv2.cvtColor(frame_variant, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(
            rgb_frame,
            number_of_times_to_upsample=1,
            model="hog"
        )

        if not face_locations:
            continue

        if len(face_locations) > 1:
            return None, None, "Multiple faces detected"

        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        if not face_encodings:
            continue

        face_landmarks = face_recognition.face_landmarks(rgb_frame, face_locations)

        if not face_landmarks:
            continue

        return face_encodings[0], face_landmarks[0], None

    return None, None, "No clear single face detected"


def extract_face_encoding_for_registration(frame):
    face_encoding, _, error = _extract_face_encoding(frame)

    if error:
        return None, error

    return face_encoding, None


def extract_face_encoding_from_frames_for_registration(frames):
    if not frames:
        return None, "Registration frames are required"

    valid_encodings = []
    no_face_count = 0
    multiple_faces_count = 0

    for frame in frames:
        face_encoding, _, error = _extract_face_encoding(frame)

        if error == "Multiple faces detected":
            multiple_faces_count += 1
            continue

        if error:
            no_face_count += 1
            continue

        valid_encodings.append(face_encoding)

    if len(valid_encodings) < MIN_REGISTRATION_FRAMES:
        return None, (
            "Registration failed. Keep one clear face centered in good light and try again "
            f"(valid frames: {len(valid_encodings)}/{len(frames)}, no-face: {no_face_count}, "
            f"multiple-faces: {multiple_faces_count})"
        )

    return np.mean(valid_encodings, axis=0), None


def _distance(point_a, point_b):
    return np.linalg.norm(np.array(point_a) - np.array(point_b))


def _eye_aspect_ratio(eye_points):
    vertical_1 = _distance(eye_points[1], eye_points[5])
    vertical_2 = _distance(eye_points[2], eye_points[4])
    horizontal = _distance(eye_points[0], eye_points[3])

    if horizontal == 0:
        return 0.0

    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def _recognize_user_id(face_encoding, known_ids, known_encodings):
    if len(known_encodings) == 0:
        return None, "No registered faces found"

    face_distances = face_recognition.face_distance(known_encodings, face_encoding)

    if len(face_distances) == 0:
        return None, "Face not recognized"

    best_match_index = int(np.argmin(face_distances))

    if face_distances[best_match_index] < MATCH_DISTANCE_THRESHOLD:
        return known_ids[best_match_index], None

    return None, "Face not recognized"


def recognize_face_from_frame(frame):
    known_ids, _, known_encodings = load_encodings()

    face_encoding, face_landmarks, error = _extract_face_data(frame)

    if error:
        return None, error

    return _recognize_user_id(face_encoding, known_ids, known_encodings)


def verify_blink_liveness(frames):
    if not frames:
        return None, "Liveness frames are required"

    known_ids, _, known_encodings = load_encodings()
    if len(known_encodings) == 0:
        return None, "No registered faces found"

    recognized_user_ids = []
    eye_ratios = []
    no_face_count = 0
    no_match_count = 0

    for frame in frames:
        face_encoding, face_landmarks, error = _extract_face_data(frame)

        if error:
            no_face_count += 1
            continue

        user_id, recognition_error = _recognize_user_id(
            face_encoding,
            known_ids,
            known_encodings
        )

        if recognition_error:
            no_match_count += 1
            continue

        left_eye = face_landmarks.get("left_eye")
        right_eye = face_landmarks.get("right_eye")

        if not left_eye or not right_eye:
            continue

        average_eye_ratio = (
            _eye_aspect_ratio(left_eye) + _eye_aspect_ratio(right_eye)
        ) / 2.0

        recognized_user_ids.append(user_id)
        eye_ratios.append(average_eye_ratio)

        if len(eye_ratios) >= MIN_VALID_LIVENESS_FRAMES:
            first_user_id = recognized_user_ids[0]

            if all(recognized_id == first_user_id for recognized_id in recognized_user_ids):
                min_eye_ratio = min(eye_ratios)
                max_eye_ratio = max(eye_ratios)
                ear_drop = max_eye_ratio - min_eye_ratio
                eyes_open_detected = max_eye_ratio >= EYE_OPEN_THRESHOLD
                eyes_closed_detected = (
                    min_eye_ratio <= EYE_CLOSED_THRESHOLD or
                    ear_drop >= BLINK_MIN_EAR_DROP
                )

                if eyes_open_detected and eyes_closed_detected:
                    return first_user_id, None

    if len(eye_ratios) < MIN_VALID_LIVENESS_FRAMES:
        return None, (
            "Liveness check failed. Keep your face centered in good light and try again "
            f"(valid frames: {len(eye_ratios)}/{len(frames)}, no-face: {no_face_count}, "
            f"no-match: {no_match_count})"
        )

    first_user_id = recognized_user_ids[0]

    if any(user_id != first_user_id for user_id in recognized_user_ids):
        return None, "Liveness check failed due to inconsistent face recognition"

    min_eye_ratio = min(eye_ratios)
    max_eye_ratio = max(eye_ratios)
    ear_drop = max_eye_ratio - min_eye_ratio

    eyes_open_detected = max_eye_ratio >= EYE_OPEN_THRESHOLD
    eyes_closed_detected = (min_eye_ratio <= EYE_CLOSED_THRESHOLD) or (ear_drop >= BLINK_MIN_EAR_DROP)

    if not (eyes_open_detected and eyes_closed_detected):
        return None, (
            "Blink not detected. Please blink naturally and try again "
            f"(eye ratio range: {min_eye_ratio:.3f}-{max_eye_ratio:.3f})"
        )

    return first_user_id, None


def mark_attendance(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    slot_context = None

    try:
        current_dt = datetime.now()
        active_slot = enrich_slot_with_live_status(
            cursor,
            get_active_slot(cursor, current_dt),
            current_dt
        )
        slots_are_configured = has_configured_slots(cursor)

        if slots_are_configured and not active_slot:
            today_slots = get_today_slots(cursor, current_dt)
            today_slot_windows = ", ".join(
                f"{slot['title']} ({slot['time_range_display']})"
                for slot in today_slots
            )

            if today_slot_windows:
                error_message = (
                    "Attendance is only allowed during an active timetable slot. "
                    f"Today's slots: {today_slot_windows}"
                )
            else:
                error_message = "Attendance is not scheduled for today"

            print("Outside configured attendance slot.")
            return False, error_message, None

        if active_slot and not active_slot.get("attendance_open", True):
            teacher_name = active_slot.get("teacher_name") or "the teacher"
            return (
                False,
                f"Attendance will open once {teacher_name} confirms {active_slot['title']}.",
                None
            )

        if not slots_are_configured:
            current_hour = current_dt.hour

            # Fallback window when no timetable slots are configured yet.
            if current_hour < 8 or current_hour > 10:
                print("Outside attendance window.")
                return False, "Attendance not allowed at this time", None

        cursor.execute(
            "SELECT id FROM students WHERE user_id = %s",
            (user_id,)
        )
        student_row = cursor.fetchone()

        if not student_row:
            print("Student record not found.")
            return False, "Student record not found", None

        student_id = student_row[0]
        today = current_dt.date()
        current_time = current_dt.time()
        slot_id = active_slot["id"] if active_slot else None

        if slot_id is None:
            cursor.execute(
                """
                SELECT 1
                FROM attendance
                WHERE student_id = %s
                  AND date = %s
                  AND slot_id IS NULL
                LIMIT 1
                """,
                (student_id, today)
            )
        else:
            cursor.execute(
                """
                SELECT 1
                FROM attendance
                WHERE student_id = %s
                  AND date = %s
                  AND slot_id = %s
                LIMIT 1
                """,
                (student_id, today, slot_id)
            )

        if cursor.fetchone():
            print(f"Attendance already marked for user_id {user_id}")
            duplicate_message = (
                "Attendance already marked for this class slot"
                if slot_id is not None
                else "Attendance already marked today"
            )
            return False, duplicate_message, None

        cursor.execute(
            """
            INSERT INTO attendance (student_id, date, time, slot_id)
            VALUES (%s, %s, %s, %s)
            """,
            (student_id, today, current_time, slot_id)
        )
        conn.commit()

        if active_slot:
            slot_context = {
                **active_slot,
                "time_range_display": active_slot.get("time_range_display")
                or format_time_range(active_slot["start_time"], active_slot["end_time"])
            }

        print(f"Attendance marked for user_id {user_id}")
        return True, None, slot_context

    except Exception as e:
        print("Error marking attendance:", e)
        conn.rollback()
        return False, "Unable to mark attendance", None
    finally:
        cursor.close()
        conn.close()
