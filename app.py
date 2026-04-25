# app.py
# Ejecuta con: python -m streamlit run app.py

import calendar
from datetime import date, time

import pandas as pd
import matplotlib.pyplot as plt

import streamlit as st

from config import (
    PRIORIDADES,
    OPCIONES_ASISTENCIA,
    TIPOS_HUECO,
    TURNOS,
    TRANSPORTES,
    HORAS_AMBULANCIA,
    REGLAS_COORDINACION,
)
from database import get_conn, init_db, fetch_all, iso_utc_from_date, now_iso
from catalogos import (
    get_especialidades,
    get_nombres_especialidades,
    specialty_requires_subspecialty,
    get_areas_por_especialidad,
    get_nombres_areas_por_especialidad,
    add_specialty_config,
    add_subspecialty_config,
    set_specialty_active,
    set_subspecialty_active,
)
from validators import (
    is_valid_dni,
    is_valid_nhc,
    hay_fisio_y_terapia_ocupacional,
    validar_preferencias_tratamiento,
    validar_hora_sesion,
)
from services import (
    preview_next_patient,
    add_waiting_patient_multiple,
    assign_next_patient,
    discharge_patient,
    add_treatment_session,
    get_treatment_sessions,
    get_session_summary,
    get_stats,
    delete_waitlist_entry,
    delete_active_treatment,
    get_clinical_followup_summary,
    generate_clinical_followup_report,
    get_patient_pain_series,
    get_patient_status_distribution,
    get_dashboard_specialty_summary,
    get_dashboard_session_summary,
    get_dashboard_adherence_by_specialty,
)
from ui_helpers import (
    priority_badge,
    specialty_label,
    estado_sesion_label,
    dia_semana_espanol,
    parse_attendance_days,
    render_mini_calendar,
)
from auth import (
    init_auth_db,
    ensure_admin_user,
    authenticate_user,
    create_user,
    get_all_users,
    change_user_password,
    reset_user_password_to_default,
    PROFESIONES_DISPONIBLES,
    PASSWORD_TEMPORAL_POR_DEFECTO,
)

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
    init_auth_db(conn)
    ensure_admin_user(conn)
except Exception as e:
    st.error(f"Error conectando con la base de datos en la nube: {e}")
    st.stop()

# -------------------- Login --------------------
if "user" not in st.session_state:
    st.session_state["user"] = None

if st.session_state["user"] is None:
    st.markdown("## Acceso al sistema")

    username_login = st.text_input("Usuario", key="login_username")
    password_login = st.text_input("Contraseña", type="password", key="login_password")

    if st.button("Iniciar sesión", key="login_button"):
        user = authenticate_user(conn, username_login.strip(), password_login)
        if user:
            st.session_state["user"] = user
            st.success("Acceso correcto.")
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")

    st.info("Usuario inicial administrador: admin · Contraseña inicial: Admin1234")
    st.stop()

if st.session_state["user"].get("must_change_password"):
    st.markdown("## Cambio obligatorio de contraseña")

    st.warning("Es tu primer acceso o el administrador ha restablecido tu contraseña. Debes actualizarla para continuar.")

    nueva_password_1 = st.text_input("Nueva contraseña", type="password", key="force_new_password_1")
    nueva_password_2 = st.text_input("Repite la nueva contraseña", type="password", key="force_new_password_2")

    if st.button("Actualizar contraseña", key="force_change_password_button"):
        if not nueva_password_1.strip():
            st.error("Introduce una nueva contraseña.")
        elif len(nueva_password_1.strip()) < 8:
            st.error("La nueva contraseña debe tener al menos 8 caracteres.")
        elif nueva_password_1 != nueva_password_2:
            st.error("Las contraseñas no coinciden.")
        elif nueva_password_1 == PASSWORD_TEMPORAL_POR_DEFECTO:
            st.error("La nueva contraseña no puede ser la contraseña temporal por defecto.")
        else:
            change_user_password(conn, st.session_state["user"]["id"], nueva_password_1.strip())
            st.session_state["user"] = authenticate_user(
                conn,
                st.session_state["user"]["username"],
                nueva_password_1.strip()
            )
            st.success("Contraseña actualizada correctamente.")
            st.rerun()

    st.stop()

