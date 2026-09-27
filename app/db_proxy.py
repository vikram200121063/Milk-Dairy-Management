"""
ScopedDB: a transparent proxy that makes every existing `current_app.db.*`
call site (151 of them, across 16 files) automatically read/write the demo
database instead of the real one, whenever the current session belongs to
a guest (Public Read-Only Demo mode - see app/routes/auth.py's
`guest_login` and the `flask seed-demo-data` CLI command).

Nothing about the routes/services themselves changes: they still just do
`current_app.db.customers.find(...)`, `current_app.db.payments.insert_one(...)`,
etc. This works because pymongo's Database object supports both attribute
access (`db.customers`) and item access (`db["customers"]`), and this class
intercepts both, so it's a drop-in replacement wherever a real `Database`
was used.

Guests never actually get to write anywhere - app/__init__.py's
`before_request` guard blocks every non-GET request for a guest session
before it reaches a route - but this proxy is what makes their GET/read
traffic land on the demo data instead of the real dairy's data, without
having to special-case is_guest in 151 different places.
"""


class ScopedDB:
    """
    Wraps a "real" pymongo Database and a "demo" pymongo Database. Every
    attribute/item access (i.e. every `db.<collection>` or `db["<collection>"]`
    the rest of the app does) is routed to the demo db when the current
    Flask session is a guest session, and to the real db otherwise.

    `session` is imported lazily inside the methods (not at module import
    time) so this module has no import-time dependency on an active Flask
    application/request context - it only needs one at actual access time,
    which is always the case for real usage.
    """

    def __init__(self, real_db, demo_db):
        # Leading underscore + object.__setattr__ avoids ever accidentally
        # tripping our own __getattr__ for these two internal attributes.
        object.__setattr__(self, "_real_db", real_db)
        object.__setattr__(self, "_demo_db", demo_db)

    def _active(self):
        try:
            from flask import session

            if session.get("is_guest"):
                return self._demo_db
        except RuntimeError:
            # No active Flask request/app context (e.g. a CLI command
            # running outside `with app.app_context()`, or code that holds
            # onto a ScopedDB reference from setup time). Fall back to the
            # real db - CLI commands should be using the plain `db`
            # variable directly anyway, never this proxy, but this keeps
            # the proxy itself safe either way.
            pass
        return self._real_db

    def __getattr__(self, name):
        # __getattr__ (not __getattribute__) only fires for names not
        # already found on this instance/class, so _real_db/_demo_db/
        # _active above are never redirected through here.
        return getattr(self._active(), name)

    def __getitem__(self, name):
        return self._active()[name]

    def __repr__(self):
        return f"ScopedDB(real={self._real_db!r}, demo={self._demo_db!r})"
