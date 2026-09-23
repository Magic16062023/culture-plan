import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "calendar.db"
HOST = os.getenv("APP_HOST", "127.0.0.1")
PORT = int(os.getenv("APP_PORT", "8000"))
CHECK_INTERVAL = int(os.getenv("NOTIFICATION_CHECK_INTERVAL", "300"))
SESSION_DAYS = 7
MAX_BODY_SIZE = 4 * 1024 * 1024
ROLES = {"admin", "leader", "user", "curator", "viewer"}
ROLE_NAMES = {
    "admin": "Администратор",
    "leader": "Руководитель",
    "user": "Пользователь",
    "curator": "Куратор",
    "viewer": "Зритель",
}
REQUIRED_EVENT_FIELDS = (
    "title", "institution", "date", "time", "place", "description", "representative"
)
ALLOWED_EVENT_FIELDS = REQUIRED_EVENT_FIELDS + ("category", "contact", "poster", "direction", "ticketUrl")


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, expected_hex):
    _, actual = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(actual, expected_hex)


def init_db():
    with connect_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                vk_id TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                owner_id TEXT REFERENCES users(id),
                direction TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                creator_id TEXT NOT NULL REFERENCES users(id),
                recipient_user_id TEXT NOT NULL REFERENCES users(id),
                days_before INTEGER NOT NULL,
                sent_at TEXT,
                sent_for_date TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(event_id, recipient_user_id, days_before)
            );
            CREATE TABLE IF NOT EXISTS calendar_feeds (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(events)")}
        if "owner_id" not in columns:
            connection.execute("ALTER TABLE events ADD COLUMN owner_id TEXT")
        if "direction" not in columns:
            connection.execute("ALTER TABLE events ADD COLUMN direction TEXT NOT NULL DEFAULT ''")
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "phone" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        if "vk_id" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN vk_id TEXT NOT NULL DEFAULT ''")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_vk_id_unique ON users(vk_id) WHERE vk_id <> ''")

    admin_id = ensure_bootstrap_admin()
    with connect_db() as connection:
        connection.execute("UPDATE events SET owner_id = ? WHERE owner_id IS NULL", (admin_id,))
        rows = connection.execute("SELECT id, data FROM events").fetchall()
        for row in rows:
            data = json.loads(row["data"])
            direction = data.get("direction", "")
            connection.execute("UPDATE events SET direction = ? WHERE id = ?", (direction, row["id"]))
            email = data.pop("notificationEmail", "")
            days = int(data.pop("notifyDaysBefore", 1) or 1)
            connection.execute("UPDATE events SET data = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), row["id"]))
            if email:
                recipient = connection.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
                if recipient:
                    connection.execute(
                        "INSERT OR IGNORE INTO notifications VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
                        (str(uuid.uuid4()), row["id"], admin_id, recipient["id"], days, datetime.now().isoformat(timespec="seconds")),
                    )


def ensure_bootstrap_admin():
    with connect_db() as connection:
        existing = connection.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if existing:
            return existing["id"]
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "admin123")
        email = os.getenv("ADMIN_EMAIL", "admin@localhost")
        salt, password_hash = hash_password(password)
        user_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO users (id, username, password_salt, password_hash, full_name, email, role, direction, phone, vk_id, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'admin', '', '', '', 1, ?)",
            (user_id, username, salt, password_hash, "Администратор", email, datetime.now().isoformat(timespec="seconds")),
        )
        print(f"Создан администратор: {username}. Смените начальный пароль после входа.")
        return user_id


def public_user(row):
    return {
        "id": row["id"], "username": row["username"], "fullName": row["full_name"],
        "email": row["email"], "role": row["role"], "roleName": ROLE_NAMES[row["role"]],
        "direction": row["direction"], "phone": row["phone"], "vkConnected": bool(row["vk_id"]),
        "active": bool(row["active"]),
    }


def get_user(user_id):
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return public_user(row) if row else None