# -------------------- Variables de usuario y catálogos --------------------
usuario_actual = st.session_state["user"]
actor_sidebar = usuario_actual["dni"]
rol_usuario = usuario_actual["role"]
nombre_usuario = usuario_actual["full_name"]
profesion_usuario = usuario_actual["profession"]

especialidades_activas = get_nombres_especialidades(conn, only_active=True)

# -------------------- Sidebar --------------------
st.sidebar.markdown("## 🏥 Tratamiento Fisioterapia")

paginas = [
    "📊 Panel de control",
    "📝 Nueva solicitud",
    "⚕️ Tratamientos activos",
    "📈 Seguimiento clínico",
    "📉 Dashboard clínico",
    "🧾 Auditoría clínica",
]

if rol_usuario == "ADMIN":
    paginas.append("⚙️ Ajustes")

page = st.sidebar.radio(
    "Secciones",
    paginas,
    index=0,
    key="nav_page"
)

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

st.sidebar.markdown(f"**Usuario:** {nombre_usuario}")
st.sidebar.markdown(f"**DNI:** {actor_sidebar}")
st.sidebar.markdown(f"**Profesión:** {profesion_usuario}")
st.sidebar.markdown(f"**Rol:** {rol_usuario}")

if st.sidebar.button("Cerrar sesión", key="logout_button"):
    st.session_state["user"] = None
    st.rerun()

# -------------------- Cabecera --------------------
waiting, active, discharged = get_stats(conn)

st.markdown('<div class="title">Sistema de Gestión de Rehabilitación</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="muted">Gestión clínica de solicitudes, tratamientos, auditoría y ajustes de configuración</div>',
    unsafe_allow_html=True
)
st.write("")

c1, c2, c3, c4 = st.columns([1, 1, 1, 2], gap="large")
with c1:
    st.markdown(
        f"<div class='kpi-card'><div class='muted'>En espera (elegibles)</div>"
        f"<div style='font-size:28px;font-weight:700'>{waiting}</div></div>",
        unsafe_allow_html=True
    )
with c2:
    st.markdown(
        f"<div class='kpi-card'><div class='muted'>En tratamiento</div>"
        f"<div style='font-size:28px;font-weight:700'>{active}</div></div>",
        unsafe_allow_html=True
    )
with c3:
    st.markdown(
        f"<div class='kpi-card'><div class='muted'>Finalizados</div>"
        f"<div style='font-size:28px;font-weight:700'>{discharged}</div></div>",
        unsafe_allow_html=True
    )
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
        st.markdown(
            "<div class='kpi-card'><div class='muted'>Siguiente candidato</div>"
            "<div style='font-size:14px'>No hay pacientes elegibles.</div></div>",
            unsafe_allow_html=True
        )

st.write("")

