# app.py
# Ejecuta con: python -m streamlit run app.py

import sqlite3
from datetime import datetime, UTC, date, time
import streamlit as st

# -------------------- Config --------------------
DB_PATH = "rehab_app_ui_v2.db"  # nombre nuevo para evitar conflictos con BDs antiguas

SPECIALTIES = ["Electroterapia", "Terapia ocupacional", "Logopedia", "Cinesiterapia"]
CINESITERAPIA_SUBSPECIALTIES = [
    "Linfedema",
    "Suelo pélvico",
    "Infantil",
    "FT respiratorio",
    "RHB cardiaca",
    "RHB neurológica",
    "Columna/raquis",
    "General",
]
PRIORITIES = ["urgente", "preferente", "ordinario"]

# Umbrales (aprox. en días, enteros)
DAYS_2_MONTHS = 60
DAYS_3_MONTHS = 90
DAYS_6_MONTHS = 183


# -------------------- DB helpers --------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn):
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        priority_level TEXT NOT NULL CHECK(priority_level IN ('urgente','preferente','ordinario')),
        specialty TEXT NOT NULL CHECK(specialty IN ('Electroterapia','Terapia ocupacional','Logopedia','Cinesiterapia')),
        subspecialty TEXT, -- NUEVO: solo para Cinesiterapia
        request_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','ASSIGNED','CANCELLED')),
        eligible INTEGER NOT NULL DEFAULT 1 CHECK(eligible IN (0,1))
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rehab_active (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        specialty TEXT NOT NULL,
        subspecialty TEXT, -- NUEVO
        start_date TEXT NOT NULL,
        source_waitlist_id INTEGER NOT NULL,
        assigned_by TEXT NOT NULL,
        assigned_at TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISCHARGED')),
        discharge_reason TEXT,
        discharged_at TEXT,

        FOREIGN KEY(source_waitlist_id) REFERENCES waitlist(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS assignment_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event TEXT NOT NULL, -- AUTO_ASSIGNMENT, DISCHARGE, MANUAL_ADD, CANCEL
        waitlist_id INTEGER,
        rehab_active_id INTEGER,
        patient_id TEXT NOT NULL,
        specialty TEXT,
        subspecialty TEXT, -- NUEVO
        chosen_priority_level TEXT,
        wait_days INTEGER,
        rule_applied TEXT,
        reason TEXT,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    conn.commit()


def fetch_all(conn, query, params=()):
    cur = conn.cursor()
    cur.execute(query, params)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return cols, rows


def iso_utc_from_date(d: date) -> str:
    return datetime.combine(d, time.min).replace(tzinfo=UTC).isoformat()


def now_iso():
    return datetime.now(UTC).isoformat()


# -------------------- Business logic --------------------
def _selection_sql(where_extra: str = "") -> str:
    """
    SQL base para seleccionar el siguiente candidato según reglas,
    opcionalmente filtrado (p.ej. por especialidad / subespecialidad).
    """
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
                CAST((julianday(?) - julianday(request_date)) AS INTEGER) AS wait_days
            FROM waitlist
            WHERE status='WAITING' AND eligible=1
            {extra}
        )
        SELECT
            id, patient_id, priority_level, specialty, subspecialty, request_date, wait_days,
            CASE
              WHEN priority_level='preferente' AND wait_days >= {DAYS_2_MONTHS} THEN 'preferente>=2m_before_urgente'
              WHEN priority_level='ordinario' AND wait_days >= {DAYS_6_MONTHS} THEN 'ordinario>=6m_before_urgente'
              WHEN priority_level='urgente' THEN 'urgente_base'
              WHEN priority_level='ordinario' AND wait_days >= {DAYS_3_MONTHS} THEN 'ordinario>=3m_before_preferente'
              WHEN priority_level='preferente' THEN 'preferente_base'
              ELSE 'ordinario_base'
            END AS rule_applied
        FROM candidates
        ORDER BY
            CASE
                WHEN priority_level='preferente' AND wait_days >= {DAYS_2_MONTHS} THEN 0
                WHEN priority_level='ordinario' AND wait_days >= {DAYS_6_MONTHS} THEN 0
                WHEN priority_level='urgente' THEN 1
                WHEN priority_level='ordinario' AND wait_days >= {DAYS_3_MONTHS} THEN 2
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
    """
    Devuelve (where_extra_sql, params_extra_list).
    """
    where_parts = []
    params = []

    if specialty_filter != "Todas":
        where_parts.append("specialty = ?")
        params.append(specialty_filter)

        # Solo tiene sentido subespecialidad si es Cinesiterapia
        if specialty_filter == "Cinesiterapia" and subspecialty_filter != "Todas":
            where_parts.append("subspecialty = ?")
            params.append(subspecialty_filter)

    where_extra = " AND ".join(where_parts)
    return where_extra, params


def preview_next_patient(conn, specialty_filter: str, subspecialty_filter: str):
    cur = conn.cursor()
    base_now = now_iso()

    where_extra, extra_params = _build_filters(specialty_filter, subspecialty_filter)
    sql = _selection_sql(where_extra=where_extra)

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
        "request_date": request_date,
        "wait_days": int(wait_days),
        "rule_applied": rule_applied,
    }


