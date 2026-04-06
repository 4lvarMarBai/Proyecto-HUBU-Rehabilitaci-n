import re

# -------------------- Validaciones básicas --------------------

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


# -------------------- Lógica clínica --------------------

def es_fisio(specialty: str) -> bool:
    return specialty in ["Electroterapia", "Cinesiterapia"]


def hay_fisio_y_terapia_ocupacional(selected_specialties: list[str]) -> bool:
    tiene_to = "Terapia ocupacional" in selected_specialties
    tiene_fisio = any(es_fisio(sp) for sp in selected_specialties)
    return tiene_to and tiene_fisio


def parse_attendance_days(attendance_days_text: str) -> list[str]:
    if not attendance_days_text:
        return []
    return [d.strip() for d in attendance_days_text.split(",") if d.strip()]


# -------------------- Reglas de tratamiento --------------------

def validar_preferencias_tratamiento(
    slot_type: str,
    time_preference: str,
    transport_mode: str,
    preferred_hour: str | None
):
    if slot_type not in ["SIMPLE", "DOBLE"]:
        return "Debes seleccionar si el hueco es simple o doble."

    if time_preference not in ["MAÑANA", "TARDE"]:
        return "Debes seleccionar mañana o tarde."

    if transport_mode not in ["NORMAL", "AMBULANCIA"]:
        return "Debes seleccionar el modo de transporte."

    if transport_mode == "AMBULANCIA":
        if time_preference != "MAÑANA":
            return "Si el paciente viene en ambulancia solo puede ser por la mañana."

        if preferred_hour not in ["09:00", "12:00"]:
            return "Si el paciente viene en ambulancia la hora debe ser 09:00 o 12:00."

    return None


def validar_regla_coordinacion(
    conn,
    patient_id: str,
    specialty: str,
    attendance_days: list[str],
    coordination_rule: str
):
    if coordination_rule not in ["MISMO_DIA", "DIAS_ALTERNOS"]:
        return None

    with conn.cursor() as cur:
        cur.execute("""
            SELECT specialty, attendance_days
            FROM rehab_active
            WHERE patient_id = %s AND status = 'ACTIVO'
        """, (patient_id,))
        tratamientos = cur.fetchall()

    for sp, attendance_days_text in tratamientos:
        dias_existentes = parse_attendance_days(attendance_days_text)

        if (
            (specialty == "Terapia ocupacional" and es_fisio(sp))
            or (es_fisio(specialty) and sp == "Terapia ocupacional")
        ):
            if coordination_rule == "MISMO_DIA":
                if set(attendance_days) != set(dias_existentes):
                    return "Fisio y Terapia ocupacional deben tener los mismos días."

            elif coordination_rule == "DIAS_ALTERNOS":
                if set(attendance_days) & set(dias_existentes):
                    return "Fisio y Terapia ocupacional no pueden coincidir en días."

    return None


# -------------------- Validación de sesiones --------------------

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

    # 🚑 Ambulancia
    if transport_mode == "AMBULANCIA":
        if hora_texto not in ["09:00", "12:00"]:
            return "Los pacientes en ambulancia solo pueden venir a las 09:00 o a las 12:00."

        if not hora_es_manana(session_time_value):
            return "Los pacientes en ambulancia solo pueden venir por la mañana."

    # 🌅 Turnos
    if time_preference == "MAÑANA" and not hora_es_manana(session_time_value):
        return "Este tratamiento está configurado para la mañana."

    if time_preference == "TARDE" and not hora_es_tarde(session_time_value):
        return "Este tratamiento está configurado para la tarde."

    # ⏰ Hora fija
    if preferred_hour and transport_mode == "AMBULANCIA":
        if hora_texto != preferred_hour:
            return f"Este paciente en ambulancia tiene como hora asignada {preferred_hour}."

    return None