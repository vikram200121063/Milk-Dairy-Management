"""
Tests for the Owner/Staff role-based access control on the financial
blueprints (buyers, buyer_sales, buyer_invoices, expenses, accounting,
rate_config, payments, notifications) and the AI assistant.
"""
from werkzeug.security import generate_password_hash

from conftest import login

RESTRICTED_GET_ROUTES = [
    "/buyers/",
    "/buyer-sales/",
    "/buyer-invoices/",
    "/expenses/",
    "/accounting/",
    "/rate-config/",
    "/payments/",
    "/notifications/",
]
OPEN_GET_ROUTES = [
    "/dashboard",
    "/customers/",
    "/milk-entries/",
    "/milk-entries/import",
]


def _seed_users(db):
    db.users.insert_one(
        {"username": "owner1", "password_hash": generate_password_hash("pw12345"), "role": "Owner"}
    )
    db.users.insert_one(
        {"username": "staff1", "password_hash": generate_password_hash("pw12345"), "role": "Staff"}
    )
    # An account created before roles existed at all - no "role" field.
    db.users.insert_one(
        {"username": "legacyadmin", "password_hash": generate_password_hash("pw12345")}
    )


def test_staff_is_blocked_from_restricted_routes(app, db, client):
    _seed_users(db)
    login(client, "staff1")

    for route in RESTRICTED_GET_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code in (301, 302), f"staff GET {route} should be blocked"


def test_staff_can_use_open_routes(app, db, client):
    _seed_users(db)
    login(client, "staff1")

    for route in OPEN_GET_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 200, f"staff GET {route} should be allowed"


def test_staff_dashboard_hides_revenue_data_and_nav_links(app, db, client):
    _seed_users(db)
    login(client, "staff1")

    resp = client.get("/dashboard")
    # The real security property: revenue/expense figures must not be in
    # the page's embedded chart JSON at all, not just visually hidden.
    assert b'"expense"' not in resp.data
    assert b"milk_quantity" in resp.data
    assert b'href="/buyers/"' not in resp.data
    assert b'href="/accounting/"' not in resp.data


def test_staff_ai_assistant_returns_json_403(app, db, client):
    _seed_users(db)
    login(client, "staff1")

    resp = client.post("/ai/ask", json={"question": "How much revenue this month?"})
    assert resp.status_code == 403
    assert b"restricted" in resp.data.lower()


def test_staff_can_still_add_a_customer(app, db, client):
    _seed_users(db)
    db.rate_configurations.insert_one(
        {"milk_type": "Cow", "base_rate": 20.0, "fat_rate_per_point": 1.0, "snf_rate_per_point": 0.5}
    )
    login(client, "staff1")

    resp = client.post(
        "/customers/add",
        data={
            "name": "Staff Added Customer",
            "mobile_number": "9812399999",
            "email": "",
            "address": "",
            "village": "V",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)


def test_owner_has_full_access_including_revenue_data(app, db, client):
    _seed_users(db)
    login(client, "owner1")

    for route in RESTRICTED_GET_ROUTES + OPEN_GET_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 200, f"owner GET {route} should be allowed"

    resp = client.get("/dashboard")
    assert b'"expense"' in resp.data


def test_legacy_account_without_role_field_behaves_as_owner(app, db, client):
    _seed_users(db)
    login(client, "legacyadmin")

    for route in RESTRICTED_GET_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 200, f"legacy (no-role) GET {route} should be allowed"


def test_guest_can_view_restricted_routes_but_not_write(app, db, client):
    _seed_users(db)
    client.get("/guest-login", follow_redirects=False)

    # RBAC doesn't block guest viewing - the pre-existing guest guard
    # (before_request) is what makes guest mode read-only, not roles.
    for route in RESTRICTED_GET_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 200, f"guest GET {route} should be allowed (read-only demo)"

    resp = client.post("/rate-config/edit/Cow", data={"base_rate": "999"}, follow_redirects=False)
    assert resp.status_code in (301, 302)
