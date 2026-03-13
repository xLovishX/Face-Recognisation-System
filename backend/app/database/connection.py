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
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # ATTENDANCE TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id SERIAL PRIMARY KEY,
        student_id INTEGER,
        date DATE,
        time TIME,
        UNIQUE(student_id, date),
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    cursor.close()
    conn.close()

    print("Tables created successfully in PostgreSQL.")