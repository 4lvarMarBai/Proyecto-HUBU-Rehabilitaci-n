# app.py
# Ejecuta con: python -m streamlit run app.py

import os
import re
import calendar
from collections import defaultdict
from datetime import datetime, UTC, date, time

import psycopg
import streamlit as st


# -------------------- Config --------------------
PRIORIDADES = ["urgente", "preferente", "ordinario"]

DIAS_2_MESES = 60
DIAS_3_MESES = 90
DIAS_6_MESES = 183

CLAVE_CONFIGURACION = "admin123"

DIAS_SEMANA = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]

TIPOS_HUECO = ["SIMPLE", "DOBLE"]
TURNOS = ["MAÑANA", "TARDE"]
TRANSPORTES = ["NORMAL", "AMBULANCIA"]
HORAS_AMBULANCIA = ["09:00", "12:00"]
REGLAS_COORDINACION = ["NINGUNA", "MISMO_DIA", "DIAS_ALTERNOS"]

FISIO_ESPECIALIDADES = ["Electroterapia", "Cinesiterapia"]


# -------------------- DB helpers --------------------
def get_database_url() -> str:
    db_url = st.secrets.get("DATABASE_URL", None)
    if not db_url:
        db_url = os.getenv("DATABASE_URL")

    if not db_url:
        raise RuntimeError(
            "No se ha encontrado DATABASE_URL. "
            "Configúrala en .streamlit/secrets.toml o como variable de entorno."
        )

    return db_url


@st.cache_resource
def get_conn():
    conn = psycopg.connect(get_database_url(), autocommit=True)
    return conn


def init_db(conn):
    with conn.cursor() as cur:
        # -------------------- Configuración dinámica --------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS specialties_config (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            requires_subspecialty BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS subspecialties_config (
            id BIGSERIAL PRIMARY KEY,
            specialty_name TEXT NOT NULL,
            name TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (specialty_name, name)
        );
        """)

        cur.execute("""
            INSERT INTO specialties_config (name, active, requires_subspecialty)
            VALUES
                ('Electroterapia', TRUE, FALSE),
                ('Terapia ocupacional', TRUE, FALSE),
                ('Logopedia', TRUE, FALSE),
                ('Cinesiterapia', TRUE, TRUE)
            ON CONFLICT (name) DO NOTHING;
        """)

        for area in [
            "Linfedema",
            "Suelo pélvico",
            "Infantil",
            "FT respiratorio",
            "RHB cardiaca",
            "RHB neurológica",
            "Columna/raquis",
            "General",
        ]:
            cur.execute("""
                INSERT INTO subspecialties_config (specialty_name, name, active)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (specialty_name, name) DO NOTHING;
            """, ("Cinesiterapia", area))

        # -------------------- Tablas principales --------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            priority_level TEXT NOT NULL CHECK(priority_level IN ('urgente','preferente','ordinario')),
            specialty TEXT NOT NULL,
            subspecialty TEXT,
            prescribed_sessions INTEGER,
            slot_type TEXT,
            time_preference TEXT,
            transport_mode TEXT,
            preferred_hour TEXT,
            coordination_rule TEXT,
            request_date TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'EN_ESPERA',
            eligible BOOLEAN NOT NULL DEFAULT TRUE
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS rehab_active (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            specialty TEXT NOT NULL,
            subspecialty TEXT,
            prescribed_sessions INTEGER,
            slot_type TEXT,
            time_preference TEXT,
            transport_mode TEXT,
            preferred_hour TEXT,
            coordination_rule TEXT,
            attendance_days TEXT,
            start_date TIMESTAMPTZ NOT NULL,
            source_waitlist_id BIGINT NOT NULL,
            assigned_by TEXT NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVO',
            discharge_reason TEXT,
            discharge_comment TEXT,
            discharged_at TIMESTAMPTZ,
            FOREIGN KEY(source_waitlist_id) REFERENCES waitlist(id)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS assignment_log (
            id BIGSERIAL PRIMARY KEY,
            event TEXT NOT NULL,
            waitlist_id BIGINT,
            rehab_active_id BIGINT,
            patient_id TEXT NOT NULL,
            specialty TEXT,
            subspecialty TEXT,
            chosen_priority_level TEXT,
            wait_days INTEGER,
            rule_applied TEXT,
            reason TEXT,
            comment TEXT,
            actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS treatment_sessions (
            id BIGSERIAL PRIMARY KEY,
            rehab_active_id BIGINT NOT NULL,
            patient_id TEXT NOT NULL,
            specialty TEXT NOT NULL,
            subspecialty TEXT,
            session_date DATE NOT NULL,
            session_time TIME,
            status TEXT NOT NULL,
            absence_reason TEXT,
            out_of_schedule_reason TEXT,
            recorded_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY(rehab_active_id) REFERENCES rehab_active(id)
        );
        """)

        # Compatibilidad con instalaciones previas
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS discharge_comment TEXT;")
        cur.execute("ALTER TABLE assignment_log ADD COLUMN IF NOT EXISTS comment TEXT;")
        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS prescribed_sessions INTEGER;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS prescribed_sessions INTEGER;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS attendance_days TEXT;")
        cur.execute("ALTER TABLE treatment_sessions ADD COLUMN IF NOT EXISTS out_of_schedule_reason TEXT;")

        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS slot_type TEXT;")
        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS time_preference TEXT;")
        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS transport_mode TEXT;")
        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS preferred_hour TEXT;")
        cur.execute("ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS coordination_rule TEXT;")

        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS slot_type TEXT;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS time_preference TEXT;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS transport_mode TEXT;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS preferred_hour TEXT;")
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS coordination_rule TEXT;")

        cur.execute("ALTER TABLE waitlist ALTER COLUMN prescribed_sessions DROP NOT NULL;")
        cur.execute("ALTER TABLE rehab_active ALTER COLUMN prescribed_sessions DROP NOT NULL;")
        cur.execute("ALTER TABLE waitlist ALTER COLUMN prescribed_sessions DROP DEFAULT;")
        cur.execute("ALTER TABLE rehab_active ALTER COLUMN prescribed_sessions DROP DEFAULT;")

        cur.execute("ALTER TABLE waitlist DROP CONSTRAINT IF EXISTS waitlist_status_check;")
        cur.execute("""
            ALTER TABLE waitlist
            ADD CONSTRAINT waitlist_status_check
            CHECK (status IN ('EN_ESPERA','ASIGNADO','CANCELADO'));
        """)

        cur.execute("ALTER TABLE rehab_active DROP CONSTRAINT IF EXISTS rehab_active_status_check;")
        cur.execute("""
            ALTER TABLE rehab_active
            ADD CONSTRAINT rehab_active_status_check
            CHECK (status IN ('ACTIVO','ALTA'));
        """)

        cur.execute("ALTER TABLE treatment_sessions DROP CONSTRAINT IF EXISTS treatment_sessions_status_check;")
        cur.execute("""
            ALTER TABLE treatment_sessions
            ADD CONSTRAINT treatment_sessions_status_check
            CHECK (status IN ('REALIZADA', 'REVISION', 'FALTA_JUSTIFICADA', 'FALTA_NO_JUSTIFICADA'));
        """)

        cur.execute("""
            UPDATE waitlist
            SET status = CASE
                WHEN status = 'WAITING' THEN 'EN_ESPERA'
                WHEN status = 'ASSIGNED' THEN 'ASIGNADO'
                WHEN status = 'CANCELLED' THEN 'CANCELADO'
                ELSE status
            END
            WHERE status IN ('WAITING', 'ASSIGNED', 'CANCELLED');
        """)

        cur.execute("""
            UPDATE rehab_active
            SET status = CASE
                WHEN status = 'ACTIVE' THEN 'ACTIVO'
                WHEN status = 'DISCHARGED' THEN 'ALTA'
                ELSE status
            END
            WHERE status IN ('ACTIVE', 'DISCHARGED');
        """)


def fetch_all(conn, query, params=()):
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [desc.name for desc in cur.description]
        rows = cur.fetchall()
    return cols, rows


