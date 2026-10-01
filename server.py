from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATABASE_PATH = DATA_DIR / "applications.sqlite3"
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_REQUEST_BYTES = 15 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 5
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
REQUIRED_FIELDS = {
    "first_name",
    "last_name",
    "date_of_birth",
    "nationality",
    "email",
    "phone",
    "street_address",
    "city",
    "country",
    "emergency_contact_name",
    "emergency_contact_phone",
    "emergency_contact_relationship",
    "programme",
    "area_of_interest",
    "study_mode",
    "preferred_intake",
    "preferred_schedule",
    "highest_education",
    "experience_level",
    "study_device",
    "motivation",
    "declaration",
    "applicant_signature",
    "application_date",
}
ALLOWED_VALUES = {
    "programme": {"certificate", "diploma", "advanced-diploma"},
    "area_of_interest": {
        "software-development",
        "networking",
        "cybersecurity",
        "data-analytics",
        "it-support",
        "general",
        "undecided",
    },
    "study_mode": {"on-campus", "online", "hybrid"},
    "preferred_schedule": {"morning", "afternoon", "evening", "weekend"},
    "highest_education": {"secondary", "certificate", "diploma", "bachelors", "postgraduate", "other"},
    "experience_level": {"none", "basic", "some", "experienced"},
    "study_device": {"laptop", "tablet", "shared", "none"},
    "gender": {"", "female", "male", "non-binary", "prefer-not-to-say", "self-describe"},
}


