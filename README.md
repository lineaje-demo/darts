# Darts

Darts is a small expense-reporting web app.

- **Employees** submit expenses with a receipt (photo/PDF upload, an e-receipt
  link, or a zip of receipts after a trip).
- **Managers** approve or reject their team's expenses and can ask for more detail.
- **Finance** sets the expense policy (rules that auto-approve or flag
  expenses), exports to Excel, and builds the month-end pack for the auditors.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:8080 and pick a persona:

| Persona | Role | Try |
|---------|------|-----|
| Alice Chen | Employee | Submit an expense, search, upload a zip of receipts |
| Bob Rivera | Manager | Approvals, "Ask for more detail" |
| Carol Diaz | Finance | Policy rules, Reports, CSV export, month-end pack |
| Dan Okafor | Employee | Has a flagged travel expense |

The app starts with demo data. Delete the `instance/` folder to reset it.

Docker:

```bash
docker build -t darts .
docker run --rm -p 8080:8080 darts
```

## Layout

```
app.py          app setup and entry point
config.py       settings
routes/         one module per page: auth, expenses, approvals, policy, reports
services/       business logic: access rules, receipts, policy engine, reminders, reports
lib/            plumbing: database, sign-in helpers, CSRF, template filters
templates/      HTML pages
static/         CSS
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DARTS_DATA_DIR`   | `./instance` | Database, receipts and reports |
| `DARTS_SECRET_KEY` | random per process | Session signing key |
| `DARTS_SETTINGS`   | unset | Optional YAML settings file |
| `DARTS_HOST` / `DARTS_PORT` | `127.0.0.1` / `8080` | Bind address |
