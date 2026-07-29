import json
import os
import sqlite3
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "minutregnskab.db"))
MAX_SAVED_SHIFTS = 10

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "change-me-in-production"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=3650),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
)


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _column_names(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                last_login_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                station TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_shifts_user_recent
            ON shifts(user_id, shift_date DESC, start_time DESC, id DESC);
            """
        )
        columns = _column_names(db, "users")
        migrations = {
            "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''",
            "last_name": "ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''",
            "is_admin": "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
            "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                db.execute(statement)

        admin_count = db.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
        if admin_count == 0:
            first_user = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first_user:
                db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_user["id"],))


def prune_old_shifts(db, user_id):
    db.execute(
        """
        DELETE FROM shifts
        WHERE user_id = ?
          AND id NOT IN (
              SELECT id FROM shifts
              WHERE user_id = ?
              ORDER BY shift_date DESC, start_time DESC, id DESC
              LIMIT ?
          )
        """,
        (user_id, user_id, MAX_SAVED_SHIFTS),
    )


@app.before_request
def ensure_database():
    init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def establish_session(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["full_name"] = " ".join(part for part in [user["first_name"], user["last_name"]] if part).strip()
    session["is_admin"] = bool(user["is_admin"])


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if len(first_name) < 2 or len(last_name) < 2:
            error = "Skriv både fornavn og efternavn."
        elif len(username) < 3:
            error = "Brugernavnet skal være mindst 3 tegn."
        elif len(password) < 8:
            error = "Adgangskoden skal være mindst 8 tegn."
        else:
            try:
                with get_db() as db:
                    user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
                    cursor = db.execute(
                        """INSERT INTO users
                           (username, password_hash, first_name, last_name, is_admin, last_login_at)
                           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (username, generate_password_hash(password), first_name, last_name, 1 if user_count == 0 else 0),
                    )
                    user = db.execute(
                        "SELECT id, username, first_name, last_name, is_admin FROM users WHERE id = ?",
                        (cursor.lastrowid,),
                    ).fetchone()
                establish_session(user)
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                error = "Brugernavnet er allerede i brug."

    return render_template("auth.html", mode="register", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT id, username, password_hash, first_name, last_name, is_admin FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))

        if not user or not check_password_hash(user["password_hash"], password):
            error = "Forkert brugernavn eller adgangskode."
        else:
            establish_session(user)
            return redirect(url_for("index"))

    return render_template("auth.html", mode="login", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template(
        "tidsregistrering.html",
        username=session["username"],
        full_name=session.get("full_name") or session["username"],
        is_admin=session.get("is_admin", False),
    )


@app.get("/admin")
@admin_required
def admin():
    with get_db() as db:
        users = db.execute(
            """
            SELECT u.id, u.username, u.first_name, u.last_name, u.is_admin,
                   u.created_at, u.last_login_at, COUNT(s.id) AS shift_count
            FROM users u
            LEFT JOIN shifts s ON s.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC, u.id DESC
            """
        ).fetchall()
    return render_template("admin.html", users=users)


@app.get("/api/shifts")
@login_required
def list_shifts():
    with get_db() as db:
        prune_old_shifts(db, session["user_id"])
        rows = db.execute(
            """SELECT id, shift_date, start_time, station, payload, created_at, updated_at
               FROM shifts WHERE user_id = ?
               ORDER BY shift_date DESC, start_time DESC, id DESC LIMIT ?""",
            (session["user_id"], MAX_SAVED_SHIFTS),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        result.append(item)
    return jsonify(result)


@app.post("/api/shifts")
@login_required
def save_shift():
    data = request.get_json(silent=True) or {}
    shift_id = data.get("id")
    shift_date = str(data.get("shift_date", "")).strip()
    start_time = str(data.get("start_time", "")).strip()
    station = str(data.get("station", "")).strip()[:120]
    payload = data.get("payload")
    if not shift_date or not start_time or not isinstance(payload, dict):
        return jsonify({"error": "Ugyldige vagtdata."}), 400

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with get_db() as db:
        if shift_id:
            cursor = db.execute(
                """UPDATE shifts SET shift_date = ?, start_time = ?, station = ?, payload = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND user_id = ?""",
                (shift_date, start_time, station, encoded, shift_id, session["user_id"]),
            )
            if cursor.rowcount == 0:
                return jsonify({"error": "Vagten blev ikke fundet."}), 404
            saved_id = int(shift_id)
        else:
            cursor = db.execute(
                "INSERT INTO shifts (user_id, shift_date, start_time, station, payload) VALUES (?, ?, ?, ?, ?)",
                (session["user_id"], shift_date, start_time, station, encoded),
            )
            saved_id = cursor.lastrowid
        prune_old_shifts(db, session["user_id"])
    return jsonify({"id": saved_id, "saved": True, "maximum": MAX_SAVED_SHIFTS})


@app.delete("/api/shifts/<int:shift_id>")
@login_required
def delete_shift(shift_id):
    with get_db() as db:
        cursor = db.execute("DELETE FROM shifts WHERE id = ? AND user_id = ?", (shift_id, session["user_id"]))
    if cursor.rowcount == 0:
        return jsonify({"error": "Vagten blev ikke fundet."}), 404
    return jsonify({"deleted": True})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