def execute_sql(conn, query, params=()):
    with conn.cursor() as cur:
        cur.execute(query, params)


def iso_utc_from_date(d: date) -> str:
    return datetime.combine(d, time.min).replace(tzinfo=UTC).isoformat()


def now_iso():
    return datetime.now(UTC).isoformat()


# -------------------- Catálogos dinámicos --------------------
def get_especialidades(conn, only_active=True):
    query = """
        SELECT name, requires_subspecialty, active
        FROM specialties_config
    """
    if only_active:
        query += " WHERE active = TRUE"
    query += " ORDER BY name ASC"

    _, rows = fetch_all(conn, query)
    return rows


def get_nombres_especialidades(conn, only_active=True):
    return [r[0] for r in get_especialidades(conn, only_active=only_active)]


def specialty_requires_subspecialty(conn, specialty_name: str) -> bool:
    _, rows = fetch_all(conn, """
        SELECT requires_subspecialty
        FROM specialties_config
        WHERE name = %s
        LIMIT 1
    """, (specialty_name,))
    return bool(rows[0][0]) if rows else False


def get_areas_por_especialidad(conn, specialty_name: str, only_active=True):
    query = """
        SELECT name, active
        FROM subspecialties_config
        WHERE specialty_name = %s
    """
    params = [specialty_name]
    if only_active:
        query += " AND active = TRUE"
    query += " ORDER BY name ASC"

    _, rows = fetch_all(conn, query, tuple(params))
    return rows


def get_nombres_areas_por_especialidad(conn, specialty_name: str, only_active=True):
    return [r[0] for r in get_areas_por_especialidad(conn, specialty_name, only_active=only_active)]


def add_specialty_config(conn, name: str, requires_subspecialty: bool):
    execute_sql(conn, """
        INSERT INTO specialties_config (name, active, requires_subspecialty)
        VALUES (%s, TRUE, %s)
        ON CONFLICT (name) DO NOTHING
    """, (name.strip(), requires_subspecialty))


def add_subspecialty_config(conn, specialty_name: str, name: str):
    execute_sql(conn, """
        INSERT INTO subspecialties_config (specialty_name, name, active)
        VALUES (%s, %s, TRUE)
        ON CONFLICT (specialty_name, name) DO NOTHING
    """, (specialty_name, name.strip()))


def set_specialty_active(conn, specialty_name: str, active: bool):
    execute_sql(conn, """
        UPDATE specialties_config
        SET active = %s
        WHERE name = %s
    """, (active, specialty_name))


def set_subspecialty_active(conn, specialty_name: str, name: str, active: bool):
    execute_sql(conn, """
        UPDATE subspecialties_config
        SET active = %s
        WHERE specialty_name = %s AND name = %s
    """, (active, specialty_name, name))


# -------------------- Validaciones --------------------
def is_valid_dni(dni: str) -> bool:
    dni = dni.strip().upper()
    if not re.fullmatch(r"\d{8}[A-Z]", dni):
        return False

    letras = "TRWAGMYFPDXBNJZSQVHLCKE"
    numero = int(dni[:8])
    letra_correcta = letras[numero % 23]
    return dni[-1] == letra_correcta


def is_valid_nhc(nhc: str) -> bool:
    nhc = nhc.strip()
    return bool(re.fullmatch(r"\d{6}", nhc))


def acceso_configuracion_permitido() -> bool:
    st.subheader("🔒 Acceso a ajustes")

    if "config_access_granted" not in st.session_state:
        st.session_state["config_access_granted"] = False

    clave = st.text_input(
        "Introduce la contraseña de ajustes",
        type="password",
        key="config_password_input"
    )

    if st.button("Acceder", key="config_login_button"):
        if clave == CLAVE_CONFIGURACION:
            st.session_state["config_access_granted"] = True
            st.success("Acceso concedido.")
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    return st.session_state["config_access_granted"]


def cerrar_configuracion_si_sale(page_actual: str):
    if "ultima_page" not in st.session_state:
        st.session_state["ultima_page"] = page_actual

    pagina_anterior = st.session_state["ultima_page"]

    if pagina_anterior == "⚙️ Ajustes" and page_actual != "⚙️ Ajustes":
        st.session_state["config_access_granted"] = False

    st.session_state["ultima_page"] = page_actual


def dia_semana_espanol(fecha: date) -> str:
    dias = {
        0: "Lunes",
        1: "Martes",
        2: "Miércoles",
        3: "Jueves",
        4: "Viernes",
        5: "Sábado",
        6: "Domingo",
    }
    return dias[fecha.weekday()]


def parse_attendance_days(attendance_days_text: str) -> list[str]:
    if not attendance_days_text:
        return []
    return [d.strip() for d in attendance_days_text.split(",") if d.strip()]


def es_fisio(specialty: str) -> bool:
    return specialty in FISIO_ESPECIALIDADES


def hay_fisio_y_terapia_ocupacional(selected_specialties: list[str]) -> bool:
    tiene_to = "Terapia ocupacional" in selected_specialties
    tiene_fisio = any(es_fisio(sp) for sp in selected_specialties)
    return tiene_to and tiene_fisio


def validar_preferencias_tratamiento(
    slot_type: str,
    time_preference: str,
    transport_mode: str,
    preferred_hour: str | None
):
    if slot_type not in TIPOS_HUECO:
        return "Debes seleccionar si el hueco es simple o doble."

    if time_preference not in TURNOS:
        return "Debes seleccionar mañana o tarde."

    if transport_mode not in TRANSPORTES:
        return "Debes seleccionar el modo de transporte."

    if transport_mode == "AMBULANCIA":
        if time_preference != "MAÑANA":
            return "Si el paciente viene en ambulancia solo puede ser por la mañana."
        if preferred_hour not in HORAS_AMBULANCIA:
            return "Si el paciente viene en ambulancia la hora debe ser 09:00 o 12:00."

    return None


def obtener_tratamientos_activos_paciente(conn, patient_id: str):
    _, rows = fetch_all(conn, """
        SELECT id, specialty, subspecialty, attendance_days, coordination_rule
        FROM rehab_active
        WHERE patient_id = %s AND status = 'ACTIVO'
    """, (patient_id,))
    return rows


def validar_regla_coordinacion(conn, patient_id: str, specialty: str, attendance_days: list[str], coordination_rule: str):
    if coordination_rule not in ["MISMO_DIA", "DIAS_ALTERNOS"]:
        return None

    tratamientos = obtener_tratamientos_activos_paciente(conn, patient_id)

    for _, sp, _, attendance_days_text, _ in tratamientos:
        dias_existentes = parse_attendance_days(attendance_days_text)

        if (
            (specialty == "Terapia ocupacional" and es_fisio(sp))
            or (es_fisio(specialty) and sp == "Terapia ocupacional")
        ):
            if coordination_rule == "MISMO_DIA":
                if set(attendance_days) != set(dias_existentes):
                    return "Si el paciente tiene Fisio y Terapia ocupacional con regla MISMO_DIA, ambos tratamientos deben tener exactamente los mismos días."
            elif coordination_rule == "DIAS_ALTERNOS":
                if set(attendance_days) & set(dias_existentes):
                    return "Si el paciente tiene Fisio y Terapia ocupacional con regla DIAS_ALTERNOS, no puede haber días coincidentes."
    return None


def hora_es_manana(hora_valor):
    if hora_valor is None:
        return False
    return hora_valor.hour < 14


def hora_es_tarde(hora_valor):
    if hora_valor is None:
        return False
    return hora_valor.hour >= 14


