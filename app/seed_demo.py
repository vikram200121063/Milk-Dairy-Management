"""
Populates the SEPARATE demo database (never the real `db`) with
realistic-looking but entirely fictional sample data, so the public
"Guest / Read-Only Demo" mode (see app/routes/auth.py's guest_login and
app/db_proxy.py's ScopedDB) has something meaningful to show on every
screen instead of empty tables.

Wherever a service function already exists for a piece of business logic
(payment cycles, buyer invoices, ledger postings), this reuses it
directly against the demo db, exactly the way `flask backfill-ledger` in
app/__init__.py does for the real db - so the demo data's derived numbers
(ledger entries, invoice totals, payment statuses) are genuinely correct
and internally consistent, not hand-faked.

Safe to run any number of times: every collection this touches is wiped
first, so re-running always produces a fresh, consistent snapshot rather
than piling up duplicates.

Invoked via `flask seed-demo-data` (see app/__init__.py).
"""
import random
import secrets
from datetime import datetime, timedelta, timezone, date as date_cls

from werkzeug.security import generate_password_hash

from app.utils.id_generator import get_next_sequence
from app.services import (
    finance_settings_service,
    ledger_service,
    payment_service,
    buyer_invoice_service,
    dunning_service,
)
from app.services.rate_service import calculate_rate

# Fixed seed so re-running produces the same-looking (if not byte-identical,
# since dates are relative to "today") demo data every time - easier to
# reason about / screenshot than fresh randomness on every run.
_RNG = random.Random(20260926)

SHIFTS = ("Morning", "Evening")
MILK_TYPES = ("Cow", "Buffalo")
PAYMENT_METHODS = ("Cash", "UPI", "Bank Transfer")
EXPENSE_CATEGORIES = (
    "Transportation/Fuel",
    "Salary",
    "Electricity",
    "Maintenance",
    "Packaging",
    "Other",
)

# Collections this seed script owns end-to-end. Wiped before every reseed.
DEMO_COLLECTIONS = (
    "users",
    "customers",
    "milk_entries",
    "rate_configurations",
    "payments",
    "buyers",
    "buyer_sales",
    "buyer_invoices",
    "buyer_payments",
    "expenses",
    "ledger_entries",
    "dunning_records",
    "interest_charges",
    "finance_settings",
    "counters",
    "notifications",
)

DEMO_CUSTOMERS = [
    {"name": "Demo Farmer 1", "village": "Sample Village North", "mobile_number": "9000000001"},
    {"name": "Demo Farmer 2", "village": "Sample Village North", "mobile_number": "9000000002"},
    {"name": "Demo Farmer 3", "village": "Sample Village South", "mobile_number": "9000000003"},
    {"name": "Demo Farmer 4", "village": "Sample Village South", "mobile_number": "9000000004"},
    {"name": "Demo Farmer 5", "village": "Sample Village East", "mobile_number": "9000000005"},
]

DEMO_BUYERS = [
    {
        "company_name": "Sample Traders Pvt Ltd",
        "contact_person": "Demo Contact A",
        "mobile_number": "9111111101",
        "email": "buyer1@example-demo.test",
        "address": "Sample Industrial Area, Demo City",
        "payment_terms_days": 15,
    },
    {
        "company_name": "Fictional Dairy Foods Co.",
        "company_name_alt": None,
        "contact_person": "Demo Contact B",
        "mobile_number": "9111111102",
        "email": "buyer2@example-demo.test",
        "address": "Demo Market Road, Sample Town",
        "payment_terms_days": 30,
    },
    {
        "company_name": "Placeholder Milk Distributors",
        "contact_person": "Demo Contact C",
        "mobile_number": "9111111103",
        "email": "buyer3@example-demo.test",
        "address": "Test Estate, Sample City",
        "payment_terms_days": 15,
    },
    {
        "company_name": "Example Creamery Ltd",
        "contact_person": "Demo Contact D",
        "mobile_number": "9111111104",
        "email": "buyer4@example-demo.test",
        "address": "Demo Bypass Road, Sample Nagar",
        "payment_terms_days": 45,
    },
]

DEMO_EXPENSES = [
    ("Expense", "Transportation/Fuel", "Diesel for collection van"),
    ("Expense", "Salary", "Staff wages - collection team"),
    ("Expense", "Electricity", "Chilling unit power bill"),
    ("Expense", "Maintenance", "Milk can & equipment repair"),
    ("Income", "Other Income", "Sale of empty containers"),
]


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _dairy_info():
    from config import Config

    return {"name": Config.DAIRY_NAME, "address": Config.DAIRY_ADDRESS, "contact": Config.DAIRY_CONTACT}


