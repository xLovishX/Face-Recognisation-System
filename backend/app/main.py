from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.routes import router as admin_router
from app.auth.routes import router as auth_router
from app.attendance.routes import router as attendance_router
from app.database.connection import create_tables
from app.profile.routes import router as profile_router
from app.teacher.routes import router as teacher_router

app = FastAPI(title="AttendX API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(attendance_router)
app.include_router(profile_router)
app.include_router(teacher_router)


@app.on_event("startup")
def ensure_database_schema():
    create_tables()

@app.get("/")
def root():
    return {"message": "AttendX API Running"}
