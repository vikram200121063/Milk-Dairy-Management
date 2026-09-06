from datetime import datetime, timezone

import click
from flask import Flask, jsonify, redirect, url_for, session
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError
from werkzeug.security import generate_password_hash

from config import Config


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

    # Enforce that usernames are unique at the database level (not just in
    # application code). create_index is safe to call every startup - it's
    # a no-op if the index already exists.
    db.users.create_index("username", unique=True)
    db.customers.create_index("customer_id", unique=True)
    db.customers.create_index("mobile_number", unique=True)
    db.customers.create_index("status")

    # Unique compound index: this is what makes duplicate entries for the
    # same customer/date/shift actually IMPOSSIBLE, not just discouraged.
    db.milk_entries.create_index(
        [("customer_id", 1), ("date", 1), ("shift", 1)], unique=True
    )
    db.milk_entries.create_index("date")
    db.milk_entries.create_index("customer_id")

    db.rate_configurations.create_index("milk_type", unique=True)

    db.payments.create_index([("customer_id", 1), ("payment_period", 1)], unique=True)
    db.payments.create_index("payment_id", unique=True)
    db.payments.create_index("payment_period")
    db.payments.create_index("payment_status")

    # Audit log of every SMS/email notification attempt (see
    # app/services/notification_service.py).
    db.notifications.create_index([("sent_at", -1)])
    db.notifications.create_index("customer_id")

    # --- Buyer Management & Accounting module ---
    # All new collections/indexes below are additive - nothing above this
    # block was touched, so the existing Customer Milk Collection &
    # Payment functionality is unaffected.
    db.buyers.create_index("buyer_id", unique=True)
    db.buyers.create_index("gst_number", unique=True, sparse=True)
    db.buyers.create_index("status")

    db.buyer_sales.create_index("sale_id", unique=True)
    db.buyer_sales.create_index([("buyer_id", 1), ("sale_date", 1)])
    db.buyer_sales.create_index("sale_date")

    db.buyer_invoices.create_index("invoice_id", unique=True)
    db.buyer_invoices.create_index([("buyer_id", 1), ("billing_period", 1)], unique=True)
    db.buyer_invoices.create_index("status")
    db.buyer_invoices.create_index("due_date")
    db.buyer_invoices.create_index("billing_period")

    db.buyer_payments.create_index("payment_id", unique=True)
    db.buyer_payments.create_index("invoice_id")
    db.buyer_payments.create_index("buyer_id")

    db.expenses.create_index("expense_id", unique=True)
    db.expenses.create_index("date")
    db.expenses.create_index("category")
    db.expenses.create_index("entry_type")

    db.dunning_records.create_index([("invoice_id", 1), ("sent_at", -1)])
    db.dunning_records.create_index("record_type")

    db.interest_charges.create_index("charge_id", unique=True)
    db.interest_charges.create_index("invoice_id")
    db.interest_charges.create_index("status")

    # --- Common Accounting module: the general ledger ---
    db.ledger_entries.create_index("entry_id", unique=True)
    db.ledger_entries.create_index([("source_collection", 1), ("source_id", 1)])
    db.ledger_entries.create_index("account")
    db.ledger_entries.create_index([("entity_type", 1), ("entity_id", 1)])
    db.ledger_entries.create_index("date")
    db.ledger_entries.create_index("transaction_id")

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