def normalize_phone(value):
    raw = str(value or "").strip()
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValueError("Некорректный номер телефона")
    return "+" + digits


def validate_email(value):
    email = str(value or "").strip().lower()
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        raise ValueError("Некорректный email")
    return email


def register_viewer(payload):
    full_name = str(payload.get("fullName", "")).strip()
    email = validate_email(payload.get("email", ""))
    phone = normalize_phone(payload.get("phone", ""))
    password = str(payload.get("password", ""))
    if not full_name or not (email or phone):
        raise ValueError("Укажите имя и телефон или email")
    if len(password) < 6:
        raise ValueError("Пароль должен содержать не менее 6 символов")
    with connect_db() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM users WHERE (? <> '' AND email = ? COLLATE NOCASE) OR (? <> '' AND phone = ?)",
            (email, email, phone, phone),
        ).fetchone()
        if duplicate:
            raise ValueError("Пользователь с таким телефоном или email уже существует")
        user_id = str(uuid.uuid4())
        username = "viewer_" + user_id.replace("-", "")[:16]
        salt, password_hash = hash_password(password)
        connection.execute(
            "INSERT INTO users (id, username, password_salt, password_hash, full_name, email, role, direction, phone, vk_id, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'viewer', '', ?, '', 1, ?)",
            (user_id, username, salt, password_hash, full_name, email, phone, datetime.now().isoformat(timespec="seconds")),
        )
    return get_user(user_id)


def vk_redirect_uri():
    explicit = os.getenv("VK_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/api/auth/vk/callback" if base else ""


def vk_configured():
    return bool(os.getenv("VK_CLIENT_ID") and vk_redirect_uri())


def vk_request(url, payload):
    request = Request(
        url,
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def finish_vk_login(code, device_id, state):
    if not code or not device_id or not state:
        raise ValueError("VK ID вернул неполный ответ")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    with connect_db() as connection:
        oauth_state = connection.execute(
            "SELECT code_verifier FROM oauth_states WHERE state_hash = ? AND expires_at > ?",
            (state_hash, datetime.now().isoformat()),
        ).fetchone()
        connection.execute("DELETE FROM oauth_states WHERE state_hash = ?", (state_hash,))
    if not oauth_state:
        raise ValueError("Сессия VK ID истекла или была подменена")
    token_payload = {
        "grant_type": "authorization_code", "code_verifier": oauth_state["code_verifier"],
        "redirect_uri": vk_redirect_uri(), "code": code, "client_id": os.environ["VK_CLIENT_ID"],
        "device_id": device_id, "state": state,
    }
    if os.getenv("VK_SERVICE_TOKEN"):
        token_payload["service_token"] = os.environ["VK_SERVICE_TOKEN"]
    tokens = vk_request("https://id.vk.ru/oauth2/auth", token_payload)
    if "access_token" not in tokens:
        raise ValueError(tokens.get("error_description", "VK ID не выдал токен"))
    if tokens.get("state") != state:
        raise ValueError("VK ID вернул неверное состояние сессии")
    profile_response = vk_request("https://id.vk.ru/oauth2/user_info", {
        "access_token": tokens["access_token"], "client_id": os.environ["VK_CLIENT_ID"]
    })
    profile = profile_response.get("user", profile_response)
    vk_id = str(profile.get("user_id") or tokens.get("user_id") or "")
    if not vk_id:
        raise ValueError("VK ID не вернул идентификатор пользователя")
    email = validate_email(profile.get("email", ""))
    phone = normalize_phone(profile.get("phone", ""))
    full_name = " ".join(filter(None, [profile.get("first_name", ""), profile.get("last_name", "")])).strip() or "Зритель VK"
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE vk_id = ?", (vk_id,)).fetchone()
        if not row:
            row = connection.execute(
                "SELECT * FROM users WHERE role = 'viewer' AND ((? <> '' AND email = ? COLLATE NOCASE) OR (? <> '' AND phone = ?)) LIMIT 1",
                (email, email, phone, phone),
            ).fetchone()
        if row:
            connection.execute(
                "UPDATE users SET vk_id = ?, email = CASE WHEN email='' THEN ? ELSE email END, phone = CASE WHEN phone='' THEN ? ELSE phone END WHERE id = ?",
                (vk_id, email, phone, row["id"]),
            )
            user_id = row["id"]
        else:
            user_id = str(uuid.uuid4())
            salt, password_hash = hash_password(secrets.token_urlsafe(32))
            connection.execute(
                "INSERT INTO users (id, username, password_salt, password_hash, full_name, email, role, direction, phone, vk_id, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'viewer', '', ?, ?, 1, ?)",
                (user_id, f"vk_{vk_id}", salt, password_hash, full_name, email, phone, vk_id, datetime.now().isoformat(timespec="seconds")),
            )
    return user_id


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = datetime.now() + timedelta(days=SESSION_DAYS)
    with connect_db() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(),))
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash, user_id, expires.isoformat()))
    return token