def add_waiting_patient(
    conn,
    patient_id: str,
    priority_level: str,
    specialty: str,
    subspecialty: str | None,
    request_date_iso: str,
    eligible: bool,
    actor: str,
):
    created_at = now_iso()

    # Forzamos subspecialty a None si no es Cinesiterapia
    if specialty != "Cinesiterapia":
        subspecialty = None

    conn.execute(
        """
        INSERT INTO waitlist (patient_id, priority_level, specialty, subspecialty, request_date, created_at, status, eligible)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (patient_id, priority_level, specialty, subspecialty, request_date_iso, created_at, "WAITING", 1 if eligible else 0),
    )

    conn.execute(
        """
        INSERT INTO assignment_log
            (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty, chosen_priority_level, wait_days, rule_applied, reason, actor, created_at)
        VALUES
            ('MANUAL_ADD', NULL, NULL, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
        """,
        (patient_id, specialty, subspecialty, priority_level, actor, created_at),
    )

    conn.commit()


def assign_next_patient(conn, assigned_by="SYSTEM", specialty_filter="Todas", subspecialty_filter="Todas"):
    now = now_iso()
    cur = conn.cursor()

    conn.execute("BEGIN IMMEDIATE")
    try:
        where_extra, extra_params = _build_filters(specialty_filter, subspecialty_filter)
        sql = _selection_sql(where_extra=where_extra)

        cur.execute(sql, tuple([now] + extra_params))
        row = cur.fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        waitlist_id, patient_id, priority_level, specialty, subspecialty, request_date, wait_days, rule_applied = row

        cur.execute("""
            INSERT INTO rehab_active
                (patient_id, specialty, subspecialty, start_date, source_waitlist_id, assigned_by, assigned_at, status)
            VALUES
                (?,?,?,?,?,? ,?,'ACTIVE')
        """, (patient_id, specialty, subspecialty, now, waitlist_id, assigned_by, now))
        rehab_active_id = cur.lastrowid

        cur.execute("""
            UPDATE waitlist
            SET status='ASSIGNED'
            WHERE id=? AND status='WAITING'
        """, (waitlist_id,))

        cur.execute("""
            INSERT INTO assignment_log
                (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty, chosen_priority_level, wait_days, rule_applied, reason, actor, created_at)
            VALUES
                ('AUTO_ASSIGNMENT', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """, (waitlist_id, rehab_active_id, patient_id, specialty, subspecialty, priority_level, int(wait_days), rule_applied, assigned_by, now))

        conn.execute("COMMIT")
        return {
            "rehab_active_id": rehab_active_id,
            "waitlist_id": waitlist_id,
            "patient_id": patient_id,
            "priority_level": priority_level,
            "specialty": specialty,
            "subspecialty": subspecialty,
            "wait_days": int(wait_days),
            "rule_applied": rule_applied,
            "request_date": request_date,
        }

    except Exception:
        conn.execute("ROLLBACK")
        raise


def discharge_patient(conn, rehab_active_id: int, reason: str, actor: str = "CLINICO"):
    now = now_iso()
    cur = conn.cursor()

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur.execute("""
            SELECT id, patient_id, specialty, subspecialty
            FROM rehab_active
            WHERE id=? AND status='ACTIVE'
        """, (rehab_active_id,))
        row = cur.fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return False

        _, patient_id, specialty, subspecialty = row

        cur.execute("""
            UPDATE rehab_active
            SET status='DISCHARGED',
                discharge_reason=?,
                discharged_at=?
            WHERE id=? AND status='ACTIVE'
        """, (reason, now, rehab_active_id))

        cur.execute("""
            INSERT INTO assignment_log
                (event, waitlist_id, rehab_active_id, patient_id, specialty, subspecialty, chosen_priority_level, wait_days, rule_applied, reason, actor, created_at)
            VALUES
                ('DISCHARGE', NULL, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
        """, (rehab_active_id, patient_id, specialty, subspecialty, reason, actor, now))

        conn.execute("COMMIT")
        return True

    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_stats(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM waitlist WHERE status='WAITING' AND eligible=1")
    waiting = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='ACTIVE'")
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM rehab_active WHERE status='DISCHARGED'")
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

conn = get_conn()
init_db(conn)

# Sidebar
st.sidebar.markdown("## 🏥 Tratamiento Fisioterapia")
page = st.sidebar.radio(
    "Navegación",
    ["Dashboard", "Nueva solicitud", "Tratamiento activo", "Auditoría"],
    index=0,
    key="nav_page"
)
st.sidebar.markdown("---")

# --- BUG FIX: keys únicas en sidebar ---
specialty_filter = st.sidebar.selectbox(
    "Filtro por especialidad",
    ["Todas"] + SPECIALTIES,
    key="sidebar_specialty_filter"
)
subspecialty_filter = "Todas"
if specialty_filter == "Cinesiterapia":
    subspecialty_filter = st.sidebar.selectbox(
        "Área (Cinesiterapia)",
        ["Todas"] + CINESITERAPIA_SUBSPECIALTIES,
        key="sidebar_cinesi_area_filter"
    )

actor_sidebar = st.sidebar.text_input("Usuario", value="CLINICO", key="sidebar_actor")

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
    st.markdown(f"<div class='kpi-card'><div class='muted'>En espera (eligibles)</div><div style='font-size:28px;font-weight:700'>{waiting}</div></div>", unsafe_allow_html=True)
with c2:
    st.markdown(f"<div class='kpi-card'><div class='muted'>En tratamiento</div><div style='font-size:28px;font-weight:700'>{active}</div></div>", unsafe_allow_html=True)
with c3:
    st.markdown(f"<div class='kpi-card'><div class='muted'>Finalizados</div><div style='font-size:28px;font-weight:700'>{discharged}</div></div>", unsafe_allow_html=True)
with c4:
    nxt = preview_next_patient(conn, specialty_filter=specialty_filter, subspecialty_filter=subspecialty_filter)
    if nxt:
        st.markdown("<div class='kpi-card'>", unsafe_allow_html=True)
        st.markdown("**Siguiente candidato (con filtros)**")
        st.write(f"Paciente: **{nxt['patient_id']}** · {priority_badge(nxt['priority_level'])}")
        st.write(f"Unidad: **{specialty_label(nxt['specialty'], nxt['subspecialty'])}**")
        st.write(f"Espera: **{nxt['wait_days']} días** · Solicitud: **{nxt['request_date'][:10]}**")
        st.caption(f"Regla aplicada: {nxt['rule_applied']}")
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='kpi-card'><div class='muted'>Siguiente candidato</div><div style='font-size:14px'>No hay pacientes elegibles.</div></div>", unsafe_allow_html=True)

st.write("")

# -------------------- Pages --------------------
if page == "Dashboard":
    left, right = st.columns([2.2, 1], gap="large")

    with left:
        st.subheader("📋 Lista de espera (WAITING)")
        now = now_iso()

        f1, f2, f3 = st.columns([1, 1, 1], gap="medium")
        with f1:
            pr_filter = st.selectbox("Prioridad", ["Todas"] + PRIORITIES, key="dash_priority")
        with f2:
            elig_filter = st.selectbox("Elegibilidad", ["Solo elegibles", "Todos"], key="dash_elig")
        with f3:
            sp_filter = st.selectbox(
                "Especialidad (tabla)",
                ["Todas"] + SPECIALTIES,
                index=(0 if specialty_filter == "Todas" else (SPECIALTIES.index(specialty_filter) + 1)),
                key="dash_specialty"
            )

        # subspecialty en tabla solo si Cinesiterapia
        ss_filter_table = "Todas"
        if sp_filter == "Cinesiterapia":
            ss_filter_table = st.selectbox(
                "Área (tabla - Cinesiterapia)",
                ["Todas"] + CINESITERAPIA_SUBSPECIALTIES,
                key="dash_cinesi_area"
            )

        where = ["status='WAITING'"]
        params = [now]

        if elig_filter == "Solo elegibles":
            where.append("eligible=1")

        if pr_filter != "Todas":
            where.append("priority_level=?")
            params.append(pr_filter)

        if sp_filter != "Todas":
            where.append("specialty=?")
            params.append(sp_filter)

            if sp_filter == "Cinesiterapia" and ss_filter_table != "Todas":
                where.append("subspecialty=?")
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
                CAST((julianday(?) - julianday(request_date)) AS INTEGER) AS wait_days,
                eligible
            FROM waitlist
            WHERE {where_sql}
            ORDER BY request_date ASC, id ASC
        """, tuple(params))

        data = []
        for r in rows:
            d = dict(zip(cols, r))
            d["priority_level"] = priority_badge(d["priority_level"])
            d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
            d.pop("subspecialty", None)
            data.append(d)

        st.dataframe(data, width="stretch", hide_index=True)

    with right:
        st.subheader("⚙️ Acciones rápidas")

        st.caption("Asignación automática (respeta reglas + desempates) usando los filtros laterales.")
        if st.button("Asignar siguiente", width="stretch", key="btn_assign_next"):
            res = assign_next_patient(
                conn,
                assigned_by=actor_sidebar.strip() or "SYSTEM",
                specialty_filter=specialty_filter,
                subspecialty_filter=subspecialty_filter
            )
            if not res:
                st.warning("No hay pacientes elegibles con esos filtros.")
            else:
                st.success(f"Asignado: {res['patient_id']} ({res['priority_level']})")
                st.info(f"{specialty_label(res['specialty'], res['subspecialty'])} · {res['wait_days']} días · Regla: {res['rule_applied']}")
                st.rerun()

        st.divider()
        st.caption("Dar de alta/baja a un paciente activo para liberar plaza.")
        colsA, rowsA = fetch_all(conn, """
            SELECT id, patient_id, specialty, subspecialty, start_date
            FROM rehab_active
            WHERE status='ACTIVE'
            ORDER BY id DESC
        """)
        if not rowsA:
            st.info("No hay pacientes activos.")
        else:
            options = {
                f"{r[0]} · {r[1]} · {specialty_label(r[2], r[3])} · inicio {str(r[4])[:10]}": r[0]
                for r in rowsA
            }
            pick = st.selectbox("Paciente activo", list(options.keys()), key="dash_active_pick")
            reason = st.selectbox("Motivo", ["FIN_TRATAMIENTO", "NO_ASISTE", "DERIVADO", "OTRO"], key="dash_discharge_reason")
            if st.button("Dar alta/baja", width="stretch", key="btn_discharge"):
                ok = discharge_patient(conn, options[pick], reason=reason, actor=actor_sidebar.strip() or "CLINICO")
                if ok:
                    st.success("Alta/Baja registrada.")
                    st.rerun()
                else:
                    st.error("No se pudo registrar (quizá ya no estaba ACTIVE).")

elif page == "Nueva solicitud":
    st.subheader("➕ Nueva solicitud de rehabilitación")

    # Mantiene el mismo formato visual (misma fila de columnas),
    # pero SIN st.form para que el selector de Área se actualice al instante.

    c1, c2, c3, c4, c5 = st.columns([1.1, 1, 1.3, 1.3, 1], gap="large")

    with c1:
        patient_id = st.text_input(
            "ID paciente",
            placeholder="Ej: P001",
            key="req_patient_id"
        )

    with c2:
        priority_level = st.selectbox(
            "Prioridad",
            PRIORITIES,
            key="req_priority"
        )

    with c3:
        specialty = st.selectbox(
            "Especialidad",
            SPECIALTIES,
            key="req_specialty"
        )

    with c4:
        subspecialty = None
        if specialty == "Cinesiterapia":
            subspecialty = st.selectbox(
                "Área",
                CINESITERAPIA_SUBSPECIALTIES,
                key="req_cinesi_area"
            )
        else:
            st.caption("Área: (no aplica)")

    with c5:
        request_dt = st.date_input(
            "Fecha de solicitud",
            value=date.today(),
            key="req_request_date"
        )

    eligible = st.checkbox("Elegible", value=True, key="req_eligible")

    # Botón igual que antes (abajo, ancho completo)
    if st.button("Guardar solicitud", width="stretch", key="req_submit"):
        if not patient_id.strip():
            st.error("Introduce un ID de paciente.")
        else:
            add_waiting_patient(
                conn,
                patient_id=patient_id.strip(),
                priority_level=priority_level,
                specialty=specialty,
                subspecialty=subspecialty,
                request_date_iso=iso_utc_from_date(request_dt),
                eligible=eligible,
                actor=actor_sidebar.strip() or "CLINICO"
            )
            st.success("Solicitud guardada.")
            st.rerun()

elif page == "Tratamiento activo":
    st.subheader("🧑‍⚕️ Pacientes en tratamiento (ACTIVE)")
    cols, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date, assigned_by, assigned_at
        FROM rehab_active
        WHERE status='ACTIVE'
        ORDER BY id DESC
    """)
    data = []
    for r in rows:
        d = dict(zip(cols, r))
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        d.pop("subspecialty", None)
        data.append(d)
    st.dataframe(data, width="stretch", hide_index=True)

    st.subheader("📁 Historial (DISCHARGED)")
    cols, rows = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date, discharged_at, discharge_reason
        FROM rehab_active
        WHERE status='DISCHARGED'
        ORDER BY discharged_at DESC
        LIMIT 300
    """)
    data = []
    for r in rows:
        d = dict(zip(cols, r))
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        d.pop("subspecialty", None)
        data.append(d)
    st.dataframe(data, width="stretch", hide_index=True)

elif page == "Auditoría":
    st.subheader("🧾 Auditoría de eventos")
    cols, rows = fetch_all(conn, """
        SELECT id, event, patient_id, specialty, subspecialty, waitlist_id, rehab_active_id,
               chosen_priority_level, wait_days, rule_applied, reason, actor, created_at
        FROM assignment_log
        ORDER BY id DESC
        LIMIT 400
    """)
    data = []
    for r in rows:
        d = dict(zip(cols, r))
        d["specialty"] = specialty_label(d["specialty"], d.get("subspecialty"))
        d.pop("subspecialty", None)
        data.append(d)
    st.dataframe(data, width="stretch", hide_index=True)

st.caption("Consejo: si cambias el esquema, usa un DB_PATH nuevo para evitar conflictos con tablas antiguas.")