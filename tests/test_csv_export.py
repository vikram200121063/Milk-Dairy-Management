"""
Tests for the "Download CSV" buttons on Customers, Milk Entries, and Buyer
Invoices - app/utils/csv_export.py plus each blueprint's export_csv route.
"""
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from conftest import login


def _seed(db):
    db.users.insert_one({"username": "owner1", "password_hash": generate_password_hash("pw12345"), "role": "Owner"})
    db.customers.insert_one(
        {
            "customer_id": "CUST0001", "name": "Ramesh", "mobile_number": "9812345678",
            "email": "", "address": "Addr", "village": "V1", "machine_code": "104",
            "status": "Active", "registration_date": datetime.now(timezone.utc),
        }
    )
    db.customers.insert_one(
        {
            "customer_id": "CUST0002", "name": "Suresh", "mobile_number": "9812345679",
            "email": "", "address": "Addr2", "village": "V2", "machine_code": "",
            "status": "Inactive", "registration_date": datetime.now(timezone.utc),
        }
    )
    db.milk_entries.insert_one(
        {
            "customer_id": "CUST0001", "customer_name": "Ramesh",
            "date": datetime(2026, 9, 20, tzinfo=timezone.utc), "shift": "Morning",
            "milk_type": "Cow", "quantity": 10.0, "fat_percentage": 4.0, "snf_percentage": 8.0,
            "rate_per_litre": 25.0, "rate_mode": "Manual", "total_amount": 250.0,
            "notes": "", "created_at": datetime.now(timezone.utc),
        }
    )


def test_customers_export_returns_csv_of_all_customers(app, db, client):
    _seed(db)
    login(client, "owner1")

    resp = client.get("/customers/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert body.startswith("Customer ID,Name,Mobile Number")
    assert "Ramesh" in body
    assert "Suresh" in body


def test_customers_export_respects_status_filter(app, db, client):
    _seed(db)
    login(client, "owner1")

    resp = client.get("/customers/export.csv?status=Active")
    body = resp.data.decode("utf-8")
    assert "Ramesh" in body
    assert "Suresh" not in body


def test_milk_entries_export_returns_csv(app, db, client):
    _seed(db)
    login(client, "owner1")

    resp = client.get("/milk-entries/export.csv")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert body.startswith("Date,Shift,Customer ID")
    assert "CUST0001" in body and "250.0" in body


def test_milk_entries_export_respects_customer_filter(app, db, client):
    _seed(db)
    login(client, "owner1")

    resp = client.get("/milk-entries/export.csv?customer_id=CUST0002")
    body = resp.data.decode("utf-8")
    assert "CUST0001" not in body


def test_buyer_invoices_export_respects_period_and_status(app, db, client):
    db.users.insert_one({"username": "owner1", "password_hash": generate_password_hash("pw12345"), "role": "Owner"})
    # Two different buyers, same billing period - a buyer can only have one
    # invoice per period (there's a unique index on buyer_id+billing_period,
    # since invoices are consolidated), so distinct invoices in the same
    # period need distinct buyers.
    db.buyers.insert_one({"buyer_id": "BUY0001", "company_name": "Test Traders", "status": "Active"})
    db.buyers.insert_one({"buyer_id": "BUY0002", "company_name": "Second Traders", "status": "Active"})
    db.buyer_invoices.insert_one(
        {
            "invoice_id": "INV0001", "buyer_id": "BUY0001", "buyer_name": "Test Traders",
            "billing_period": "2026-09-B2", "total_amount": 5000.0, "amount_paid": 0.0,
            "remaining_amount": 5000.0, "status": "Unpaid",
            "due_date": datetime(2026, 10, 5, tzinfo=timezone.utc), "overdue_days": 0,
        }
    )
    db.buyer_invoices.insert_one(
        {
            "invoice_id": "INV0002", "buyer_id": "BUY0002", "buyer_name": "Second Traders",
            "billing_period": "2026-09-B2", "total_amount": 2000.0, "amount_paid": 2000.0,
            "remaining_amount": 0.0, "status": "Paid",
            "due_date": datetime(2026, 10, 5, tzinfo=timezone.utc), "overdue_days": 0,
        }
    )
    login(client, "owner1")

    resp = client.get("/buyer-invoices/export.csv?year=2026&month=9&period=2")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert body.startswith("Invoice ID,Buyer ID,Buyer Name")
    assert "INV0001" in body and "INV0002" in body

    resp2 = client.get("/buyer-invoices/export.csv?year=2026&month=9&period=2&status=Paid")
    body2 = resp2.data.decode("utf-8")
    assert "INV0002" in body2
    assert "INV0001" not in body2


def test_staff_cannot_export_buyer_invoices(app, db, client):
    db.users.insert_one({"username": "staff1", "password_hash": generate_password_hash("pw12345"), "role": "Staff"})
    login(client, "staff1")

    resp = client.get("/buyer-invoices/export.csv", follow_redirects=False)
    assert resp.status_code in (301, 302)