def _seed_rate_configs(db):
    db.rate_configurations.insert_many(
        [
            {
                "milk_type": "Cow",
                "base_rate": 25.0,
                "fat_rate_per_point": 1.2,
                "snf_rate_per_point": 0.8,
                "updated_at": datetime.now(timezone.utc),
            },
            {
                "milk_type": "Buffalo",
                "base_rate": 30.0,
                "fat_rate_per_point": 1.5,
                "snf_rate_per_point": 1.0,
                "updated_at": datetime.now(timezone.utc),
            },
        ]
    )
    return 2


def _seed_guest_user(db):
    db.users.insert_one(
        {
            "username": "guest",
            # Harmless random password - the guest never logs in with it
            # (guest_login bypasses password checks entirely), this exists
            # only so the users document has the same shape every real
            # user document has.
            "password_hash": generate_password_hash(secrets.token_hex(16)),
            "created_at": datetime.now(timezone.utc),
        }
    )
    return 1


def _seed_customers(db):
    docs = []
    for c in DEMO_CUSTOMERS:
        seq = get_next_sequence(db, "customer_id")
        docs.append(
            {
                "customer_id": f"CUST{seq:04d}",
                "name": c["name"],
                "mobile_number": c["mobile_number"],
                "email": "",
                "address": "Demo address, not a real location",
                "village": c["village"],
                "registration_date": datetime.now(timezone.utc) - timedelta(days=180),
                "status": "Active",
            }
        )
    db.customers.insert_many(docs)
    return docs


def _seed_milk_entries(db, customers, start_date, end_date):
    """
    One entry per customer per day in [start_date, end_date] (inclusive) -
    alternating Morning/Evening shift so both show up somewhere without
    doubling the record count - using the Automatic rate (calculate_rate)
    so the figures are exactly as internally consistent as the real app
    produces them. Posts the matching ledger entry for every one, exactly
    like app/routes/milk_entries.py does on every real add. Kept to one
    entry/day (not one per shift) so the demo dataset stays small: with 5
    customers over a ~20-day window (needed to cover two full 10-day
    payment cycles - see _seed_customer_payment_cycles) that's ~100
    entries, not ~400.
    """
    count = 0
    day = start_date
    while day <= end_date:
        date_dt = _utc(day.year, day.month, day.day)
        for customer in customers:
            milk_type = MILK_TYPES[hash((customer["customer_id"], day.toordinal())) % 2]
            shift = SHIFTS[day.toordinal() % 2]
            if True:
                quantity = round(_RNG.uniform(6.0, 14.0), 2)
                fat = round(_RNG.uniform(3.5, 6.0), 2)
                snf = round(_RNG.uniform(8.0, 9.2), 2)
                rate, error = calculate_rate(db, milk_type, fat, snf)
                if error:
                    continue
                total_amount = round(quantity * rate, 2)
                entry_doc = {
                    "customer_id": customer["customer_id"],
                    "customer_name": customer["name"],
                    "date": date_dt,
                    "shift": shift,
                    "milk_type": milk_type,
                    "quantity": quantity,
                    "fat_percentage": fat,
                    "snf_percentage": snf,
                    "rate_per_litre": rate,
                    "rate_mode": "Automatic",
                    "total_amount": total_amount,
                    "notes": "",
                    "created_at": date_dt,
                }
                result = db.milk_entries.insert_one(entry_doc)
                entry_doc["_id"] = result.inserted_id
                ledger_service.post_milk_purchase(db, entry_doc, str(entry_doc["_id"]))
                count += 1
        day += timedelta(days=1)
    return count


