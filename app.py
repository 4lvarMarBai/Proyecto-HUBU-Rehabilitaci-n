# app.py
# Ejecuta con: python -m streamlit run app.py

import calendar
from datetime import date, time

import streamlit as st

from config import (
    PRIORIDADES,
    DIAS_SEMANA,
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
    acceso_configuracion_permitido,
    cerrar_configuracion_si_sale,
    preview_next_patient,
    add_waiting_patient_multiple,
    assign_next_patient,
    discharge_patient,
    add_treatment_session,
    get_treatment_sessions,
    get_session_summary,
    get_stats,
)
from ui_helpers import (
    priority_badge,
    specialty_label,
    estado_sesion_label,
    dia_semana_espanol,
    parse_attendance_days,
    render_mini_calendar,
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
except Exception as e:
    st.error(f"Error conectando con la base de datos en la nube: {e}")
    st.stop()

especialidades_activas = get_nombres_especialidades(conn, only_active=True)

# -------------------- Sidebar --------------------
st.sidebar.markdown("## 🏥 Tratamiento Fisioterapia")
page = st.sidebar.radio(
    "Secciones",
    [
        "📊 Panel de control",
        "📝 Nueva solicitud",
        "⚕️ Tratamientos activos",
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