# -------------------- Páginas --------------------
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
        attendance_pattern = st.selectbox(
            "Frecuencia del tratamiento",
            ["LXV", "MJ", "LABORABLES"],
            format_func=lambda x: {
                "LXV": "Lunes, Miércoles y Viernes",
                "MJ": "Martes y Jueves",
                "LABORABLES": "Todos los días laborables",
            }[x],
            key="assign_attendance_pattern"
        )

        attendance_days = OPCIONES_ASISTENCIA[attendance_pattern]

        if st.button("Asignar siguiente", width="stretch", key="btn_assign_next"):
            try:
                res = assign_next_patient(
                    conn,
                    assigned_by=actor_sidebar,
                    assigned_clinician_dni=actor_sidebar,
                    assigned_clinician_name=nombre_usuario,
                    assigned_clinician_profession=profesion_usuario,
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
                st.success(
                    f"Asignado NHC: {res['patient_id']} ({res['priority_level']}) "
                    f"al clínico {res['assigned_clinician_name']}"
                )
                st.info(
                    f"{specialty_label(res['specialty'], res['subspecialty'])} · "
                    f"{res['wait_days']} días de espera · "
                    f"Regla: {res['rule_applied']} · "
                    f"Frecuencia: {attendance_pattern} · "
                    f"Clínico responsable: {res['assigned_clinician_name']} "
                    f"({res['assigned_clinician_profession']})"
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
            reason = st.selectbox(
                "Motivo del alta",
                ["FIN_TRATAMIENTO", "NO_ASISTE", "DERIVADO", "OTRO"],
                key="dash_discharge_reason"
            )
            comment = st.text_area(
                "Comentario clínico",
                placeholder="Escribe observaciones sobre el alta...",
                key="dash_discharge_comment"
            )

            if st.button("Dar alta", width="stretch", key="btn_discharge"):
                if reason == "OTRO" and not comment.strip():
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
        slot_type = st.selectbox("Tipo de hueco", TIPOS_HUECO, key="req_slot_type")

    with cfg2:
        time_preference = st.selectbox("Turno", TURNOS, key="req_time_preference")

    with cfg3:
        transport_mode = st.selectbox("Transporte", TRANSPORTES, key="req_transport_mode")

    preferred_hour = None
    with cfg4:
        if transport_mode == "AMBULANCIA":
            preferred_hour = st.selectbox("Hora ambulancia", HORAS_AMBULANCIA, key="req_preferred_hour")
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

    st.caption(
        "Se creará una solicitud independiente por cada especialidad seleccionada. "
        "Si la especialidad requiere área, se creará una por cada área seleccionada."
    )

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
                            "prescribed_sessions": int(sesiones_por_area[(sp, sub)])
                            if sesiones_por_area.get((sp, sub)) else None
                        })
                else:
                    requests.append({
                        "specialty": sp,
                        "subspecialty": None,
                        "prescribed_sessions": int(sesiones_por_especialidad[sp])
                        if sesiones_por_especialidad.get(sp) else None
                    })

            add_waiting_patient_multiple(
                conn,
                patient_id=patient_id,
                priority_level=priority_level,
                requests=requests,
                request_date_iso=iso_utc_from_date(request_dt),
                eligible=eligible,
                actor=actor_sidebar,
                slot_type=slot_type,
                time_preference=time_preference,
                transport_mode=transport_mode,
                preferred_hour=preferred_hour,
                coordination_rule=coordination_rule
            )

            st.success(f"Solicitud guardada para NHC {patient_id}. Se han creado {len(requests)} entradas.")
            st.rerun()

elif page == "⚕️ Tratamientos activos":
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

        st.markdown("### Registro clínico de la sesión")

        clinical_note = st.text_area(
            "Nota clínica",
            placeholder="Describe evolución, hallazgos, respuesta al tratamiento, tolerancia, etc.",
            key="session_clinical_note"
        )

        col_a, col_b = st.columns(2)

        with col_a:
            pain_eva = st.number_input(
                "Dolor EVA (0-10)",
                min_value=0,
                max_value=10,
                value=0,
                step=1,
                key="session_pain_eva"
            )

        with col_b:
            goal_status = st.selectbox(
                "Estado del objetivo terapéutico",
                ["", "NO_INICIADO", "PARCIAL", "CUMPLIDO", "EMPEORA"],
                key="session_goal_status"
            )

        functional_status = st.text_input(
            "Valoración funcional breve",
            placeholder="Ej: mejora de la marcha, mayor rango articular, sin cambios en equilibrio...",
            key="session_functional_status"
        )

        incidents = st.text_area(
            "Incidencias",
            placeholder="Ej: mala tolerancia, reagudización, fatiga, mareo, sin incidencias...",
            key="session_incidents"
        )

        if st.button("Guardar sesión o falta", width="stretch", key="btn_save_session"):
            if session_status in ["FALTA_JUSTIFICADA", "FALTA_NO_JUSTIFICADA"] and not absence_reason.strip():
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
                    recorded_by=actor_sidebar,
                    clinical_note=clinical_note,
                    pain_eva=pain_eva if session_status in ["REALIZADA", "REVISION"] else None,
                    functional_status=functional_status,
                    goal_status=goal_status,
                    incidents=incidents,
                )

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