def _seed_customer_payment_cycles(db, reference_date):
    """
    Generates the completed 10-day payment cycles (1-10, 11-20) for
    `reference_date`'s calendar month via
    payment_service.generate_payments_for_cycle - the exact function real
    payment generation uses - then records real payments against them via
    payment_service.record_payment: cycle 1 fully paid for every
    customer, cycle 2 partially paid, so the Payments screen shows a
    realistic mix of statuses.

    `reference_date` must be a date the seeded milk_entries actually
    cover (pass milk_end, not "today") - milk_start/milk_end can land in
    the previous calendar month when today is the 1st or 2nd of a month,
    and generating cycles for "today's" month in that case would find no
    matching milk_entries and silently produce 0 payments.
    """
    year, month = reference_date.year, reference_date.month
    payments_count = 0
    payment_records_count = 0

    for cycle_number in (1, 2):
        payment_service.generate_payments_for_cycle(db, year, month, cycle_number)

    for cycle_number in (1, 2):
        period = payment_service.make_payment_period(year, month, cycle_number)
        for payment in db.payments.find({"payment_period": period}):
            payments_count += 1
            if payment["final_payable_amount"] <= 0:
                continue
            if cycle_number == 1:
                amount = payment["final_payable_amount"]  # pay in full
            else:
                amount = round(payment["final_payable_amount"] * 0.5, 2)  # partially paid
            if amount <= 0:
                continue
            ok, _ = payment_service.record_payment(
                db,
                payment["payment_id"],
                amount,
                _RNG.choice(PAYMENT_METHODS),
                f"DEMO-REF-{payment['payment_id']}",
                datetime.now(timezone.utc) - timedelta(days=3 if cycle_number == 1 else 1),
            )
            if ok:
                payment_records_count += 1

    return payments_count, payment_records_count


def _seed_buyers(db):
    docs = []
    for b in DEMO_BUYERS:
        seq = get_next_sequence(db, "buyer_id")
        docs.append(
            {
                "buyer_id": f"BYR{seq:04d}",
                "company_name": b["company_name"],
                "contact_person": b["contact_person"],
                "mobile_number": b["mobile_number"],
                "email": b["email"],
                "address": b["address"],
                "gst_number": None,
                "payment_terms_days": b["payment_terms_days"],
                "credit_limit": 100000.0,
                "status": "Active",
                "created_at": datetime.now(timezone.utc) - timedelta(days=180),
            }
        )
    db.buyers.insert_many(docs)
    return docs


def _seed_buyer_sales(db, buyers, start_date, end_date):
    count = 0
    day = start_date
    while day <= end_date:
        date_dt = _utc(day.year, day.month, day.day)
        for buyer in buyers:
            if _RNG.random() < 0.4:
                # Not every buyer buys every day - keeps the data looking real.
                continue
            milk_type = _RNG.choice(MILK_TYPES)
            quantity = round(_RNG.uniform(80.0, 220.0), 2)
            rate = round(_RNG.uniform(38.0, 48.0), 2)
            gross_amount = round(quantity * rate, 2)
            tax_amount = round(gross_amount * 0.02, 2)
            other_charges = 0.0
            total_amount = round(gross_amount + tax_amount + other_charges, 2)

            seq = get_next_sequence(db, "buyer_sale_id")
            sale_doc = {
                "sale_id": f"BSL{seq:05d}",
                "buyer_id": buyer["buyer_id"],
                "buyer_name": buyer["company_name"],
                "sale_date": date_dt,
                "milk_type": milk_type,
                "quality_grade": "A",
                "quantity": quantity,
                "rate_per_litre": rate,
                "gross_amount": gross_amount,
                "tax_percentage": 2.0,
                "tax_amount": tax_amount,
                "other_charges": other_charges,
                "total_amount": total_amount,
                "notes": "",
                "created_at": date_dt,
            }
            db.buyer_sales.insert_one(sale_doc)
            ledger_service.post_buyer_sale(db, sale_doc)
            count += 1
        day += timedelta(days=1)
    return count


def _backdate_invoice(db, invoice, invoice_date, settings):
    """
    generate_invoices_for_period() always stamps a brand-new invoice's
    invoice_date as "now" (there's no historical-date parameter - a real
    invoice really is generated the day someone clicks "Generate"). To
    get a realistic mix of Paid / Partially Paid / Unpaid / Overdue
    invoices for the demo, this backdates invoice_date/due_date for one
    already-generated invoice, then recomputes its status exactly the way
    buyer_invoice_service does everywhere else (compute_invoice_status),
    so the rest of the app can't tell the difference.
    """
    payment_terms_days = invoice.get("payment_terms_days") or settings["default_payment_terms_days"]
    due_date = invoice_date + timedelta(days=payment_terms_days)
    now = datetime.now(timezone.utc)
    status = buyer_invoice_service.compute_invoice_status(
        invoice["remaining_amount"], invoice["amount_paid"], due_date, now
    )
    db.buyer_invoices.update_one(
        {"_id": invoice["_id"]},
        {"$set": {"invoice_date": invoice_date, "due_date": due_date, "status": status, "updated_at": now}},
    )
    invoice["invoice_date"] = invoice_date
    invoice["due_date"] = due_date
    invoice["status"] = status
    return invoice


