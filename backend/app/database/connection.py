import psycopg2


def get_connection():
    return psycopg2.connect(
        host="localhost",
        database="smart_attendance",
        user="postgres",
        password="lr0205"  # <-- replace with your real password
    )


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # USERS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(20) NOT NULL CHECK(role IN ('admin','teacher','student'))
    );
    """)

    # STUDENTS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE,
        encoding TEXT NOT NULL,
        phone_number VARCHAR(32),
        department VARCHAR(255),
        academic_year VARCHAR(64),
        student_code VARCHAR(100),
        bio TEXT,
        profile_photo TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # ATTENDANCE SLOTS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance_slots (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        teacher_user_id INTEGER,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
        FOREIGN KEY(teacher_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)

    # TEACHER SLOT CONFIRMATIONS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teacher_slot_confirmations (
        id SERIAL PRIMARY KEY,
        slot_id INTEGER NOT NULL,
        teacher_user_id INTEGER NOT NULL,
        session_date DATE NOT NULL,
        confirmed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(slot_id) REFERENCES attendance_slots(id) ON DELETE CASCADE,
        FOREIGN KEY(teacher_user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # ATTENDANCE TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id SERIAL PRIMARY KEY,
        student_id INTEGER,
        date DATE,
        time TIME,
        slot_id INTEGER,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
        FOREIGN KEY(slot_id) REFERENCES attendance_slots(id) ON DELETE RESTRICT
    );
    """)

    # Schema migration for older databases
    cursor.execute("""
    ALTER TABLE attendance_slots
    ADD COLUMN IF NOT EXISTS effective_from DATE NOT NULL DEFAULT CURRENT_DATE
    """)

    cursor.execute("""
    ALTER TABLE attendance_slots
    ADD COLUMN IF NOT EXISTS teacher_user_id INTEGER
    """)

    cursor.execute("""
    ALTER TABLE attendance
    ADD COLUMN IF NOT EXISTS slot_id INTEGER
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS phone_number VARCHAR(32)
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS department VARCHAR(255)
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS academic_year VARCHAR(64)
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS student_code VARCHAR(100)
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS bio TEXT
    """)

    cursor.execute("""
    ALTER TABLE students
    ADD COLUMN IF NOT EXISTS profile_photo TEXT
    """)

    cursor.execute("""
    ALTER TABLE attendance
    DROP CONSTRAINT IF EXISTS attendance_student_id_date_key
    """)

    cursor.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'attendance_slots_teacher_user_id_fkey'
        ) THEN
            ALTER TABLE attendance_slots
            ADD CONSTRAINT attendance_slots_teacher_user_id_fkey
            FOREIGN KEY (teacher_user_id)
            REFERENCES users(id)
            ON DELETE SET NULL;
        END IF;
    END
    $$;
    """)

    cursor.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'attendance_slot_id_fkey'
        ) THEN
            ALTER TABLE attendance
            ADD CONSTRAINT attendance_slot_id_fkey
            FOREIGN KEY (slot_id)
            REFERENCES attendance_slots(id)
            ON DELETE RESTRICT;
        END IF;
    END
    $$;
    """)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS attendance_legacy_unique_idx
    ON attendance (student_id, date)
    WHERE slot_id IS NULL
    """)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS attendance_slot_unique_idx
    ON attendance (student_id, date, slot_id)
    WHERE slot_id IS NOT NULL
    """)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS teacher_slot_confirmations_unique_idx
    ON teacher_slot_confirmations (slot_id, session_date)
    """)

    conn.commit()
    cursor.close()
    conn.close()

    print("Tables created successfully in PostgreSQL.")
