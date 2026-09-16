import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import ssl
import string
from datetime import timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "minutregnskab.db"))
MAX_SAVED_SHIFTS = 10
SHARE_CODE_HOURS = 30
SHARE_CODE_LENGTH = 10
MAX_PAYLOAD_BYTES = 64 * 1024
SESSION_LIFETIME_HOURS = max(1, min(int(os.environ.get("SESSION_LIFETIME_HOURS", "12")), 168))
PASSWORD_RESET_MINUTES = max(10, min(int(os.environ.get("PASSWORD_RESET_MINUTES", "60")), 240))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "true").lower() == "true"
SMTP_SSL = os.environ.get("SMTP_SSL", "false").lower() == "true"

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY skal angives")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_LIFETIME_HOURS),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=MAX_PAYLOAD_BYTES,
)


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
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
                email TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                auth_version INTEGER NOT NULL DEFAULT 0,
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
                share_code TEXT,
                share_expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS shift_members (
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (shift_id, user_id),
                FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_shifts_user_recent
            ON shifts(user_id, shift_date DESC, start_time DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_shift_members_user
            ON shift_members(user_id, shift_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shifts_active_share_code
            ON shifts(share_code) WHERE share_code IS NOT NULL;

            CREATE TABLE IF NOT EXISTS share_code_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_share_code_attempts_recent
            ON share_code_attempts(user_id, created_at);

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user
            ON password_reset_tokens(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_expiry
            ON password_reset_tokens(expires_at);
            """
        )
        user_columns = _column_names(db, "users")
        user_migrations = {
            "email": "ALTER TABLE users ADD COLUMN email TEXT",
            "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''",
            "last_name": "ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''",
            "is_admin": "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
            "auth_version": "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0",
            "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        }
        for column, statement in user_migrations.items():
            if column not in user_columns:
                db.execute(statement)
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
               ON users(email COLLATE NOCASE)
               WHERE email IS NOT NULL AND email <> ''"""
        )

        shift_columns = _column_names(db, "shifts")
        shift_migrations = {
            "share_code": "ALTER TABLE shifts ADD COLUMN share_code TEXT",
            "share_expires_at": "ALTER TABLE shifts ADD COLUMN share_expires_at TEXT",
        }
        for column, statement in shift_migrations.items():
            if column not in shift_columns:
                db.execute(statement)

        db.execute(
            """INSERT OR IGNORE INTO shift_members (shift_id, user_id, joined_at)
               SELECT id, user_id, created_at FROM shifts"""
        )
        admin_count = db.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
        if admin_count == 0:
            first_user = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first_user:
                db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_user["id"],))


def prune_old_shifts(db, user_id):
    rows = db.execute(
        """SELECT s.id FROM shifts s
           JOIN shift_members sm ON sm.shift_id = s.id
           WHERE sm.user_id = ?
           ORDER BY s.shift_date DESC, s.start_time DESC, s.id DESC
           LIMIT -1 OFFSET ?""",
        (user_id, MAX_SAVED_SHIFTS),
    ).fetchall()
    for row in rows:
        shift_id = row["id"]
        owner = db.execute("SELECT user_id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if owner and owner["user_id"] == user_id:
            member_count = db.execute("SELECT COUNT(*) AS count FROM shift_members WHERE shift_id = ?", (shift_id,)).fetchone()["count"]
            if member_count == 1:
                db.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
            else:
                db.execute("DELETE FROM shift_members WHERE shift_id = ? AND user_id = ?", (shift_id, user_id))
        else:
            db.execute("DELETE FROM shift_members WHERE shift_id = ? AND user_id = ?", (shift_id, user_id))


@app.before_request
def ensure_database():
    init_db()
    session.setdefault("_csrf_token", secrets.token_urlsafe(32))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["_csrf_token"]):
            abort(400, description="Ugyldig CSRF-token")


@app.context_processor
def template_security_values():
    return {"csrf_token": session.get("_csrf_token", "")}


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        with get_db() as db:
            user = db.execute(
                "SELECT auth_version FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not user or int(user["auth_version"]) != int(session.get("auth_version", -1)):
            session.clear()
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
    session["auth_version"] = int(user["auth_version"])


def user_can_access_shift(db, shift_id, user_id):
    return db.execute(
        "SELECT 1 FROM shift_members WHERE shift_id = ? AND user_id = ?",
        (shift_id, user_id),
    ).fetchone() is not None


def serialize_shift(db, row, user_id):
    item = dict(row)
    item["payload"] = json.loads(item["payload"])
    item["is_owner"] = item["user_id"] == user_id
    item["share_active"] = bool(
        item.get("share_code")
        and item.get("share_expires_at")
        and db.execute("SELECT datetime(?) > CURRENT_TIMESTAMP AS active", (item["share_expires_at"],)).fetchone()["active"]
    )
    members = db.execute(
        """SELECT u.id, u.username, u.first_name, u.last_name
           FROM shift_members sm JOIN users u ON u.id = sm.user_id
           WHERE sm.shift_id = ? ORDER BY sm.joined_at, u.id""",
        (item["id"],),
    ).fetchall()
    item["members"] = [dict(member) for member in members]
    if not item["is_owner"]:
        item["share_code"] = None
    return item


def create_unique_code(db):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(30):
        code = "".join(secrets.choice(alphabet) for _ in range(SHARE_CODE_LENGTH))
        if not db.execute("SELECT 1 FROM shifts WHERE share_code = ?", (code,)).fetchone():
            return code
    raise RuntimeError("Kunne ikke oprette en unik vagt-kode.")


def normalize_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return ""
    local, domain = email.rsplit("@", 1)
    if not local or not domain or " " in email:
        return ""
    return email


def password_reset_url(token):
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL skal angives for password reset")
    return f"{PUBLIC_BASE_URL}{url_for('reset_password', token=token)}"


def send_password_reset_email(recipient, first_name, reset_url):
    if not SMTP_HOST or not SMTP_FROM:
        raise RuntimeError("SMTP_HOST og SMTP_FROM skal angives for password reset")

    message = EmailMessage()
    message["Subject"] = "Nulstil din adgangskode til Minutregnskab"
    message["From"] = SMTP_FROM
    message["To"] = recipient
    greeting = f"Hej {first_name}," if first_name else "Hej,"
    message.set_content(
        f"""{greeting}

Du har bedt om at nulstille din adgangskode til Minutregnskab.

Åbn dette link for at vælge en ny adgangskode:
{reset_url}

Linket udløber om {PASSWORD_RESET_MINUTES} minutter og kan kun bruges én gang.

Hvis du ikke har bedt om dette, kan du ignorere mailen.
"""
    )

    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if SMTP_SSL else smtplib.SMTP
    with smtp_class(SMTP_HOST, SMTP_PORT, timeout=15, context=context) if SMTP_SSL else smtp_class(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        if not SMTP_SSL and SMTP_STARTTLS:
            server.starttls(context=context)
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(message)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(first_name) < 2 or len(last_name) < 2:
            error = "Skriv både fornavn og efternavn."
        elif not email:
            error = "Skriv en gyldig e-mailadresse."
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
                           (username, password_hash, email, first_name, last_name, is_admin, last_login_at)
                           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (
                            username,
                            generate_password_hash(password),
                            email,
                            first_name,
                            last_name,
                            1 if user_count == 0 else 0,
                        ),
                    )
                    user = db.execute(
                        """SELECT id, username, first_name, last_name, is_admin, auth_version
                           FROM users WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                establish_session(user)
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                error = "Brugernavn eller e-mail er allerede i brug."
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
                """SELECT id, username, password_hash, first_name, last_name, is_admin, auth_version
                   FROM users WHERE username = ?""",
                (username,),
            ).fetchone()
            valid = bool(user and check_password_hash(user["password_hash"], password))
            if valid:
                db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        if not valid:
            error = "Forkert brugernavn eller adgangskode."
        else:
            establish_session(user)
            return redirect(url_for("index"))
    return render_template("auth.html", mode="login", error=error)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = None
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        normalized = normalize_email(identifier)
        token = None
        recipient = None
        first_name = ""

        with get_db() as db:
            db.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at <= CURRENT_TIMESTAMP OR used_at IS NOT NULL"
            )
            if normalized:
                user = db.execute(
                    """SELECT id, email, first_name
                       FROM users WHERE email IS NOT NULL AND lower(email) = lower(?)""",
                    (normalized,),
                ).fetchone()
            else:
                user = db.execute(
                    "SELECT id, email, first_name FROM users WHERE username = ?",
                    (identifier,),
                ).fetchone()

            if user and user["email"]:
                recent = db.execute(
                    """SELECT 1 FROM password_reset_tokens
                       WHERE user_id = ? AND created_at >= datetime('now', '-60 seconds')
                       LIMIT 1""",
                    (user["id"],),
                ).fetchone()
                if not recent:
                    token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                    db.execute(
                        "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE user_id = ? AND used_at IS NULL",
                        (user["id"],),
                    )
                    db.execute(
                        """INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                           VALUES (?, ?, datetime('now', ?))""",
                        (user["id"], token_hash, f"+{PASSWORD_RESET_MINUTES} minutes"),
                    )
                    recipient = user["email"]
                    first_name = user["first_name"]

        if token and recipient:
            try:
                send_password_reset_email(recipient, first_name, password_reset_url(token))
            except Exception:
                app.logger.exception("Kunne ikke sende password-reset mail")

        message = (
            "Hvis oplysningerne matcher en konto med registreret e-mail, "
            "sender vi et link til nulstilling af adgangskoden."
        )
    return render_template("auth.html", mode="forgot", message=message)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if len(token) > 200:
        return render_template("auth.html", mode="reset", invalid_token=True), 400

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_db() as db:
        reset = db.execute(
            """SELECT prt.id, prt.user_id, u.username
               FROM password_reset_tokens prt
               JOIN users u ON u.id = prt.user_id
               WHERE prt.token_hash = ?
                 AND prt.used_at IS NULL
                 AND prt.expires_at > CURRENT_TIMESTAMP""",
            (token_hash,),
        ).fetchone()

    if not reset:
        return render_template("auth.html", mode="reset", invalid_token=True), 400

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            error = "Adgangskoden skal være mindst 8 tegn."
        elif password != confirm_password:
            error = "De to adgangskoder er ikke ens."
        else:
            with get_db() as db:
                current = db.execute(
                    """SELECT id, user_id FROM password_reset_tokens
                       WHERE token_hash = ? AND used_at IS NULL
                         AND expires_at > CURRENT_TIMESTAMP""",
                    (token_hash,),
                ).fetchone()
                if not current:
                    return render_template("auth.html", mode="reset", invalid_token=True), 400
                db.execute(
                    """UPDATE users
                       SET password_hash = ?, auth_version = auth_version + 1
                       WHERE id = ?""",
                    (generate_password_hash(password), current["user_id"]),
                )
                db.execute(
                    "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE user_id = ? AND used_at IS NULL",
                    (current["user_id"],),
                )
            session.clear()
            return render_template(
                "auth.html",
                mode="reset_done",
                message="Din adgangskode er ændret. Du kan nu logge ind med den nye adgangskode.",
            )

    return render_template("auth.html", mode="reset", error=error, invalid_token=False)


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    error = None
    message = None
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        if not email:
            error = "Skriv en gyldig e-mailadresse."
        else:
            try:
                with get_db() as db:
                    db.execute("UPDATE users SET email = ? WHERE id = ?", (email, session["user_id"]))
                message = "Din e-mailadresse er gemt."
            except sqlite3.IntegrityError:
                error = "E-mailadressen bruges allerede af en anden konto."

    with get_db() as db:
        user = db.execute(
            """SELECT username, email, first_name, last_name
               FROM users WHERE id = ?""",
            (session["user_id"],),
        ).fetchone()
    return render_template("account.html", user=user, error=error, message=message)


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
            """SELECT u.id, u.username, u.email, u.first_name, u.last_name, u.is_admin,
                      u.created_at, u.last_login_at, COUNT(DISTINCT sm.shift_id) AS shift_count
               FROM users u LEFT JOIN shift_members sm ON sm.user_id = u.id
               GROUP BY u.id ORDER BY u.created_at DESC, u.id DESC"""
        ).fetchall()
    return render_template("admin.html", users=users)


@app.get("/api/shifts")
@login_required
def list_shifts():
    with get_db() as db:
        prune_old_shifts(db, session["user_id"])
        rows = db.execute(
            """SELECT s.id, s.user_id, s.shift_date, s.start_time, s.station, s.payload,
                      s.share_code, s.share_expires_at, s.created_at, s.updated_at
               FROM shifts s JOIN shift_members sm ON sm.shift_id = s.id
               WHERE sm.user_id = ?
               ORDER BY s.shift_date DESC, s.start_time DESC, s.id DESC LIMIT ?""",
            (session["user_id"], MAX_SAVED_SHIFTS),
        ).fetchall()
        result = [serialize_shift(db, row, session["user_id"]) for row in rows]
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
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return jsonify({"error": "Vagtdata er for store."}), 413
    with get_db() as db:
        if shift_id:
            if not user_can_access_shift(db, int(shift_id), session["user_id"]):
                return jsonify({"error": "Vagten blev ikke fundet."}), 404
            db.execute(
                """UPDATE shifts SET shift_date = ?, start_time = ?, station = ?, payload = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (shift_date, start_time, station, encoded, shift_id),
            )
            saved_id = int(shift_id)
        else:
            cursor = db.execute(
                "INSERT INTO shifts (user_id, shift_date, start_time, station, payload) VALUES (?, ?, ?, ?, ?)",
                (session["user_id"], shift_date, start_time, station, encoded),
            )
            saved_id = cursor.lastrowid
            db.execute("INSERT INTO shift_members (shift_id, user_id) VALUES (?, ?)", (saved_id, session["user_id"]))
        prune_old_shifts(db, session["user_id"])
        row = db.execute(
            """SELECT id, user_id, shift_date, start_time, station, payload, share_code,
                      share_expires_at, created_at, updated_at FROM shifts WHERE id = ?""",
            (saved_id,),
        ).fetchone()
        result = serialize_shift(db, row, session["user_id"])
    return jsonify(result)


@app.post("/api/shifts/<int:shift_id>/share")
@login_required
def share_shift(shift_id):
    with get_db() as db:
        shift = db.execute("SELECT user_id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if not shift or shift["user_id"] != session["user_id"]:
            return jsonify({"error": "Kun den, der oprettede vagten, kan lave en kode."}), 403
        code = create_unique_code(db)
        db.execute(
            """UPDATE shifts SET share_code = ?,
               share_expires_at = datetime('now', ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (code, f"+{SHARE_CODE_HOURS} hours", shift_id),
        )
    return jsonify({"code": code, "expires_in_hours": SHARE_CODE_HOURS})


@app.delete("/api/shifts/<int:shift_id>/share")
@login_required
def close_share(shift_id):
    with get_db() as db:
        cursor = db.execute(
            "UPDATE shifts SET share_code = NULL, share_expires_at = NULL WHERE id = ? AND user_id = ?",
            (shift_id, session["user_id"]),
        )
    if cursor.rowcount == 0:
        return jsonify({"error": "Vagten blev ikke fundet."}), 404
    return jsonify({"closed": True})


@app.post("/api/shifts/join")
@login_required
def join_shift():
    data = request.get_json(silent=True) or {}
    code = "".join(character for character in str(data.get("code", "")).upper() if character.isalnum())
    if len(code) != SHARE_CODE_LENGTH:
        return jsonify({"error": f"Vagt-koden skal være på {SHARE_CODE_LENGTH} tegn."}), 400
    with get_db() as db:
        db.execute("DELETE FROM share_code_attempts WHERE created_at < datetime('now', '-1 day')")
        attempts = db.execute(
            "SELECT COUNT(*) AS count FROM share_code_attempts WHERE user_id = ? AND created_at >= datetime('now', '-15 minutes')",
            (session["user_id"],),
        ).fetchone()["count"]
        if attempts >= 10:
            return jsonify({"error": "For mange kodeforsøg. Prøv igen senere."}), 429
        shift = db.execute(
            """SELECT id FROM shifts WHERE share_code = ?
               AND share_expires_at IS NOT NULL AND datetime(share_expires_at) > CURRENT_TIMESTAMP""",
            (code,),
        ).fetchone()
        if not shift:
            db.execute("INSERT INTO share_code_attempts (user_id) VALUES (?)", (session["user_id"],))
            return jsonify({"error": "Koden findes ikke eller er udløbet."}), 404
        db.execute("DELETE FROM share_code_attempts WHERE user_id = ?", (session["user_id"],))
        db.execute(
            "INSERT OR IGNORE INTO shift_members (shift_id, user_id) VALUES (?, ?)",
            (shift["id"], session["user_id"]),
        )
        row = db.execute(
            """SELECT id, user_id, shift_date, start_time, station, payload, share_code,
                      share_expires_at, created_at, updated_at FROM shifts WHERE id = ?""",
            (shift["id"],),
        ).fetchone()
        result = serialize_shift(db, row, session["user_id"])
    return jsonify(result)


@app.delete("/api/shifts/<int:shift_id>")
@login_required
def delete_shift(shift_id):
    with get_db() as db:
        shift = db.execute("SELECT user_id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if not shift or not user_can_access_shift(db, shift_id, session["user_id"]):
            return jsonify({"error": "Vagten blev ikke fundet."}), 404
        if shift["user_id"] == session["user_id"]:
            db.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        else:
            db.execute("DELETE FROM shift_members WHERE shift_id = ? AND user_id = ?", (shift_id, session["user_id"]))
    return jsonify({"deleted": True})


@app.get("/health")
def health():
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok", "database": "ok"})
    except sqlite3.Error:
        return jsonify({"status": "error", "database": "unavailable"}), 503


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
