# app.py
# Ejecuta con: python -m streamlit run app.py

import os
import re
from datetime import datetime, UTC, date, time

import psycopg
import streamlit as st


# -------------------- Config --------------------
ESPECIALIDADES = ["Electroterapia", "Terapia ocupacional", "Logopedia", "Cinesiterapia"]
SUBESPECIALIDADES_CINESITERAPIA = [
    "Linfedema",
    "Suelo pélvico",
    "Infantil",
    "FT respiratorio",
    "RHB cardiaca",
    "RHB neurológica",
    "Columna/raquis",
    "General",
]
PRIORIDADES = ["urgente", "preferente", "ordinario"]

DIAS_2_MESES = 60
DIAS_3_MESES = 90
DIAS_6_MESES = 183


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
        # -------------------- Tablas --------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            priority_level TEXT NOT NULL CHECK(priority_level IN ('urgente','preferente','ordinario')),
            specialty TEXT NOT NULL CHECK(specialty IN ('Electroterapia','Terapia ocupacional','Logopedia','Cinesiterapia')),
            subspecialty TEXT,
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
            recorded_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY(rehab_active_id) REFERENCES rehab_active(id)
        );
        """)

        # -------------------- Columnas añadidas en versiones nuevas --------------------
        cur.execute("ALTER TABLE rehab_active ADD COLUMN IF NOT EXISTS discharge_comment TEXT;")
        cur.execute("ALTER TABLE assignment_log ADD COLUMN IF NOT EXISTS comment TEXT;")

        # -------------------- Migración de constraints antiguos --------------------
        # waitlist.status
        cur.execute("ALTER TABLE waitlist DROP CONSTRAINT IF EXISTS waitlist_status_check;")
        cur.execute("""
            ALTER TABLE waitlist
            ADD CONSTRAINT waitlist_status_check
            CHECK (status IN ('EN_ESPERA','ASIGNADO','CANCELADO'));
        """)

        # rehab_active.status
        cur.execute("ALTER TABLE rehab_active DROP CONSTRAINT IF EXISTS rehab_active_status_check;")
        cur.execute("""
            ALTER TABLE rehab_active
            ADD CONSTRAINT rehab_active_status_check
            CHECK (status IN ('ACTIVO','ALTA'));
        """)

        # treatment_sessions.status
        cur.execute("ALTER TABLE treatment_sessions DROP CONSTRAINT IF EXISTS treatment_sessions_status_check;")
        cur.execute("""
            ALTER TABLE treatment_sessions
            ADD CONSTRAINT treatment_sessions_status_check
            CHECK (status IN ('REALIZADA', 'FALTA_JUSTIFICADA', 'FALTA_NO_JUSTIFICADA'));
        """)

        # -------------------- Valores antiguos -> nuevos --------------------
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


def iso_utc_from_date(d: date) -> str:
    return datetime.combine(d, time.min).replace(tzinfo=UTC).isoformat()


def now_iso():
    return datetime.now(UTC).isoformat()


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

        if specialty_filter == "Cinesiterapia" and subspecialty_filter != "Todas":
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
):
    created_at = now_iso()

    with conn.transaction():
        with conn.cursor() as cur:
            for req in requests:
                specialty = req["specialty"]
                subspecialty = req.get("subspecialty")

                if specialty != "Cinesiterapia":
                    subspecialty = None

                cur.execute(
                    """
                    INSERT INTO waitlist (
                        patient_id, priority_level, specialty, subspecialty,
                        request_date, created_at, status, eligible
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        patient_id,
                        priority_level,
                        specialty,
                        subspecialty,
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
                        ('ALTA_MANUAL_LISTA_ESPERA', NULL, NULL, %s, %s, %s, %s, NULL, NULL, NULL, NULL, %s, %s)
                    """,
                    (patient_id, specialty, subspecialty, priority_level, actor, created_at),
                )


def assign_next_patient(conn, assigned_by="SISTEMA", specialty_filter="Todas", subspecialty_filter="Todas"):
    now = now_iso()

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
                INSERT INTO rehab_active
                    (patient_id, specialty, subspecialty, start_date, source_waitlist_id, assigned_by, assigned_at, status)
                VALUES
                    (%s,%s,%s,%s,%s,%s,%s,'ACTIVO')
                RETURNING id
            """, (patient_id, specialty, subspecialty, now, waitlist_id, assigned_by, now))
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
                    ('ASIGNACION_AUTOMATICA', %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s)
            """, (
                waitlist_id, rehab_active_id, patient_id, specialty, subspecialty,
                priority_level, int(wait_days), rule_applied, assigned_by, now
            ))

            return {
                "rehab_active_id": rehab_active_id,
                "waitlist_id": waitlist_id,
                "patient_id": patient_id,
                "priority_level": priority_level,
                "specialty": specialty,
                "subspecialty": subspecialty,
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
    recorded_by: str,
):
    created_at = now_iso()

    if status == "REALIZADA":
        absence_reason = None
    elif not absence_reason.strip():
        absence_reason = "Sin especificar"

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO treatment_sessions
                (rehab_active_id, patient_id, specialty, subspecialty, session_date, session_time,
                 status, absence_reason, recorded_by, created_at)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            rehab_active_id,
            patient_id,
            specialty,
            subspecialty,
            session_date_value,
            session_time_value,
            status,
            absence_reason,
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
            recorded_by,
            created_at
        FROM treatment_sessions
        WHERE rehab_active_id = %s
        ORDER BY session_date DESC, session_time DESC NULLS LAST, id DESC
    """, (rehab_active_id,))
    return cols, rows


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
    if specialty == "Cinesiterapia" and subspecialty:
        return f"{specialty} · {subspecialty}"
    return specialty