def user_from_token(token):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect_db() as connection:
        row = connection.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1",
            (token_hash, datetime.now().isoformat()),
        ).fetchone()
    return public_user(row) if row else None


def serialize_event(row, user, include_poster=True):
    event = json.loads(row["data"])
    if not include_poster:
        event["hasPoster"] = bool(event.get("poster"))
        event["poster"] = ""
    event.update({
        "id": row["id"], "ownerId": row["owner_id"], "ownerName": row["owner_name"] or "",
        "direction": row["direction"], "createdAt": row["created_at"],
    })
    event["canEdit"] = can_edit_event(user, event)
    event["canNotify"] = user["role"] in {"admin", "leader", "curator"}
    return event


def list_events(user):
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT events.*, users.full_name owner_name FROM events LEFT JOIN users ON users.id = events.owner_id "
            "ORDER BY json_extract(data, '$.date'), json_extract(data, '$.time')"
        ).fetchall()
    return [serialize_event(row, user, include_poster=False) for row in rows]


def get_event(event_id, user):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT events.*, users.full_name owner_name FROM events LEFT JOIN users ON users.id = events.owner_id "
            "WHERE events.id = ?", (event_id,),
        ).fetchone()
    if not row:
        raise ValueError("Мероприятие не найдено")
    return serialize_event(row, user)


def get_event_poster(event_id):
    with connect_db() as connection:
        row = connection.execute("SELECT data FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        raise ValueError("Мероприятие не найдено")
    poster = json.loads(row["data"]).get("poster", "")
    if not poster.startswith("data:image/") or "," not in poster:
        raise ValueError("Афиша не найдена")
    metadata, encoded = poster.split(",", 1)
    content_type = metadata.removeprefix("data:").split(";", 1)[0]
    try:
        return content_type, base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("Некорректные данные афиши") from error


def can_edit_event(user, event):
    if user["role"] == "admin":
        return True
    if user["role"] == "user":
        return event.get("ownerId") == user["id"]
    if user["role"] == "curator":
        return bool(user["direction"] and event.get("direction") == user["direction"])
    return False


def validate_event(payload, user):
    if not isinstance(payload, dict):
        raise ValueError("Ожидался объект мероприятия")
    event = {}
    for field in ALLOWED_EVENT_FIELDS:
        value = payload.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"Некорректное поле: {field}")
        event[field] = value.strip()
    missing = [field for field in REQUIRED_EVENT_FIELDS if not event[field]]
    if missing:
        raise ValueError("Не заполнены обязательные поля: " + ", ".join(missing))
    try:
        datetime.strptime(event["date"], "%Y-%m-%d")
        datetime.strptime(event["time"], "%H:%M")
    except ValueError as error:
        raise ValueError("Некорректные дата или время") from error
    if len(event["poster"]) > 2_500_000:
        raise ValueError("Файл афиши слишком большой")
    if event["ticketUrl"]:
        ticket_url = urlparse(event["ticketUrl"])
        if ticket_url.scheme not in {"http", "https"} or not ticket_url.netloc:
            raise ValueError("Укажите корректную ссылку на сервис продажи билетов")
    if user["role"] == "curator":
        event["direction"] = user["direction"]
    return event


