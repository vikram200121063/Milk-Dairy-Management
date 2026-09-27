"""
Shared pytest fixtures for the whole test suite.

Every test gets a brand-new in-memory database (tests/fake_mongo.py) and a
brand-new Flask app instance via the `app` fixture, so tests never see each
other's data, never touch a real MongoDB Atlas cluster, and can run in any
order. No real Razorpay account is used either - tests/_fakes/razorpay
stands in for the real `razorpay` package's Client during the test run, so
nothing here ever makes a real network call or costs anything. Running
this suite is completely free.

Run the whole suite from the project root with:
    pytest
"""
import os
import sys
from pathlib import Path

# Make the project root importable (so `from app import create_app` and
# `from config import Config` work regardless of where `pytest` is run
# from) and put tests/ itself on sys.path so `import fake_mongo` below
# resolves to tests/fake_mongo.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for p in (str(PROJECT_ROOT), str(TESTS_DIR), str(TESTS_DIR / "_fakes")):
    if p not in sys.path:
        sys.path.insert(0, p)

import fake_mongo  # tests/fake_mongo.py

# This MUST run before `app` (or anything importing pymongo/razorpay) is
# imported for the first time in this process - it swaps fake, in-memory
# stand-ins into sys.modules for "pymongo", "bson", and (via the
# tests/_fakes path added above) "razorpay".
fake_mongo.install()

import pytest

# --- Test environment configuration ---------------------------------------
# Harmless placeholder values - never a real secret, database, or paid
# account. Config.validate() only checks that SECRET_KEY/MONGO_URI are
# *present*, and payment_gateway_service.gateway_configured() /
# webhook_configured() only check that these Razorpay values are *set* -
# nothing here ever reaches a real service (see fake_mongo.install() above
# and tests/_fakes/razorpay).
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("MONGO_URI", "mongodb://fake/")
os.environ.setdefault("MONGO_DB_NAME", "milk_dairy_test")
os.environ.setdefault("FLASK_DEBUG", "True")
os.environ.setdefault("RAZORPAY_KEY_ID", "rzp_test_fake123")
os.environ.setdefault("RAZORPAY_KEY_SECRET", "fakesecret")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "whsec_test_fake")

from app import create_app  # noqa: E402  (must come after fake_mongo.install())


@pytest.fixture
def app():
    """
    A fresh Flask app, backed by a fresh in-memory database (every call to
    create_app() builds a brand-new fake MongoClient - see fake_mongo.py -
    so no test can see another test's data).
    """
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def db(app):
    """Direct access to the fresh in-memory database, for seeding/assertions."""
    return app.db


@pytest.fixture
def client(app):
    """Flask's test client, bound to the same fresh app/database as `db`."""
    return app.test_client()


def login(client, username, password="pw12345"):
    """Posts the login form and returns the response (doesn't follow redirects)."""
    return client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
