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
