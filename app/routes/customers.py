import re
from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)

from app.utils.decorators import login_required
from app.utils.id_generator import get_next_sequence
from app.services import ledger_service

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

# Indian 10-digit mobile numbers start with 6-9. Adjust this pattern if
# your dairy's customers use a different numbering format.
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _read_and_validate_form(form):
    """
    Pulls fields out of the submitted form, trims whitespace, and checks
    them. Returns (list_of_error_strings, cleaned_data_dict).
    Centralizing this means add_customer and edit_customer can't drift
    apart and validate things differently.
    """
    data = {
        "name": form.get("name", "").strip(),
        "mobile_number": form.get("mobile_number", "").strip(),
        "email": form.get("email", "").strip(),
        "address": form.get("address", "").strip(),
        "village": form.get("village", "").strip(),
    }

    errors = []
    if not data["name"]:
        errors.append("Customer name is required.")
    if not MOBILE_RE.match(data["mobile_number"]):
        errors.append("Enter a valid 10-digit mobile number.")
    # Email is optional (used only for invoice/milk-entry notifications),
    # but if one is provided it must look like a real address.
    if data["email"] and not EMAIL_RE.match(data["email"]):
        errors.append("Enter a valid email address, or leave it blank.")
    if not data["village"]:
        errors.append("Village/Location is required.")

    return errors, data


@customers_bp.route("/")
@login_required
def list_customers():
    query_text = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    mongo_filter = {}
    if query_text:
        # Search across name, mobile number, and customer ID at once.
        # re.escape prevents someone typing regex special characters
        # (like "(" or "*") from breaking or exploiting the query.
        pattern = re.escape(query_text)
        mongo_filter["$or"] = [
            {"name": {"$regex": pattern, "$options": "i"}},
            {"mobile_number": {"$regex": pattern}},
            {"customer_id": {"$regex": pattern, "$options": "i"}},
        ]
    if status_filter in ("Active", "Inactive"):
        mongo_filter["status"] = status_filter

    customers = list(
        current_app.db.customers.find(mongo_filter).sort("registration_date", -1)
    )

    return render_template(
        "customers/list.html",
        customers=customers,
        query_text=query_text,
        status_filter=status_filter,
    )


@customers_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_customer():
    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        if data["mobile_number"] and current_app.db.customers.find_one(
            {"mobile_number": data["mobile_number"]}
        ):
            errors.append("A customer with this mobile number already exists.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("customers/form.html", customer=data, mode="add")

        seq = get_next_sequence(current_app.db, "customer_id")
        customer_id = f"CUST{seq:04d}"

        current_app.db.customers.insert_one(
            {
                "customer_id": customer_id,
                "name": data["name"],
                "mobile_number": data["mobile_number"],
                "email": data["email"],
                "address": data["address"],
                "village": data["village"],
                "registration_date": datetime.now(timezone.utc),
                "status": "Active",
            }
        )
        flash(f"Customer {customer_id} added successfully.", "success")
        return redirect(url_for("customers.list_customers"))

    return render_template("customers/form.html", customer={}, mode="add")


@customers_bp.route("/edit/<customer_id>", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    customer = current_app.db.customers.find_one({"customer_id": customer_id})
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.list_customers"))

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        # Make sure the mobile number isn't already used by a *different* customer
        duplicate = current_app.db.customers.find_one(
            {
                "mobile_number": data["mobile_number"],
                "customer_id": {"$ne": customer_id},
            }
        )
        if duplicate:
            errors.append("Another customer already uses this mobile number.")

        if errors:
            for error in errors:
                flash(error, "danger")
            merged = {**customer, **data}
            return render_template("customers/form.html", customer=merged, mode="edit")

        current_app.db.customers.update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "name": data["name"],
                    "mobile_number": data["mobile_number"],
                    "email": data["email"],
                    "address": data["address"],
                    "village": data["village"],
                }
            },
        )
        flash(f"Customer {customer_id} updated successfully.", "success")
        return redirect(url_for("customers.profile", customer_id=customer_id))

    return render_template("customers/form.html", customer=customer, mode="edit")


@customers_bp.route("/<customer_id>")
@login_required
def profile(customer_id):
    customer = current_app.db.customers.find_one({"customer_id": customer_id})
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.list_customers"))

    ledger = ledger_service.get_entity_ledger(current_app.db, "customer", customer_id)
    return render_template(
        "customers/profile.html",
        customer=customer,
        ledger=ledger,
        detail_url=url_for("accounting.payable_detail", customer_id=customer_id),
    )


@customers_bp.route("/deactivate/<customer_id>", methods=["POST"])
@login_required
def deactivate(customer_id):
    # Soft delete only - we NEVER hard-delete a customer, since their
    # historical milk entries and payments must remain intact.
    current_app.db.customers.update_one(
        {"customer_id": customer_id}, {"$set": {"status": "Inactive"}}
    )
    flash(f"Customer {customer_id} marked inactive.", "info")
    return redirect(request.referrer or url_for("customers.list_customers"))


@customers_bp.route("/activate/<customer_id>", methods=["POST"])
@login_required
def activate(customer_id):
    current_app.db.customers.update_one(
        {"customer_id": customer_id}, {"$set": {"status": "Active"}}
    )
    flash(f"Customer {customer_id} reactivated.", "info")
    return redirect(request.referrer or url_for("customers.list_customers"))
