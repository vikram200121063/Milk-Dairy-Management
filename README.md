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
- Branded, printable and PDF invoices with your dairy's logo, a colored header,
  and a status badge (Paid / Partially Paid / Pending)
- Dashboard with live statistics
- **Customer self-service portal** (`/portal`): customers create their own login using
  the mobile number already on file, then view their daily milk records, current-cycle
  totals, pending balance, and past payment history/invoices (view, print, download PDF)
- **Email notifications**: email a customer their milk-collection record for a
  single entry, or email every entry for a given date + shift in one click (defaults to
  today and a shift guessed from the current time). 10-day invoices can be emailed
  one at a time or for a whole cycle at once, with the PDF invoice attached. A
  Notification Log page shows exactly what was sent, to whom, and whether it succeeded.
- **Buyer Management & Accounting module** (`/buyers`, `/buyer-sales`, `/buyer-invoices`,
  `/expenses`, `/accounting`): manage large buyers/companies, record milk sales to them,
  generate consolidated invoices every 15 days, track partial/multiple payments, run an
  automated dunning cycle (overdue → reminder → grace period → late interest), manage
  business expenses, and view a Profit & Loss / Cash Flow dashboard. See its own section
  below.
- **Import milk entries from a milk analyzer machine** (`/milk-entries/import`): upload
  the CSV/Excel file your milk testing machine's software exports (Fat%, SNF%, quantity
  per sample) instead of typing each reading in by hand. See its own section below.
- **Role-based access (Owner / Staff) with a "Manage Staff" screen**: give
  day-to-day helpers a login that can record customers and milk entries but
  can't see revenue, buyer/accounting data, or payment records - add,
  promote/demote, reset the password for, or delete accounts entirely from
  the UI (Profile menu → Manage Staff), no terminal access needed. See its
  own section below.
- **CSV export**: download the Customers, Milk Entries, or Buyer Invoices list as a
  CSV file (respects whatever search/filter is currently applied), via the
  "⬇ Download CSV" button on each list page.
- **Razorpay payment webhook**: an optional server-to-server backstop so a payment
  still gets credited even if the customer/buyer's browser never makes it back to
  the app after paying. See its own section below.
- **Automated test suite** (`tests/`, run with `pytest`): covers the milk machine
  import, roles, CSV export, guest mode, and the payment webhook end-to-end. Free
  to run - no real database or Razorpay account needed. See "Running Tests" below.

### Customer Portal Notes

- A customer must already exist as a record (added by the admin) before they can
  register a portal login — registration "claims" that record via mobile number match,
  it does not create a new customer.
- Customer sessions (`session['customer_id']`) are completely separate from admin
  sessions (`session['user_id']`), so the two logins never interfere with each other.
- Customers can only ever see their own milk entries and payments — every portal
  route filters by the logged-in customer's `customer_id` and every invoice route
  double-checks ownership before rendering.

### Invoice Branding

- Both the on-screen invoice and the downloadable PDF use the dairy's logo at
  `app/static/img/logo-mark.svg`. To use your own logo, just replace that file
  (SVG works best; PNG/JPG also work - drop a `logo.png` or `logo-mark.png` in
  the same folder and the PDF will pick it up automatically, see
  `_find_logo_path()` in `app/services/invoice_service.py`).
- If no logo file is found, invoices still generate fine - the header just
  falls back to text-only.

### Email Notification Notes

- Notifications go out over plain SMTP - works with Gmail (use an App Password) or
  any other provider. Set `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` in `.env`.
- Customers need an `email` on their profile (optional field on the customer form)
  to receive anything; customers without one are silently skipped and noted as such
  in the Notification Log rather than treated as an error.
- A free Gmail account tops out around 100 emails/day sent via SMTP - fine for a
  single dairy's daily volume, but if you outgrow it, point the same SMTP settings
  at a transactional email service (SendGrid, Amazon SES, Mailgun, etc.) instead.
- Every send attempt (success or failure) is logged to the `notifications`
  collection and viewable at **Notifications** in the admin nav, so nothing is a
  silent no-op.

## Milk Machine Data Import

Milk analyzer machines (Lactoscan, EKOMILK, Milkotester, and similar) measure Fat%,
SNF%, and volume per sample and let their bundled software export the day's readings
as a CSV or Excel file. This app can't talk to the machine directly (it runs on a
cloud server, not next to the machine in your dairy), so the supported workflow is:
export from the machine's software, then upload that file at **Milk Entries → Import
from Machine**.