@contextmanager
def database_connection(database_path: Path):
    connection = sqlite3.connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submitted_at TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                files_json TEXT NOT NULL
            )"""
        )


class ApplicationHandler(BaseHTTPRequestHandler):
    database_path = DATABASE_PATH
    upload_dir = UPLOAD_DIR
    admin_password = ""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_file(ROOT / "index.html", "text/html; charset=utf-8")
        elif path == "/admin":
            self.send_file(ROOT / "admin.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self.send_file(ROOT / "style.css", "text/css; charset=utf-8")
        elif path == "/submitted":
            self.send_file(ROOT / "submitted.html", "text/html; charset=utf-8")
        elif path == "/api/applications":
            if not self.authorized():
                return
            self.list_applications()
        elif path.startswith("/api/files/"):
            if not self.authorized():
                return
            self.download_file(unquote(path.removeprefix("/api/files/")))
        else:
            self.send_error(404, "Page not found")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/applications":
            self.send_error(404, "Page not found")
            return

        content_length = self.headers.get("Content-Length", "")
        if not content_length.isdigit():
            self.send_error(400, "A valid Content-Length header is required")
            return
        size = int(content_length)
        if size > MAX_REQUEST_BYTES:
            self.send_error(413, "The application and its files exceed the size limit")
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            self.send_error(400, "The application form must be submitted as multipart data")
            return

        try:
            body = self.rfile.read(size)
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
            )
            fields, uploads = self.parse_form(message)
            self.validate_application(fields, uploads)
            self.save_application(fields, uploads)
        except (UnicodeError, ValueError) as error:
            self.send_error(400, str(error))
            return
        except OSError:
            self.send_error(500, "The application could not be saved")
            return

        self.send_response(303)
        self.send_header("Location", "/submitted")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def parse_form(self, message) -> tuple[dict[str, str], list[dict[str, object]]]:
        if not message.is_multipart():
            raise ValueError("The submitted form data is invalid")

        fields: dict[str, str] = {}
        uploads: list[dict[str, object]] = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                if not payload:
                    continue
                uploads.append(
                    {
                        "original_name": Path(filename.replace("\\", "/")).name,
                        "content": payload,
                    }
                )
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset).strip()
        return fields, uploads

    @staticmethod
    def validate_application(fields: dict[str, str], uploads: list[dict[str, object]]) -> None:
        missing = sorted(name for name in REQUIRED_FIELDS if not fields.get(name))
        if missing:
            raise ValueError("Complete all required application fields")
        if fields["declaration"] != "confirmed":
            raise ValueError("The applicant declaration must be accepted")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", fields["email"]):
            raise ValueError("Enter a valid email address")
        if not re.fullmatch(r"\d{4}-\d{2}", fields["preferred_intake"]):
            raise ValueError("Choose a valid preferred intake month")
        year, month = map(int, fields["preferred_intake"].split("-"))
        if year < 1 or not 1 <= month <= 12:
            raise ValueError("Choose a valid preferred intake month")
        for name, allowed in ALLOWED_VALUES.items():
            if fields.get(name, "") not in allowed:
                raise ValueError(f"Choose a valid {name.replace('_', ' ')}")
        if len(uploads) > MAX_FILES:
            raise ValueError(f"Upload no more than {MAX_FILES} files")
        for upload in uploads:
            filename = str(upload["original_name"])
            extension = Path(filename).suffix.lower()
            content = upload["content"]
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError("Documents must be PDF, JPG, or PNG files")
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("Each uploaded document must be 5 MB or smaller")

    def save_application(self, fields: dict[str, str], uploads: list[dict[str, object]]) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        file_records: list[dict[str, object]] = []
        try:
            for upload in uploads:
                extension = Path(str(upload["original_name"])).suffix.lower()
                stored_name = f"{uuid.uuid4().hex}{extension}"
                path = self.upload_dir / stored_name
                path.write_bytes(upload["content"])
                saved_paths.append(path)
                file_records.append(
                    {
                        "original_name": upload["original_name"],
                        "stored_name": stored_name,
                        "size": len(upload["content"]),
                    }
                )

            initialize_database(self.database_path)
            with database_connection(self.database_path) as connection:
                connection.execute(
                    "INSERT INTO applications (submitted_at, fields_json, files_json) VALUES (?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        json.dumps(fields, ensure_ascii=False),
                        json.dumps(file_records, ensure_ascii=False),
                    ),
                )
        except Exception:
            for path in saved_paths:
                path.unlink(missing_ok=True)
            raise

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(token, self.admin_password):
            return True
        self.send_json({"error": "Invalid staff password"}, status=401)
        return False

    def list_applications(self) -> None:
        initialize_database(self.database_path)
        with database_connection(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, submitted_at, fields_json, files_json FROM applications ORDER BY id DESC"
            ).fetchall()
        applications = [
            {
                "id": row["id"],
                "submitted_at": row["submitted_at"],
                "fields": json.loads(row["fields_json"]),
                "files": json.loads(row["files_json"]),
            }
            for row in rows
        ]
        self.send_json(applications)

    def download_file(self, stored_name: str) -> None:
        if not stored_name or Path(stored_name).name != stored_name:
            self.send_error(404, "Document not found")
            return
        initialize_database(self.database_path)
        with database_connection(self.database_path) as connection:
            rows = connection.execute("SELECT files_json FROM applications").fetchall()
        original_name = None
        for (files_json,) in rows:
            for file_record in json.loads(files_json):
                if file_record["stored_name"] == stored_name:
                    original_name = file_record["original_name"]
                    break
            if original_name:
                break
        path = self.upload_dir / stored_name
        if not original_name or not path.is_file():
            self.send_error(404, "Document not found")
            return

        content = path.read_bytes()
        content_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(original_name)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_file(self, path: Path, content_type: str) -> None:
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, "Page not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: object, status: int = 200) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        super().log_message(format, *args)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    database_path: Path = DATABASE_PATH,
    upload_dir: Path = UPLOAD_DIR,
    admin_password: str = "",
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredApplicationHandler",
        (ApplicationHandler,),
        {
            "database_path": Path(database_path),
            "upload_dir": Path(upload_dir),
            "admin_password": admin_password,
        },
    )
    initialize_database(Path(database_path))
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    password = os.environ.get("ICT_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
    if "ICT_ADMIN_PASSWORD" not in os.environ:
        print("Generated staff dashboard password: " + password, flush=True)
        print("Set ICT_ADMIN_PASSWORD in your environment to keep the same password after restart.", flush=True)

    port = int(os.environ.get("ICT_PORT", "8000"))
    server = create_server(port=port, admin_password=password)
    print(f"ICT applications: http://127.0.0.1:{port}", flush=True)
    print(f"Staff dashboard:  http://127.0.0.1:{port}/admin", flush=True)
    print(f"Database:         {DATABASE_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ICT application server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()