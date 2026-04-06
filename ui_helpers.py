import calendar
from collections import defaultdict
import streamlit as st
from database import fetch_all


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


def dia_semana_espanol(fecha):
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