import hashlib
from database import fetch_all, execute_sql


PROFESIONES_DISPONIBLES = [
    "Fisioterapeuta",
    "Terapeuta ocupacional",
    "Logopeda",
    "Médico rehabilitador",
    "Administrador",
]

PASSWORD_TEMPORAL_POR_DEFECTO = "123456789"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


def init_auth_db(conn):
    execute_sql(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            dni TEXT NOT NULL UNIQUE,
            profession TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('ADMIN', 'CLINICO')),
            must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    execute_sql(conn, """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS profession TEXT;
    """)

    execute_sql(conn, """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT TRUE;
    """)


def create_user(conn, username: str, full_name: str, dni: str, profession: str, role: str):
    password_hash = hash_password(PASSWORD_TEMPORAL_POR_DEFECTO)

    execute_sql(conn, """
        INSERT INTO users (
            username, full_name, dni, profession, password_hash, role,
            must_change_password, active
        )
        VALUES (%s, %s, %s, %s, %s, %s, TRUE, TRUE)
        ON CONFLICT (username) DO NOTHING
    """, (username, full_name, dni, profession, password_hash, role))


def ensure_admin_user(conn):
    _, rows = fetch_all(conn, "SELECT id FROM users WHERE username = %s", ("admin",))
    if not rows:
        execute_sql(conn, """
            INSERT INTO users (
                username, full_name, dni, profession, password_hash, role,
                must_change_password, active
            )
            VALUES (%s, %s, %s, %s, %s, %s, FALSE, TRUE)
        """, (
            "admin",
            "Administrador del sistema",
            "00000000T",
            "Administrador",
            hash_password("Admin1234"),
            "ADMIN",
        ))


def authenticate_user(conn, username: str, password: str):
    _, rows = fetch_all(conn, """
        SELECT id, username, full_name, dni, profession, password_hash, role, must_change_password, active
        FROM users
        WHERE username = %s
        LIMIT 1
    """, (username,))

    if not rows:
        return None

    user_id, db_username, full_name, dni, profession, password_hash, role, must_change_password, active = rows[0]

    if not active:
        return None

    if not verify_password(password, password_hash):
        return None

    return {
        "id": user_id,
        "username": db_username,
        "full_name": full_name,
        "dni": dni,
        "profession": profession,
        "role": role,
        "must_change_password": must_change_password,
    }


def change_user_password(conn, user_id: int, new_password: str):
    new_hash = hash_password(new_password)

    execute_sql(conn, """
        UPDATE users
        SET password_hash = %s,
            must_change_password = FALSE
        WHERE id = %s
    """, (new_hash, user_id))


def reset_user_password_to_default(conn, user_id: int):
    default_hash = hash_password(PASSWORD_TEMPORAL_POR_DEFECTO)

    execute_sql(conn, """
        UPDATE users
        SET password_hash = %s,
            must_change_password = TRUE
        WHERE id = %s
    """, (default_hash, user_id))


def get_all_users(conn):
    _, rows = fetch_all(conn, """
        SELECT id, username, full_name, dni, profession, role, must_change_password, active, created_at
        FROM users
        ORDER BY username ASC
    """)
    return rows