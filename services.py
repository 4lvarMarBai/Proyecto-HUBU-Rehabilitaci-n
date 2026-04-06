# services.py

import streamlit as st
from datetime import datetime, UTC

from database import fetch_all, execute_sql, now_iso
from validators import validar_regla_coordinacion


# -------------------- CONFIG ACCESS --------------------
CLAVE_CONFIGURACION = "admin123"


def acceso_configuracion_permitido() -> bool:
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
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    return st.session_state["config_access_granted"]


def cerrar_configuracion_si_sale(page_actual: str):
    if "ultima_page" not in st.session_state:
        st.session_state["ultima_page"] = page_actual

    if st.session_state["ultima_page"] == "⚙️ Ajustes" and page_actual != "⚙️ Ajustes":
        st.session_state["config_access_granted"] = False

    st.session_state["ultima_page"] = page_actual


# -------------------- STATS --------------------
def get_stats(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM waitlist WHERE status='EN_ESPERA' AND eligible=TRUE")
        waiting = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='ACTIVO'")
        active = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='ALTA'")
        discharged = cur.fetchone()[0]

    return waiting, active, discharged


# -------------------- PREVIEW --------------------
def preview_next_patient(conn, specialty_filter, subspecialty_filter):
    _, rows = fetch_all(conn, """
        SELECT id, patient_id, priority_level, specialty, subspecialty, request_date
        FROM waitlist
        WHERE status='EN_ESPERA'
        ORDER BY request_date ASC
        LIMIT 1
    """)

    if not rows:
        return None

    r = rows[0]

    return {
        "waitlist_id": r[0],
        "patient_id": r[1],
        "priority_level": r[2],
        "specialty": r[3],
        "subspecialty": r[4],
        "request_date": str(r[5]),
        "wait_days": 0,
        "rule_applied": "BÁSICO"
    }


# -------------------- CREATE WAITLIST --------------------
def add_waiting_patient_multiple(
    conn,
    patient_id,
    priority_level,
    requests,
    request_date_iso,
    eligible,
    actor,
    slot_type,
    time_preference,
    transport_mode,
    preferred_hour,
    coordination_rule
):
    now = now_iso()

    with conn.cursor() as cur:
        for req in requests:
            cur.execute("""
                INSERT INTO waitlist (
                    patient_id, priority_level, specialty, subspecialty,
                    prescribed_sessions, slot_type, time_preference,
                    transport_mode, preferred_hour, coordination_rule,
                    request_date, created_at, status, eligible
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'EN_ESPERA',%s)
            """, (
                patient_id,
                priority_level,
                req["specialty"],
                req["subspecialty"],
                req["prescribed_sessions"],
                slot_type,
                time_preference,
                transport_mode,
                preferred_hour,
                coordination_rule,
                request_date_iso,
                now,
                eligible
            ))


# -------------------- ASSIGN --------------------
def assign_next_patient(conn, assigned_by, specialty_filter, subspecialty_filter, attendance_days):
    now = now_iso()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, patient_id, specialty, subspecialty
            FROM waitlist
            WHERE status='EN_ESPERA'
            ORDER BY request_date ASC
            LIMIT 1
        """)
        row = cur.fetchone()

        if not row:
            return None

        waitlist_id, patient_id, specialty, subspecialty = row

        error = validar_regla_coordinacion(
            conn,
            patient_id,
            specialty,
            attendance_days,
            "NINGUNA"
        )

        if error:
            raise ValueError(error)

        cur.execute("""
            INSERT INTO rehab_active (
                patient_id, specialty, subspecialty,
                attendance_days, start_date,
                source_waitlist_id, assigned_by, assigned_at, status
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVO')
            RETURNING id
        """, (
            patient_id,
            specialty,
            subspecialty,
            ",".join(attendance_days),
            now,
            waitlist_id,
            assigned_by,
            now
        ))

        rehab_id = cur.fetchone()[0]

        cur.execute("""
            UPDATE waitlist
            SET status='ASIGNADO'
            WHERE id=%s
        """, (waitlist_id,))

    return {
        "patient_id": patient_id,
        "specialty": specialty,
        "subspecialty": subspecialty,
        "attendance_days": attendance_days
    }


# -------------------- DISCHARGE --------------------
def discharge_patient(conn, rehab_active_id, reason, comment, actor):
    now = now_iso()

    execute_sql(conn, """
        UPDATE rehab_active
        SET status='ALTA',
            discharge_reason=%s,
            discharge_comment=%s,
            discharged_at=%s
        WHERE id=%s
    """, (reason, comment, now, rehab_active_id))

    return True


# -------------------- SESSIONS --------------------
def add_treatment_session(
    conn,
    rehab_active_id,
    patient_id,
    specialty,
    subspecialty,
    session_date_value,
    session_time_value,
    status,
    absence_reason,
    out_of_schedule_reason,
    recorded_by
):
    now = now_iso()

    execute_sql(conn, """
        INSERT INTO treatment_sessions (
            rehab_active_id, patient_id, specialty, subspecialty,
            session_date, session_time, status,
            absence_reason, out_of_schedule_reason,
            recorded_by, created_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
        now
    ))


def get_treatment_sessions(conn, rehab_active_id):
    return fetch_all(conn, """
        SELECT *
        FROM treatment_sessions
        WHERE rehab_active_id=%s
        ORDER BY session_date DESC
    """, (rehab_active_id,))


def get_session_summary(conn, rehab_active_id):
    _, rows = fetch_all(conn, """
        SELECT status, COUNT(*)
        FROM treatment_sessions
        WHERE rehab_active_id=%s
        GROUP BY status
    """, (rehab_active_id,))

    resumen = {
        "REALIZADA": 0,
        "REVISION": 0,
        "FALTA_JUSTIFICADA": 0,
        "FALTA_NO_JUSTIFICADA": 0,
    }

    for s, n in rows:
        resumen[s] = n

    return resumen