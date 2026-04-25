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
    rehab_active_id: int,
    patient_id: str,
    specialty: str,
    subspecialty: str | None,
    session_date_value,
    session_time_value,
    status: str,
    absence_reason: str,
    out_of_schedule_reason: str,
    recorded_by: str,
    clinical_note: str = "",
    pain_eva: int | None = None,
    functional_status: str = "",
    goal_status: str = "",
    incidents: str = "",
):
    created_at = now_iso()

    if status in ["REALIZADA", "REVISION"]:
        absence_reason = None
    elif not absence_reason.strip():
        absence_reason = "Sin especificar"

    if not out_of_schedule_reason.strip():
        out_of_schedule_reason = None

    if clinical_note is not None:
        clinical_note = clinical_note.strip()

    if functional_status is not None:
        functional_status = functional_status.strip()

    if goal_status is not None:
        goal_status = goal_status.strip()

    if incidents is not None:
        incidents = incidents.strip()

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO treatment_sessions
                (
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
                    created_at,
                    clinical_note,
                    pain_eva,
                    functional_status,
                    goal_status,
                    incidents
                )
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            created_at,
            clinical_note if clinical_note else None,
            pain_eva,
            functional_status if functional_status else None,
            goal_status if goal_status else None,
            incidents if incidents else None,
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
            created_at,
            clinical_note,
            pain_eva,
            functional_status,
            goal_status,
            incidents
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        ORDER BY session_date DESC, session_time DESC NULLS LAST, id DESC
    """, (rehab_active_id,))
    return cols, rows

def get_clinical_followup_summary(conn, rehab_active_id: int):
    _, rows = fetch_all(conn, """
        SELECT
            session_date,
            status,
            pain_eva,
            functional_status,
            goal_status,
            clinical_note
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        ORDER BY session_date ASC, id ASC
    """, (rehab_active_id,))

    total_sessions = 0
    realizadas = 0
    revisiones = 0
    faltas_justificadas = 0
    faltas_no_justificadas = 0

    pain_values = []
    last_functional_status = None
    last_goal_status = None

    for row in rows:
        _, status, pain_eva, functional_status, goal_status, _ = row
        total_sessions += 1

        if status == "REALIZADA":
            realizadas += 1
        elif status == "REVISION":
            revisiones += 1
        elif status == "FALTA_JUSTIFICADA":
            faltas_justificadas += 1
        elif status == "FALTA_NO_JUSTIFICADA":
            faltas_no_justificadas += 1

        if pain_eva is not None:
            pain_values.append(int(pain_eva))

        if functional_status:
            last_functional_status = functional_status

        if goal_status:
            last_goal_status = goal_status

    adherence_denominator = realizadas + revisiones + faltas_justificadas + faltas_no_justificadas
    adherence_percent = 0
    if adherence_denominator > 0:
        adherence_percent = round(((realizadas + revisiones) / adherence_denominator) * 100, 1)

    first_pain = pain_values[0] if pain_values else None
    last_pain = pain_values[-1] if pain_values else None

    pain_trend = "Sin datos"
    if first_pain is not None and last_pain is not None:
        if last_pain < first_pain:
            pain_trend = "Mejora"
        elif last_pain > first_pain:
            pain_trend = "Empeora"
        else:
            pain_trend = "Sin cambios"

    abandonment_risk = faltas_no_justificadas >= 2
    stagnation_risk = last_goal_status in ["NO_INICIADO", "PARCIAL"] and pain_trend == "Sin cambios"

    return {
        "total_sessions": total_sessions,
        "realizadas": realizadas,
        "revisiones": revisiones,
        "faltas_justificadas": faltas_justificadas,
        "faltas_no_justificadas": faltas_no_justificadas,
        "adherence_percent": adherence_percent,
        "first_pain": first_pain,
        "last_pain": last_pain,
        "pain_trend": pain_trend,
        "last_functional_status": last_functional_status,
        "last_goal_status": last_goal_status,
        "abandonment_risk": abandonment_risk,
        "stagnation_risk": stagnation_risk,
    }

def generate_clinical_followup_report(conn, rehab_active_id: int):
    summary = get_clinical_followup_summary(conn, rehab_active_id)

    texto = []
    texto.append("INFORME AUTOMÁTICO DE SEGUIMIENTO CLÍNICO")
    texto.append("")
    texto.append(f"Sesiones registradas: {summary['total_sessions']}")
    texto.append(f"Sesiones realizadas: {summary['realizadas']}")
    texto.append(f"Revisiones: {summary['revisiones']}")
    texto.append(f"Faltas justificadas: {summary['faltas_justificadas']}")
    texto.append(f"Faltas no justificadas: {summary['faltas_no_justificadas']}")
    texto.append(f"Adherencia estimada: {summary['adherence_percent']}%")
    texto.append("")

    if summary["first_pain"] is not None and summary["last_pain"] is not None:
        texto.append(
            f"Evolución del dolor (EVA): inicial {summary['first_pain']} / actual {summary['last_pain']} "
            f"→ tendencia: {summary['pain_trend']}."
        )
    else:
        texto.append("Evolución del dolor (EVA): sin datos suficientes.")

    if summary["last_functional_status"]:
        texto.append(f"Última valoración funcional: {summary['last_functional_status']}.")
    else:
        texto.append("Última valoración funcional: sin registro.")

    if summary["last_goal_status"]:
        texto.append(f"Estado del objetivo terapéutico: {summary['last_goal_status']}.")
    else:
        texto.append("Estado del objetivo terapéutico: sin registro.")

    texto.append("")

    if summary["abandonment_risk"]:
        texto.append("Alerta: posible riesgo de abandono terapéutico por faltas no justificadas.")
    else:
        texto.append("No se detectan signos claros de abandono terapéutico.")

    if summary["stagnation_risk"]:
        texto.append("Alerta: posible estancamiento funcional. Se recomienda revisar el plan terapéutico.")
    else:
        texto.append("No se detectan signos claros de estancamiento funcional.")

    return "\n".join(texto)

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

def get_patient_pain_series(conn, rehab_active_id: int):
    cols, rows = fetch_all(conn, """
        SELECT session_date, pain_eva
        FROM treatment_sessions
        WHERE rehab_active_id = %s
          AND pain_eva IS NOT NULL
          AND status IN ('REALIZADA', 'REVISION')
        ORDER BY session_date ASC, id ASC
    """, (rehab_active_id,))
    return cols, rows


def get_patient_status_distribution(conn, rehab_active_id: int):
    cols, rows = fetch_all(conn, """
        SELECT status, COUNT(*) AS total
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        GROUP BY status
        ORDER BY status ASC
    """, (rehab_active_id,))
    return cols, rows


def get_dashboard_specialty_summary(conn):
    cols, rows = fetch_all(conn, """
        SELECT
            specialty,
            COUNT(*) FILTER (WHERE status = 'ACTIVO') AS activos,
            COUNT(*) FILTER (WHERE status = 'ALTA') AS altas
        FROM rehab_active
        GROUP BY specialty
        ORDER BY specialty ASC
    """)
    return cols, rows


def get_dashboard_session_summary(conn):
    cols, rows = fetch_all(conn, """
        SELECT
            status,
            COUNT(*) AS total
        FROM treatment_sessions
        GROUP BY status
        ORDER BY status ASC
    """)
    return cols, rows


def get_dashboard_adherence_by_specialty(conn):
    cols, rows = fetch_all(conn, """
        SELECT
            specialty,
            COUNT(*) FILTER (WHERE status IN ('REALIZADA', 'REVISION')) AS asistidas,
            COUNT(*) FILTER (WHERE status IN ('FALTA_JUSTIFICADA', 'FALTA_NO_JUSTIFICADA')) AS faltas,
            COUNT(*) AS total
        FROM treatment_sessions
        GROUP BY specialty
        ORDER BY specialty ASC
    """)
    return cols, rows