# OM Dairy — Code Walkthrough

A complete guide to how this application is built, what every file does, and
how the pieces connect to each other.

---

## 1. The big picture

This is a **Flask + MongoDB** web application for running a milk dairy business.
It handles two opposite sides of the same trade:

| Side | Who | Direction of money | Accounting meaning |
|---|---|---|---|
| **Customer** | Farmers who *supply* milk to the dairy | Dairy **pays** them | Expense + Accounts **Payable** |
| **Buyer** | Shops/hotels who *buy* milk from the dairy | They **pay** the dairy | Revenue + Accounts **Receivable** |

Everything else in the app — invoices, payments, reminders, reports — hangs off
those two flows, and a central **Accounting module** reads both.

### The layered architecture

The code is organised in four layers. Requests flow down; data flows back up.

```
   Browser
      │
      ▼
┌──────────────────────────────────────────────────────┐
│  ROUTES        app/routes/*.py                       │  ← HTTP: read the form,
│  "the waiters"                                       │    validate, redirect,
│                                                      │    pick a template
└──────────────────────────────────────────────────────┘
      │ calls
      ▼
┌──────────────────────────────────────────────────────┐
│  SERVICES      app/services/*.py                     │  ← Business logic:
│  "the kitchen"                                       │    rates, invoices,
│                                                      │    interest, the ledger
└──────────────────────────────────────────────────────┘
      │ reads/writes
      ▼
┌──────────────────────────────────────────────────────┐
│  DATABASE      MongoDB collections                   │  ← Plain documents
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  TEMPLATES     app/templates/*.html                  │  ← Jinja2 HTML,
│  + STATIC      app/static/{css,js}                   │    CSS, JavaScript
└──────────────────────────────────────────────────────┘
```

**The rule that keeps this clean:** routes never contain business logic, and
services never know about HTTP. That's why the same `payment_service` can be
called from a web route today and a scheduled job tomorrow without changes.

---

## 2. Boot sequence — what happens when the app starts

### `run.py` — the front door

```python
from app import create_app
app = create_app()
```

Tiny by design. It just asks the factory for an app and runs it. In production
you'd point Gunicorn at this same object instead.

### `config.py` — settings

Reads environment variables (via `.env`) into a `Config` class:

- `SECRET_KEY` — signs the session cookie. **Must** be secret in production.
- `MONGO_URI` / `MONGO_DB_NAME` — where the database lives.
- `MAX_CONTENT_LENGTH` — caps uploads at 8 MB (profile photos).
- SMTP settings — for sending email.

### `app/__init__.py` — the application factory

This is the **wiring hub of the entire project**. Read this file first when you
come back to the code. In order, `create_app()`:

1. **Creates the Flask app** and loads `Config`.
2. **Connects to MongoDB** and attaches the handle as `app.db`. Every route
   afterwards reaches the database through `current_app.db` — there's no
   global connection floating around.
3. **Creates indexes** for every collection. Two kinds matter:
   - *Unique* indexes (`buyer_id`, `sale_id`, `invoice_id`…) — the database
     itself refuses duplicates, so a double-submitted form can't create two
     records.
   - *Performance* indexes (`date`, `status`, `buyer_id`…) — make the reports
     fast as data grows.