def estado_sesion_label(status: str) -> str:
    if status == "REALIZADA":
        return "Realizada"
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
</style>
""", unsafe_allow_html=True)

try:
    conn = get_conn()
    init_db(conn)
except Exception as e:
    st.error(f"Error conectando con la base de datos en la nube: {e}")
    st.stop()

# Sidebar
st.sidebar.markdown("## 🏥 Tratamiento Fisioterapia")
page = st.sidebar.radio(
    "Navegación",
    ["Panel clínico", "Nueva solicitud", "Tratamiento activo", "Auditoría"],
    index=0,
    key="nav_page"
)
st.sidebar.markdown("---")

specialty_filter = st.sidebar.selectbox(
    "Filtro por especialidad",
    ["Todas"] + ESPECIALIDADES,
    key="sidebar_specialty_filter"
)
subspecialty_filter = "Todas"
if specialty_filter == "Cinesiterapia":
    subspecialty_filter = st.sidebar.selectbox(
        "Área (Cinesiterapia)",
        ["Todas"] + SUBESPECIALIDADES_CINESITERAPIA,
        key="sidebar_cinesi_area_filter"
    )

actor_sidebar = st.sidebar.text_input(
    "DNI del clínico (opcional en nueva solicitud)",
    value="",
    key="sidebar_actor"
).strip().upper()

if actor_sidebar and not is_valid_dni(actor_sidebar):
    st.sidebar.error("Introduce un DNI válido. Ejemplo: 12345678Z")

waiting, active, discharged = get_stats(conn)

# Header
st.markdown('<div class="title">Gestión de Lista de Espera y Tratamiento</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="muted">Prioridades: urgente / preferente / ordinario · '
    'Especialidades: Electroterapia, Terapia ocupacional, Logopedia, Cinesiterapia</div>',
    unsafe_allow_html=True
)
st.write("")

# KPI cards
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

# -------------------- Pages --------------------
if page == "Panel clínico":
    left, right = st.columns([2.2, 1], gap="large")

    with left:
        st.subheader("📋 Lista de espera")
        now = now_iso()

        f1, f2, f3 = st.columns([1, 1, 1], gap="medium")
        with f1:
            pr_filter = st.selectbox("Prioridad", ["Todas"] + PRIORIDADES, key="dash_priority")
        with f2:
            elig_filter = st.selectbox("Elegibilidad", ["Solo elegibles", "Todos"], key="dash_elig")
        with f3:
            sp_filter = st.selectbox(
                "Especialidad (tabla)",
                ["Todas"] + ESPECIALIDADES,
                index=(0 if specialty_filter == "Todas" else (ESPECIALIDADES.index(specialty_filter) + 1)),
                key="dash_specialty"
            )

        ss_filter_table = "Todas"
        if sp_filter == "Cinesiterapia":
            ss_filter_table = st.selectbox(
                "Área (tabla - Cinesiterapia)",
                ["Todas"] + SUBESPECIALIDADES_CINESITERAPIA,
                key="dash_cinesi_area"
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

            if sp_filter == "Cinesiterapia" and ss_filter_table != "Todas":
                where.append("subspecialty=%s")
                params.append(ss_filter_table)

        where_sql = " AND ".join(where)

        cols, rows = fetch_all(conn, f"""
            SELECT
                id,
                patient_id,
                priority_level,
                specialty,
                subspecialty,
                request_date,
                FLOOR(EXTRACT(EPOCH FROM (%s::timestamptz - request_date)) / 86400)::INTEGER AS wait_days,
                eligible
            FROM waitlist
            WHERE {where_sql}
            ORDER BY request_date ASC, id ASC
        """, tuple(params))

        data = []
        for r in rows:
            d = dict(zip(cols, r))
            if "patient_id" in d:
                d["NHC"] = d.pop("patient_id")
            d["priority_level"] = priority_badge(d["priority_level"])
            d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
            if hasattr(d["request_date"], "isoformat"):
                d["request_date"] = d["request_date"].isoformat()
            d["eligible"] = "Sí" if d.get("eligible") else "No"
            d.pop("subspecialty", None)
            data.append(d)

        st.dataframe(data, width="stretch", hide_index=True)

    with right:
        st.subheader("⚙️ Acciones rápidas")

        st.caption("Asignación automática usando los filtros laterales.")
        if st.button("Asignar siguiente", width="stretch", key="btn_assign_next"):
            if not actor_sidebar:
                st.error("Debes introducir el DNI del clínico.")
            elif not is_valid_dni(actor_sidebar):
                st.error("El DNI introducido no es válido.")
            else:
                res = assign_next_patient(
                    conn,
                    assigned_by=actor_sidebar,
                    specialty_filter=specialty_filter,
                    subspecialty_filter=subspecialty_filter
                )
                if not res:
                    st.warning("No hay pacientes elegibles con esos filtros.")
                else:
                    st.success(f"Asignado NHC: {res['patient_id']} ({res['priority_level']})")
                    st.info(
                        f"{specialty_label(res['specialty'], res['subspecialty'])} · {res['wait_days']} días · Regla: {res['rule_applied']}"
                    )
                    st.rerun()

        st.divider()
        st.caption("Dar alta a un NHC activo para liberar plaza.")
        colsA, rowsA = fetch_all(conn, """
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
                    ok = discharge_patient(
                        conn,
                        options[pick],
                        reason=reason,
                        comment=comment.strip(),
                        actor=actor_sidebar
                    )
                    if ok:
                        st.success("Alta registrada.")
                        st.rerun()
                    else:
                        st.error("No se pudo registrar el alta.")