def resolve_seed_billing_period():
    """
    Picks the most recently FULLY COMPLETED half-month billing period as
    of today, and returns (year, month, period_number, period_start,
    period_end) - all as real UTC datetimes from
    buyer_invoice_service.get_period_boundaries, so callers can seed
    buyer_sales dates that are guaranteed to fall inside this exact
    period no matter what day of the month "today" happens to be.

    Deliberately the PREVIOUS period, not "whatever period contains
    today" (current_period_defaults()): the current period may have only
    just started (e.g. today is the 1st or the 16th), leaving no room
    for a several-day sales window before "today" without spilling into
    the previous period or even the previous month - which is exactly
    what silently produced 0 buyer_invoices in earlier testing whenever
    "today" landed near a period boundary. The previous period is always
    fully in the past, so a sales window anywhere inside it is safe.
    """
    year, month, current_period = buyer_invoice_service.current_period_defaults()
    if current_period == 1:
        # Previous period is period 2 of the previous month.
        if month == 1:
            inv_year, inv_month = year - 1, 12
        else:
            inv_year, inv_month = year, month - 1
        inv_period = 2
    else:
        inv_year, inv_month, inv_period = year, month, 1

    start, end = buyer_invoice_service.get_period_boundaries(inv_year, inv_month, inv_period)
    return inv_year, inv_month, inv_period, start, end


def _seed_buyer_invoices_and_payments(db, year, month, period_number, settings):
    """
    Generates ONE half-month invoice period via
    buyer_invoice_service.generate_invoices_for_period (the real
    invoicing logic) - one invoice per buyer, so 4 buyers = 4 invoices,
    matching the "4-5 records per screen" size the rest of this demo
    dataset aims for - backdates them well into the past so dunning/
    interest have something real to act on, then records a realistic
    mix of payments: full, partial, and none, and finally runs the real
    dunning cycle (dunning_service.run_dunning_cycle) so overdue status/
    reminder log/interest charges are genuinely computed, not faked.

    `year`/`month`/`period_number` MUST be exactly what resolve_seed_billing_period()
    returned, and the buyer_sales fed into this period must have been
    seeded using that same function's `start`/`end` - otherwise
    generate_invoices_for_period finds no sales in range and silently
    produces 0 invoices (this happened during testing before this
    function took an explicit, pre-resolved period instead of guessing).

    The dunning cycle call is wrapped in try/except: it can attempt a
    real email send (via ai_service/email_service) if this dairy's real
    ANTHROPIC/OPENAI/GEMINI/GROQ or SMTP credentials happen to be
    configured in this environment, and any unexpected failure there
    (network, a provider's rate limit, etc.) must not wipe out the
    invoices/buyers/etc. already inserted above it - it would otherwise
    leave the rest of the seed silently unreported downstream, which is
    the most likely explanation if a previous run of this command showed
    "only customers" and nothing else.
    """
    invoice_count = 0
    payment_count = 0

    buyer_invoice_service.generate_invoices_for_period(db, year, month, period_number, settings)

    period1 = buyer_invoice_service.make_billing_period(year, month, period_number)
    period1_invoices = list(db.buyer_invoices.find({"billing_period": period1}).sort("buyer_name", 1))
    invoice_count += len(period1_invoices)

    # Backdated far enough that, with typical payment terms, the due date
    # is already in the past -> a real candidate for Overdue once dunning
    # runs below.
    old_invoice_date = datetime.now(timezone.utc) - timedelta(days=40)
    for invoice in period1_invoices:
        _backdate_invoice(db, invoice, old_invoice_date, settings)

    # Realistic payment mix: buyer 0 pays in full, buyer 1 pays half, the
    # rest are left unpaid (and will surface as Overdue once dunning runs).
    for i, invoice in enumerate(period1_invoices):
        if invoice["total_amount"] <= 0:
            continue
        if i == 0:
            amount = invoice["total_amount"]
        elif i == 1:
            amount = round(invoice["total_amount"] * 0.5, 2)
        else:
            continue
        if amount <= 0:
            continue
        ok, _ = buyer_invoice_service.record_buyer_payment(
            db,
            invoice["invoice_id"],
            amount,
            _RNG.choice(PAYMENT_METHODS),
            f"DEMO-REF-{invoice['invoice_id']}",
            datetime.now(timezone.utc) - timedelta(days=15),
        )
        if ok:
            payment_count += 1

    # Run the REAL dunning cycle: marks the still-unpaid invoices
    # Overdue, logs reminder attempts, and applies late-payment interest
    # where the grace period has already passed. Never let a failure
    # here (e.g. a real AI/SMTP call misbehaving) take down the invoices,
    # payments, buyers etc. already committed above.
    try:
        dunning_service.run_dunning_cycle(db, _dairy_info())
    except Exception as exc:  # noqa: BLE001 - intentionally broad, see docstring
        import click

        click.echo(f"  (warning: dunning cycle step failed, continuing without it: {exc})")

    return invoice_count, payment_count


