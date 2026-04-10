# services.py


from datetime import datetime, UTC

from database import fetch_all, execute_sql, now_iso
from validators import validar_regla_coordinacion, validar_dias_asistencia


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
def assign_next_patient(
    conn,
    assigned_by,
    assigned_clinician_dni,
    assigned_clinician_name,
    assigned_clinician_profession,
    specialty_filter,
    subspecialty_filter,
    attendance_days
):
    now = now_iso()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                id,
                patient_id,
                priority_level,
                specialty,
                subspecialty,
                FLOOR(EXTRACT(EPOCH FROM (%s::timestamptz - request_date)) / 86400)::INTEGER AS wait_days
            FROM waitlist
            WHERE status = 'EN_ESPERA'
            ORDER BY request_date ASC
            LIMIT 1
        """, (now,))
        row = cur.fetchone()

        if not row:
            return None

        waitlist_id, patient_id, priority_level, specialty, subspecialty, wait_days = row

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
                patient_id,
                specialty,
                subspecialty,
                attendance_days,
                start_date,
                source_waitlist_id,
                assigned_by,
                assigned_at,
                assigned_clinician_dni,
                assigned_clinician_name,
                assigned_clinician_profession,
                status
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVO')
            RETURNING id
        """, (
            patient_id,
            specialty,
            subspecialty,
            ",".join(attendance_days),
            now,
            waitlist_id,
            assigned_by,
            now,
            assigned_clinician_dni,
            assigned_clinician_name,
            assigned_clinician_profession,
        ))

        rehab_id = cur.fetchone()[0]

        cur.execute("""
            UPDATE waitlist
            SET status = 'ASIGNADO'
            WHERE id = %s
        """, (waitlist_id,))

    return {
        "rehab_id": rehab_id,
        "patient_id": patient_id,
        "priority_level": priority_level,
        "specialty": specialty,
        "subspecialty": subspecialty,
        "attendance_days": attendance_days,
        "assigned_clinician_dni": assigned_clinician_dni,
        "assigned_clinician_name": assigned_clinician_name,
        "assigned_clinician_profession": assigned_clinician_profession,
        "wait_days": int(wait_days),
        "rule_applied": "orden_por_fecha",
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

def delete_waitlist_entry(conn, waitlist_id: int, actor: str):
    """
    Elimina una solicitud de lista de espera y deja constancia en auditoría.
    Solo debe usarlo un ADMIN desde la interfaz.
    """
    now = now_iso()

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, patient_id, specialty, subspecialty, priority_level
                FROM waitlist
                WHERE id = %s
            """, (waitlist_id,))
            row = cur.fetchone()

            if not row:
                return False, "La solicitud no existe."

            _, patient_id, specialty, subspecialty, priority_level = row

            cur.execute("""
                INSERT INTO assignment_log
                    (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                     chosen_priority_level, wait_days, rule_applied, reason, comment, actor, created_at)
                VALUES
                    ('ELIMINACION_ADMIN_LISTA_ESPERA', %s, NULL, %s, %s, %s, %s, NULL, NULL,
                     'ELIMINADO_POR_ADMIN', 'Registro eliminado por administrador', %s, %s)
            """, (
                waitlist_id,
                patient_id,
                specialty,
                subspecialty,
                priority_level,
                actor,
                now
            ))

            cur.execute("""
                DELETE FROM waitlist
                WHERE id = %s
            """, (waitlist_id,))

    return True, "Solicitud eliminada correctamente."


def delete_active_treatment(conn, rehab_active_id: int, actor: str):
    """
    Elimina un tratamiento activo y sus sesiones asociadas, dejando auditoría.
    Solo debe usarlo un ADMIN.
    """
    now = now_iso()

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, patient_id, specialty, subspecialty
                FROM rehab_active
                WHERE id = %s
            """, (rehab_active_id,))
            row = cur.fetchone()

            if not row:
                return False, "El tratamiento no existe."

            _, patient_id, specialty, subspecialty = row

            cur.execute("""
                INSERT INTO assignment_log
                    (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                     chosen_priority_level, wait_days, rule_applied, reason, comment, actor, created_at)
                VALUES
                    ('ELIMINACION_ADMIN_TRATAMIENTO', NULL, %s, %s, %s, %s, NULL, NULL, NULL,
                     'ELIMINADO_POR_ADMIN', 'Tratamiento eliminado por administrador', %s, %s)
            """, (
                rehab_active_id,
                patient_id,
                specialty,
                subspecialty,
                actor,
                now
            ))

            cur.execute("""
                DELETE FROM treatment_sessions
                WHERE rehab_active_id = %s
            """, (rehab_active_id,))

            cur.execute("""
                DELETE FROM rehab_active
                WHERE id = %s
            """, (rehab_active_id,))

    return True, "Tratamiento eliminado correctamente."