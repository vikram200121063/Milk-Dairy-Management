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

    return app
