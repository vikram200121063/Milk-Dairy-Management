# Milk Dairy Management System

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

See the deployment notes shared alongside this project for step-by-step Azure App Service (free F1 tier) instructions, including production configuration and a Render.com fallback if the Azure free tier's daily CPU quota becomes limiting.

Production entry point for any WSGI host: **`run:app`** (i.e. `gunicorn run:app`).
