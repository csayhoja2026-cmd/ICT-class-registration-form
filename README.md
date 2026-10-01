# ICT Class Application

This project uses Python's standard library and SQLite to save applications on the computer running the server. No package installation is required.

## Start the application

From this folder, run:

```powershell
python server.py
```

Open the application at <http://127.0.0.1:8000>. The server prints a generated staff dashboard password on startup; open <http://127.0.0.1:8000/admin> and enter it to review applications. The generated password changes each time the server restarts.

To choose a persistent password in PowerShell before starting the server:

```powershell
$env:ICT_ADMIN_PASSWORD = "replace-this-with-a-long-unique-password"
python server.py
```

Stop the server with `Ctrl+C`. To use another local port, set `$env:ICT_PORT` before starting it.

## Stored data

Application records are stored in `data/applications.sqlite3`; uploaded PDF and image files are stored in `data/uploads/`. Both are created on first startup and excluded from Git. The staff dashboard and document downloads require the staff password. Back up the database and uploads folder together.

The database and uploaded documents are not encrypted at rest. Protect access to this Windows account and any backups containing applicant information.

The server listens on `127.0.0.1` only, so it is available on this computer, not publicly online. Hosting applications for remote applicants requires a deployed server, HTTPS, persistent protected storage, and production-grade staff authentication and backups. Do not put real applicant records in a public repository.

## Tests

Run the end-to-end storage and access-control checks with:

```powershell
python -m unittest discover -s tests
```