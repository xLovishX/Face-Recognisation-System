from fastapi import FastAPI
from backend.app.admin.routes import router as admin_router
from fastapi.middleware.cors import CORSMiddleware
from backend.app.auth.routes import router as auth_router
from backend.app.attendance.routes import router as attendance_router
from backend.app.admin.routes import router as admin_router
app = FastAPI(title="Smart Attendance API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(attendance_router)
app.include_router(admin_router)

@app.get("/")
def root():
    return {"message": "Smart Attendance API Running"}