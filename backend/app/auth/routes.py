from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.app.database.connection import get_connection
from backend.app.auth.security import (
    hash_password,
    verify_password,
    create_access_token
)
from backend.app.auth.dependencies import require_role
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter(prefix="/auth", tags=["Authentication"])


class UserRegister(BaseModel):
    name: str
    email: str
    password: str
    role: str  # admin, teacher, student


class StudentCreate(BaseModel):
    name: str
    email: str
    password: str


@router.post("/create-student")
def create_student(student: StudentCreate, admin=Depends(require_role("admin"))):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = %s", (student.email,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pw = hash_password(student.password)

    # 1️⃣ Insert into users and get ID
    cursor.execute(
        "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING id",
        (student.name, student.email, hashed_pw, "student")
    )

    user_id = cursor.fetchone()[0]

    # 2️⃣ Insert into students table
    cursor.execute(
        "INSERT INTO students (user_id, encoding) VALUES (%s, %s)",
        (user_id, "")  # empty encoding for now
    )

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "message": "Student user created successfully",
        "user_id": user_id
    }


@router.post("/register")
def register(user: UserRegister):

    if user.role not in ["admin", "teacher", "student"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = %s", (user.email,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pw = hash_password(user.password)

    cursor.execute(
        "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
        (user.name, user.email, hashed_pw, user.role)
    )

    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "User registered successfully"}


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, password_hash, role FROM users WHERE email = %s",
        (form_data.username,)
    )

    user = cursor.fetchone()

    cursor.close()
    conn.close()
    print("User fetched from DB:", user)

    if not user:
        raise HTTPException(status_code=400, detail="Invalid credentials")

    user_id, password_hash, role = user

    if not verify_password(form_data.password, password_hash):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    access_token = create_access_token({
        "sub": str(user_id),
        "role": role
    })

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role
    }