def validar_hora_sesion(selected_patient: dict, session_time_value):
    transport_mode = selected_patient.get("transport_mode")
    time_preference = selected_patient.get("time_preference")
    preferred_hour = selected_patient.get("preferred_hour")

    if session_time_value is None:
        return None

    hora_texto = str(session_time_value)[:5]

    if transport_mode == "AMBULANCIA":
        if hora_texto not in HORAS_AMBULANCIA:
            return "Los pacientes en ambulancia solo pueden venir a las 09:00 o a las 12:00."
        if not hora_es_manana(session_time_value):
            return "Los pacientes en ambulancia solo pueden venir por la mañana."

    if time_preference == "MAÑANA" and not hora_es_manana(session_time_value):
        return "Este tratamiento está configurado para la mañana."
    if time_preference == "TARDE" and not hora_es_tarde(session_time_value):
        return "Este tratamiento está configurado para la tarde."

    if preferred_hour and transport_mode == "AMBULANCIA" and hora_texto != preferred_hour:
        return f"Este paciente en ambulancia tiene como hora asignada {preferred_hour}."

    return None


# -------------------- Business logic --------------------
def _selection_sql(where_extra: str = "") -> str:
    extra = f" AND {where_extra} " if where_extra else ""
    return f"""
        WITH candidates AS (
            SELECT
                id,
                patient_id,
                priority_level,
                specialty,
                subspecialty,
                request_date,
                FLOOR(EXTRACT(EPOCH FROM (%s::timestamptz - request_date)) / 86400)::INTEGER AS wait_days
            FROM waitlist
            WHERE status='EN_ESPERA' AND eligible=TRUE
            {extra}
        )
        SELECT
            id, patient_id, priority_level, specialty, subspecialty, request_date, wait_days,
            CASE
              WHEN priority_level='preferente' AND wait_days >= {DIAS_2_MESES} THEN 'preferente_supera_2_meses'
              WHEN priority_level='ordinario' AND wait_days >= {DIAS_6_MESES} THEN 'ordinario_supera_6_meses'
              WHEN priority_level='urgente' THEN 'urgente_base'
              WHEN priority_level='ordinario' AND wait_days >= {DIAS_3_MESES} THEN 'ordinario_supera_3_meses'
              WHEN priority_level='preferente' THEN 'preferente_base'
              ELSE 'ordinario_base'
            END AS rule_applied
        FROM candidates
        ORDER BY
            CASE
                WHEN priority_level='preferente' AND wait_days >= {DIAS_2_MESES} THEN 0
                WHEN priority_level='ordinario' AND wait_days >= {DIAS_6_MESES} THEN 0
                WHEN priority_level='urgente' THEN 1
                WHEN priority_level='ordinario' AND wait_days >= {DIAS_3_MESES} THEN 2
                WHEN priority_level='preferente' THEN 3
                ELSE 4
            END ASC,
            CASE
                WHEN priority_level='urgente' THEN 0
                WHEN priority_level='preferente' THEN 1
                ELSE 2
            END ASC,
            wait_days DESC,
            request_date ASC,
            id ASC
        LIMIT 1
    """


def _build_filters(specialty_filter: str, subspecialty_filter: str):
    where_parts = []
    params = []

    if specialty_filter != "Todas":
        where_parts.append("specialty = %s")
        params.append(specialty_filter)

        if subspecialty_filter != "Todas":
            where_parts.append("subspecialty = %s")
            params.append(subspecialty_filter)

    where_extra = " AND ".join(where_parts)
    return where_extra, params


def preview_next_patient(conn, specialty_filter: str, subspecialty_filter: str):
    base_now = now_iso()

    where_extra, extra_params = _build_filters(specialty_filter, subspecialty_filter)
    sql = _selection_sql(where_extra=where_extra)

    with conn.cursor() as cur:
        cur.execute(sql, tuple([base_now] + extra_params))
        row = cur.fetchone()

    if not row:
        return None

    waitlist_id, patient_id, priority_level, specialty, subspecialty, request_date, wait_days, rule_applied = row
    return {
        "waitlist_id": waitlist_id,
        "patient_id": patient_id,
        "priority_level": priority_level,
        "specialty": specialty,
        "subspecialty": subspecialty,
        "request_date": request_date.isoformat() if hasattr(request_date, "isoformat") else str(request_date),
        "wait_days": int(wait_days),
        "rule_applied": rule_applied,
    }


def add_waiting_patient_multiple(
    conn,
    patient_id: str,
    priority_level: str,
    requests: list[dict],
    request_date_iso: str,
    eligible: bool,
    actor: str,
    slot_type: str,
    time_preference: str,
    transport_mode: str,
    preferred_hour: str | None,
    coordination_rule: str,
):
    created_at = now_iso()

    with conn.transaction():
        with conn.cursor() as cur:
            for req in requests:
                specialty = req["specialty"]
                subspecialty = req.get("subspecialty")
                prescribed_sessions = req.get("prescribed_sessions")

                if prescribed_sessions in ("", None):
                    prescribed_sessions = None
                else:
                    prescribed_sessions = int(prescribed_sessions)

                if not specialty_requires_subspecialty(conn, specialty):
                    subspecialty = None

                regla_guardada = coordination_rule if (
                    (specialty == "Terapia ocupacional" or es_fisio(specialty))
                ) else "NINGUNA"

                cur.execute(
                    """
                    INSERT INTO waitlist (
                        patient_id, priority_level, specialty, subspecialty,
                        prescribed_sessions, slot_type, time_preference, transport_mode,
                        preferred_hour, coordination_rule, request_date, created_at, status, eligible
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        patient_id,
                        priority_level,
                        specialty,
                        subspecialty,
                        prescribed_sessions,
                        slot_type,
                        time_preference,
                        transport_mode,
                        preferred_hour,
                        regla_guardada,
                        request_date_iso,
                        created_at,
                        "EN_ESPERA",
                        eligible,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO assignment_log
                        (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                         chosen_priority_level, wait_days, rule_applied, reason, comment, actor, created_at)
                    VALUES
                        ('ALTA_MANUAL_LISTA_ESPERA', NULL, NULL, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s, %s)
                    """,
                    (
                        patient_id,
                        specialty,
                        subspecialty,
                        priority_level,
                        f"Hueco: {slot_type} · Turno: {time_preference} · Transporte: {transport_mode}" + (
                            f" · Hora: {preferred_hour}" if preferred_hour else ""
                        ),
                        actor,
                        created_at
                    ),
                )