def save_event(payload, user, event_id=None):
    if user["role"] not in {"admin", "user", "curator"}:
        raise PermissionError("Ваша роль не позволяет изменять мероприятия")
    event = validate_event(payload, user)
    now = datetime.now().isoformat(timespec="seconds")
    event_id = event_id or str(uuid.uuid4())
    with connect_db() as connection:
        existing = connection.execute(
            "SELECT events.*, users.full_name owner_name FROM events LEFT JOIN users ON users.id = events.owner_id WHERE events.id = ?",
            (event_id,),
        ).fetchone()
        if existing:
            existing_event = serialize_event(existing, user)
            if not can_edit_event(user, existing_event):
                raise PermissionError("Нет права редактировать это мероприятие")
            owner_id, created_at = existing["owner_id"], existing["created_at"]
        else:
            owner_id, created_at = user["id"], now
        connection.execute(
            "INSERT OR REPLACE INTO events (id, data, owner_id, direction, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, json.dumps(event, ensure_ascii=False), owner_id, event["direction"], created_at, now),
        )
        owner = connection.execute("SELECT full_name FROM users WHERE id = ?", (owner_id,)).fetchone()
    event.update({"id": event_id, "ownerId": owner_id, "ownerName": owner["full_name"], "createdAt": created_at})
    event["canEdit"] = can_edit_event(user, event)
    event["canNotify"] = user["role"] in {"admin", "leader", "curator"}
    return event


def allowed_recipients(user):
    with connect_db() as connection:
        if user["role"] in {"admin", "leader"}:
            rows = connection.execute("SELECT * FROM users WHERE active = 1 AND email <> '' ORDER BY full_name").fetchall()
        elif user["role"] == "curator":
            rows = connection.execute(
                "SELECT * FROM users WHERE active = 1 AND email <> '' AND (id = ? OR role = 'leader') ORDER BY full_name",
                (user["id"],),
            ).fetchall()
        else:
            rows = []
    return [public_user(row) for row in rows]


def ics_escape(value):
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_ics_line(line):
    parts, current = [], ""
    for character in line:
        candidate = current + character
        limit = 74 if not parts else 73
        if len(candidate.encode("utf-8")) > limit and current:
            parts.append(current)
            current = character
        else:
            current = candidate
    parts.append(current)
    return "\r\n ".join(parts)