elif page == "📈 Seguimiento clínico":
    st.subheader("Seguimiento clínico del paciente")

    _, rowsA = fetch_all(conn, """
        SELECT id, patient_id, specialty, subspecialty, start_date
        FROM rehab_active
        WHERE status='ACTIVO'
        ORDER BY id DESC
    """)

    if not rowsA:
        st.info("No hay tratamientos activos para seguimiento.")
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
            "Selecciona tratamiento",
            list(options.keys()),
            key="followup_patient_pick"
        )

        selected_patient = options[selected_label]

        summary = get_clinical_followup_summary(conn, selected_patient["rehab_active_id"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Adherencia", f"{summary['adherence_percent']}%")
        c2.metric("Dolor inicial", summary["first_pain"] if summary["first_pain"] is not None else "Sin datos")
        c3.metric("Dolor actual", summary["last_pain"] if summary["last_pain"] is not None else "Sin datos")
        c4.metric("Tendencia EVA", summary["pain_trend"])

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Sesiones realizadas", summary["realizadas"])
        c6.metric("Revisiones", summary["revisiones"])
        c7.metric("Faltas justificadas", summary["faltas_justificadas"])
        c8.metric("Faltas no justificadas", summary["faltas_no_justificadas"])

        st.markdown("### Alertas clínicas")

        if summary["abandonment_risk"]:
            st.warning("Posible riesgo de abandono terapéutico.")
        else:
            st.success("Sin indicios claros de abandono terapéutico.")

        if summary["stagnation_risk"]:
            st.warning("Posible estancamiento funcional. Revisar plan terapéutico.")
        else:
            st.success("Sin indicios claros de estancamiento funcional.")

        st.markdown("### Última situación clínica")

        st.write(f"**Valoración funcional:** {summary['last_functional_status'] or 'Sin registro'}")
        st.write(f"**Estado del objetivo:** {summary['last_goal_status'] or 'Sin registro'}")

        st.markdown("### Informe automático")

        report = generate_clinical_followup_report(conn, selected_patient["rehab_active_id"])
        st.text_area(
            "Informe generado",
            value=report,
            height=320,
            key="clinical_followup_report"
        )

        st.markdown("### Historial clínico de sesiones")

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
            d["nota_clinica"] = d.pop("clinical_note")
            d["eva"] = d.pop("pain_eva")
            d["funcional"] = d.pop("functional_status")
            d["objetivo"] = d.pop("goal_status")
            d["incidencias"] = d.pop("incidents")
            d.pop("subspecialty", None)
            sess_data.append(d)

        if sess_data:
            st.dataframe(sess_data, width="stretch", hide_index=True)
        else:
            st.info("No hay sesiones registradas para este tratamiento.")

elif page == "📉 Dashboard clínico":
    st.subheader("Dashboard clínico")

    tab1, tab2 = st.tabs(["Paciente individual", "Resumen global"])

    with tab1:
        st.markdown("### Evolución del paciente")

        _, rowsA = fetch_all(conn, """
            SELECT id, patient_id, specialty, subspecialty, start_date
            FROM rehab_active
            WHERE status = 'ACTIVO'
            ORDER BY id DESC
        """)

        if not rowsA:
            st.info("No hay tratamientos activos para visualizar.")
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
                "Selecciona tratamiento",
                list(options.keys()),
                key="dashboard_patient_pick"
            )
            selected_patient = options[selected_label]

            resumen = get_clinical_followup_summary(conn, selected_patient["rehab_active_id"])

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Adherencia", f"{resumen['adherence_percent']}%")
            m2.metric("Realizadas", resumen["realizadas"])
            m3.metric("Revisiones", resumen["revisiones"])
            m4.metric("Faltas no justificadas", resumen["faltas_no_justificadas"])

            # -------- Evolución EVA --------
            st.markdown("#### Evolución del dolor (EVA)")
            pain_cols, pain_rows = get_patient_pain_series(conn, selected_patient["rehab_active_id"])

            if pain_rows:
                pain_df = pd.DataFrame(pain_rows, columns=pain_cols)
                pain_df["session_date"] = pd.to_datetime(pain_df["session_date"])

                fig, ax = plt.subplots(figsize=(8, 4))
                ax.plot(pain_df["session_date"], pain_df["pain_eva"], marker="o")
                ax.set_xlabel("Fecha")
                ax.set_ylabel("EVA")
                ax.set_title("Evolución EVA")
                ax.grid(True, alpha=0.3)
                st.pyplot(fig)
            else:
                st.info("No hay datos EVA suficientes para este tratamiento.")

            # -------- Distribución de estados --------
            st.markdown("#### Distribución de sesiones")
            dist_cols, dist_rows = get_patient_status_distribution(conn, selected_patient["rehab_active_id"])

            if dist_rows:
                dist_df = pd.DataFrame(dist_rows, columns=dist_cols)
                dist_df["status"] = dist_df["status"].map({
                    "REALIZADA": "Realizada",
                    "REVISION": "Revisión",
                    "FALTA_JUSTIFICADA": "Falta justificada",
                    "FALTA_NO_JUSTIFICADA": "Falta no justificada",
                })

                fig2, ax2 = plt.subplots(figsize=(7, 4))
                ax2.bar(dist_df["status"], dist_df["total"])
                ax2.set_xlabel("Estado")
                ax2.set_ylabel("Número de sesiones")
                ax2.set_title("Distribución de estados de sesión")
                plt.xticks(rotation=20)
                st.pyplot(fig2)
            else:
                st.info("No hay sesiones registradas para mostrar la distribución.")

            # -------- Resumen clínico --------
            st.markdown("#### Resumen clínico automático")
            report = generate_clinical_followup_report(conn, selected_patient["rehab_active_id"])
            st.text_area(
                "Informe de seguimiento",
                value=report,
                height=260,
                key="dashboard_followup_report"
            )

    with tab2:
        st.markdown("### Resumen global del servicio")

        # -------- Especialidades --------
        sp_cols, sp_rows = get_dashboard_specialty_summary(conn)
        if sp_rows:
            sp_df = pd.DataFrame(sp_rows, columns=sp_cols)

            st.markdown("#### Pacientes por especialidad")
            fig3, ax3 = plt.subplots(figsize=(8, 4))
            ax3.bar(sp_df["specialty"], sp_df["activos"])
            ax3.set_xlabel("Especialidad")
            ax3.set_ylabel("Pacientes activos")
            ax3.set_title("Pacientes activos por especialidad")
            plt.xticks(rotation=20)
            st.pyplot(fig3)

            st.dataframe(sp_df, width="stretch", hide_index=True)
        else:
            st.info("No hay datos de especialidades para mostrar.")

        # -------- Estados de sesión globales --------
        ss_cols, ss_rows = get_dashboard_session_summary(conn)
        if ss_rows:
            ss_df = pd.DataFrame(ss_rows, columns=ss_cols)
            ss_df["status"] = ss_df["status"].map({
                "REALIZADA": "Realizada",
                "REVISION": "Revisión",
                "FALTA_JUSTIFICADA": "Falta justificada",
                "FALTA_NO_JUSTIFICADA": "Falta no justificada",
            })

            st.markdown("#### Estados globales de las sesiones")
            fig4, ax4 = plt.subplots(figsize=(8, 4))
            ax4.bar(ss_df["status"], ss_df["total"])
            ax4.set_xlabel("Estado")
            ax4.set_ylabel("Total")
            ax4.set_title("Distribución global de estados de sesión")
            plt.xticks(rotation=20)
            st.pyplot(fig4)
        else:
            st.info("No hay sesiones registradas globalmente.")

        # -------- Adherencia por especialidad --------
        ad_cols, ad_rows = get_dashboard_adherence_by_specialty(conn)
        if ad_rows:
            ad_df = pd.DataFrame(ad_rows, columns=ad_cols)

            ad_df["adherencia_pct"] = ad_df.apply(
                lambda row: round((row["asistidas"] / row["total"]) * 100, 1) if row["total"] > 0 else 0,
                axis=1
            )

            st.markdown("#### Adherencia por especialidad")
            fig5, ax5 = plt.subplots(figsize=(8, 4))
            ax5.bar(ad_df["specialty"], ad_df["adherencia_pct"])
            ax5.set_xlabel("Especialidad")
            ax5.set_ylabel("Adherencia (%)")
            ax5.set_title("Adherencia por especialidad")
            plt.xticks(rotation=20)
            st.pyplot(fig5)

            st.dataframe(ad_df, width="stretch", hide_index=True)
        else:
            st.info("No hay datos de adherencia por especialidad.")

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

    if rol_usuario != "ADMIN":
        st.error("No tienes permisos para acceder a Ajustes.")
        st.stop()

    st.subheader("⚙️ Ajustes del sistema")

    st.markdown("### Restablecer contraseña de usuario")

    usuarios_reset = get_all_users(conn)
    usuarios_reset_options = {
        f"{u[0]} · {u[1]} · {u[2]} · {u[3]} · {u[4]} · {u[5]}": u[0]
        for u in usuarios_reset
    }

    if usuarios_reset_options:
        selected_user_reset = st.selectbox(
            "Selecciona el usuario al que quieres restablecer la contraseña",
            list(usuarios_reset_options.keys()),
            key="reset_user_select"
        )

        confirm_reset_password = st.checkbox(
            f"Confirmo que quiero restablecer la contraseña a {PASSWORD_TEMPORAL_POR_DEFECTO}",
            key="confirm_reset_password"
        )

        if st.button("Restablecer contraseña", key="reset_password_button"):
            if rol_usuario != "ADMIN":
                st.error("Solo el administrador puede restablecer contraseñas.")
            elif not confirm_reset_password:
                st.error("Debes confirmar el restablecimiento.")
            else:
                reset_user_password_to_default(
                    conn,
                    usuarios_reset_options[selected_user_reset]
                )
                st.success(
                    f"Contraseña restablecida a {PASSWORD_TEMPORAL_POR_DEFECTO}. "
                    "En el próximo acceso se obligará al usuario a cambiarla."
                )
                st.rerun()
    else:
        st.info("No hay usuarios disponibles para restablecer contraseña.")

    st.markdown("### Corrección de errores de registro")

    with st.expander("🚫 Eliminar solicitud de lista de espera"):
        _, waitlist_rows = fetch_all(conn, """
            SELECT id, patient_id, specialty, subspecialty, priority_level, request_date
            FROM waitlist
            ORDER BY id DESC
            LIMIT 300
        """)

        if not waitlist_rows:
            st.info("No hay solicitudes en lista de espera.")
        else:
            waitlist_options = {
                f"{r[0]} · NHC {r[1]} · {specialty_label(r[2], r[3])} · {r[4]} · {str(r[5])[:10]}": r[0]
                for r in waitlist_rows
            }

            selected_waitlist = st.selectbox(
                "Selecciona la solicitud a eliminar",
                list(waitlist_options.keys()),
                key="admin_delete_waitlist_pick"
            )

            confirm_delete_waitlist = st.checkbox(
                "Confirmo que quiero eliminar esta solicitud",
                key="admin_confirm_delete_waitlist"
            )

            if st.button("Eliminar solicitud", key="admin_delete_waitlist_button"):
                if rol_usuario != "ADMIN":
                    st.error("Solo el administrador puede eliminar solicitudes.")
                elif not confirm_delete_waitlist:
                    st.error("Debes confirmar la eliminación.")
                else:
                    ok, msg = delete_waitlist_entry(
                        conn,
                        waitlist_options[selected_waitlist],
                        actor_sidebar
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    with st.expander("⛔ Eliminar tratamiento activo"):
        _, active_rows = fetch_all(conn, """
            SELECT id, patient_id, specialty, subspecialty, start_date
            FROM rehab_active
            WHERE status = 'ACTIVO'
            ORDER BY id DESC
            LIMIT 300
        """)

        if not active_rows:
            st.info("No hay tratamientos activos.")
        else:
            active_options = {
                f"{r[0]} · NHC {r[1]} · {specialty_label(r[2], r[3])} · inicio {str(r[4])[:10]}": r[0]
                for r in active_rows
            }

            selected_active = st.selectbox(
                "Selecciona el tratamiento activo a eliminar",
                list(active_options.keys()),
                key="admin_delete_active_pick"
            )

            confirm_delete_active = st.checkbox(
                "Confirmo que quiero eliminar este tratamiento y sus sesiones",
                key="admin_confirm_delete_active"
            )

            if st.button("Eliminar tratamiento", key="admin_delete_active_button"):
                if rol_usuario != "ADMIN":
                    st.error("Solo el administrador puede eliminar tratamientos.")
                elif not confirm_delete_active:
                    st.error("Debes confirmar la eliminación.")
                else:
                    ok, msg = delete_active_treatment(
                        conn,
                        active_options[selected_active],
                        actor_sidebar
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    st.markdown("### Usuarios del sistema")

    with st.expander("💉 Registrar nuevo clínico"):
        u1, u2 = st.columns(2)
        with u1:
            new_username = st.text_input("Nombre de usuario", key="new_user_username")
            new_full_name = st.text_input("Nombre completo", key="new_user_full_name")
            new_dni = st.text_input("DNI", key="new_user_dni").strip().upper()
        with u2:
            new_profession = st.selectbox(
                "Profesión",
                PROFESIONES_DISPONIBLES,
                key="new_user_profession"
            )
            new_role = st.selectbox("Rol", ["ADMIN", "CLINICO"], key="new_user_role")
            st.info(f"La contraseña inicial del usuario será: {PASSWORD_TEMPORAL_POR_DEFECTO}")

        if st.button("Registrar usuario", key="create_user_button"):

            if rol_usuario != "ADMIN":
                st.error("Solo el administrador puede registrar usuarios.")

            elif not new_username.strip():
                st.error("Introduce un nombre de usuario.")

            elif not new_full_name.strip():
                st.error("Introduce el nombre completo.")

            elif not is_valid_dni(new_dni):
                st.error("El DNI no es válido.")

            else:
                _, dni_existente = fetch_all(
                    conn,
                    "SELECT id FROM users WHERE dni = %s LIMIT 1",
                    (new_dni,)
                )

                if dni_existente:
                    st.error("Ya existe un usuario registrado con ese DNI.")

                else:
                    create_user(
                        conn=conn,
                        username=new_username.strip(),
                        full_name=new_full_name.strip(),
                        dni=new_dni,
                        profession=new_profession,
                        role=new_role
                    )
                    st.success("Usuario registrado correctamente.")
                    st.rerun()

    st.markdown("### Listado de usuarios")
    usuarios = get_all_users(conn)
    data_users = []
    for u in usuarios:
        data_users.append({
            "id": u[0],
            "usuario": u[1],
            "nombre": u[2],
            "dni": u[3],
            "profesión": u[4],
            "rol": u[5],
            "cambio_clave_pendiente": "Sí" if u[6] else "No",
            "activo": "Sí" if u[7] else "No",
            "creado": str(u[8])[:19],
        })
    st.dataframe(data_users, width="stretch", hide_index=True)

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