def assign_next_patient(
    conn,
    assigned_by="SISTEMA",
    specialty_filter="Todas",
    subspecialty_filter="Todas",
    attendance_days=None
):
    now = now_iso()

    if attendance_days is None:
        attendance_days = []

    attendance_days_text = ",".join(attendance_days)

    with conn.transaction():
        with conn.cursor() as cur:
            where_extra, extra_params = _build_filters(specialty_filter, subspecialty_filter)
            sql = _selection_sql(where_extra=where_extra)

            cur.execute(sql, tuple([now] + extra_params))
            row = cur.fetchone()
            if not row:
                return None

            waitlist_id, patient_id, priority_level, specialty, subspecialty, request_date, wait_days, rule_applied = row

            cur.execute("""
                SELECT prescribed_sessions, slot_type, time_preference, transport_mode, preferred_hour, coordination_rule
                FROM waitlist
                WHERE id = %s
            """, (waitlist_id,))
            prescribed_sessions, slot_type, time_preference, transport_mode, preferred_hour, coordination_rule = cur.fetchone()

            error_coordinacion = validar_regla_coordinacion(
                conn=conn,
                patient_id=patient_id,
                specialty=specialty,
                attendance_days=attendance_days,
                coordination_rule=coordination_rule or "NINGUNA"
            )
            if error_coordinacion:
                raise ValueError(error_coordinacion)

            cur.execute("""
                INSERT INTO rehab_active
                    (patient_id, specialty, subspecialty, prescribed_sessions, slot_type, time_preference,
                     transport_mode, preferred_hour, coordination_rule, attendance_days,
                     start_date, source_waitlist_id, assigned_by, assigned_at, status)
                VALUES
                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVO')
                RETURNING id
            """, (
                patient_id,
                specialty,
                subspecialty,
                prescribed_sessions,
                slot_type,
                time_preference,
                transport_mode,
                preferred_hour,
                coordination_rule,
                attendance_days_text,
                now,
                waitlist_id,
                assigned_by,
                now
            ))
            rehab_active_id = cur.fetchone()[0]

            cur.execute("""
                UPDATE waitlist
                SET status='ASIGNADO'
                WHERE id=%s AND status='EN_ESPERA'
            """, (waitlist_id,))

            cur.execute("""
                INSERT INTO assignment_log
                    (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                     chosen_priority_level, wait_days, rule_applied, reason, comment, actor, created_at)
                VALUES
                    ('ASIGNACION_AUTOMATICA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                waitlist_id,
                rehab_active_id,
                patient_id,
                specialty,
                subspecialty,
                priority_level,
                int(wait_days),
                rule_applied,
                f"Días asignados: {attendance_days_text}",
                f"Hueco: {slot_type} · Turno: {time_preference} · Transporte: {transport_mode}" + (
                    f" · Hora: {preferred_hour}" if preferred_hour else ""
                ) + (
                    f" · Coordinación: {coordination_rule}" if coordination_rule and coordination_rule != "NINGUNA" else ""
                ),
                assigned_by,
                now
            ))

            return {
                "rehab_active_id": rehab_active_id,
                "waitlist_id": waitlist_id,
                "patient_id": patient_id,
                "priority_level": priority_level,
                "specialty": specialty,
                "subspecialty": subspecialty,
                "prescribed_sessions": prescribed_sessions,
                "slot_type": slot_type,
                "time_preference": time_preference,
                "transport_mode": transport_mode,
                "preferred_hour": preferred_hour,
                "coordination_rule": coordination_rule,
                "attendance_days": attendance_days,
                "wait_days": int(wait_days),
                "rule_applied": rule_applied,
                "request_date": request_date.isoformat() if hasattr(request_date, "isoformat") else str(request_date),
            }


def discharge_patient(conn, rehab_active_id: int, reason: str, comment: str, actor: str):
    now = now_iso()

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, patient_id, specialty, subspecialty
                FROM rehab_active
                WHERE id=%s AND status='ACTIVO'
            """, (rehab_active_id,))
            row = cur.fetchone()

            if not row:
                return False

            _, patient_id, specialty, subspecialty = row

            cur.execute("""
                UPDATE rehab_active
                SET status='ALTA',
                    discharge_reason=%s,
                    discharge_comment=%s,
                    discharged_at=%s
                WHERE id=%s AND status='ACTIVO'
            """, (reason, comment, now, rehab_active_id))

            cur.execute("""
                INSERT INTO assignment_log
                    (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                     chosen_priority_level, wait_days, rule_applied, reason, comment, actor, created_at)
                VALUES
                    ('ALTA_TRATAMIENTO', NULL, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s, %s, %s)
            """, (rehab_active_id, patient_id, specialty, subspecialty, reason, comment, actor, now))

            return True


def add_treatment_session(
    conn,
    rehab_active_id: int,
    patient_id: str,
    specialty: str,
    subspecialty: str | None,
    session_date_value: date,
    session_time_value,
    status: str,
    absence_reason: str,
    out_of_schedule_reason: str,
    recorded_by: str,
):
    created_at = now_iso()

    if status in ["REALIZADA", "REVISION"]:
        absence_reason = None
    elif not absence_reason.strip():
        absence_reason = "Sin especificar"

    if not out_of_schedule_reason.strip():
        out_of_schedule_reason = None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO treatment_sessions
                (rehab_active_id, patient_id, specialty, subspecialty, session_date, session_time,
                 status, absence_reason, out_of_schedule_reason, recorded_by, created_at)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            rehab_active_id,
            patient_id,
            specialty,
            subspecialty,
            session_date_value,
            session_time_value,
            status,
            absence_reason,
            out_of_schedule_reason,
            recorded_by,
            created_at
        ))


def get_treatment_sessions(conn, rehab_active_id: int):
    cols, rows = fetch_all(conn, """
        SELECT
            id,
            rehab_active_id,
            patient_id,
            specialty,
            subspecialty,
            session_date,
            session_time,
            status,
            absence_reason,
            out_of_schedule_reason,
            recorded_by,
            created_at
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        ORDER BY session_date DESC, session_time DESC NULLS LAST, id DESC
    """, (rehab_active_id,))
    return cols, rows


def get_session_summary(conn, rehab_active_id: int):
    _, rows = fetch_all(conn, """
        SELECT status, COUNT(*) AS total
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        GROUP BY status
    """, (rehab_active_id,))

    resumen = {
        "REALIZADA": 0,
        "REVISION": 0,
        "FALTA_JUSTIFICADA": 0,
        "FALTA_NO_JUSTIFICADA": 0,
    }

    for r in rows:
        status, total = r
        resumen[status] = total

    return resumen


def render_mini_calendar(conn, rehab_active_id: int, year: int, month: int):
    _, rows = fetch_all(conn, """
        SELECT session_date, status
        FROM treatment_sessions
        WHERE rehab_active_id = %s
          AND EXTRACT(YEAR FROM session_date) = %s
          AND EXTRACT(MONTH FROM session_date) = %s
        ORDER BY session_date ASC
    """, (rehab_active_id, year, month))

    sesiones_por_dia = defaultdict(list)
    for session_date, status in rows:
        sesiones_por_dia[session_date.day].append(status)

    colores = {
        "REALIZADA": "#4CAF50",
        "REVISION": "#2196F3",
        "FALTA_JUSTIFICADA": "#FFC107",
        "FALTA_NO_JUSTIFICADA": "#F44336",
    }

    cal = calendar.Calendar(firstweekday=0)
    semanas = cal.monthdayscalendar(year, month)
    nombres_dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    html = """
    <style>
    .mini-calendario {
        width: 100%;
        max-width: 520px;
        border-collapse: collapse;
        margin-top: 0.5rem;
        font-size: 0.9rem;
    }
    .mini-calendario th, .mini-calendario td {
        border: 1px solid #ddd;
        width: 14.2%;
        height: 72px;
        vertical-align: top;
        padding: 4px;
        background: white;
    }
    .mini-cal-dia {
        font-weight: 700;
        margin-bottom: 4px;
    }
    .mini-cal-dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        margin: 2px 3px 0 0;
    }
    .mini-cal-vacio {
        background: #f7f7f7;
    }
    .leyenda {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin: 0.5rem 0 0.8rem 0;
        font-size: 0.9rem;
    }
    .leyenda-item {
        display: flex;
        align-items: center;
        gap: 6px;
    }

    section[data-testid="stSidebar"] {
        background-color: #f4f6f8;
    }

    section[data-testid="stSidebar"] .stRadio > div {
        gap: 0.4rem;
    }

    section[data-testid="stSidebar"] label {
        font-weight: 600;
    }
    </style>
    """

    html += '<div class="leyenda">'
    html += f'<div class="leyenda-item"><span class="mini-cal-dot" style="background:{colores["REALIZADA"]};"></span>Realizada</div>'
    html += f'<div class="leyenda-item"><span class="mini-cal-dot" style="background:{colores["REVISION"]};"></span>Revisión</div>'
    html += f'<div class="leyenda-item"><span class="mini-cal-dot" style="background:{colores["FALTA_JUSTIFICADA"]};"></span>Falta justificada</div>'
    html += f'<div class="leyenda-item"><span class="mini-cal-dot" style="background:{colores["FALTA_NO_JUSTIFICADA"]};"></span>Falta no justificada</div>'
    html += '</div>'

    html += '<table class="mini-calendario">'
    html += "<tr>" + "".join(f"<th>{d}</th>" for d in nombres_dias) + "</tr>"

    for semana in semanas:
        html += "<tr>"
        for dia in semana:
            if dia == 0:
                html += '<td class="mini-cal-vacio"></td>'
            else:
                html += f'<td><div class="mini-cal-dia">{dia}</div>'
                estados = sesiones_por_dia.get(dia, [])
                for estado in estados:
                    color = colores.get(estado, "#999999")
                    html += f'<span class="mini-cal-dot" style="background:{color};" title="{estado_sesion_label(estado)}"></span>'
                html += "</td>"
        html += "</tr>"

    html += "</table>"
    st.markdown(html, unsafe_allow_html=True)


