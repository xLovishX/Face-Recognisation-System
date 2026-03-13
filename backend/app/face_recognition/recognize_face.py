import cv2
import face_recognition
import json
import numpy as np
from datetime import datetime
from backend.app.database.connection import get_connection

EYE_CLOSED_THRESHOLD = 0.21
EYE_OPEN_THRESHOLD = 0.24
BLINK_MIN_EAR_DROP = 0.03
MIN_VALID_LIVENESS_FRAMES = 3
MATCH_DISTANCE_THRESHOLD = 0.6
MAX_FRAME_WIDTH = 1280


def load_encodings():
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
        print("No registered faces found.")
        return [], [], []

    known_ids = []
    known_names = []
    known_encodings = []

    for user_id, name, encoding_str in rows:
        known_ids.append(user_id)
        known_names.append(name)
        known_encodings.append(np.array(json.loads(encoding_str)))

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
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(l_channel)
    enhanced_lab = cv2.merge((enhanced_l, a_channel, b_channel))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)


def _frame_variants(frame):
    normalized_frame = _normalize_frame(frame)

    if normalized_frame is None:
        return []

    variants = [normalized_frame]
    enhanced_frame = _enhance_frame(normalized_frame)

    if not np.array_equal(enhanced_frame, normalized_frame):
        variants.append(enhanced_frame)

    return variants


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
    if not known_encodings:
        return None, "No registered faces found"

    matches = face_recognition.compare_faces(known_encodings, face_encoding)
    face_distances = face_recognition.face_distance(known_encodings, face_encoding)

    if len(face_distances) == 0:
        return None, "Face not recognized"

    best_match_index = np.argmin(face_distances)

    if matches[best_match_index] and face_distances[best_match_index] < MATCH_DISTANCE_THRESHOLD:
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
    if not known_encodings:
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

    current_hour = datetime.now().hour

    # Attendance allowed between 8AM and 10AM
    if current_hour < 8 or current_hour > 10:
        print("Outside attendance window.")
        cursor.close()
        conn.close()
        return False, "Attendance not allowed at this time"

    cursor.execute(
        "SELECT id FROM students WHERE user_id = %s",
        (user_id,)
    )

    student_row = cursor.fetchone()

    if not student_row:
        print("Student record not found.")
        cursor.close()
        conn.close()
        return False, "Student record not found"

    student_id = student_row[0]

    today = datetime.now().date()
    current_time = datetime.now().time()

    try:
        cursor.execute("""
            INSERT INTO attendance (student_id, date, time)
            VALUES (%s, %s, %s)
            ON CONFLICT (student_id, date)
            DO NOTHING
        """, (student_id, today, current_time))

        inserted = cursor.rowcount > 0
        conn.commit()

        if inserted:
            print(f"Attendance marked for user_id {user_id}")
            cursor.close()
            conn.close()
            return True, None

        print(f"Attendance already marked for user_id {user_id}")
        cursor.close()
        conn.close()
        return False, "Attendance already marked today"

    except Exception as e:
        print("Error marking attendance:", e)
        conn.rollback()
        cursor.close()
        conn.close()
        return False, "Unable to mark attendance"
