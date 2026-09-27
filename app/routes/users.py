"""
Owner-only "Manage Staff" screen: add, list, change the role of, reset the
password for, and remove admin accounts (Owner/Staff) from the UI, so
running this dairy day-to-day never needs terminal/server access.

The `flask create-admin` / `flask create-staff` / `flask reset-password`
CLI commands (app/__init__.py) still exist and still work - they're the
only way to create the very first Owner account (there's no logged-in
Owner yet to use this screen with) - but for every account after that,
this is the normal way to manage who can log in.
"""
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from werkzeug.security import generate_password_hash

from app.utils.decorators import owner_required

users_bp = Blueprint("users", __name__, url_prefix="/users")

ROLES = ("Owner", "Staff")


def _is_owner_doc(user_doc):
    """Matches the same default used everywhere else: no role field = Owner."""
    return user_doc.get("role", "Owner") == "Owner"


@users_bp.route("/")
@owner_required
def list_users():
    accounts = list(current_app.db.users.find().sort("username", 1))
    return render_template("users/list.html", accounts=accounts, is_owner_doc=_is_owner_doc)


@users_bp.route("/add", methods=["GET", "POST"])
@owner_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "Staff")

        errors = []
        if not username:
            errors.append("Username is required.")
        elif current_app.db.users.find_one({"username": username}):
            errors.append(f"An account with username '{username}' already exists.")

        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if role not in ROLES:
            errors.append("Choose a valid role.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("users/form.html", username=username, role=role)

        current_app.db.users.insert_one(
            {
                "username": username,
                "password_hash": generate_password_hash(password),
                "role": role,
                "created_at": datetime.now(timezone.utc),
            }
        )
        flash(f"{role} account '{username}' created successfully.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/form.html", username="", role="Staff")


@users_bp.route("/<username>/reset-password", methods=["GET", "POST"])
@owner_required
def reset_password(username):
    account = current_app.db.users.find_one({"username": username})
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("users.list_users"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("users/reset_password.html", username=username)

        current_app.db.users.update_one(
            {"username": username}, {"$set": {"password_hash": generate_password_hash(password)}}
        )
        flash(f"Password for '{username}' has been reset.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/reset_password.html", username=username)


@users_bp.route("/<username>/role", methods=["POST"])
@owner_required
def change_role(username):
    # You can never be locked out through this screen: you can't change
    # your own role or delete your own account (see delete_user below),
    # and every route here requires you to already be an Owner - so your
    # own account always stays a valid Owner login no matter what you do
    # to anyone else's.
    if username == session.get("username"):
        flash("You can't change your own role.", "danger")
        return redirect(url_for("users.list_users"))

    account = current_app.db.users.find_one({"username": username})
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("users.list_users"))

    new_role = request.form.get("role")
    if new_role not in ROLES:
        flash("Choose a valid role.", "danger")
        return redirect(url_for("users.list_users"))

    current_app.db.users.update_one({"username": username}, {"$set": {"role": new_role}})
    flash(f"'{username}' is now {new_role}.", "success")
    return redirect(url_for("users.list_users"))


@users_bp.route("/<username>/delete", methods=["POST"])
@owner_required
def delete_user(username):
    if username == session.get("username"):
        flash("You can't delete your own account while logged in as it.", "danger")
        return redirect(url_for("users.list_users"))

    account = current_app.db.users.find_one({"username": username})
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("users.list_users"))

    current_app.db.users.delete_one({"username": username})
    flash(f"Account '{username}' deleted.", "info")
    return redirect(url_for("users.list_users"))
