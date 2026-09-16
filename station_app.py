import sqlite3

import app as app_module
from flask import redirect, render_template, request, url_for

app = app_module.app

_original_init_db = app_module.init_db


def init_db_with_stations():
    """Run the normal database setup and add the user/station organisation layer."""
    _original_init_db()
    with app_module.get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        user_columns = app_module._column_names(db, "users")
        if "station_id" not in user_columns:
            try:
                db.execute("ALTER TABLE users ADD COLUMN station_id INTEGER")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_station ON users(station_id, last_name, first_name)"
        )


# app.ensure_database resolves init_db through the app module globals at request time,
# so replacing it here keeps the existing migration behaviour and adds station setup.
app_module.init_db = init_db_with_stations


def _normalise_station_name(value):
    return " ".join(str(value or "").strip().split())


def _redirect_admin(*, notice=None, error=None):
    values = {}
    if notice:
        values["notice"] = notice
    if error:
        values["error"] = error
    return redirect(url_for("admin", **values))


@app_module.admin_required
def admin_with_stations():
    with app_module.get_db() as db:
        station_rows = db.execute(
            """SELECT s.id, s.name, s.created_at, COUNT(u.id) AS user_count
               FROM stations s
               LEFT JOIN users u ON u.station_id = s.id
               GROUP BY s.id
               ORDER BY s.name COLLATE NOCASE"""
        ).fetchall()
        user_rows = db.execute(
            """SELECT u.id, u.username, u.email, u.first_name, u.last_name, u.is_admin,
                      u.station_id, u.created_at, u.last_login_at,
                      s.name AS station_name,
                      COUNT(DISTINCT sm.shift_id) AS shift_count
               FROM users u
               LEFT JOIN stations s ON s.id = u.station_id
               LEFT JOIN shift_members sm ON sm.user_id = u.id
               GROUP BY u.id
               ORDER BY COALESCE(s.name, '') COLLATE NOCASE,
                        u.last_name COLLATE NOCASE,
                        u.first_name COLLATE NOCASE,
                        u.username COLLATE NOCASE"""
        ).fetchall()

    stations = [dict(row) for row in station_rows]
    users = [dict(row) for row in user_rows]
    users_by_station = {station["id"]: [] for station in stations}
    unassigned_users = []

    for user in users:
        station_id = user.get("station_id")
        if station_id in users_by_station:
            users_by_station[station_id].append(user)
        else:
            unassigned_users.append(user)

    station_groups = [
        {"station": station, "users": users_by_station[station["id"]]}
        for station in stations
    ]

    return render_template(
        "admin.html",
        users=users,
        stations=stations,
        station_groups=station_groups,
        unassigned_users=unassigned_users,
        notice=request.args.get("notice", ""),
        error=request.args.get("error", ""),
    )


# Keep the existing /admin endpoint and url_for('admin'), but replace the view.
app.view_functions["admin"] = admin_with_stations


@app.post("/admin/stations")
@app_module.admin_required
def admin_create_station():
    name = _normalise_station_name(request.form.get("name", ""))
    if len(name) < 2:
        return _redirect_admin(error="Stationsnavnet skal være mindst 2 tegn.")
    if len(name) > 80:
        return _redirect_admin(error="Stationsnavnet må højst være 80 tegn.")

    try:
        with app_module.get_db() as db:
            db.execute("INSERT INTO stations (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        return _redirect_admin(error="Der findes allerede en station med det navn.")

    return _redirect_admin(notice=f"Stationen {name} er oprettet.")


@app.post("/admin/users/<int:user_id>/station")
@app_module.admin_required
def admin_set_user_station(user_id):
    raw_station_id = request.form.get("station_id", "").strip()
    station_id = None

    if raw_station_id:
        try:
            station_id = int(raw_station_id)
        except ValueError:
            return _redirect_admin(error="Den valgte station er ugyldig.")

    with app_module.get_db() as db:
        user = db.execute(
            "SELECT id, first_name, last_name, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return _redirect_admin(error="Brugeren blev ikke fundet.")

        station_name = None
        if station_id is not None:
            station = db.execute(
                "SELECT id, name FROM stations WHERE id = ?", (station_id,)
            ).fetchone()
            if not station:
                return _redirect_admin(error="Den valgte station findes ikke længere.")
            station_name = station["name"]

        db.execute(
            "UPDATE users SET station_id = ? WHERE id = ?",
            (station_id, user_id),
        )

    display_name = " ".join(
        part for part in [user["first_name"], user["last_name"]] if part
    ).strip() or user["username"]
    destination = station_name or "Uden station"
    return _redirect_admin(notice=f"{display_name} er flyttet til {destination}.")