4. **Hardens the session cookie** — `HTTPONLY` (JavaScript can't steal it),
   `SAMESITE=Lax` (CSRF protection), `SECURE` (HTTPS-only outside debug).
5. **Registers every blueprint** — this is what maps URLs to code.
6. **Registers the `backfill-ledger` CLI command** (explained in §6).

### Blueprints = URL prefixes

A *blueprint* is Flask's way of grouping related routes. Each file in
`app/routes/` defines one:

| Blueprint | URL prefix | File |
|---|---|---|
| `auth` | `/login`, `/logout` | `auth.py` |
| `dashboard` | `/dashboard` | `dashboard.py` |
| `customers` | `/customers` | `customers.py` |
| `milk_entries` | `/milk-entries` | `milk_entries.py` |
| `rate_config` | `/rate-config` | `rate_config.py` |
| `payments` | `/payments` | `payments.py` |
| `notifications` | `/notifications` | `notifications.py` |
| `buyers` | `/buyers` | `buyers.py` |
| `buyer_sales` | `/buyer-sales` | `buyer_sales.py` |
| `buyer_invoices` | `/buyer-invoices` | `buyer_invoices.py` |
| `expenses` | `/expenses` | `expenses.py` |
| `accounting` | `/accounting` | `accounting.py` |
| `customer_portal` | `/portal` | `customer_portal.py` |
| `about` | `/about` | `about.py` |
| `profile` | `/profile` | `profile.py` |

So `@customers_bp.route("/add")` becomes the URL **`/customers/add`**.

---

## 3. How one request travels through the system

Take a concrete example: **an operator records 12.5 L of milk from a farmer.**

```
1. BROWSER    Operator submits the form at /milk-entries/add
                  │
2. ROUTE      app/routes/milk_entries.py → add_entry()
              • Reads request.form
              • Validates: quantity > 0? customer exists? not a duplicate
                shift for that date?
                  │
3. SERVICE    app/services/rate_service.py → calculate_rate()
              • Looks up rate_configurations for "Cow"
              • Applies base rate + FAT premium + SNF premium
              → returns ₹40.00 per litre
                  │
4. WRITE      total_amount = 12.5 × 40.00 = ₹500.00
              db.milk_entries.insert_one({...})
                  │
5. LEDGER     app/services/ledger_service.py → post_milk_purchase()
              Posts the double-entry pair automatically:
                  Dr  Milk Purchase Expense   ₹500
                  Cr  Accounts Payable        ₹500
                  │
6. NOTIFY     app/services/notification_service.py → notify_milk_entry()
              Emails the farmer a receipt, logs the attempt
                  │
7. RESPONSE   flash("Milk entry recorded…") → redirect to the list page
```

Step 5 is the important one: **the operator never thinks about accounting**, but
the books update themselves. That single design decision is what makes the whole
Accounting module possible.

---

## 4. The database — 15 collections

MongoDB is *schemaless*, so these "schemas" are conventions the code maintains,
not rules the database enforces (except unique indexes).

### Customer side

| Collection | Holds | Key fields |
|---|---|---|
| `customers` | Farmers who supply milk | `customer_id` (CUST0001), `name`, `village`, `status` |
| `milk_entries` | Each collection event | `customer_id`, `date`, `shift`, `quantity`, `fat_percentage`, `snf_percentage`, `rate_per_litre`, `total_amount` |
| `rate_configurations` | Pricing rules per milk type | `milk_type`, base rate, FAT/SNF increments |
| `payments` | 10-day payout cycles | `payment_id`, `payment_period`, `gross_amount`, `deductions`, `amount_paid`, `remaining_amount`, `payment_status` |

### Buyer side

| Collection | Holds | Key fields |
|---|---|---|
| `buyers` | Companies buying milk | `buyer_id` (BYR0001), `company_name`, `payment_terms_days`, `credit_limit` |
| `buyer_sales` | Each bulk sale | `sale_id`, `buyer_id`, `sale_date`, `quantity`, `rate_per_litre`, `tax_amount`, `total_amount` |
| `buyer_invoices` | Half-month consolidated bills | `invoice_id`, `billing_period`, `due_date`, `total_amount`, `amount_paid`, `remaining_amount`, `status` |
| `buyer_payments` | Payments received | `payment_id`, `invoice_id`, `amount`, `payment_date` |
| `interest_charges` | Late-payment interest | `charge_id`, `interest_amount`, `waived_amount` |
| `dunning_records` | Reminder audit trail | `invoice_id`, `record_type`, `sent_at` |

### Shared

| Collection | Holds |
|---|---|
| `expenses` | Manual expense/income entries (fuel, repairs, scrap sales) |
| `notifications` | Log of every email attempt |
| **`ledger_entries`** | **The general ledger — see §5** |
| `users` | Admin logins (+ `profile_photo`) |
| `counters` | Auto-increment sequences for human-readable IDs |

### A note on IDs

`app/utils/id_generator.py` provides `get_next_sequence()`. MongoDB's native
`_id` is unique but ugly (`507f1f77bcf86cd799439011`). For anything a human will
read or type — customer IDs, invoice numbers — the code keeps its own counters
so you get `CUST0001`, `BINV00042`. It uses MongoDB's atomic `$inc`, so two
simultaneous requests can never receive the same number.

---

## 5. The general ledger — the heart of the accounting module

**File: `app/services/ledger_service.py`**

This is the most important file to understand. Everything in the Accounting
section reads from the `ledger_entries` collection it maintains.

### Chart of accounts

| Account | Type | Normal balance |
|---|---|---|
| Accounts Receivable | Asset | Debit |
| Cash/Bank | Asset | Debit |
| Milk Purchase Expense | Expense | Debit |
| Other Expense | Expense | Debit |
| Accounts Payable | Liability | Credit |
| Milk Sales Revenue | Revenue | Credit |
| Interest Income | Revenue | Credit |
| Other Income | Revenue | Credit |

### Every business event → one Debit/Credit pair

| Event | Debit | Credit |
|---|---|---|
| Buyer sale | Accounts Receivable | Milk Sales Revenue |
| Buyer pays invoice | Cash/Bank | Accounts Receivable |
| Late interest charged | Accounts Receivable | Interest Income |
| Interest waived | Interest Income | Accounts Receivable |
| Milk collected from farmer | Milk Purchase Expense | Accounts Payable |
| Dairy pays farmer | Accounts Payable | Cash/Bank |
| Quality deduction | Accounts Payable | Milk Purchase Expense |
| Manual expense | Other Expense | Cash/Bank |
| Manual income | Cash/Bank | Other Income |

> **Why deductions credit Milk Purchase Expense** rather than Other Income: a
> quality deduction means the dairy pays *less for that milk*. Netting it against
> the cost keeps your true cost of goods accurate, instead of inflating both
> cost and income.

### Idempotency — how edits don't corrupt the books

Every posting is keyed by `(source_collection, source_id)`. `post_transaction()`
**deletes any existing entries for that key first**, then inserts the new pair.
The consequences:

- **Edit a sale** from 500 L to 1000 L → ledger updates to the new amount. No duplicate.
- **Delete a sale** → `void_transaction()` removes both legs. No orphans.
- **Re-run the backfill** → replaces itself with identical figures. Safe.

Two events legitimately repeat under the same record: a payment cycle paid in
several installments, and an interest charge waived more than once. Those get a
uniquely-suffixed `source_id` (`PAY00001-pay-7`) so each real cash event keeps
its own permanent row instead of overwriting the previous one.

### Where the hooks live

`ledger_service` is called from exactly these places — one line each:

| Called from | Function |
|---|---|
| `routes/buyer_sales.py` (add/edit/delete) | `post_buyer_sale` / `void_buyer_sale` |
| `services/buyer_invoice_service.py` | `post_buyer_payment` |
| `services/dunning_service.py` | `post_interest_charge` / `post_interest_waiver` |
| `routes/milk_entries.py` (add/edit) | `post_milk_purchase` |
| `services/payment_service.py` | `post_customer_payment` / `post_customer_deduction` |
| `routes/expenses.py` (add/edit/delete) | `post_expense_entry` / `void_expense_entry` |

The business modules stay focused on their own job; accounting happens as a
side effect.

---

## 6. The service layer, file by file

### `rate_service.py`
Milk pricing. `calculate_rate()` takes milk type, FAT % and SNF %, and returns
₹/litre from the configured base rate plus quality increments.

### `payment_service.py`
The **customer 10-day payment cycle**. Splits each month into three cycles
(1–10, 11–20, 21–end), sums that farmer's milk entries, carries forward any
previous pending balance, applies deductions, and tracks partial payments.
Calls the ledger on payment and on deduction.

### `buyer_invoice_service.py`
The **buyer half-month billing cycle** (1st–15th, 16th–end). Consolidates
`buyer_sales` into one invoice, sets the due date from the buyer's payment
terms, and records payments against it. Unlike customer cycles, buyer invoices
are *not* rolled forward — each stands alone so interest can be tracked per
invoice.

### `dunning_service.py`
Chases late buyers: escalating reminders, and daily-prorated interest on overdue
balances using the configured annual rate. Supports partial waivers.

### `finance_settings_service.py`
Stores configurable financial policy — default payment terms, grace period,
interest rate, default tax %. Read by the invoice and dunning services so the
numbers aren't hard-coded.

### `accounting_service.py`
All reporting. Every figure comes from `ledger_entries`, so no two pages can
disagree. Provides: `build_dashboard()`, `revenue_report()`, `expense_report()`,
`cash_bank_summary()`, `profit_and_loss()`, `monthly_trend()`.

**The key distinction it maintains:**

- **Profit & Loss is accrual-based** — counted when the sale or purchase *happens*.
- **Cash Flow is cash-based** — counted when money actually *moves*.

A buyer settling an old invoice raises Cash/Bank but adds no new revenue — the
revenue was booked when the sale was made.

### `invoice_service.py`
Generates printable **PDF invoices** with ReportLab (logo, status badge,
line items) for both customer payouts and buyer invoices.

### `email_service.py` / `notification_service.py`
`email_service` builds and sends the actual SMTP messages. `notification_service`
decides *when* to send and writes an audit row into `notifications` for every
attempt — so a failed send is visible rather than silent.

---

## 7. Templates — how the UI is assembled

### Inheritance

`base.html` is the shell every page extends: `<head>`, the navbar, flash
messages, and these extension points:

```jinja
{% block title %}        page title
{% block body_class %}   lets a page set its own background theme
{% block extra_head %}   page-specific CSS
{% block content %}      the actual page
{% block extra_scripts %} page-specific JS
```

A child page fills in the blocks:

```jinja
{% extends "base.html" %}
{% block content %} ...this page's HTML... {% endblock %}
```

### The navbar and section sub-navs

The top navbar has four links — **Customer, Buyer, Accounting, About Us** — plus
the profile avatar menu. Clicking a section reveals that section's own pill-style
sub-nav, defined once in `partials/`:

- `partials/customer_nav.html` → Customers · Milk Entries · Rate Config · Payments · Notifications
- `partials/buyer_nav.html` → Buyers · Buyer Sales · Buyer Invoices · Dunning Log
- `partials/accounting_nav.html` → Dashboard · Receivables · Payables · Revenue · Cost Report · Expenses & Income · Cash & Bank · P&L · Settings

Each page sets `{% set active = 'customers' %}` before including the partial,
which highlights the current tab. **One file to edit** when a section gains a page.

### Other shared partials

| Partial | Used by |
|---|---|
| `ledger_section.html` | Full statement table with running balance — receivable & payable detail pages |
| `accounting_summary.html` | Compact balance card — buyer & customer profile pages |
| `accounting_range_form.html` | The date-range picker on accounting reports |

---

## 8. The three visual designs

The app deliberately runs **three distinct looks**, each with its own stylesheet.

### `style.css` — the global design system
The base look for every ordinary page. Defines the palette (indigo/violet
primary, cyan/green/amber/rose accents) and restyles all shared Bootstrap
components: cards, buttons, tables, badges, forms, the navbar.

> Because every page uses these same shared classes, changing a token here
> updates the entire application at once.

### `dashboard.css` — the main dashboard (light, glassy)
Scoped to `body.dash-page`. Frosted glass cards over a soft gradient, floating
SVG art, hover lift, animated counters.

### `accounting_dashboard.css` — the Accounting dashboard (dark, 3D)
Scoped to `body.acct-page`. A deliberately different world — dark navy canvas,
glowing accents, and **real 3D**:

- Stat tiles use `perspective` + `rotateX/rotateY` that follow your cursor, with
  children on separate `translateZ` planes so they parallax.
- The trend bars are genuine extruded boxes — front, top and side faces built
  with `rotateX(90deg)` / `rotateY(90deg)` inside a `preserve-3d` stage.

All three honour `prefers-reduced-motion`, disabling animation for users who ask
for it in their OS settings.

### JavaScript

| File | Job |
|---|---|
| `count-up.js` | Shared number-animation utility (`window.dashCountUp()`) |
| `dashboard.js` | Main dashboard's Chart.js charts |
| `accounting_dashboard.js` | Cursor tilt, the profit gauge arc, the SVG money-flow diagram, growing the 3D bars |

The money-flow diagram is drawn in code: revenue enters as one band and splits
into proportionally-sized ribbons for milk cost, expenses, and profit. It rescales
when the dairy runs at a loss, and shows an empty state at zero data.

---

## 9. Security

| Concern | How it's handled |
|---|---|
| Passwords | Never stored — only Werkzeug hashes (`generate_password_hash`) |
| Access control | `@login_required` in `app/utils/decorators.py` on every protected route |
| Session theft | `HTTPONLY` cookie — JavaScript can't read it |
| CSRF | `SAMESITE=Lax` cookie |
| XSS | Jinja2 auto-escapes all template variables by default |
| Upload abuse | 8 MB cap; extension allow-list; images re-encoded through Pillow |
| Duplicate records | Unique database indexes, enforced by MongoDB itself |

---

## 10. Setup

```bash
pip install -r requirements.txt

cp .env.example .env       # then edit: SECRET_KEY, MONGO_URI, SMTP…

python run.py              # http://localhost:5000
```

### One-time step if you already had data

If the database has records that pre-date the accounting module, generate their
ledger entries once:

```bash
flask backfill-ledger
```

This walks every existing sale, payment, milk entry, interest charge, deduction
and expense, and posts the matching Debit/Credit pair. It's **idempotent** —
running it twice is harmless.

---

## 11. Where to make common changes

| You want to… | Edit |
|---|---|
| Change milk pricing rules | `services/rate_service.py` + `/rate-config` page |
| Change payment terms / interest rate | `/accounting/settings` (no code change) |
| Change the payout cycle length | `services/payment_service.py` |
| Change buyer billing periods | `services/buyer_invoice_service.py` |
| Add an accounting report | `services/accounting_service.py` → `routes/accounting.py` → new template → add to `partials/accounting_nav.html` |
| Change colours app-wide | The `:root` variables in `static/css/style.css` |
| Add a page to a section | Create the template, add the route, add one line to the section's nav partial |
| Change how a transaction is booked | `services/ledger_service.py` (the `post_*` functions) |

---

## 12. Quick file reference

```
run.py                    Entry point
config.py                 Settings from environment
requirements.txt          Dependencies

app/__init__.py           ★ Application factory — wiring hub, indexes,
                            blueprint registration, backfill CLI command
app/utils/
  decorators.py           @login_required
  id_generator.py         Atomic human-readable ID counters

app/routes/               HTTP layer — one file per feature area
  auth · dashboard · customers · milk_entries · rate_config · payments
  notifications · buyers · buyer_sales · buyer_invoices · expenses
  accounting · customer_portal · about · profile

app/services/             Business logic
  ledger_service.py       ★ Double-entry engine — the accounting core
  accounting_service.py   ★ All reporting, reads only from the ledger
  rate_service.py         Milk pricing from FAT/SNF
  payment_service.py      Customer 10-day payout cycles
  buyer_invoice_service.py Buyer half-month invoicing
  dunning_service.py      Overdue reminders + interest
  finance_settings_service.py Configurable financial policy
  invoice_service.py      PDF generation (ReportLab)
  email_service.py        SMTP sending
  notification_service.py Notification decisions + audit log

app/templates/            Jinja2 HTML
  base.html               ★ Shell every page extends
  partials/               Shared components (navs, ledger table, range picker)
  <feature>/              One folder per feature area

app/static/
  css/style.css                    ★ Global design system
  css/dashboard.css                Main dashboard (light/glass)
  css/accounting_dashboard.css     Accounting dashboard (dark/3D)
  js/count-up.js                   Shared counter animation
  js/dashboard.js                  Main dashboard charts
  js/accounting_dashboard.js       3D tilt, gauge, money-flow diagram
```

---

## 13. The five ideas that explain everything else

1. **Routes handle HTTP; services hold the logic.** Keeps business rules
   testable and reusable.
2. **Every transaction auto-posts to the ledger.** The books are never "updated
   later" — they're always current.
3. **All reports read from that one ledger.** No two pages can disagree, because
   there's only one source of truth.
4. **Postings are idempotent, keyed by source record.** Edits and deletes stay
   correct; the backfill is safe to re-run.
5. **Shared partials and CSS tokens.** Change a nav or a colour in one file and
   the whole application follows.

Understand those five, and the rest of the codebase reads itself.
