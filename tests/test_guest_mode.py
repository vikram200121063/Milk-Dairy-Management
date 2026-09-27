"""
Tests for the public "Guest / Read-Only Demo" mode (app/routes/auth.py's
guest_login route and app/__init__.py's block_guest_writes before_request
guard). Guest mode browses this dairy's real data (there is no separate
demo database) but can never write to it.
"""
from werkzeug.security import generate_password_hash


def test_guest_login_sets_session_flags_and_redirects(client):
    resp = client.get("/guest-login", follow_redirects=False)
    assert resp.status_code in (301, 302)

    with client.session_transaction() as sess:
        assert sess.get("is_guest") is True
        assert sess.get("user_id")


def test_guest_can_view_dashboard_with_demo_banner(client):
    client.get("/guest-login", follow_redirects=False)

    resp = client.get("/dashboard", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Demo Mode" in resp.data


def test_guest_write_is_blocked_and_does_not_persist(app, db, client):
    client.get("/guest-login", follow_redirects=False)

    before_count = db.customers.count_documents({})
    resp = client.post(
        "/customers/add",
        data={
            "name": "Guest Should Not Persist",
            "mobile_number": "9999999999",
            "email": "",
            "address": "x",
            "village": "x",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)

    after_count = db.customers.count_documents({})
    assert after_count == before_count


def test_guest_cannot_delete_an_expense(client):
    client.get("/guest-login", follow_redirects=False)
    resp = client.post("/expenses/delete/EXP00001", follow_redirects=False)
    assert resp.status_code in (301, 302)


def test_pay_blueprint_is_exempt_from_the_guest_write_block(app, db, client):
    """
    The public buyer-payment-link flow (app/routes/pay.py) must keep
    working even if the same browser previously visited /guest-login and
    never logged out of the admin demo - a buyer paying a real invoice
    should never be silently blocked by someone else's leftover admin
    session in a shared browser. This hits create-order with a bad/expired
    token (no real invoice needed) just to prove the guest guard doesn't
    intercept it with a redirect-and-flash before the route's own "invalid
    link" handling runs.
    """
    client.get("/guest-login", follow_redirects=False)
    resp = client.post("/pay/invoice/not-a-real-token/create-order", follow_redirects=False)
    # Blocked-by-guest-guard would be a 301/302 redirect to the referrer;
    # the pay blueprint's own handling returns a JSON 404 instead.
    assert resp.status_code == 404


def test_real_login_is_unaffected_by_a_prior_guest_session(app, db, client):
    """
    A guest browsing in one session/cookie must never affect an unrelated
    real admin session (a different browser, or the same browser after
    logging out) - each Flask test client is its own cookie jar, so a
    second client here stands in for "a different login session" and
    proves nothing written by the real login leaks into or is blocked by
    the guest one.
    """
    db.users.insert_one({"username": "realadmin", "password_hash": generate_password_hash("pw12345")})

    # The guest session from the `client` fixture keeps browsing...
    client.get("/guest-login", follow_redirects=False)

    # ...while a completely separate session logs in for real.
    real_client = app.test_client()
    login_resp = real_client.post(
        "/login", data={"username": "realadmin", "password": "pw12345"}, follow_redirects=False
    )
    assert login_resp.status_code in (301, 302)

    with real_client.session_transaction() as sess:
        assert not sess.get("is_guest")

    before = db.customers.count_documents({})
    post_resp = real_client.post(
        "/customers/add",
        data={
            "name": "Real Customer One",
            "mobile_number": "9812345678",
            "email": "",
            "address": "Real address",
            "village": "Real village",
        },
        follow_redirects=False,
    )
    assert post_resp.status_code in (301, 302)

    after = db.customers.count_documents({})
    assert after == before + 1
