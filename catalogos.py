from database import fetch_all, execute_sql


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