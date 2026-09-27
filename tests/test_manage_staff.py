"""
Tests for the UI-based "Manage Staff" screen (app/routes/users.py) - lets
an Owner add, list, change the role of, reset the password for, and
delete admin accounts without needing terminal/CLI access.
"""
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import login


def _seed_owner(db, username="owner1"):
    db.users.insert_one({"username": username, "password_hash": generate_password_hash("pw12345"), "role": "Owner"})


def test_staff_cannot_reach_manage_staff(app, db, client):
    db.users.insert_one({"username": "staff1", "password_hash": generate_password_hash("pw12345"), "role": "Staff"})
    login(client, "staff1")

    resp = client.get("/users/", follow_redirects=False)
    assert resp.status_code in (301, 302)


def test_owner_can_list_and_add_a_staff_account(app, db, client):
    _seed_owner(db)
    login(client, "owner1")

    resp = client.get("/users/")
    assert resp.status_code == 200
    assert b"owner1" in resp.data

    resp = client.get("/users/add")
    assert resp.status_code == 200

    resp = client.get("/users/owner1/reset-password")
    assert resp.status_code == 200

    resp = client.post(
        "/users/add",
        data={"username": "newstaff", "password": "pw12345", "confirm_password": "pw12345", "role": "Staff"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)

    created = db.users.find_one({"username": "newstaff"})
    assert created is not None
    assert created["role"] == "Staff"
    assert check_password_hash(created["password_hash"], "pw12345")


def test_add_account_rejects_duplicate_username_and_mismatched_passwords(app, db, client):
    _seed_owner(db)
    login(client, "owner1")

    resp = client.post(
        "/users/add",
        data={"username": "owner1", "password": "pw12345", "confirm_password": "pw12345", "role": "Owner"},
        follow_redirects=True,
    )
    assert b"already exists" in resp.data
    assert db.users.count_documents({}) == 1

    resp2 = client.post(
        "/users/add",
        data={"username": "brandnew", "password": "pw12345", "confirm_password": "different", "role": "Staff"},
        follow_redirects=True,
    )
    assert b"do not match" in resp2.data
    assert db.users.find_one({"username": "brandnew"}) is None


def test_owner_can_reset_another_accounts_password(app, db, client):
    _seed_owner(db)
    db.users.insert_one(
        {"username": "staff1", "password_hash": generate_password_hash("oldpassword"), "role": "Staff"}
    )
    login(client, "owner1")

    resp = client.post(
        "/users/staff1/reset-password",
        data={"password": "brandnewpw", "confirm_password": "brandnewpw"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)

    updated = db.users.find_one({"username": "staff1"})
    assert check_password_hash(updated["password_hash"], "brandnewpw")
    assert not check_password_hash(updated["password_hash"], "oldpassword")


def test_owner_can_promote_and_demote_another_account(app, db, client):
    _seed_owner(db)
    db.users.insert_one({"username": "staff1", "password_hash": generate_password_hash("pw12345"), "role": "Staff"})
    login(client, "owner1")

    client.post("/users/staff1/role", data={"role": "Owner"}, follow_redirects=False)
    assert db.users.find_one({"username": "staff1"})["role"] == "Owner"

    client.post("/users/staff1/role", data={"role": "Staff"}, follow_redirects=False)
    assert db.users.find_one({"username": "staff1"})["role"] == "Staff"


def test_owner_can_demote_the_only_other_owner_and_remains_owner_themselves(app, db, client):
    """
    Demoting every other Owner to Staff is allowed - you can never be
    locked out through this screen because you can't touch your own
    account (see the two tests below), so your own login always stays a
    valid Owner account no matter what you do to everyone else's.
    """
    _seed_owner(db)
    db.users.insert_one({"username": "owner2", "password_hash": generate_password_hash("pw12345"), "role": "Owner"})
    login(client, "owner1")

    resp = client.post("/users/owner2/role", data={"role": "Staff"}, follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert db.users.find_one({"username": "owner2"})["role"] == "Staff"
    assert db.users.find_one({"username": "owner1"})["role"] == "Owner"


def test_cannot_change_your_own_role(app, db, client):
    _seed_owner(db)
    db.users.insert_one(
        {"username": "owner2", "password_hash": generate_password_hash("pw12345"), "role": "Owner"}
    )
    login(client, "owner1")

    resp = client.post("/users/owner1/role", data={"role": "Staff"}, follow_redirects=True)
    assert b"own role" in resp.data
    assert db.users.find_one({"username": "owner1"})["role"] == "Owner"


def test_cannot_delete_your_own_account(app, db, client):
    _seed_owner(db)
    login(client, "owner1")

    resp = client.post("/users/owner1/delete", follow_redirects=True)
    assert b"own account" in resp.data
    assert db.users.find_one({"username": "owner1"}) is not None


def test_owner_can_delete_the_only_other_owner(app, db, client):
    _seed_owner(db)
    db.users.insert_one({"username": "owner2", "password_hash": generate_password_hash("pw12345"), "role": "Owner"})
    login(client, "owner1")

    resp = client.post("/users/owner2/delete", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert db.users.find_one({"username": "owner2"}) is None
    # owner1 (the actor) is always untouched by an action targeting someone
    # else, so there's always at least one Owner login left afterwards.
    assert db.users.find_one({"username": "owner1"}) is not None


def test_guest_cannot_write_on_manage_staff(app, db, client):
    _seed_owner(db)
    client.get("/guest-login", follow_redirects=False)

    resp = client.get("/users/")
    assert resp.status_code == 200  # guest can view (RBAC defaults guest to Owner)

    before = db.users.count_documents({})
    resp2 = client.post(
        "/users/add",
        data={"username": "sneaky", "password": "pw12345", "confirm_password": "pw12345", "role": "Owner"},
        follow_redirects=False,
    )
    assert resp2.status_code in (301, 302)
    assert db.users.count_documents({}) == before
