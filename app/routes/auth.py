from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
)
from werkzeug.security import check_password_hash

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, don't show the login form again
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are both required.", "danger")
            return render_template("login.html")

        user = current_app.db.users.find_one({"username": username})

        # Note: we deliberately give the same error for "no such user" and
        # "wrong password" so we don't reveal which usernames exist.
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = str(user["_id"])
            session["username"] = user["username"]
            session["profile_photo"] = user.get("profile_photo")
            # Accounts created before roles existed have no "role" field;
            # default them to "Owner" so nobody loses access silently.
            session["role"] = user.get("role", "Owner")
            flash(f"Welcome back, {user['username']}!", "success")

            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/guest-login")
def guest_login():
    """
    Public "View Live Demo" entry point - no password. Flags the session
    `is_guest` and sends the visitor straight to the dashboard - they
    browse this dairy's REAL, LIVE data (current_app.db, unchanged, same
    as everyone else) but can never save a change: app/__init__.py's
    before_request guard blocks every non-GET request for a guest
    session before it reaches a route.

    session["user_id"] is intentionally NOT a real user's id here (there
    is no separate guest account) - it's just a truthy placeholder so the
    admin nav renders. app/routes/profile.py explicitly checks
    `is_guest` first and redirects away before it would otherwise try
    (and fail) to look this up as a real user.
    """
    session.clear()
    session["user_id"] = "guest"
    session["username"] = "Guest Viewer"
    session["profile_photo"] = None
    session["is_guest"] = True
    flash(
        "You're viewing this dairy's live data in read-only mode – "
        "nothing you add, edit, or delete here is actually saved.",
        "info",
    )
    return redirect(url_for("dashboard.index"))