elif page == "Nueva solicitud":
    st.subheader("➕ Nueva solicitud de rehabilitación")

    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 1.1], gap="large")

    with c1:
        patient_id = st.text_input(
            "NHC del paciente",
            placeholder="Ej: 123456",
            key="req_patient_id"
        ).strip()

    with c2:
        priority_level = st.selectbox(
            "Prioridad",
            PRIORIDADES,
            key="req_priority"
        )

    with c3:
        request_dt = st.date_input(
            "Fecha de solicitud",
            value=date.today(),
            key="req_request_date"
        )

    with c4:
        eligible = st.checkbox("Elegible", value=True, key="req_eligible")

    selected_specialties = st.multiselect(
        "Especialidades",
        ESPECIALIDADES,
        key="req_specialties_multi"
    )

    cinesi_subspecialties = []
    if "Cinesiterapia" in selected_specialties:
        cinesi_subspecialties = st.multiselect(
            "Áreas de Cinesiterapia",
            SUBESPECIALIDADES_CINESITERAPIA,
            key="req_cinesi_areas_multi"
        )
        st.caption("Si seleccionas Cinesiterapia, debes indicar al menos un área.")

    st.caption("Se creará una solicitud independiente por cada especialidad seleccionada. En Cinesiterapia, una por cada área seleccionada.")

    if st.button("Guardar solicitud", width="stretch", key="req_submit"):
        if not patient_id:
            st.error("Introduce el NHC del paciente.")
        elif not is_valid_nhc(patient_id):
            st.error("El NHC debe tener exactamente 6 números.")
        elif not selected_specialties:
            st.error("Selecciona al menos una especialidad.")
        else:
            requests = []

            for sp in selected_specialties:
                if sp == "Cinesiterapia":
                    if not cinesi_subspecialties:
                        st.error("Debes seleccionar al menos un área para Cinesiterapia.")
                        st.stop()

                    for sub in cinesi_subspecialties:
                        requests.append({
                            "specialty": "Cinesiterapia",
                            "subspecialty": sub
                        })
                else:
                    requests.append({
                        "specialty": sp,
                        "subspecialty": None
                    })

            actor_para_guardar = actor_sidebar if actor_sidebar and is_valid_dni(actor_sidebar) else "SIN_DNI"

            add_waiting_patient_multiple(
                conn,
                patient_id=patient_id,
                priority_level=priority_level,
                requests=requests,
                request_date_iso=iso_utc_from_date(request_dt),
                eligible=eligible,
                actor=actor_para_guardar
            )

            st.success(f"Solicitud guardada para NHC {patient_id}. Se han creado {len(requests)} entradas.")
            st.rerun()

