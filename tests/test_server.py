import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


def multipart_form(fields, files=()):
    boundary = "----ict-application-test-boundary"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    for name, filename, content_type, content in files:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b""
    for part in parts:
        body += part.encode() if isinstance(part, str) else part
    return boundary, body


class ApplicationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.server = server.create_server(
            port=0,
            database_path=root / "applications.sqlite3",
            upload_dir=root / "uploads",
            admin_password="test-staff-password",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    def test_submission_is_saved_and_retrieved_with_protected_document(self):
        fields = {
            "first_name": "Avery",
            "last_name": "Applicant",
            "date_of_birth": "2002-05-14",
            "nationality": "Example",
            "email": "avery@example.org",
            "phone": "+15551234567",
            "street_address": "1 Sample Street",
            "city": "Sampletown",
            "country": "Exampleland",
            "emergency_contact_name": "Casey Contact",
            "emergency_contact_phone": "+15557654321",
            "emergency_contact_relationship": "Parent",
            "programme": "certificate",
            "area_of_interest": "software-development",
            "study_mode": "online",
            "preferred_intake": "2027-01",
            "preferred_schedule": "evening",
            "highest_education": "secondary",
            "experience_level": "none",
            "study_device": "laptop",
            "motivation": "I want to learn software development.",
            "declaration": "confirmed",
            "applicant_signature": "Avery Applicant",
            "application_date": "2026-10-01",
        }
        boundary, body = multipart_form(
            fields,
            [("documents", "results.pdf", "application/pdf", b"%PDF-test-document")],
        )
        request = Request(
            f"{self.base_url}/applications",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Application received", response.read())

        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(f"{self.base_url}/api/applications")
        self.assertEqual(unauthorized.exception.code, 401)

        request = Request(
            f"{self.base_url}/api/applications",
            headers={"Authorization": "Bearer test-staff-password"},
        )
        with urlopen(request) as response:
            applications = json.loads(response.read())
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["fields"]["email"], "avery@example.org")
        self.assertEqual(applications[0]["files"][0]["original_name"], "results.pdf")

        stored_name = applications[0]["files"][0]["stored_name"]
        request = Request(
            f"{self.base_url}/api/files/{stored_name}",
            headers={"Authorization": "Bearer test-staff-password"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.read(), b"%PDF-test-document")

    def test_missing_required_fields_are_rejected(self):
        boundary, body = multipart_form({"first_name": "Avery"})
        request = Request(
            f"{self.base_url}/applications",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as invalid:
            urlopen(request)
        self.assertEqual(invalid.exception.code, 400)


if __name__ == "__main__":
    unittest.main()