def get_stats(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM waitlist WHERE status='EN_ESPERA' AND eligible=TRUE")
        waiting = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='ACTIVO'")
        active = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='ALTA'")
        discharged = cur.fetchone()[0]

    return waiting, active, discharged


def priority_badge(p: str) -> str:
    if p == "urgente":
        return "🔴 urgente"
    if p == "preferente":
        return "🟠 preferente"
    return "🟢 ordinario"


def specialty_label(specialty: str, subspecialty: str | None) -> str:
    if subspecialty:
        return f"{specialty} · {subspecialty}"
    return specialty


def estado_sesion_label(status: str) -> str:
    if status == "REALIZADA":
        return "Realizada"
    if status == "REVISION":
        return "Revisión"
    if status == "FALTA_JUSTIFICADA":
        return "Falta justificada"
    if status == "FALTA_NO_JUSTIFICADA":
        return "Falta no justificada"
    return status


# -------------------- UI --------------------
st.set_page_config(page_title="Tratamiento Fisioterapia", layout="wide")

st.markdown("""
<style>
    .kpi-card {
        border: 1px solid rgba(0,0,0,0.08);
        border-radius: 14px;
        padding: 14px 16px;
        background: rgba(255,255,255,0.65);
    }
    .muted { color: rgba(0,0,0,0.55); font-size: 0.9rem; }
    .title { font-size: 1.4rem; font-weight: 700; }

    section[data-testid="stSidebar"] {
        background-color: #f4f6f8;
    }

    section[data-testid="stSidebar"] .stRadio > div {
        gap: 0.4rem;
    }

    section[data-testid="stSidebar"] label {
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

try:
    conn = get_conn()
    init_db(conn)
except Exception as e:
    st.error(f"Error conectando con la base de datos en la nube: {e}")
    st.stop()

especialidades_activas = get_nombres_especialidades(conn, only_active=True)

st.sidebar.markdown("## 🏥 Tratamiento Fisioterapia")
page = st.sidebar.radio(
    "Secciones",
    [
        "📊 Panel de control",
        "📝 Nueva solicitud",
        "🧑‍⚕️ Tratamientos activos",
        "🧾 Auditoría clínica",
        "⚙️ Ajustes",
    ],
    index=0,
    key="nav_page"
)

cerrar_configuracion_si_sale(page)

st.sidebar.markdown("---")

specialty_filter = st.sidebar.selectbox(
    "Filtro por especialidad",
    ["Todas"] + especialidades_activas,
    key="sidebar_specialty_filter"
)

subspecialty_filter = "Todas"
if specialty_filter != "Todas" and specialty_requires_subspecialty(conn, specialty_filter):
    areas_sidebar = get_nombres_areas_por_especialidad(conn, specialty_filter, only_active=True)
    subspecialty_filter = st.sidebar.selectbox(
        f"Área ({specialty_filter})",
        ["Todas"] + areas_sidebar,
        key="sidebar_area_filter"
    )

actor_sidebar = st.sidebar.text_input(
    "DNI del clínico (opcional en nueva solicitud)",
    value="",
    key="sidebar_actor"
).strip().upper()

if actor_sidebar and not is_valid_dni(actor_sidebar):
    st.sidebar.error("Introduce un DNI válido. Ejemplo: 12345678Z")

waiting, active, discharged = get_stats(conn)

st.markdown('<div class="title">Sistema de Gestión de Rehabilitación</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="muted">Gestión clínica de solicitudes, tratamientos, auditoría y ajustes de configuración</div>',
    unsafe_allow_html=True
)
st.write("")

c1, c2, c3, c4 = st.columns([1, 1, 1, 2], gap="large")
with c1:
    st.markdown(f"<div class='kpi-card'><div class='muted'>En espera (elegibles)</div><div style='font-size:28px;font-weight:700'>{waiting}</div></div>", unsafe_allow_html=True)
with c2:
    st.markdown(f"<div class='kpi-card'><div class='muted'>En tratamiento</div><div style='font-size:28px;font-weight:700'>{active}</div></div>", unsafe_allow_html=True)
with c3:
    st.markdown(f"<div class='kpi-card'><div class='muted'>Finalizados</div><div style='font-size:28px;font-weight:700'>{discharged}</div></div>", unsafe_allow_html=True)
with c4:
    nxt = preview_next_patient(conn, specialty_filter=specialty_filter, subspecialty_filter=subspecialty_filter)
    if nxt:
        st.markdown("<div class='kpi-card'>", unsafe_allow_html=True)
        st.markdown("**Siguiente candidato (con filtros)**")
        st.write(f"NHC: **{nxt['patient_id']}** · {priority_badge(nxt['priority_level'])}")
        st.write(f"Unidad: **{specialty_label(nxt['specialty'], nxt['subspecialty'])}**")
        st.write(f"Espera: **{nxt['wait_days']} días** · Solicitud: **{nxt['request_date'][:10]}**")
        st.caption(f"Regla aplicada: {nxt['rule_applied']}")
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='kpi-card'><div class='muted'>Siguiente candidato</div><div style='font-size:14px'>No hay pacientes elegibles.</div></div>", unsafe_allow_html=True)

st.write("")

if page == "📊 Panel de control":
    left, right = st.columns([2.2, 1], gap="large")

    with left:
        st.subheader("Listado de pacientes en espera")
        now = now_iso()

        f1, f2, f3 = st.columns([1, 1, 1], gap="medium")
        with f1:
            pr_filter = st.selectbox("Prioridad", ["Todas"] + PRIORIDADES, key="dash_priority")
        with f2:
            elig_filter = st.selectbox("Elegibilidad", ["Solo elegibles", "Todos"], key="dash_elig")
        with f3:
            sp_filter = st.selectbox(
                "Especialidad (tabla)",
                ["Todas"] + especialidades_activas,
                index=(0 if specialty_filter == "Todas" else (especialidades_activas.index(specialty_filter) + 1)),
                key="dash_specialty"
            )

        ss_filter_table = "Todas"
        if sp_filter != "Todas" and specialty_requires_subspecialty(conn, sp_filter):
            areas_tabla = get_nombres_areas_por_especialidad(conn, sp_filter, only_active=True)
            ss_filter_table = st.selectbox(
                f"Área (tabla - {sp_filter})",
                ["Todas"] + areas_tabla,
                key="dash_area"
            )

        where = ["status='EN_ESPERA'"]
        params = [now]

        if elig_filter == "Solo elegibles":
            where.append("eligible=TRUE")

        if pr_filter != "Todas":
            where.append("priority_level=%s")
            params.append(pr_filter)

        if sp_filter != "Todas":
            where.append("specialty=%s")
            params.append(sp_filter)

            if ss_filter_table != "Todas":
                where.append("subspecialty=%s")
                params.append(ss_filter_table)

        where_sql = " AND ".join(where)

        _, rows = fetch_all(conn, f"""
            SELECT
                id,
                patient_id,
                priority_level,
                specialty,
                subspecialty,
                prescribed_sessions,
                request_date,
                FLOOR(EXTRACT(EPOCH FROM (%s::timestamptz - request_date)) / 86400)::INTEGER AS wait_days,
                eligible
            FROM waitlist
            WHERE {where_sql}
            ORDER BY request_date ASC, id ASC
        """, tuple(params))

        data = [{"NHC": r[1]} for r in rows]
        st.dataframe(data, width="stretch", hide_index=True)

    with right:
        st.subheader("Acciones clínicas")

        st.caption("Asignación automática usando los filtros laterales.")
        attendance_days = st.multiselect(
            "Días de asistencia del tratamiento",
            DIAS_SEMANA,
            key="assign_attendance_days"
        )

        if st.button("Asignar siguiente", width="stretch", key="btn_assign_next"):
            if not actor_sidebar:
                st.error("Debes introducir el DNI del clínico.")
            elif not is_valid_dni(actor_sidebar):
                st.error("El DNI introducido no es válido.")
            elif not attendance_days:
                st.error("Debes seleccionar al menos un día de asistencia.")
            else:
                try:
                    res = assign_next_patient(
                        conn,
                        assigned_by=actor_sidebar,
                        specialty_filter=specialty_filter,
                        subspecialty_filter=subspecialty_filter,
                        attendance_days=attendance_days
                    )
                except ValueError as e:
                    st.error(str(e))
                    st.stop()

                if not res:
                    st.warning("No hay pacientes elegibles con esos filtros.")
                else:
                    st.success(f"Asignado NHC: {res['patient_id']} ({res['priority_level']})")
                    st.info(
                        f"{specialty_label(res['specialty'], res['subspecialty'])} · "
                        f"{res['wait_days']} días · Regla: {res['rule_applied']} · "
                        f"Días: {', '.join(res['attendance_days'])} · "
                        f"Hueco: {res['slot_type']} · Turno: {res['time_preference']} · "
                        f"Transporte: {res['transport_mode']}" +
                        (f" · Hora: {res['preferred_hour']}" if res['preferred_hour'] else "")
                    )
                    st.rerun()

        st.divider()
        st.caption("Formalización del alta de un tratamiento activo.")
        _, rowsA = fetch_all(conn, """
            SELECT id, patient_id, specialty, subspecialty, start_date
            FROM rehab_active
            WHERE status='ACTIVO'
            ORDER BY id DESC
        """)
        if not rowsA:
            st.info("No hay pacientes activos.")
        else:
            options = {
                f"{r[0]} · {r[1]} · {specialty_label(r[2], r[3])} · inicio {str(r[4])[:10]}": r[0]
                for r in rowsA
            }
            pick = st.selectbox("NHC activo", list(options.keys()), key="dash_active_pick")
            reason = st.selectbox("Motivo del alta", ["FIN_TRATAMIENTO", "NO_ASISTE", "DERIVADO", "OTRO"], key="dash_discharge_reason")
            comment = st.text_area(
                "Comentario clínico",
                placeholder="Escribe observaciones sobre el alta...",
                key="dash_discharge_comment"
            )

            if st.button("Dar alta", width="stretch", key="btn_discharge"):
                if not actor_sidebar:
                    st.error("Debes introducir el DNI del clínico.")
                elif not is_valid_dni(actor_sidebar):
                    st.error("El DNI introducido no es válido.")
                elif reason == "OTRO" and not comment.strip():
                    st.error("Debes añadir un comentario clínico cuando el motivo es OTRO.")
                else:
                    ok = discharge_patient(conn, options[pick], reason=reason, comment=comment.strip(), actor=actor_sidebar)
                    if ok:
                        st.success("Alta registrada.")
                        st.rerun()
                    else:
                        st.error("No se pudo registrar el alta.")

elif page == "📝 Nueva solicitud":
    st.subheader("Registro de nueva solicitud de rehabilitación")

    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 1.1], gap="large")

    with c1:
        patient_id = st.text_input(
            "NHC del paciente",
            placeholder="Ej: 123456",
            key="req_patient_id"
        ).strip()

    with c2:
        priority_level = st.selectbox("Prioridad", PRIORIDADES, key="req_priority")

    with c3:
        request_dt = st.date_input("Fecha de solicitud", value=date.today(), key="req_request_date")

    with c4:
        eligible = st.checkbox("Elegible", value=True, key="req_eligible")

    selected_specialties = st.multiselect(
        "Especialidades",
        especialidades_activas,
        key="req_specialties_multi"
    )

    st.markdown("### Configuración del tratamiento")

    cfg1, cfg2, cfg3, cfg4 = st.columns([1, 1, 1, 1], gap="medium")

    with cfg1:
        slot_type = st.selectbox(
            "Tipo de hueco",
            TIPOS_HUECO,
            key="req_slot_type"
        )

    with cfg2:
        time_preference = st.selectbox(
            "Turno",
            TURNOS,
            key="req_time_preference"
        )

    with cfg3:
        transport_mode = st.selectbox(
            "Transporte",
            TRANSPORTES,
            key="req_transport_mode"
        )

    preferred_hour = None
    with cfg4:
        if transport_mode == "AMBULANCIA":
            preferred_hour = st.selectbox(
                "Hora ambulancia",
                HORAS_AMBULANCIA,
                key="req_preferred_hour"
            )
        else:
            st.caption("Hora fija: no aplica")

    coordination_rule = "NINGUNA"
    if hay_fisio_y_terapia_ocupacional(selected_specialties):
        coordination_rule = st.selectbox(
            "Coordinación Fisio + Terapia ocupacional",
            REGLAS_COORDINACION,
            index=1,
            key="req_coordination_rule"
        )
        st.caption("MISMO_DIA: ambos tratamientos comparten días. DIAS_ALTERNOS: no pueden coincidir.")

    sesiones_por_especialidad = {}
    areas_por_especialidad = {}
    sesiones_por_area = {}

    if selected_specialties:
        st.markdown("### Número de sesiones orientativas por especialidad (opcional)")
        for sp in selected_specialties:
            if not specialty_requires_subspecialty(conn, sp):
                sesiones_por_especialidad[sp] = st.text_input(
                    f"Sesiones orientativas para {sp} (opcional)",
                    placeholder="Ej: 10",
                    key=f"sessions_{sp}"
                ).strip()

    for sp in selected_specialties:
        if specialty_requires_subspecialty(conn, sp):
            areas_disponibles = get_nombres_areas_por_especialidad(conn, sp, only_active=True)
            areas_por_especialidad[sp] = st.multiselect(
                f"Áreas de {sp}",
                areas_disponibles,
                key=f"areas_{sp}"
            )
            st.caption(f"Si seleccionas {sp}, debes indicar al menos un área.")

            if areas_por_especialidad[sp]:
                st.markdown(f"### Número de sesiones orientativas por área de {sp} (opcional)")
                for sub in areas_por_especialidad[sp]:
                    sesiones_por_area[(sp, sub)] = st.text_input(
                        f"Sesiones orientativas para {sp} · {sub} (opcional)",
                        placeholder="Ej: 10",
                        key=f"sessions_{sp}_{sub}"
                    ).strip()

    st.caption("Se creará una solicitud independiente por cada especialidad seleccionada. Si la especialidad requiere área, se creará una por cada área seleccionada.")

    if st.button("Guardar solicitud", width="stretch", key="req_submit"):
        if not patient_id:
            st.error("Introduce el NHC del paciente.")
        elif not is_valid_nhc(patient_id):
            st.error("El NHC debe tener exactamente 6 números.")
        elif not selected_specialties:
            st.error("Selecciona al menos una especialidad.")
        else:
            error_preferencias = validar_preferencias_tratamiento(
                slot_type=slot_type,
                time_preference=time_preference,
                transport_mode=transport_mode,
                preferred_hour=preferred_hour
            )
            if error_preferencias:
                st.error(error_preferencias)
                st.stop()

            for sp, valor in sesiones_por_especialidad.items():
                if valor and not valor.isdigit():
                    st.error(f"Las sesiones de {sp} deben ser un número entero o dejarse en blanco.")
                    st.stop()

            for (sp, sub), valor in sesiones_por_area.items():
                if valor and not valor.isdigit():
                    st.error(f"Las sesiones de {sp} · {sub} deben ser un número entero o dejarse en blanco.")
                    st.stop()

            requests = []

            for sp in selected_specialties:
                if specialty_requires_subspecialty(conn, sp):
                    if not areas_por_especialidad.get(sp):
                        st.error(f"Debes seleccionar al menos un área para {sp}.")
                        st.stop()

                    for sub in areas_por_especialidad[sp]:
                        requests.append({
                            "specialty": sp,
                            "subspecialty": sub,
                            "prescribed_sessions": int(sesiones_por_area[(sp, sub)]) if sesiones_por_area.get((sp, sub)) else None
                        })
                else:
                    requests.append({
                        "specialty": sp,
                        "subspecialty": None,
                        "prescribed_sessions": int(sesiones_por_especialidad[sp]) if sesiones_por_especialidad.get(sp) else None
                    })

            actor_para_guardar = actor_sidebar if actor_sidebar and is_valid_dni(actor_sidebar) else "SIN_DNI"

            add_waiting_patient_multiple(
                conn,
                patient_id=patient_id,
                priority_level=priority_level,
                requests=requests,
                request_date_iso=iso_utc_from_date(request_dt),
                eligible=eligible,
                actor=actor_para_guardar,
                slot_type=slot_type,
                time_preference=time_preference,
                transport_mode=transport_mode,
                preferred_hour=preferred_hour,
                coordination_rule=coordination_rule
            )

            st.success(f"Solicitud guardada para NHC {patient_id}. Se han creado {len(requests)} entradas.")
            st.rerun()

elif page == "🧑‍⚕️ Tratamientos activos":
    st.subheader("Pacientes con tratamiento activo")
    _, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, prescribed_sessions, start_date, assigned_by, assigned_at
        FROM rehab_active
        WHERE status='ACTIVO'
        ORDER BY id DESC
    """)
    data = [{"NHC": r[1]} for r in rows]
    st.dataframe(data, width="stretch", hide_index=True)

    st.divider()
    st.subheader("🗓️ Registro de sesiones por NHC")

    _, rowsA = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, prescribed_sessions, attendance_days,
               slot_type, time_preference, transport_mode, preferred_hour, coordination_rule, start_date
        FROM rehab_active
        WHERE status='ACTIVO'
        ORDER BY id DESC
    """)

    if not rowsA:
        st.info("No hay pacientes activos para registrar sesiones.")
    else:
        options = {
            f"{r[0]} · {r[1]} · {specialty_label(r[2], r[3])} · inicio {str(r[11])[:10]}": {
                "rehab_active_id": r[0],
                "patient_id": r[1],
                "specialty": r[2],
                "subspecialty": r[3],
                "prescribed_sessions": r[4],
                "attendance_days": parse_attendance_days(r[5]),
                "slot_type": r[6],
                "time_preference": r[7],
                "transport_mode": r[8],
                "preferred_hour": r[9],
                "coordination_rule": r[10],
                "start_date": r[11],
            }
            for r in rowsA
        }

        selected_label = st.selectbox(
            "Selecciona NHC en tratamiento",
            list(options.keys()),
            key="session_patient_pick"
        )
        selected_patient = options[selected_label]

        dias_texto = ", ".join(selected_patient["attendance_days"]) if selected_patient["attendance_days"] else "No definidos"
        st.caption(f"Días de asistencia establecidos: {dias_texto}")

        st.caption(
            f"Hueco: {selected_patient['slot_type']} · "
            f"Turno: {selected_patient['time_preference']} · "
            f"Transporte: {selected_patient['transport_mode']}" +
            (f" · Hora fija: {selected_patient['preferred_hour']}" if selected_patient['preferred_hour'] else "")
        )

        if selected_patient.get("coordination_rule") and selected_patient["coordination_rule"] != "NINGUNA":
            st.caption(f"Coordinación con otro tratamiento: {selected_patient['coordination_rule']}")

        resumen = get_session_summary(conn, selected_patient["rehab_active_id"])
        realizadas_y_revision = resumen["REALIZADA"] + resumen["REVISION"]

        if selected_patient["prescribed_sessions"] is not None:
            restantes = max(selected_patient["prescribed_sessions"] - realizadas_y_revision, 0)
        else:
            restantes = None

        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Sesiones orientativas", selected_patient["prescribed_sessions"] if selected_patient["prescribed_sessions"] is not None else "No definidas")
        r2.metric("Realizadas", resumen["REALIZADA"])
        r3.metric("Revisiones", resumen["REVISION"])
        r4.metric("Faltas justificadas", resumen["FALTA_JUSTIFICADA"])
        r5.metric("Faltas no justificadas", resumen["FALTA_NO_JUSTIFICADA"])

        if restantes is None:
            st.caption("Sesiones orientativas pendientes: no definidas")
        else:
            st.caption(f"Sesiones orientativas pendientes: {restantes}")

        c1, c2, c3 = st.columns([1, 1, 1], gap="medium")
        with c1:
            session_date_value = st.date_input("Día del tratamiento", value=date.today(), key="session_date")
        with c2:
            session_time_value = st.time_input("Hora del tratamiento", value=time(9, 0), key="session_time")
        with c3:
            session_status = st.selectbox(
                "Estado de la sesión",
                ["REALIZADA", "REVISION", "FALTA_JUSTIFICADA", "FALTA_NO_JUSTIFICADA"],
                format_func=estado_sesion_label,
                key="session_status"
            )

        absence_reason = ""
        out_of_schedule_reason = ""
        dia_sesion = dia_semana_espanol(session_date_value)
        fuera_de_dias = bool(selected_patient["attendance_days"]) and dia_sesion not in selected_patient["attendance_days"]

        if fuera_de_dias:
            st.warning(f"La fecha seleccionada cae en {dia_sesion}, que no está dentro de los días establecidos del tratamiento.")
            out_of_schedule_reason = st.text_area(
                "Motivo por el que se registra fuera de los días establecidos",
                placeholder="Ej: festivo compensado, cambio puntual, reorganización de agenda...",
                key="session_out_of_schedule_reason"
            )

        if session_status in ["FALTA_JUSTIFICADA", "FALTA_NO_JUSTIFICADA"]:
            absence_reason = st.text_area(
                "Motivo de la falta",
                placeholder="Ejemplo: enfermedad, cita médica, no acudió...",
                key="session_absence_reason"
            )
            if session_status == "FALTA_NO_JUSTIFICADA":
                st.warning("Esta falta quedará registrada como NO JUSTIFICADA.")

        error_hora = None
        if session_status in ["REALIZADA", "REVISION"]:
            error_hora = validar_hora_sesion(selected_patient, session_time_value)

        if st.button("Guardar sesión o falta", width="stretch", key="btn_save_session"):
            if not actor_sidebar:
                st.error("Debes introducir el DNI del clínico.")
            elif not is_valid_dni(actor_sidebar):
                st.error("El DNI introducido no es válido.")
            elif session_status in ["FALTA_JUSTIFICADA", "FALTA_NO_JUSTIFICADA"] and not absence_reason.strip():
                st.error("Debes indicar el motivo de la falta.")
            elif fuera_de_dias and not out_of_schedule_reason.strip():
                st.error("Debes indicar el motivo por el que se registra fuera de los días establecidos.")
            elif error_hora:
                st.error(error_hora)
            else:
                add_treatment_session(
                    conn=conn,
                    rehab_active_id=selected_patient["rehab_active_id"],
                    patient_id=selected_patient["patient_id"],
                    specialty=selected_patient["specialty"],
                    subspecialty=selected_patient["subspecialty"],
                    session_date_value=session_date_value,
                    session_time_value=session_time_value if session_status in ["REALIZADA", "REVISION"] else None,
                    status=session_status,
                    absence_reason=absence_reason.strip(),
                    out_of_schedule_reason=out_of_schedule_reason.strip(),
                    recorded_by=actor_sidebar
                )
                st.success("Registro guardado correctamente.")
                st.rerun()

        st.markdown("### Calendario mensual")
        hoy = date.today()
        mes_cal = st.selectbox(
            "Mes",
            list(range(1, 13)),
            index=hoy.month - 1,
            format_func=lambda m: calendar.month_name[m],
            key="calendar_month"
        )
        anio_cal = st.number_input("Año", min_value=2020, max_value=2100, value=hoy.year, step=1, key="calendar_year")

        render_mini_calendar(conn, selected_patient["rehab_active_id"], int(anio_cal), int(mes_cal))

        st.markdown("### Historial del NHC seleccionado")
        sess_cols, sess_rows = get_treatment_sessions(conn, selected_patient["rehab_active_id"])

        sess_data = []
        for r in sess_rows:
            d = dict(zip(sess_cols, r))
            d["NHC"] = d.pop("patient_id")
            d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
            if hasattr(d["session_date"], "isoformat"):
                d["session_date"] = d["session_date"].isoformat()
            d["session_time"] = str(d["session_time"])[:5] if d.get("session_time") is not None else ""
            d["status"] = estado_sesion_label(d["status"])
            if hasattr(d["created_at"], "isoformat"):
                d["created_at"] = d["created_at"].isoformat()
            d["motivo_falta"] = d.pop("absence_reason")
            d["motivo_fuera_dias"] = d.pop("out_of_schedule_reason")
            d["registrado_por"] = d.pop("recorded_by")
            d.pop("subspecialty", None)
            sess_data.append(d)

        if sess_data:
            st.dataframe(sess_data, width="stretch", hide_index=True)
        else:
            st.info("Todavía no hay sesiones registradas para este NHC.")

    st.subheader("Histórico de pacientes con alta")
    _, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, prescribed_sessions, start_date, discharged_at, discharge_reason, discharge_comment
        FROM rehab_active
        WHERE status='ALTA'
        ORDER BY discharged_at DESC
        LIMIT 300
    """)
    data = [{"NHC": r[1]} for r in rows]
    st.dataframe(data, width="stretch", hide_index=True)