def _seed_expenses(db, start_date, end_date):
    count = 0
    for i, (entry_type, category, description) in enumerate(DEMO_EXPENSES):
        day = start_date + timedelta(days=(i * 4) % max((end_date - start_date).days, 1))
        date_dt = _utc(day.year, day.month, day.day)
        amount = round(_RNG.uniform(1500.0, 12000.0), 2)
        seq = get_next_sequence(db, "expense_id")
        expense_doc = {
            "expense_id": f"EXP{seq:05d}",
            "entry_type": entry_type,
            "category": category,
            "description": description,
            "amount": amount,
            "date": date_dt,
            "created_at": date_dt,
        }
        db.expenses.insert_one(expense_doc)
        ledger_service.post_expense_entry(db, expense_doc)
        count += 1
    return count


def seed_demo_data(db, progress=None):
    """
    Wipes and repopulates every collection the demo relies on. Returns a
    dict of {label: count} for the CLI command to print.

    `progress`, if given, is called with a short label right before each
    step starts (e.g. progress("customers")). The CLI command passes
    click.echo here so that if a step ever fails on a real deployment,
    the terminal shows exactly how far the seed got before the traceback
    - instead of a single crash with everything-or-nothing visibility,
    which is the most likely explanation if a previous run appeared to
    populate "only customers" and nothing else.
    """
    def _progress(label):
        if progress:
            progress(label)

    _progress("clearing old demo data")
    for name in DEMO_COLLECTIONS:
        db[name].delete_many({})

    today = date_cls.today()
    # 20 days = exactly two 10-day payment cycles (1-10, 11-20), which is
    # the minimum needed for _seed_customer_payment_cycles to show one
    # fully-paid and one partially-paid cycle - kept short on purpose so
    # the demo dataset stays small (see docstrings below for why).
    milk_start = today - timedelta(days=20)
    milk_end = today - timedelta(days=1)

    # See resolve_seed_billing_period()'s docstring for why this has to be
    # the previous (fully completed) period rather than "whatever period
    # contains today", and why buyer_sales below is dated using this
    # period's own start/end rather than a fixed "last 6 days" window.
    inv_year, inv_month, inv_period, period_start, period_end = resolve_seed_billing_period()
    sales_start = period_start.date()
    sales_end = min(period_end.date(), milk_end)

    counts = {}

    _progress("rate configurations")
    counts["rate_configurations"] = _seed_rate_configs(db)

    _progress("guest user account")
    counts["users (guest)"] = _seed_guest_user(db)

    _progress("customers")
    customers = _seed_customers(db)
    counts["customers"] = len(customers)

    _progress("milk entries")
    counts["milk_entries"] = _seed_milk_entries(db, customers, milk_start, milk_end)

    _progress("customer payment cycles")
    settings = finance_settings_service.get_settings(db)
    payments_generated, payments_recorded = _seed_customer_payment_cycles(db, milk_end)
    counts["payments (cycles)"] = payments_generated
    counts["customer payment transactions recorded"] = payments_recorded

    _progress("buyers")
    buyers = _seed_buyers(db)
    counts["buyers"] = len(buyers)

    _progress("buyer sales")
    counts["buyer_sales"] = _seed_buyer_sales(db, buyers, sales_start, sales_end)

    _progress("buyer invoices, payments & dunning")
    invoice_count, buyer_payment_count = _seed_buyer_invoices_and_payments(
        db, inv_year, inv_month, inv_period, settings
    )
    counts["buyer_invoices"] = invoice_count
    counts["buyer_payments"] = buyer_payment_count

    _progress("expenses")
    counts["expenses"] = _seed_expenses(db, milk_start, milk_end)

    counts["ledger_entries"] = db.ledger_entries.count_documents({})
    counts["dunning_records"] = db.dunning_records.count_documents({})
    counts["interest_charges"] = db.interest_charges.count_documents({})

    return counts
