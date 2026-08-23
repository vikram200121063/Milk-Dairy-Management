from datetime import datetime, timezone, date as date_cls

from flask import Blueprint, render_template, session, current_app

from app.utils.decorators import login_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    db = current_app.db
    today = date_cls.today()
    # Milk entries are stored at UTC midnight for their calendar day (see
    # milk_entries.py), so an exact match against today's midnight finds
    # every entry logged "today".
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    total_customers = db.customers.count_documents({})
    active_customers = db.customers.count_documents({"status": "Active"})

    today_entries = list(db.milk_entries.find({"date": today_start}))
    today_stats = {
        "count": len(today_entries),
        "quantity": sum(e["quantity"] for e in today_entries),
        "amount": sum(e["total_amount"] for e in today_entries),
    }

    pending_payments = list(
        db.payments.find({"payment_status": {"$in": ["Pending", "Partially Paid"]}})
    )
    pending_stats = {
        "count": len(pending_payments),
        "amount": sum(p["remaining_amount"] for p in pending_payments),
    }

    recent_entries = list(db.milk_entries.find().sort("created_at", -1).limit(5))
    recent_payments = list(db.payments.find().sort("updated_at", -1).limit(5))

    return render_template(
        "dashboard.html",
        username=session.get("username"),
        total_customers=total_customers,
        active_customers=active_customers,
        today_stats=today_stats,
        pending_stats=pending_stats,
        recent_entries=recent_entries,
        recent_payments=recent_payments,
    )