elif page == "🧾 Auditoría clínica":
    st.subheader("Registro de auditoría clínica")

    f1, f2, f3 = st.columns([1, 1, 1], gap="medium")
    with f1:
        audit_pr_filter = st.selectbox("Prioridad", ["Todas"] + PRIORIDADES, key="audit_priority")
    with f2:
        audit_elig_filter = st.selectbox("Elegibilidad", ["Solo elegibles", "Todos"], key="audit_elig")
    with f3:
        audit_sp_filter = st.selectbox(
            "Especialidad",
            ["Todas"] + especialidades_activas,
            index=(0 if specialty_filter == "Todas" else (especialidades_activas.index(specialty_filter) + 1)),
            key="audit_specialty"
        )

    audit_ss_filter = "Todas"
    if audit_sp_filter != "Todas" and specialty_requires_subspecialty(conn, audit_sp_filter):
        areas_audit = get_nombres_areas_por_especialidad(conn, audit_sp_filter, only_active=True)
        audit_ss_filter = st.selectbox(
            f"Área ({audit_sp_filter})",
            ["Todas"] + areas_audit,
            key="audit_area"
        )

    where = []
    params = []

    if audit_elig_filter == "Solo elegibles":
        where.append("COALESCE(w.eligible, FALSE) = TRUE")

    if audit_pr_filter != "Todas":
        where.append("al.chosen_priority_level = %s")
        params.append(audit_pr_filter)

    if audit_sp_filter != "Todas":
        where.append("al.specialty = %s")
        params.append(audit_sp_filter)

        if audit_ss_filter != "Todas":
            where.append("al.subspecialty = %s")
            params.append(audit_ss_filter)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    _, rows = fetch_all(conn, f"""
        SELECT
            al.id,
            al.event,
            al.patient_id,
            al.specialty,
            al.subspecialty,
            al.waitlist_id,
            al.rehab_active_id,
            al.chosen_priority_level,
            al.wait_days,
            al.rule_applied,
            al.reason,
            al.comment,
            al.actor,
            al.created_at,
            w.eligible
        FROM assignment_log al
        LEFT JOIN waitlist w
            ON al.waitlist_id = w.id
        {where_sql}
        ORDER BY al.id DESC
        LIMIT 400
    """, tuple(params))

    data = [{"NHC": r[2]} for r in rows]
    st.dataframe(data, width="stretch", hide_index=True)