### One-time setup

Open each customer's record (**Customers → Edit**) and set their **Milk Machine
Code** to match the Card No / sample ID your machine assigns them. This is how an
imported row gets matched to the right customer - the machine has no idea of names,
only numeric codes.

### Importing

1. Export the day's (or week's) readings from your machine's software as `.csv` or
   `.xlsx`. Only **Machine Code**, **Fat%**, **SNF%**, and **Quantity** are required
   in the file - download the template from the import page to see the expected
   columns and their accepted alternate header names (e.g. "Card No" also works for
   Machine Code).
2. If the file doesn't include a Date, Shift, or Milk Type column (many machines
   don't log all three), pick defaults for the whole batch on the upload form.
3. **Nothing is saved yet.** The next screen shows every row with a status - Ready,
   Duplicate (an entry already exists for that customer/date/shift), Unknown Code (no
   customer has that machine code), or Invalid (bad/missing data) - so you can review
   before committing. Uncheck any row you don't want, then confirm.
4. Rates are always calculated automatically from Fat%/SNF% using your current Rate
   Configuration - imported entries are priced exactly like a manual "Automatic"
   entry, and the rate is recomputed server-side at import time rather than trusted
   from the file.
5. Every import (who, when, how many added/skipped) is recorded and shown on the
   import page, the same audit-log pattern used for email notifications.

This feature is entirely free - it uses only the standard library (for `.csv`) and
`openpyxl` (for `.xlsx`), both open-source with no account or API key required.

## Roles & Staff Access

Two roles:

- **Owner** (default for every existing/legacy account) - full access to everything:
  customers, milk entries, buyers, buyer sales/invoices, expenses, the accounting
  dashboard, rate configuration, payments, notifications, the AI assistant, and
  Manage Staff (below).
- **Staff** - can add/edit customers and record milk entries (including the machine
  import), and can see the "Today's Milk Collection" dashboard card and the milk
  collection chart. Everything money-related is hidden, not just visually - revenue
  figures, the expense/revenue chart, accounts receivable/payable, buyer &
  accounting screens, payments, and rate configuration are stripped out before the
  page is even rendered, so there's no page-source trick that leaks them. Staff
  attempting to open a restricted page directly is redirected back to the dashboard
  with a message; a restricted AJAX call gets a plain 403.

### Managing accounts from the app (no terminal needed)

Log in as an Owner and open your profile menu (top right) → **Manage Staff**
(`/users`). From there you can, entirely from the UI:

- **Add an account** - pick a username, password, and role (Owner or Staff).
- **Reset any account's password.**
- **Promote/demote** an account between Owner and Staff.
- **Delete** an account.

The only two things it won't let you do to yourself are change your own role
or delete your own account - both would risk locking you out, and there's
always at least one other Owner (or the terminal, below) if you ever need to
undo something about your own login.

### The CLI commands (only needed to create the very first account)

The very first Owner account has to be created from the terminal, since
there's no one logged in yet to open Manage Staff with:

```bash
export FLASK_APP=run.py          # Windows (cmd): set FLASK_APP=run.py
flask create-admin <username>    # you'll be prompted for a password
```