elif page == "Tratamiento activo":
    st.subheader("🧑‍⚕️ NHC en tratamiento")
    cols, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date, assigned_by, assigned_at
        FROM rehab_active
        WHERE status='ACTIVO'
        ORDER BY id DESC
    """)
    data = []
    for r in rows:
        d = dict(zip(cols, r))
        if "patient_id" in d:
            d["NHC"] = d.pop("patient_id")
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        if hasattr(d["start_date"], "isoformat"):
            d["start_date"] = d["start_date"].isoformat()
        if hasattr(d["assigned_at"], "isoformat"):
            d["assigned_at"] = d["assigned_at"].isoformat()
        d.pop("subspecialty", None)
        data.append(d)
    st.dataframe(data, width="stretch", hide_index=True)

    st.divider()
    st.subheader("🗓️ Registro de sesiones por NHC")

    colsA, rowsA = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date
        FROM rehab_active
        WHERE status='ACTIVO'
        ORDER BY id DESC
    """)

    if not rowsA:
        st.info("No hay pacientes activos para registrar sesiones.")
    else:
        options = {
            f"{r[0]} · {r[1]} · {specialty_label(r[2], r[3])} · inicio {str(r[4])[:10]}": {
                "rehab_active_id": r[0],
                "patient_id": r[1],
                "specialty": r[2],
                "subspecialty": r[3],
                "start_date": r[4],
            }
            for r in rowsA
        }

        selected_label = st.selectbox(
            "Selecciona NHC en tratamiento",
            list(options.keys()),
            key="session_patient_pick"
        )
        selected_patient = options[selected_label]

        c1, c2, c3 = st.columns([1, 1, 1], gap="medium")

        with c1:
            session_date_value = st.date_input(
                "Día del tratamiento",
                value=date.today(),
                key="session_date"
            )

        with c2:
            session_time_value = st.time_input(
                "Hora del tratamiento",
                value=time(9, 0),
                key="session_time"
            )

        with c3:
            session_status = st.selectbox(
                "Estado de la sesión",
                ["REALIZADA", "FALTA_JUSTIFICADA", "FALTA_NO_JUSTIFICADA"],
                format_func=estado_sesion_label,
                key="session_status"
            )

        absence_reason = ""
        if session_status != "REALIZADA":
            absence_reason = st.text_area(
                "Motivo de la falta",
                placeholder="Ejemplo: enfermedad, cita médica, no acudió...",
                key="session_absence_reason"
            )
            if session_status == "FALTA_NO_JUSTIFICADA":
                st.warning("Esta falta quedará registrada como NO JUSTIFICADA.")

        if st.button("Guardar sesión o falta", width="stretch", key="btn_save_session"):
            if not actor_sidebar:
                st.error("Debes introducir el DNI del clínico.")
            elif not is_valid_dni(actor_sidebar):
                st.error("El DNI introducido no es válido.")
            elif session_status != "REALIZADA" and not absence_reason.strip():
                st.error("Debes indicar el motivo de la falta.")
            else:
                add_treatment_session(
                    conn=conn,
                    rehab_active_id=selected_patient["rehab_active_id"],
                    patient_id=selected_patient["patient_id"],
                    specialty=selected_patient["specialty"],
                    subspecialty=selected_patient["subspecialty"],
                    session_date_value=session_date_value,
                    session_time_value=session_time_value if session_status == "REALIZADA" else None,
                    status=session_status,
                    absence_reason=absence_reason.strip(),
                    recorded_by=actor_sidebar
                )
                st.success("Registro guardado correctamente.")
                st.rerun()

        st.markdown("### Historial del NHC seleccionado")
        sess_cols, sess_rows = get_treatment_sessions(conn, selected_patient["rehab_active_id"])

        sess_data = []
        for r in sess_rows:
            d = dict(zip(sess_cols, r))
            if "patient_id" in d:
                d["NHC"] = d.pop("patient_id")
            d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
            if hasattr(d["session_date"], "isoformat"):
                d["session_date"] = d["session_date"].isoformat()
            d["session_time"] = str(d["session_time"])[:5] if d.get("session_time") is not None else ""
            d["status"] = estado_sesion_label(d["status"])
            if hasattr(d["created_at"], "isoformat"):
                d["created_at"] = d["created_at"].isoformat()
            d.pop("subspecialty", None)
            sess_data.append(d)

        if sess_data:
            st.dataframe(sess_data, width="stretch", hide_index=True)
        else:
            st.info("Todavía no hay sesiones registradas para este NHC.")

    st.subheader("📁 Historial de NHC dados de alta")
    cols, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date, discharged_at, discharge_reason, discharge_comment
        FROM rehab_active
        WHERE status='ALTA'
        ORDER BY discharged_at DESC
        LIMIT 300
    """)
    data = []
    for r in rows:
        d = dict(zip(cols, r))
        if "patient_id" in d:
            d["NHC"] = d.pop("patient_id")
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        if hasattr(d["start_date"], "isoformat"):
            d["start_date"] = d["start_date"].isoformat()
        if hasattr(d["discharged_at"], "isoformat"):
            d["discharged_at"] = d["discharged_at"].isoformat()
        d.pop("subspecialty", None)
        data.append(d)
    st.dataframe(data, width="stretch", hide_index=True)

elif page == "Auditoría":
    st.subheader("🧾 Auditoría de eventos")

    f1, f2, f3 = st.columns([1, 1, 1], gap="medium")
    with f1:
        audit_pr_filter = st.selectbox(
            "Prioridad",
            ["Todas"] + PRIORIDADES,
            key="audit_priority"
        )
    with f2:
        audit_elig_filter = st.selectbox(
            "Elegibilidad",
            ["Solo elegibles", "Todos"],
            key="audit_elig"
        )
    with f3:
        audit_sp_filter = st.selectbox(
            "Especialidad",
            ["Todas"] + ESPECIALIDADES,
            index=(0 if specialty_filter == "Todas" else (ESPECIALIDADES.index(specialty_filter) + 1)),
            key="audit_specialty"
        )

    audit_ss_filter = "Todas"
    if audit_sp_filter == "Cinesiterapia":
        audit_ss_filter = st.selectbox(
            "Área (Cinesiterapia)",
            ["Todas"] + SUBESPECIALIDADES_CINESITERAPIA,
            key="audit_cinesi_area"
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

        if audit_sp_filter == "Cinesiterapia" and audit_ss_filter != "Todas":
            where.append("al.subspecialty = %s")
            params.append(audit_ss_filter)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    cols, rows = fetch_all(conn, f"""
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

    data = []
    for r in rows:
        d = dict(zip(cols, r))
        if "patient_id" in d:
            d["NHC"] = d.pop("patient_id")
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        if d.get("chosen_priority_level"):
            d["chosen_priority_level"] = priority_badge(d["chosen_priority_level"])
        if hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        d["eligible"] = "Sí" if d.get("eligible") is True else ("No" if d.get("eligible") is False else "")
        d.pop("subspecialty", None)
        data.append(d)

    st.dataframe(data, width="stretch", hide_index=True)

st.caption("Consejo: guarda las credenciales en Secrets y no dentro del código.")