elif page == "⚙️ Ajustes":
    if not acceso_configuracion_permitido():
        st.stop()

    st.subheader("⚙️ Ajustes del sistema")

    tab1, tab2, tab3 = st.tabs(["Especialidades", "Áreas", "Listado actual"])

    with tab1:
        st.markdown("### Añadir nueva especialidad")
        n1, n2 = st.columns([2, 1])
        with n1:
            nueva_especialidad = st.text_input(
                "Nombre de la especialidad",
                placeholder="Ej: Rehabilitación vestibular",
                key="cfg_new_specialty"
            ).strip()
        with n2:
            requiere_area = st.checkbox(
                "Requiere área",
                value=False,
                key="cfg_requires_subspecialty"
            )

        if st.button("Guardar especialidad", key="cfg_save_specialty", width="stretch"):
            if not nueva_especialidad:
                st.error("Introduce el nombre de la especialidad.")
            else:
                add_specialty_config(conn, nueva_especialidad, requiere_area)
                st.success("Especialidad guardada.")
                st.rerun()

        st.markdown("### Activar o desactivar especialidades")
        especialidades_todas = get_especialidades(conn, only_active=False)
        if especialidades_todas:
            for nombre, req_sub, active in especialidades_todas:
                c1, c2, c3 = st.columns([2.5, 1.2, 1])
                with c1:
                    st.write(f"**{nombre}**")
                with c2:
                    st.write("Con áreas" if req_sub else "Sin áreas")
                with c3:
                    nuevo_estado = st.toggle(
                        "Activa",
                        value=active,
                        key=f"toggle_specialty_{nombre}",
                        label_visibility="collapsed"
                    )
                    if nuevo_estado != active:
                        set_specialty_active(conn, nombre, nuevo_estado)
                        st.rerun()

    with tab2:
        st.markdown("### Añadir nueva área")
        especialidades_con_area = [n for n, req, _ in get_especialidades(conn, only_active=False) if req]

        if not especialidades_con_area:
            st.info("No hay especialidades configuradas que requieran áreas.")
        else:
            a1, a2 = st.columns([1.5, 2])
            with a1:
                especialidad_destino = st.selectbox(
                    "Especialidad",
                    especialidades_con_area,
                    key="cfg_area_specialty"
                )
            with a2:
                nueva_area = st.text_input(
                    "Nombre del área",
                    placeholder="Ej: Dolor crónico",
                    key="cfg_new_area"
                ).strip()

            if st.button("Guardar área", key="cfg_save_area", width="stretch"):
                if not nueva_area:
                    st.error("Introduce el nombre del área.")
                else:
                    add_subspecialty_config(conn, especialidad_destino, nueva_area)
                    st.success("Área guardada.")
                    st.rerun()

            st.markdown("### Activar o desactivar áreas")
            for esp in especialidades_con_area:
                st.markdown(f"**{esp}**")
                areas = get_areas_por_especialidad(conn, esp, only_active=False)
                if not areas:
                    st.caption("Sin áreas registradas.")
                else:
                    for area_nombre, area_activa in areas:
                        c1, c2 = st.columns([3, 1])
                        with c1:
                            st.write(area_nombre)
                        with c2:
                            nuevo_estado = st.toggle(
                                "Activa",
                                value=area_activa,
                                key=f"toggle_area_{esp}_{area_nombre}",
                                label_visibility="collapsed"
                            )
                            if nuevo_estado != area_activa:
                                set_subspecialty_active(conn, esp, area_nombre, nuevo_estado)
                                st.rerun()

    with tab3:
        st.markdown("### Especialidades")
        rows = get_especialidades(conn, only_active=False)
        data = []
        for nombre, req_sub, active in rows:
            data.append({
                "especialidad": nombre,
                "requiere_area": "Sí" if req_sub else "No",
                "activa": "Sí" if active else "No",
            })
        st.dataframe(data, width="stretch", hide_index=True)

        st.markdown("### Áreas")
        _, rows_areas = fetch_all(conn, """
            SELECT specialty_name, name, active
            FROM subspecialties_config
            ORDER BY specialty_name ASC, name ASC
        """)
        data_areas = []
        for specialty_name, name, active in rows_areas:
            data_areas.append({
                "especialidad": specialty_name,
                "área": name,
                "activa": "Sí" if active else "No",
            })
        st.dataframe(data_areas, width="stretch", hide_index=True)

st.caption("Consejo: guarda las credenciales en Secrets y no dentro del código.")