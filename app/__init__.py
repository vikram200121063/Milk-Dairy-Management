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

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(milk_entries_bp)
    app.register_blueprint(rate_config_bp)
    app.register_blueprint(payments_bp)

    # --- Root route: send visitors to the dashboard (if logged in) or login page ---
    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard.index"))
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

    return app