def build_ics():
    timezone = os.getenv("APP_TIMEZONE", "Europe/Moscow")
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Culture Plan//RU",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Культурный план",
        f"X-WR-TIMEZONE:{timezone}",
    ]
    with connect_db() as connection:
        rows = connection.execute("SELECT id, data FROM events ORDER BY json_extract(data, '$.date'), json_extract(data, '$.time')").fetchall()
    for row in rows:
        event = json.loads(row["data"])
        start = event["date"].replace("-", "") + "T" + event["time"].replace(":", "") + "00"
        description = event.get("description", "")
        if event.get("institution"):
            description += f"\nУчреждение: {event['institution']}"
        if event.get("direction"):
            description += f"\nНаправление: {event['direction']}"
        if event.get("ticketUrl"):
            description += f"\nБилеты: {event['ticketUrl']}"
        lines.extend([
            "BEGIN:VEVENT", f"UID:{row['id']}@culture-plan", f"DTSTAMP:{now}",
            f"DTSTART;TZID={timezone}:{start}", f"SUMMARY:{ics_escape(event['title'])}",
            f"LOCATION:{ics_escape(event.get('place', ''))}", f"DESCRIPTION:{ics_escape(description)}",
            f"CATEGORIES:{ics_escape(event.get('category', 'Мероприятие'))}",
            *([f"URL:{ics_escape(event['ticketUrl'])}"] if event.get("ticketUrl") else []), "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def create_notification(event_id, payload, user):
    if user["role"] not in {"admin", "leader", "curator"}:
        raise PermissionError("Нет права создавать уведомления")
    recipient_id = str(payload.get("recipientUserId", ""))
    try:
        days = int(payload.get("daysBefore", 1))
    except (TypeError, ValueError) as error:
        raise ValueError("Некорректный срок уведомления") from error
    if days not in (0, 1, 3, 7, 14):
        raise ValueError("Некорректный срок уведомления")
    recipients = {item["id"]: item for item in allowed_recipients(user)}
    if recipient_id not in recipients:
        raise PermissionError("Получатель недоступен для вашей роли")
    with connect_db() as connection:
        event = connection.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not event:
            raise ValueError("Мероприятие не найдено")
        notification_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO notifications VALUES (?, ?, ?, ?, ?, NULL, NULL, ?) "
            "ON CONFLICT(event_id, recipient_user_id, days_before) DO UPDATE SET creator_id=excluded.creator_id, sent_at=NULL, sent_for_date=NULL",
            (notification_id, event_id, user["id"], recipient_id, days, datetime.now().isoformat(timespec="seconds")),
        )
    return {"ok": True, "recipient": recipients[recipient_id]["fullName"], "daysBefore": days}


def validate_user_payload(payload, editing=False):
    username = str(payload.get("username", "")).strip()
    full_name = str(payload.get("fullName", "")).strip()
    email = validate_email(payload.get("email", ""))
    phone = normalize_phone(payload.get("phone", ""))
    role = str(payload.get("role", "")).strip()
    direction = str(payload.get("direction", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not full_name or role not in ROLES:
        raise ValueError("Заполните логин, имя и роль")
    if not editing and len(password) < 6:
        raise ValueError("Пароль должен содержать не менее 6 символов")
    if role == "curator" and not direction:
        raise ValueError("Для куратора укажите направление")
    return username, full_name, email, phone, role, direction, password


def smtp_configured():
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def send_notification(event, recipient):
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    username, password = os.getenv("SMTP_USERNAME", ""), os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_SSL", "false").lower() == "true"
    event_date = datetime.strptime(event["date"], "%Y-%m-%d").strftime("%d.%m.%Y")
    message = EmailMessage()
    message["Subject"] = f"Напоминание: {event['title']}"
    message["From"], message["To"] = os.environ["SMTP_FROM"], recipient
    message.set_content(
        f"Приближается мероприятие «{event['title']}».\n\nДата: {event_date}\nВремя: {event['time']}\n"
        f"Место: {event['place']}\nУчреждение: {event['institution']}\n"
        f"{('Билеты: ' + event['ticketUrl'] + chr(10)) if event.get('ticketUrl') else ''}\n{event['description']}\n"
    )
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=30) as smtp:
        if not use_ssl:
            smtp.ehlo(); smtp.starttls(); smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def check_notifications():
    if not smtp_configured():
        return 0
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT notifications.*, events.data, users.email recipient_email FROM notifications "
            "JOIN events ON events.id = notifications.event_id JOIN users ON users.id = notifications.recipient_user_id "
            "WHERE users.active = 1 AND users.email <> ''"
        ).fetchall()
    today, sent_count = date.today(), 0
    for row in rows:
        event = json.loads(row["data"])
        event_date = datetime.strptime(event["date"], "%Y-%m-%d").date()
        if not event_date - timedelta(days=row["days_before"]) <= today <= event_date:
            continue
        if row["sent_for_date"] == event["date"]:
            continue
        try:
            send_notification(event, row["recipient_email"])
            with connect_db() as connection:
                connection.execute(
                    "UPDATE notifications SET sent_at = ?, sent_for_date = ? WHERE id = ?",
                    (datetime.now().isoformat(timespec="seconds"), event["date"], row["id"]),
                )
            sent_count += 1
            print(f"Уведомление отправлено: {row['recipient_email']} — {event['title']}")
        except Exception as error:
            print(f"Ошибка отправки уведомления {row['id']}: {error}")
    return sent_count


def notification_worker():
    while True:
        check_notifications()
        time.sleep(max(CHECK_INTERVAL, 60))


class CalendarHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, payload, status=HTTPStatus.OK, cookie=None):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(content)

    def send_ics(self, content):
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Disposition", 'inline; filename="culture-calendar.ics"')
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location, cookie=None):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def public_base_url(self):
        configured = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if configured:
            return configured
        protocol = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        return f"{protocol}://{self.headers.get('Host', f'{HOST}:{PORT}')}"

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Некорректный размер запроса") from error
        if length <= 0 or length > MAX_BODY_SIZE:
            raise ValueError("Запрос пуст или слишком велик")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Некорректный JSON") from error

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["session"].value if "session" in cookie else ""

    def current_user(self):
        return user_from_token(self.session_token())

    def require_user(self, roles=None):
        user = self.current_user()
        if not user:
            self.send_json({"error": "Требуется вход"}, HTTPStatus.UNAUTHORIZED)
            return None
        if roles and user["role"] not in roles:
            self.send_json({"error": "Недостаточно прав"}, HTTPStatus.FORBIDDEN)
            return None
        return user

    def handle_error(self, error):
        if isinstance(error, PermissionError):
            self.send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
        elif isinstance(error, sqlite3.IntegrityError):
            self.send_json({"error": "Логин уже используется или запись уже существует"}, HTTPStatus.CONFLICT)
        else:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/api/auth/vk/start":
            if not vk_configured():
                self.send_json({"error": "VK ID не настроен"}, HTTPStatus.SERVICE_UNAVAILABLE); return
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            with connect_db() as connection:
                connection.execute("DELETE FROM oauth_states WHERE expires_at < ?", (datetime.now().isoformat(),))
                connection.execute(
                    "INSERT INTO oauth_states VALUES (?, ?, ?)",
                    (hashlib.sha256(state.encode()).hexdigest(), verifier, (datetime.now() + timedelta(minutes=10)).isoformat()),
                )
            query = urlencode({
                "response_type": "code", "client_id": os.environ["VK_CLIENT_ID"],
                "redirect_uri": vk_redirect_uri(), "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256", "scope": "email phone",
            })
            self.send_redirect(f"https://id.vk.ru/authorize?{query}"); return
        if path == "/api/auth/vk/callback":
            try:
                query = parse_qs(parsed_url.query)
                if query.get("error"):
                    raise ValueError(query.get("error_description", query["error"])[0])
                user_id = finish_vk_login(
                    query.get("code", [""])[0], query.get("device_id", [""])[0], query.get("state", [""])[0]
                )
                token = create_session(user_id)
                cookie = f"session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_DAYS * 86400}"
                self.send_redirect("/", cookie); return
            except Exception as error:
                self.send_redirect("/?" + urlencode({"vk_error": str(error)})); return
        if path in ("/", "/index.html"):
            self.path = "/index.html"; super().do_GET(); return
        if path.startswith("/calendar/") and path.endswith(".ics"):
            token = path.removeprefix("/calendar/").removesuffix(".ics")
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with connect_db() as connection:
                feed = connection.execute(
                    "SELECT 1 FROM calendar_feeds JOIN users ON users.id = calendar_feeds.user_id "
                    "WHERE calendar_feeds.token_hash = ? AND users.active = 1", (token_hash,),
                ).fetchone()
            if not feed:
                self.send_error(HTTPStatus.NOT_FOUND); return
            self.send_ics(build_ics()); return
        if path == "/api/auth/me":
            user = self.current_user()
            self.send_json({"user": user, "smtpConfigured": smtp_configured(), "vkConfigured": vk_configured()}); return
        user = self.require_user()
        if not user:
            return
        if path == "/api/events":
            self.send_json(list_events(user)); return
        if path.startswith("/api/events/") and path.endswith("/poster"):
            try:
                event_id = path.removeprefix("/api/events/").removesuffix("/poster").strip("/")
                content_type, content = get_event_poster(event_id)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except ValueError as error:
                self.handle_error(error)
            return
        if path.startswith("/api/events/"):
            self.send_json(get_event(path.removeprefix("/api/events/"), user)); return
        if path == "/api/notification-recipients":
            self.send_json(allowed_recipients(user)); return
        if path == "/api/calendar-feed":
            with connect_db() as connection:
                feed = connection.execute("SELECT created_at FROM calendar_feeds WHERE user_id = ?", (user["id"],)).fetchone()
            self.send_json({"active": bool(feed), "createdAt": feed["created_at"] if feed else None, "publicUrlConfigured": bool(os.getenv("PUBLIC_BASE_URL"))}); return
        if path == "/api/users":
            if user["role"] != "admin":
                self.send_json({"error": "Недостаточно прав"}, HTTPStatus.FORBIDDEN); return
            with connect_db() as connection:
                rows = connection.execute("SELECT * FROM users ORDER BY full_name").fetchall()
            self.send_json([public_user(row) for row in rows]); return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/auth/login":
                payload = self.read_json()
                identifier = str(payload.get("username", "")).strip()
                normalized_phone = ""
                try:
                    normalized_phone = normalize_phone(identifier)
                except ValueError:
                    pass
                with connect_db() as connection:
                    rows = connection.execute(
                        "SELECT * FROM users WHERE active = 1 AND (username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE OR (? <> '' AND phone = ?))",
                        (identifier, identifier, normalized_phone, normalized_phone),
                    ).fetchall()
                password = str(payload.get("password", ""))
                row = next((candidate for candidate in rows if verify_password(password, candidate["password_salt"], candidate["password_hash"])), None)
                if not row:
                    self.send_json({"error": "Неверный логин или пароль"}, HTTPStatus.UNAUTHORIZED); return
                token = create_session(row["id"])
                cookie = f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_DAYS * 86400}"
                self.send_json({"user": public_user(row)}, cookie=cookie); return
            if path == "/api/auth/register":
                registered = register_viewer(self.read_json())
                token = create_session(registered["id"])
                cookie = f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_DAYS * 86400}"
                self.send_json({"user": registered}, HTTPStatus.CREATED, cookie); return
            if path == "/api/auth/logout":
                token = self.session_token()
                if token:
                    with connect_db() as connection:
                        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
                self.send_json({"ok": True}, cookie="session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"); return
            user = self.require_user()
            if not user:
                return
            if path == "/api/events":
                self.send_json(save_event(self.read_json(), user), HTTPStatus.CREATED); return
            if path == "/api/calendar-feed":
                token = secrets.token_urlsafe(36)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                created_at = datetime.now().isoformat(timespec="seconds")
                with connect_db() as connection:
                    connection.execute(
                        "INSERT INTO calendar_feeds VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash, created_at=excluded.created_at",
                        (user["id"], token_hash, created_at),
                    )
                url = f"{self.public_base_url()}/calendar/{token}.ics"
                self.send_json({"url": url, "createdAt": created_at}, HTTPStatus.CREATED); return
            if path == "/api/users":
                if user["role"] != "admin":
                    raise PermissionError("Недостаточно прав")
                username, full_name, email, phone, role, direction, password = validate_user_payload(self.read_json())
                salt, password_hash = hash_password(password)
                new_id = str(uuid.uuid4())
                with connect_db() as connection:
                    connection.execute(
                        "INSERT INTO users (id, username, password_salt, password_hash, full_name, email, role, direction, phone, vk_id, active, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?)",
                        (new_id, username, salt, password_hash, full_name, email, role, direction, phone, datetime.now().isoformat(timespec="seconds")),
                    )
                self.send_json(get_user(new_id), HTTPStatus.CREATED); return
            if path.startswith("/api/events/") and path.endswith("/notifications"):
                event_id = path.removeprefix("/api/events/").removesuffix("/notifications").strip("/")
                self.send_json(create_notification(event_id, self.read_json(), user), HTTPStatus.CREATED); return
            if path == "/api/notifications/check":
                if user["role"] != "admin":
                    raise PermissionError("Недостаточно прав")
                if not smtp_configured():
                    self.send_json({"error": "SMTP не настроен"}, HTTPStatus.SERVICE_UNAVAILABLE); return
                self.send_json({"sent": check_notifications()}); return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, PermissionError, sqlite3.IntegrityError) as error:
            self.handle_error(error)

    def do_PUT(self):
        path = urlparse(self.path).path
        user = self.require_user()
        if not user:
            return
        try:
            if path.startswith("/api/events/"):
                event_id = path.removeprefix("/api/events/")
                self.send_json(save_event(self.read_json(), user, event_id)); return
            if path.startswith("/api/users/"):
                if user["role"] != "admin":
                    raise PermissionError("Недостаточно прав")
                user_id = path.removeprefix("/api/users/")
                payload = self.read_json()
                username, full_name, email, phone, role, direction, password = validate_user_payload(payload, True)
                active = 1 if payload.get("active", True) else 0
                if user_id == user["id"] and (not active or role != "admin"):
                    raise PermissionError("Нельзя отключить себя или изменить свою роль")
                with connect_db() as connection:
                    exists = connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
                    if not exists:
                        raise ValueError("Пользователь не найден")
                    connection.execute("UPDATE users SET username=?, full_name=?, email=?, phone=?, role=?, direction=?, active=? WHERE id=?", (username, full_name, email, phone, role, direction, active, user_id))
                    if password:
                        if len(password) < 6:
                            raise ValueError("Пароль должен содержать не менее 6 символов")
                        salt, password_hash = hash_password(password)
                        connection.execute("UPDATE users SET password_salt=?, password_hash=? WHERE id=?", (salt, password_hash, user_id))
                self.send_json(get_user(user_id)); return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, PermissionError, sqlite3.IntegrityError) as error:
            self.handle_error(error)

    def do_DELETE(self):
        path = urlparse(self.path).path
        user = self.require_user()
        if not user:
            return
        if path == "/api/calendar-feed":
            with connect_db() as connection:
                connection.execute("DELETE FROM calendar_feeds WHERE user_id = ?", (user["id"],))
            self.send_json({"ok": True}); return
        if not path.startswith("/api/events/"):
            self.send_error(HTTPStatus.NOT_FOUND); return
        event_id = path.removeprefix("/api/events/")
        with connect_db() as connection:
            row = connection.execute("SELECT events.*, users.full_name owner_name FROM events LEFT JOIN users ON users.id=events.owner_id WHERE events.id=?", (event_id,)).fetchone()
            if not row:
                self.send_json({"error": "Мероприятие не найдено"}, HTTPStatus.NOT_FOUND); return
            if not can_edit_event(user, serialize_event(row, user)):
                self.send_json({"error": "Нет права удалять это мероприятие"}, HTTPStatus.FORBIDDEN); return
            connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self.send_json({"ok": True})

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")


if __name__ == "__main__":
    init_db()
    threading.Thread(target=notification_worker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), CalendarHandler)
    print(f"Культурный план запущен: http://{HOST}:{PORT}")
    print("SMTP настроен." if smtp_configured() else "SMTP не настроен: email-уведомления отключены.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()
