from datetime import datetime, timezone

import click
from flask import Flask, jsonify, redirect, url_for, session, request, flash
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError
from werkzeug.security import generate_password_hash

from config import Config


def _create_indexes(database):
    """
    Enforces uniqueness/lookup indexes at the database level (not just in
    application code). create_index is safe to call every startup - it's
    a no-op if the index already exists. Called once for the real db and
    once for the demo db (see create_app below), so both have identical
    indexes/unique constraints - including inside the demo db, so the
    `guest` user and demo sample data behave exactly like the real thing.
    """
    database.users.create_index("username", unique=True)
    database.customers.create_index("customer_id", unique=True)
    database.customers.create_index("mobile_number", unique=True)
    database.customers.create_index("status")

    # Unique compound index: this is what makes duplicate entries for the
    # same customer/date/shift actually IMPOSSIBLE, not just discouraged.
    database.milk_entries.create_index(
        [("customer_id", 1), ("date", 1), ("shift", 1)], unique=True
    )
    database.milk_entries.create_index("date")
    database.milk_entries.create_index("customer_id")

    database.rate_configurations.create_index("milk_type", unique=True)

    database.payments.create_index([("customer_id", 1), ("payment_period", 1)], unique=True)
    database.payments.create_index("payment_id", unique=True)
    database.payments.create_index("payment_period")
    database.payments.create_index("payment_status")

    # Audit log of every SMS/email notification attempt (see
    # app/services/notification_service.py).
    database.notifications.create_index([("sent_at", -1)])
    database.notifications.create_index("customer_id")

    # --- Buyer Management & Accounting module ---
    # All new collections/indexes below are additive - nothing above this
    # block was touched, so the existing Customer Milk Collection &
    # Payment functionality is unaffected.
    database.buyers.create_index("buyer_id", unique=True)
    database.buyers.create_index("gst_number", unique=True, sparse=True)
    database.buyers.create_index("status")

    database.buyer_sales.create_index("sale_id", unique=True)
    database.buyer_sales.create_index([("buyer_id", 1), ("sale_date", 1)])
    database.buyer_sales.create_index("sale_date")

    database.buyer_invoices.create_index("invoice_id", unique=True)
    database.buyer_invoices.create_index([("buyer_id", 1), ("billing_period", 1)], unique=True)
    database.buyer_invoices.create_index("status")
    database.buyer_invoices.create_index("due_date")
    database.buyer_invoices.create_index("billing_period")

    database.buyer_payments.create_index("payment_id", unique=True)
    database.buyer_payments.create_index("invoice_id")
    database.buyer_payments.create_index("buyer_id")

    database.expenses.create_index("expense_id", unique=True)
    database.expenses.create_index("date")
    database.expenses.create_index("category")
    database.expenses.create_index("entry_type")

    database.dunning_records.create_index([("invoice_id", 1), ("sent_at", -1)])
    database.dunning_records.create_index("record_type")

    database.interest_charges.create_index("charge_id", unique=True)
    database.interest_charges.create_index("invoice_id")
    database.interest_charges.create_index("status")

    # --- Common Accounting module: the general ledger ---
    database.ledger_entries.create_index("entry_id", unique=True)
    database.ledger_entries.create_index([("source_collection", 1), ("source_id", 1)])
    database.ledger_entries.create_index("account")
    database.ledger_entries.create_index([("entity_type", 1), ("entity_id", 1)])
    database.ledger_entries.create_index("date")
    database.ledger_entries.create_index("transaction_id")