`flask create-staff <username>` and `flask reset-password <username>` also
still work the same way, as a fallback if you ever need to manage accounts
without logging in (e.g. you're locked out of every Owner account) - but
day-to-day, Manage Staff above is the normal way to do all of this.

## Buyer Management & Accounting Module

A second, independent business flow layered on top of the customer side, for
selling collected milk onward to large buyers/companies and tracking the
dairy's overall financial performance. Nothing in this module changes how
Customer Milk Collection & Payment works - it reads from `milk_entries` and
`payments` for accounting figures, but never writes to them.

**Business flow:** Customer delivers milk → *(existing)* dairy records the
entry and calculates what's owed to the customer → dairy sells milk onward to
a buyer → a consolidated buyer invoice is generated every 15 days → the
buyer's payment(s) are tracked → if unpaid past the due date, a reminder is
sent, a grace period runs, and late interest accrues on what's still owed →
all of this feeds a Profit & Loss / Cash Flow dashboard.

- **Buyers** (`/buyers`) - company/name, contact person, mobile, email,
  address, optional GST number, payment terms (days), credit limit, and
  active/inactive status. Mirrors the Customers screen.
- **Buyer Sales** (`/buyer-sales`) - one row per milk sale to a buyer: date,
  milk type/quality, quantity, rate/litre, tax %, other charges, and the
  computed total. Not tied to an invoice until one is generated (same pattern
  as `milk_entries` and the 10-day payment cycle).
- **Buyer Invoices** (`/buyer-invoices`) - "Generate Invoices" consolidates
  every sale to each buyer within a half-month period (1st-15th / 16th-end)
  into one invoice, safe to re-run anytime (it recalculates totals from the
  underlying sales but never erases a payment or interest charge already
  applied). Each invoice supports multiple partial payments, each recorded as
  its own document with date/method/reference - a full transaction history,
  not just a running total.
- **Dunning** - due date passes → invoice marked **Overdue** and overdue days
  are counted → a polite reminder email is sent automatically (also
  available as a manual "Send Reminder" button) → after a configurable grace
  period, simple interest starts accruing daily on the remaining balance →
  every reminder and every interest charge is logged (Dunning Log, and the
  Interest History table on each invoice), and an admin can waive or
  partially adjust any interest charge with a note. Run the whole cycle
  on-demand with the "Run Dunning Check" button, or automate it with:
  ```bash
  flask process-dunning
  ```
  (there's no background job runner built in, so wire this command up to a
  daily cron job / scheduled task for hands-off operation).
- **Expenses & Other Income** (`/expenses`) - Transportation/Fuel, Salary,
  Electricity, Maintenance, Packaging, Other, plus an "Other Income" entry
  type for the Revenue formula below (kept in the same collection with an
  `entry_type` field rather than a separate one).
- **Accounting Dashboard** (`/accounting`) - Today / This Week / This Month /
  Custom Range reporting:
  - **Profit & Loss** (accrual basis): Revenue (milk sold to buyers, pre-tax,
    + other income) − Milk Purchase Cost (what's owed to customers) = Gross
    Profit; minus Other Expenses and net Interest/Late Charges = Net
    Profit/Loss.
  - **Cash Flow** (cash basis, kept deliberately separate from P&L): Cash
    Received (buyer payments + other income) − Cash Paid (to customers +
    expenses) = Net Cash Flow. A buyer settling an already-invoiced balance
    is a cash event, not new revenue; paying a customer for milk already
    collected is a cash event, not a new expense - both were already booked
    when the sale/purchase happened.
  - **Snapshot** (as of today, independent of the selected range): Buyer
    Outstanding Receivables, Customer Outstanding Payables, Overdue Invoices,
    and Interest Outstanding.
  - **Financial Settings** (`/accounting/settings`) - default payment terms,
    grace period (days), annual interest rate, and default tax % on new
    sales.

### Notes & known trade-offs

- "Buyer Portal" was built as an **admin-side management module** (like the
  existing Customers/Payments screens), not a buyer-facing self-login portal
  like `/portal` for customers. Let us know if buyer self-service login is
  also wanted - it would follow the same pattern as `customer_portal.py`.
- **Cash Paid to Customers** on the Accounting Dashboard is an approximation:
  the existing `payments` collection (left untouched, on purpose) stores only
  the most recent payment date/amount per 10-day cycle rather than a full
  transaction ledger. For a cycle paid in one transaction (the common case)
  this is exact; a cycle paid in several installments will only reflect the
  latest one in the cash-flow date range. Buyer payments, by contrast, do
  have a full per-transaction ledger (`buyer_payments`).
- Interest is simple (non-compounding) interest, but is recalculated
  incrementally each time the dunning cycle runs, on the *current* remaining
  balance (which may already include earlier interest) - directly matching
  the requirement that interest applies "on the remaining unpaid balance."
  Running the dunning check twice on the same day never double-charges.

### New Collections

`buyers`, `buyer_sales`, `buyer_invoices`, `buyer_payments`, `expenses`
(also holds Other Income via `entry_type`), `dunning_records`,
`interest_charges`, `finance_settings` - all additive; no existing
collection's schema changed.

## AI Features

Everything here is powered by `app/services/ai_service.py` and is entirely optional:
leave the active provider's API key blank in `.env` and the app behaves exactly as
it did before - no crashes, no dead buttons, just a "not configured" message
wherever an AI control would otherwise appear.

### Choosing a provider

Pick one with `AI_PROVIDER` in `.env` and set that provider's key - nothing else
needs to change, the four features below work the same regardless of which you pick
(with one exception noted below):

| `AI_PROVIDER` | Cost | Get a key | Notes |
|---|---|---|---|
| `gemini` | **Free tier** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Supports everything below, including the receipt scanner. Best free option. |
| `groq` | **Free tier** | [console.groq.com/keys](https://console.groq.com/keys) | Very fast; runs open models (Llama, etc). Receipt scanning isn't available on it (no reliable vision + tool-calling support). |
| `anthropic` | Paid, no free tier | [console.anthropic.com](https://console.anthropic.com/) | Claude. |
| `openai` | Paid, no free tier | [platform.openai.com](https://platform.openai.com/) | GPT. |

`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`/`GROQ_API_KEY` and their matching
`*_MODEL` settings all live in `.env` - see `.env.example`. You only need to fill in
the one matching whatever `AI_PROVIDER` you chose.

### The four features

- **Business Assistant** - a floating chat widget on every admin page (bottom
  right) that answers plain-language questions about your own data - *"Which
  buyers are overdue?"*, *"How's revenue this month?"*, *"Who supplied the most
  milk this week?"*. It only ever reads through a small, fixed set of safe
  lookup functions (customer/buyer summaries, revenue & expense figures,
  overdue invoices, pending payments) - it can't run arbitrary queries or
  change any data.
- **AI-drafted dunning reminders** - when the automated dunning cycle
  (`flask process-dunning` / "Run Dunning Check") sends a payment reminder to
  an overdue buyer, the AI drafts the message paragraph itself, tuned in tone
  to that buyer's history (a warm first nudge vs. a firmer repeat-offender
  message). If AI isn't configured, or the call fails for any reason, it
  silently falls back to the original static wording - a reminder always goes
  out either way.
- **AI Profit & Loss summary** - an "AI Summary" button on the Accounting
  dashboard turns the currently-selected period's figures into a 3-5 sentence
  plain-English readout. `flask send-pl-summary` emails the same thing for the
  current month to `ADMIN_NOTIFICATION_EMAIL` - wire it up to a monthly cron
  job / scheduled task the same way as `process-dunning`.
- **Receipt photo auto-fill** - on the Add/Edit Expense form, upload a photo of
  a receipt and the AI's vision reads it and pre-fills amount, date,
  description, and category. It never saves anything itself - you still review
  and submit the form. **Not available when `AI_PROVIDER=groq`** (see table above).

## Online Payments (Razorpay)

Like the AI features, this is entirely optional: leave `RAZORPAY_KEY_ID` /
`RAZORPAY_KEY_SECRET` blank in `.env` and the app behaves exactly as before -
every "Pay Now" button and payment link simply doesn't appear, and the
existing manual "record a payment" forms keep working untouched.

### Getting free test keys

1. Sign up at [dashboard.razorpay.com](https://dashboard.razorpay.com/signup) (free, no KYC needed to use test mode).
2. Make sure you're in **Test Mode** (toggle top-left of the dashboard).
3. Go to **Settings → API Keys → Generate Test Key**, and copy the Key ID and Key Secret into `.env` (or Render's environment variables):
   ```
   RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
   RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
   ```
4. No charges ever happen in test mode - it's free to use for as long as you like. Switching to real payments later only means generating **live** keys instead; no code changes are needed.

### Where it shows up

- **Customer portal → My Payments** - a "Pay Now" button appears next to any
  payment cycle with a remaining balance, opening Razorpay's checkout.
- **Buyer Invoices → invoice detail** - a "🔗 Copy Payment Link" button
  generates a signed, shareable link (valid 30 days, no login required) that
  a buyer can open to pay their outstanding invoice online.

### Trying it out (test mode)

Use Razorpay's standard test values - no real money moves:

- **Test card:** `4111 1111 1111 1111`, any future expiry, any CVV, any OTP
- **Test UPI ID:** `success@razorpay`

### How it works under the hood

`app/services/payment_gateway_service.py` wraps Razorpay's Orders API. The
amount charged is always computed server-side from the payment/invoice
record in MongoDB (never trusted from the browser), and every "successful"
payment is verified against Razorpay's HMAC signature before the database is
updated - so a tampered or forged confirmation can't mark an invoice as paid.

### Payment webhook (optional backstop)

Normally, the customer/buyer's browser calls back into the app right after
paying to confirm and credit the payment. If that browser closes, loses its
connection, or hits a JS error before that call fires, the charge succeeded
on Razorpay's side but nothing here would ever know - the webhook exists so
that doesn't happen.

1. In the Razorpay Dashboard, go to **Settings → Webhooks → Add New Webhook**.
2. Set the URL to `https://<your-app-domain>/pay/webhook` and subscribe to at
   least the **`payment.captured`** event.
3. Razorpay shows you a webhook secret - put it in `.env` (or Render's
   environment variables) as `RAZORPAY_WEBHOOK_SECRET`. This is a different
   value from `RAZORPAY_KEY_SECRET`.
4. That's it - leave it blank and everything still works exactly as before
   (browser-only confirmation); set it and the webhook becomes a second,
   independent way for the same payment to get credited.

Whichever of the browser callback or the webhook reaches the app first for a
given Razorpay payment actually credits it; the other is a harmless no-op -
so a payment can never be credited twice even if both fire, and is never
missed if only one does. The webhook also verifies Razorpay's signature on
every request (using `RAZORPAY_WEBHOOK_SECRET`) and rejects anything that
doesn't check out, so it can't be spoofed by a random POST to that URL.

## Tech Stack

- **Backend:** Python, Flask (application factory + blueprints)
- **Database:** MongoDB (MongoDB Atlas free tier)
- **Frontend:** Bootstrap 5, vanilla JS
- **PDF generation:** ReportLab
- **AI features:** Anthropic / OpenAI / Google Gemini / Groq (pick one) - optional, see [AI Features](#ai-features)
- **Online payments:** Razorpay (test/sandbox mode, free) - optional, see [Online Payments](#online-payments-razorpay)
- **Production server:** Gunicorn
- **Testing:** pytest, with a free in-memory MongoDB/Razorpay test double (no real database or account needed) - see [Running Tests](#running-tests)

## Project Structure

```text
milk-dairy-management/
├── app/
│   ├── __init__.py          # Application factory, DB connection, blueprint registration
│   ├── routes/               # One blueprint per feature area (customers, buyers, accounting, ...)
│   ├── services/              # Business logic (rate calc, payment cycles, invoices, dunning, accounting)
│   ├── templates/
│   ├── static/
│   └── utils/                 # Shared helpers (decorators, ID generator)
├── tests/                     # pytest suite - see "Running Tests" below
├── config.py                  # Reads all configuration from environment variables
├── requirements.txt
├── requirements-dev.txt       # Extra packages needed only to run tests/
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

## Running Tests

The `tests/` directory has an automated `pytest` suite covering the
Milk Machine Import, Owner/Staff roles, CSV export, the Guest/Read-Only
Demo, and the Razorpay webhook. It's completely free to run - no real
MongoDB, no real Razorpay account, and no network access are needed:
`tests/fake_mongo.py` is a small in-memory stand-in for MongoDB (stdlib
only) and `tests/_fakes/razorpay` is a stand-in for the Razorpay SDK, both
swapped in automatically by `tests/conftest.py` for the duration of the
test run only - the real app never uses either one.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Every test gets its own fresh, empty in-memory database (see
`tests/conftest.py`'s `app`/`db`/`client` fixtures), so tests never
interfere with each other and can run in any order. Add new tests as
`tests/test_*.py` files using those same fixtures.

## Environment Variables

| Variable | Description |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name (default `milk_dairy`) |
| `SECRET_KEY` | Random secret for signing session cookies |
| `FLASK_DEBUG` | `True` locally, `False` in production |
| `DAIRY_NAME`, `DAIRY_ADDRESS`, `DAIRY_CONTACT` | Shown on invoices |
| `AI_PROVIDER` | Which AI provider powers the 4 AI features - `anthropic`, `openai`, `gemini`, or `groq` (see [AI Features](#ai-features)) |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Used when `AI_PROVIDER=anthropic` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | Used when `AI_PROVIDER=openai` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Used when `AI_PROVIDER=gemini` |
| `GROQ_API_KEY` / `GROQ_MODEL` | Used when `AI_PROVIDER=groq` |
| `ADMIN_NOTIFICATION_EMAIL` | Where `flask send-pl-summary` emails the monthly AI P&L narrative |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Enables online "Pay Now" (customer portal + buyer invoice links) - see [Online Payments](#online-payments-razorpay). Optional, free in test mode. |
| `RAZORPAY_WEBHOOK_SECRET` | Enables the optional payment webhook backstop - see [Payment webhook](#payment-webhook-optional-backstop). Optional. |

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
