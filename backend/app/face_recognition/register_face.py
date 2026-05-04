import cv2
import face_recognition
import json
import numpy as np
from app.database.connection import get_connection


def register_face():
    user_id = input("Enter student user_id: ").strip()

    if not user_id.isdigit():
        print("Invalid user_id.")
        return

    user_id = int(user_id)

    conn = get_connection()
    cursor = conn.cursor()

    # PostgreSQL uses %s
    cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
    result = cursor.fetchone()
    print("Query result:", result)

    if not result or result[0] != "student":
        print("User not found or not a student.")
        cursor.close()
        conn.close()
        return

    cursor.close()
    conn.close()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Camera not accessible.")
        return

    print("Press 's' to capture face. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = face_recognition.face_locations(rgb_frame)

        for (top, right, bottom, left) in faces:
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)

        cv2.imshow("Register Face", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            if len(faces) != 1:
                print("Ensure exactly one face visible.")
                continue

            encoding = face_recognition.face_encodings(rgb_frame, faces)[0]
            encoding_str = json.dumps(encoding.tolist())

            conn = get_connection()
            cursor = conn.cursor()

            # PostgreSQL UPSERT (replace equivalent)
            cursor.execute("""
                INSERT INTO students (user_id, encoding)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET encoding = EXCLUDED.encoding
            """, (user_id, encoding_str))

            conn.commit()

            cursor.execute("SELECT id, user_id FROM students;")
            print("Students table now:", cursor.fetchall())

            cursor.close()
            conn.close()

            print("Face registered successfully.")
            break

        if key == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    register_face()
