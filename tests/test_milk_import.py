"""
End-to-end tests for the "Import from Milk Machine" feature
(app/services/milk_import_service.py, app/routes/milk_entries.py's
import_page/import_template/import_preview/import_commit routes).
"""
import io
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from conftest import login

CSV_CONTENT = (
    "Machine Code,Date,Shift,Milk Type,Quantity,Fat%,SNF%\n"
    "104,2026-09-20,Evening,Cow,10.5,4.5,8.8\n"  # ready
    "104,2026-09-27,Morning,Cow,5.0,4.0,8.0\n"  # duplicate (matches seeded entry)
    "999,2026-09-20,Evening,Cow,7.0,4.0,8.0\n"  # unknown machine code
    "104,2026-09-20,Evening,Cow,-3,4.0,8.0\n"  # invalid (bad quantity)
)


def _seed(db):
    db.users.insert_one({"username": "admin", "password_hash": generate_password_hash("pw12345")})

    db.rate_configurations.insert_one(
        {"milk_type": "Cow", "base_rate": 20.0, "fat_rate_per_point": 1.0, "snf_rate_per_point": 0.5}
    )
    db.rate_configurations.insert_one(
        {"milk_type": "Buffalo", "base_rate": 22.0, "fat_rate_per_point": 1.2, "snf_rate_per_point": 0.6}
    )

    # Two customers: one with a machine code set, one without - proves an
    # imported row only matches a customer by machine code, never by name.
    db.customers.insert_one(
        {
            "customer_id": "CUST0001",
            "name": "Ramesh",
            "mobile_number": "9812345678",
            "email": "",
            "address": "",
            "village": "Test Village",
            "machine_code": "104",
            "registration_date": datetime.now(timezone.utc),
            "status": "Active",
        }
    )
    db.customers.insert_one(
        {
            "customer_id": "CUST0002",
            "name": "Suresh",
            "mobile_number": "9812345679",
            "email": "",
            "address": "",
            "village": "Test Village",
            "machine_code": "",
            "registration_date": datetime.now(timezone.utc),
            "status": "Active",
        }
    )
    # An existing entry for Ramesh on 2026-09-27/Morning, so the import's
    # duplicate-detection path has something to collide with.
    db.milk_entries.insert_one(
        {
            "customer_id": "CUST0001",
            "customer_name": "Ramesh",
            "date": datetime(2026, 9, 27, tzinfo=timezone.utc),
            "shift": "Morning",
            "milk_type": "Cow",
            "quantity": 5.0,
            "fat_percentage": 4.0,
            "snf_percentage": 8.0,
            "rate_per_litre": 28.0,
            "rate_mode": "Automatic",
            "total_amount": 140.0,
            "notes": "",
            "created_at": datetime.now(timezone.utc),
        }
    )


def test_import_page_and_template_load(app, db, client):
    _seed(db)
    login(client, "admin")

    resp = client.get("/milk-entries/import")
    assert resp.status_code == 200

    resp = client.get("/milk-entries/import/template")
    assert resp.status_code == 200
    assert b"machine_code" in resp.data


def test_preview_flags_every_row_status(app, db, client):
    _seed(db)
    login(client, "admin")

    data = {
        "file": (io.BytesIO(CSV_CONTENT.encode("utf-8")), "machine_export.csv"),
        "default_date": "2026-09-20",
        "default_shift": "Evening",
        "default_milk_type": "Cow",
    }
    resp = client.post("/milk-entries/import/preview", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Ready" in body
    assert "Duplicate" in body
    assert "Unknown Code" in body
    assert "Invalid" in body


def test_commit_inserts_only_ready_rows_with_server_side_rate(app, db):
    _seed(db)
    with app.app_context():
        from app.services import milk_import_service

        raw_rows, err = milk_import_service.parse_import_file(
            type("F", (), {"filename": "x.csv", "read": lambda self: CSV_CONTENT.encode("utf-8")})()
        )
        assert err is None
        assert len(raw_rows) == 4

        preview = milk_import_service.build_preview(db, raw_rows, "2026-09-20", "Evening", "Cow")
        statuses = [r["status"] for r in preview]
        assert statuses == ["ready", "duplicate", "unknown_code", "invalid"]

        ready_rows = [r for r in preview if r["status"] == "ready"]
        before_count = db.milk_entries.count_documents({})
        summary = milk_import_service.commit_import(db, ready_rows, "machine_export.csv", "admin")
        after_count = db.milk_entries.count_documents({})

        assert summary["inserted"] == 1
        assert after_count == before_count + 1

        new_entry = db.milk_entries.find_one({"customer_id": "CUST0001", "shift": "Evening"})
        assert new_entry is not None
        assert new_entry["customer_name"] == "Ramesh"
        # Rate is always recomputed server-side from the current rate
        # configuration, never trusted from the uploaded file.
        expected_rate = round(20.0 + 4.5 * 1.0 + 8.8 * 0.5, 2)
        assert new_entry["rate_per_litre"] == expected_rate
        assert new_entry["total_amount"] == round(10.5 * expected_rate, 2)

        import_log = list(db.milk_imports.find())
        assert len(import_log) == 1
        assert import_log[0]["inserted"] == 1

        # Re-running the same commit again must skip as duplicate, not
        # double-insert the same reading.
        before_count2 = db.milk_entries.count_documents({})
        summary2 = milk_import_service.commit_import(db, ready_rows, "machine_export.csv", "admin")
        after_count2 = db.milk_entries.count_documents({})
        assert summary2["skipped_duplicate"] == 1
        assert summary2["inserted"] == 0
        assert after_count2 == before_count2


def test_guest_cannot_commit_an_import(app, db, client):
    _seed(db)
    client.get("/guest-login", follow_redirects=False)
    resp = client.post(
        "/milk-entries/import/commit", data={"filename": "x.csv", "rows_json": "[]"}, follow_redirects=False
    )
    assert resp.status_code in (301, 302)
