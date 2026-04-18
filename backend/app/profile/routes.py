import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException

from backend.app.auth.dependencies import require_role
from backend.app.auth.security import hash_password, verify_password
from backend.app.database.connection import get_connection

MAX_PROFILE_PHOTO_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/profile", tags=["Profile"])


def _serialize_profile_row(profile_row):
    (
        name,
        email,
        role,
        phone_number,
        department,
        academic_year,
        student_code,
        bio,
        profile_photo
    ) = profile_row

    return {
        "name": name,
        "email": email,
        "role": role,
        "phone_number": phone_number or "",
        "department": department or "",
        "academic_year": academic_year or "",
        "student_code": student_code or "",
        "bio": bio or "",
        "profile_photo": profile_photo
    }


def _fetch_profile(cursor, user_id):
    cursor.execute(
        """
        SELECT
            users.name,
            users.email,
            users.role,
            students.phone_number,
            students.department,
            students.academic_year,
            students.student_code,
            students.bio,
            students.profile_photo
        FROM students
        JOIN users ON students.user_id = users.id
        WHERE users.id = %s
        """,
        (user_id,)
    )
    profile_row = cursor.fetchone()

    if not profile_row:
        raise HTTPException(status_code=400, detail="Student profile not found")

    return _serialize_profile_row(profile_row)


def _validate_profile_photo(image):
    if not isinstance(image, str) or "," not in image or not image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Invalid profile photo data")

    try:
        image_bytes = base64.b64decode(image.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid profile photo data") from exc

    if len(image_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Profile photo must be 2 MB or smaller")

    return image


@router.get("/me")
def get_my_profile(current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    try:
        return _fetch_profile(cursor, user_id)
    finally:
        cursor.close()
        conn.close()


@router.put("/me")
def update_my_profile(data: dict, current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])
    name = str(data.get("name", "")).strip()

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    phone_number = str(data.get("phone_number", "")).strip() or None
    department = str(data.get("department", "")).strip() or None
    academic_year = str(data.get("academic_year", "")).strip() or None
    student_code = str(data.get("student_code", "")).strip() or None
    bio = str(data.get("bio", "")).strip() or None

    if bio and len(bio) > 500:
        raise HTTPException(status_code=400, detail="Bio must be 500 characters or fewer")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE users SET name = %s WHERE id = %s",
            (name, user_id)
        )
        cursor.execute(
            """
            UPDATE students
            SET
                phone_number = %s,
                department = %s,
                academic_year = %s,
                student_code = %s,
                bio = %s
            WHERE user_id = %s
            """,
            (phone_number, department, academic_year, student_code, bio, user_id)
        )
        conn.commit()

        return {
            "message": "Profile updated successfully",
            "profile": _fetch_profile(cursor, user_id)
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error updating profile:", exc)
        raise HTTPException(status_code=500, detail="Unable to update profile")
    finally:
        cursor.close()
        conn.close()


@router.post("/change-password")
def change_my_password(data: dict, current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])
    current_password = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))

    if not current_password or not new_password:
        raise HTTPException(status_code=400, detail="Current and new password are required")

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT password_hash FROM users WHERE id = %s",
            (user_id,)
        )
        user_row = cursor.fetchone()

        if not user_row:
            raise HTTPException(status_code=400, detail="User not found")

        if not verify_password(current_password, user_row[0]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(new_password), user_id)
        )
        conn.commit()

        return {"message": "Password changed successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        print("Error changing password:", exc)
        raise HTTPException(status_code=500, detail="Unable to change password")
    finally:
        cursor.close()
        conn.close()


@router.post("/photo")
def upload_profile_photo(data: dict, current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])
    image = _validate_profile_photo(data.get("image"))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE students SET profile_photo = %s WHERE user_id = %s",
            (image, user_id)
        )
        conn.commit()

        return {
            "message": "Profile photo updated successfully",
            "profile_photo": image
        }
    except Exception as exc:
        conn.rollback()
        print("Error updating profile photo:", exc)
        raise HTTPException(status_code=500, detail="Unable to update profile photo")
    finally:
        cursor.close()
        conn.close()


@router.delete("/photo")
def remove_profile_photo(current_user=Depends(require_role("student"))):
    user_id = int(current_user["sub"])

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE students SET profile_photo = NULL WHERE user_id = %s",
            (user_id,)
        )
        conn.commit()

        return {"message": "Profile photo removed successfully"}
    except Exception as exc:
        conn.rollback()
        print("Error removing profile photo:", exc)
        raise HTTPException(status_code=500, detail="Unable to remove profile photo")
    finally:
        cursor.close()
        conn.close()
