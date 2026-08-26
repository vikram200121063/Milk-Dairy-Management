# Milk Dairy Management System

**Live demo:** [https://milk-dairy-management-nqkb.onrender.com](https://milk-dairy-management-nqkb.onrender.com)
*(Hosted on Render's free tier — the app sleeps after 15 minutes of inactivity, so the first load after a while can take 30-60 seconds to wake up.)*

A Flask + MongoDB web application for a milk dairy to manage customers, record daily milk collection, calculate payments on a 10-day cycle, and generate invoices.

## Features

- Admin authentication (session-based, hashed passwords, no public registration)
- Customer management (add, search, edit, deactivate)
- Daily milk collection entry with duplicate prevention (one entry per customer/date/shift)
- Manual or automatic (Fat%/SNF%-based) rate calculation
- 10-day payment cycles with pending-balance carry-forward
- Printable and PDF invoices
- Dashboard with live statistics

## Tech Stack

- **Backend:** Python, Flask (application factory + blueprints)
- **Database:** MongoDB (MongoDB Atlas free tier)
- **Frontend:** Bootstrap 5, vanilla JS
- **PDF generation:** ReportLab
- **Production server:** Gunicorn

## Project Structure

```text
milk-dairy-management/
├── app/
│   ├── __init__.py          # Application factory, DB connection, blueprint registration
│   ├── routes/               # One blueprint per feature area
│   ├── services/              # Business logic (rate calc, payment cycles, invoices)
│   ├── templates/
│   ├── static/
│   └── utils/                 # Shared helpers (decorators, ID generator)
├── config.py                  # Reads all configuration from environment variables
├── requirements.txt
├── run.py                     # Entry point / WSGI target (run:app)
├── .env.example
└── README.md
```

## Local Setup

1. Create a [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) free (M0) cluster, a database user, and allow network access from your IP (or `0.0.0.0/0` for development).
2. Clone this repo and set up a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in `MONGO_URI`, `SECRET_KEY`, and the `DAIRY_*` details.
4. Create your admin account:
   ```bash
   export FLASK_APP=run.py       # Windows (cmd): set FLASK_APP=run.py
   flask create-admin youradmin
   ```
5. Run it:
   ```bash
   python run.py
   ```
   Visit `http://127.0.0.1:5000`.

## Environment Variables

| Variable | Description |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name (default `milk_dairy`) |
| `SECRET_KEY` | Random secret for signing session cookies |
| `FLASK_DEBUG` | `True` locally, `False` in production |
| `DAIRY_NAME`, `DAIRY_ADDRESS`, `DAIRY_CONTACT` | Shown on invoices |

## Deployment

Deployed on **[Render](https://render.com)** (free Web Service tier) with continuous deployment from this GitHub repo's `main` branch — every push to `main` triggers an automatic rebuild and redeploy.

- **Live URL:** https://milk-dairy-management-nqkb.onrender.com
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn run:app`
- **Database:** MongoDB Atlas free (M0) cluster — the same cluster used for local development, configured via the `MONGO_URI` environment variable in Render's dashboard (never committed to the repo)

### Free tier trade-off

Render's free instances spin down after 15 minutes without traffic. The next visit after that wakes it back up automatically, but takes 30-60 seconds before the page responds. This is expected behavior, not a bug — there's no data loss or downtime, just a one-time delay per idle period.

### Managing the deployed app

There's no public registration or password-reset page by design (security decision from Phase 2). Admin accounts are managed from the terminal, pointed at the same Atlas cluster the deployed app uses:

```bash
export FLASK_APP=run.py          # Windows (cmd): set FLASK_APP=run.py
flask create-admin <username>    # create a new admin account
flask reset-password <username>  # reset an existing admin's password
```

Production entry point for any WSGI host: **`run:app`** (i.e. `gunicorn run:app`).
