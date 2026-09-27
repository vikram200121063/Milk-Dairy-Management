from functools import wraps
from flask import session, redirect, url_for, flash, request


def login_required(view_func):
    """
    Decorator that redirects anonymous visitors to the login page.
    Usage:
        @app.route("/some-protected-page")
        @login_required
        def some_view():
            ...
    Put @login_required directly under @<blueprint>.route(...) so it runs
    on every request to that view.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            # Remember where the user was trying to go, so we can send
            # them back there after a successful login.
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view


def owner_required(view_func):
    """
    Like @login_required, but also requires the "Owner" role - use this on
    top of (not instead of) routes that touch money or business-sensitive
    data: accounting, expenses, buyer invoices/sales, rate configuration,
    recording payments, and the notification/dunning log. A "Staff" user
    (see the `flask create-staff` CLI command) can log in and use the
    day-to-day customer/milk-entry screens, but is redirected away from
    these with an explanation rather than seeing a raw 403.

    Accounts created before roles existed have no "role" field at all;
    `session.get("role", "Owner")` treats that as "Owner" so nobody who
    already had full access loses it silently. The Guest/read-only demo
    session (see auth.guest_login) also never sets "role", so a visitor
    browsing the live demo still sees every page - guest mode's own
    before_request guard (app/__init__.py) is what keeps it read-only,
    not this decorator.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        if session.get("role", "Owner") != "Owner":
            flash("This section is restricted to the dairy owner.", "warning")
            return redirect(url_for("dashboard.index"))
        return view_func(*args, **kwargs)

    return wrapped_view


def owner_required_json(view_func):
    """
    Same access rule as @owner_required, but for JSON/AJAX endpoints (the
    Business Assistant chat API) where a redirect response would just
    break the calling JavaScript. Returns a 403 JSON error instead.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        from flask import jsonify

        if "user_id" not in session:
            return jsonify({"error": "Please log in to continue."}), 401
        if session.get("role", "Owner") != "Owner":
            return jsonify({"error": "This is restricted to the dairy owner."}), 403
        return view_func(*args, **kwargs)

    return wrapped_view


def customer_login_required(view_func):
    """
    Decorator that redirects anonymous visitors to the customer portal
    login page. Mirrors login_required above but checks the separate
    'customer_id' session key, so an admin session and a customer
    session never get confused with each other.
    Usage:
        @customer_portal_bp.route("/some-page")
        @customer_login_required
        def some_view():
            ...
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("customer_portal.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view