def create_app():
    """
    Application factory: builds and configures the Flask app.
    Using a factory (instead of a global `app = Flask(__name__)`) makes it
    easy to test the app and to create multiple instances with different
    configs later if needed.
    """
    Config.validate()

    app = Flask(__name__)
    app.config.from_object(Config)

    # --- MongoDB connection ---
    # We create ONE client for the whole application and reuse it.
    # PyMongo's MongoClient already manages a connection pool internally,
    # so we do NOT want to create a new client per-request.
    try:
        client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
        # The ping command is cheap and confirms we can actually reach Atlas,
        # not just that the URI parsed correctly.
        client.admin.command("ping")
    except (ConnectionFailure, ConfigurationError) as exc:
        raise RuntimeError(
            f"Could not connect to MongoDB. Check your MONGO_URI in .env. Details: {exc}"
        )

    db = client[Config.MONGO_DB_NAME]

    # Store client/db on the app object so routes and blueprints can access
    # them via `current_app.db` without importing a global variable.
    app.mongo_client = client
    app.db = db

    _create_indexes(db)

    # --- Session cookie security ---
    # HTTPONLY: JavaScript can't read the cookie (mitigates XSS cookie theft)
    # SAMESITE: cookie isn't sent on cross-site requests (mitigates CSRF)
    # SECURE: only send the cookie over HTTPS - enable once deployed behind HTTPS
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not app.config.get("DEBUG", False)

    # --- Register blueprints ---
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.customers import customers_bp
    from app.routes.milk_entries import milk_entries_bp
    from app.routes.rate_config import rate_config_bp
    from app.routes.payments import payments_bp
    from app.routes.customer_portal import customer_portal_bp
    from app.routes.notifications import notifications_bp
    from app.routes.buyers import buyers_bp
    from app.routes.buyer_sales import buyer_sales_bp
    from app.routes.buyer_invoices import buyer_invoices_bp
    from app.routes.expenses import expenses_bp
    from app.routes.accounting import accounting_bp
    from app.routes.about import about_bp
    from app.routes.profile import profile_bp
    from app.routes.ai_assistant import ai_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(milk_entries_bp)
    app.register_blueprint(rate_config_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(customer_portal_bp)
    app.register_blueprint(notifications_bp)
    # --- Buyer Management & Accounting module ---
    app.register_blueprint(buyers_bp)
    app.register_blueprint(buyer_sales_bp)
    app.register_blueprint(buyer_invoices_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(accounting_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(ai_bp)

    # --- AI features (Business Assistant widget, dunning drafts, P&L
    # narrative, receipt auto-fill) - all gated behind ANTHROPIC_API_KEY.
    # Every template can check `ai_enabled` to decide whether to show an
    # AI-powered control at all, so nothing dead-ends when it's unset.
    @app.context_processor
    def inject_ai_flag():
        from app.services import ai_service

        return {"ai_enabled": ai_service.ai_configured()}

    # --- Public "Guest / Read-Only Demo" mode -------------------------
    # Lets HR/recruiters/anyone try the app with no login and no
    # password, browsing this dairy's REAL, LIVE data - but never able
    # to change anything (see the before_request guard below). There is
    # no separate demo database: guest_login() (app/routes/auth.py) just
    # flags the session `is_guest`, and every route still reads/writes
    # `current_app.db` exactly as it always has.
    #
    # NOTE: because this shows real data, anyone with the link can see
    # this dairy's actual customers/buyers, their names, phone numbers,
    # and financial records. If that's not acceptable for a public
    # resume link, the previous separate-fake-data approach (removed at
    # the user's request) is the safer alternative to revisit.

    # Every template can check `is_guest` to hide write-only controls or
    # (more importantly) AI features that POST to an endpoint expecting a
    # JSON response - see partials/ai_assistant_widget.html,
    # accounting/dashboard.html, and expenses/form.html.
    @app.context_processor
    def inject_guest_flag():
        return {"is_guest": session.get("is_guest", False)}

    @app.before_request
    def block_guest_writes():
        """
        A guest session may only ever GET/HEAD/OPTIONS. Every mutating
        route in this app is POST (there are no GET routes with side
        effects), so this one blanket check is sufficient to make the
        entire app read-only for guests without needing a list of
        allowed/blocked endpoints that could drift out of date.
        """
        if session.get("is_guest") and request.method not in ("GET", "HEAD", "OPTIONS"):
            flash("This is a read-only demo – changes aren't saved here.", "warning")
            return redirect(request.referrer or url_for("dashboard.index"))

    # --- Root route: send visitors to the right place based on who (if
    # anyone) is logged in - admin staff, a customer, or a fresh visitor ---
    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard.index"))
        if "customer_id" in session:
            return redirect(url_for("customer_portal.dashboard"))
        return redirect(url_for("auth.login"))

    # --- Health check route (moved from "/" - useful for deployment platforms) ---
    @app.route("/health")
    def health_check():
        try:
            app.db.command("ping")
            mongo_status = "connected"
        except Exception as exc:
            mongo_status = f"error: {exc}"

        return jsonify(
            {
                "app": "Milk Dairy Management",
                "status": "running",
                "database": Config.MONGO_DB_NAME,
                "mongodb": mongo_status,
            }
        )

    # --- CLI command: flask create-admin ---
    # Run this from the terminal to create the first (or additional) admin
    # accounts. There is intentionally no public /register web page.
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        """Create an admin user. Usage: flask create-admin <username>"""
        if db.users.find_one({"username": username}):
            click.echo(f"Error: user '{username}' already exists.")
            return

        db.users.insert_one(
            {
                "username": username,
                "password_hash": generate_password_hash(password),
                "created_at": datetime.now(timezone.utc),
            }
        )
        click.echo(f"Admin user '{username}' created successfully.")

    # --- CLI command: flask reset-password ---
    # Use this when you forget an existing admin's password, instead of
    # creating a throwaway new account. Requires terminal/server access,
    # same as create-admin - there is still no public reset-password page.
    @app.cli.command("reset-password")
    @click.argument("username")
    @click.password_option()
    def reset_password(username, password):
        """Reset an existing admin's password. Usage: flask reset-password <username>"""
        user = db.users.find_one({"username": username})
        if not user:
            click.echo(f"Error: no user found with username '{username}'.")
            return

        db.users.update_one(
            {"username": username},
            {"$set": {"password_hash": generate_password_hash(password)}},
        )
        click.echo(f"Password for '{username}' has been reset.")

    # --- CLI command: flask process-dunning ---
    # Runs the full buyer-invoice dunning cycle (mark overdue -> send the
    # first reminder -> apply late-payment interest once the grace period
    # has passed). This project has no background job runner, so wire
    # this command up to a daily cron job / scheduled task to automate
    # it; it's also available as a "Run Dunning Check" button in the
    # Buyer Invoices screen for on-demand use. Safe to run repeatedly.
    @app.cli.command("process-dunning")
    def process_dunning():
        """Run the buyer invoice dunning cycle (overdue -> reminder -> grace period -> interest)."""
        from app.services import dunning_service

        dairy_info = {
            "name": Config.DAIRY_NAME,
            "address": Config.DAIRY_ADDRESS,
            "contact": Config.DAIRY_CONTACT,
        }
        with app.app_context():
            stats = dunning_service.run_dunning_cycle(db, dairy_info)
        click.echo(
            f"Checked {stats['checked']} invoice(s). "
            f"Newly overdue: {stats['marked_overdue']}. "
            f"Reminders sent: {stats['reminders_sent']}. "
            f"Interest applied to {stats['interest_applied']} invoice(s) "
            f"(\u20b9{stats['interest_total']:.2f} total)."
        )

    # --- CLI command: flask send-pl-summary ---
    # Emails ADMIN_NOTIFICATION_EMAIL a short, AI-written plain-English
    # summary of this calendar month's Profit & Loss so far. Wire this up
    # to a monthly (or weekly) cron job / scheduled task the same way as
    # process-dunning; the "AI Summary" button on the Accounting dashboard
    # covers on-demand use for any date range. Does nothing (and says so)
    # if ANTHROPIC_API_KEY or ADMIN_NOTIFICATION_EMAIL isn't set.
    @app.cli.command("send-pl-summary")
    def send_pl_summary():
        """Email an AI-written summary of this month's Profit & Loss to ADMIN_NOTIFICATION_EMAIL."""
        from app.services import accounting_service, ai_service, email_service

        if not Config.ADMIN_NOTIFICATION_EMAIL:
            click.echo("ADMIN_NOTIFICATION_EMAIL is not set - nothing to send. See .env.example.")
            return

        with app.app_context():
            if not ai_service.ai_configured():
                click.echo("ANTHROPIC_API_KEY is not set - cannot generate an AI summary.")
                return

            data = accounting_service.build_dashboard(db, "this_month")
            label = data["range_label"]
            narrative, error = ai_service.generate_pnl_narrative(data, label, Config.DAIRY_NAME)
            if error:
                click.echo(f"Could not generate the summary: {error}")
                return

            if not email_service.email_configured():
                click.echo("SMTP is not configured, so the summary could not be emailed. Here it is instead:\n")
                click.echo(narrative)
                return

            subject = f"{Config.DAIRY_NAME} \u2013 P&L Summary ({label})"
            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;">
              <h2 style="color:#1F4A3A;">Profit &amp; Loss Summary \u2013 {label}</h2>
              <p style="white-space: pre-wrap;">{narrative}</p>
              <p style="color:#999; font-size:12px; margin-top:24px;">
                Auto-generated by {Config.DAIRY_NAME}'s AI assistant from the figures in your
                Accounting dashboard. Open the dashboard for the full breakdown.
              </p>
            </div>
            """
            try:
                email_service.send_email(Config.ADMIN_NOTIFICATION_EMAIL, subject, html_body)
                click.echo(f"P&L summary emailed to {Config.ADMIN_NOTIFICATION_EMAIL}.")
            except email_service.EmailSendError as exc:
                click.echo(f"Could not send the email: {exc}")

    # --- CLI command: flask backfill-ledger ---
    # One-time migration for the common Accounting module: generates
    # ledger_entries for every buyer sale, buyer payment, interest charge/
    # waiver, milk entry, customer payment, customer deduction, and manual
    # expense/income entry that already existed before this feature was
    # added. Safe to run more than once - every posting here is either
    # keyed by the transaction's own permanent id (buyer sales, buyer
    # payments, interest charges, milk entries, expenses - exactly the
    # same id the live hooks use, so backfilling twice just replaces
    # itself with identical figures) or, for the two fields that only
    # ever store a single CUMULATIVE value with no per-installment history
    # (a payment cycle's amount_paid, an interest charge's waived_amount),
    # a dedicated "-backfill" id that is separate from the ids new live
    # payments/waivers get - so re-running this after real usage has
    # started won't double-count new activity, but also won't retroactively
    # capture it (new activity is already covered by the live hooks).
    # Run this once, right after deploying the Accounting module.
    @app.cli.command("backfill-ledger")
    def backfill_ledger():
        """One-time: post ledger entries for all pre-existing transactions."""
        from app.services import ledger_service as L

        counts = {
            "sales": 0, "buyer_payments": 0, "interest_charges": 0, "interest_waivers": 0,
            "milk_entries": 0, "customer_payments": 0, "deductions": 0, "expenses": 0,
        }

        for sale in db.buyer_sales.find():
            L.post_buyer_sale(db, sale)
            counts["sales"] += 1

        for payment in db.buyer_payments.find():
            L.post_buyer_payment(db, payment)
            counts["buyer_payments"] += 1

        for charge in db.interest_charges.find():
            L.post_interest_charge(db, charge)
            counts["interest_charges"] += 1
            waived = charge.get("waived_amount", 0.0)
            if waived:
                L.post_transaction(
                    db,
                    source_collection="interest_charges",
                    source_id=f"{charge['charge_id']}-waiver-backfill",
                    transaction_type="Interest Waived",
                    date=charge.get("waived_at") or charge["calculation_date"],
                    debit_account=L.INTEREST_INCOME,
                    credit_account=L.ACCOUNTS_RECEIVABLE,
                    amount=waived,
                    entity_type="buyer",
                    entity_id=charge["buyer_id"],
                    entity_name=charge["buyer_name"],
                    description=f"Waiver on invoice {charge.get('invoice_id', '')} (backfilled)"
                    + (f" \u2013 {charge['note']}" if charge.get("note") else ""),
                )
                counts["interest_waivers"] += 1

        for entry in db.milk_entries.find():
            L.post_milk_purchase(db, entry, str(entry["_id"]))
            counts["milk_entries"] += 1

        for payment in db.payments.find():
            amount_paid = payment.get("amount_paid", 0.0)
            if amount_paid:
                L.post_transaction(
                    db,
                    source_collection="payments",
                    source_id=f"{payment['payment_id']}-backfill",
                    transaction_type="Payment Made",
                    date=payment.get("payment_date") or payment.get("updated_at"),
                    debit_account=L.ACCOUNTS_PAYABLE,
                    credit_account=L.CASH_BANK,
                    amount=amount_paid,
                    entity_type="customer",
                    entity_id=payment["customer_id"],
                    entity_name=payment["customer_name"],
                    description=f"{payment.get('payment_method') or 'Payment'} \u2013 "
                    f"cycle {payment.get('payment_period', '')} (backfilled)",
                )
                counts["customer_payments"] += 1
            if payment.get("deductions"):
                L.post_customer_deduction(db, payment, payment["deductions"])
                counts["deductions"] += 1

        for expense in db.expenses.find():
            L.post_expense_entry(db, expense)
            counts["expenses"] += 1

        click.echo(
            "Ledger backfill complete: "
            f"{counts['sales']} sale(s), {counts['buyer_payments']} buyer payment(s), "
            f"{counts['interest_charges']} interest charge(s), {counts['interest_waivers']} waiver(s), "
            f"{counts['milk_entries']} milk entrie(s), {counts['customer_payments']} customer payment(s), "
            f"{counts['deductions']} deduction(s), {counts['expenses']} expense/income entrie(s)."
        )

    return app
