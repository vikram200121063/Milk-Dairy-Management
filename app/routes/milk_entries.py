from datetime import datetime, timezone, date as date_cls

from bson import ObjectId
from bson.errors import InvalidId
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from pymongo.errors import DuplicateKeyError

from app.utils.decorators import login_required
from app.services.rate_service import calculate_rate

milk_entries_bp = Blueprint("milk_entries", __name__, url_prefix="/milk-entries")

SHIFTS = ("Morning", "Evening")
MILK_TYPES = ("Cow", "Buffalo")


def _parse_date(date_str):
    """
    Parse an HTML date input (YYYY-MM-DD) into a UTC midnight datetime.
    Storing every entry's date at midnight (no time-of-day component)
    means two entries "on the same day" always compare equal in MongoDB,
    which is essential for both the duplicate check and the unique index.
    """
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _to_float(form, field_name, label, errors):
    raw = form.get(field_name, "").strip()
    try:
        return float(raw)
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None


def _read_and_validate_form(form):
    """Centralized validation used by both add_entry and edit_entry."""
    errors = []
    customer_id = form.get("customer_id", "").strip()
    date_str = form.get("date", "").strip()
    shift = form.get("shift", "").strip()
    milk_type = form.get("milk_type", "").strip()
    notes = form.get("notes", "").strip()

    rate_mode = form.get("rate_mode", "Manual").strip()
    if rate_mode not in ("Manual", "Automatic"):
        rate_mode = "Manual"

    quantity = _to_float(form, "quantity", "Quantity", errors)
    fat = _to_float(form, "fat_percentage", "Fat percentage", errors)
    snf = _to_float(form, "snf_percentage", "SNF percentage", errors)

    # In Manual mode the admin's typed rate is authoritative (after
    # validation). In Automatic mode we deliberately do NOT read
    # rate_per_litre from the form at all - the server calculates it
    # itself further down, so a tampered/incorrect client value can
    # never be used.
    rate = None
    if rate_mode == "Manual":
        rate = _to_float(form, "rate_per_litre", "Rate per litre", errors)
        if rate is not None and rate < 0:
            errors.append("Rate per litre cannot be negative.")

    if not customer_id:
        errors.append("Please select a customer.")

    parsed_date = None
    if not date_str:
        errors.append("Date is required.")
    else:
        try:
            parsed_date = _parse_date(date_str)
            if parsed_date.date() > date_cls.today():
                errors.append("Date cannot be in the future.")
        except ValueError:
            errors.append("Invalid date format.")

    if shift not in SHIFTS:
        errors.append("Shift must be Morning or Evening.")
    if milk_type not in MILK_TYPES:
        errors.append("Milk type must be Cow or Buffalo.")
    if quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than 0.")
    if fat is not None and not (0 <= fat <= 100):
        errors.append("Fat percentage must be between 0 and 100.")
    if snf is not None and not (0 <= snf <= 100):
        errors.append("SNF percentage must be between 0 and 100.")

    data = {
        "customer_id": customer_id,
        "date_str": date_str,
        "date": parsed_date,
        "shift": shift,
        "milk_type": milk_type,
        "quantity": quantity,
        "fat_percentage": fat,
        "snf_percentage": snf,
        "rate_mode": rate_mode,
        "rate_per_litre": rate,
        "notes": notes,
    }
    return errors, data


@milk_entries_bp.route("/")
@login_required
def list_entries():
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    shift = request.args.get("shift", "")
    milk_type = request.args.get("milk_type", "")
    customer_id = request.args.get("customer_id", "")

    filters = {}
    date_filter = {}
    if date_from:
        try:
            date_filter["$gte"] = _parse_date(date_from)
        except ValueError:
            pass
    if date_to:
        try:
            date_filter["$lte"] = _parse_date(date_to)
        except ValueError:
            pass
    if date_filter:
        filters["date"] = date_filter
    if shift in SHIFTS:
        filters["shift"] = shift
    if milk_type in MILK_TYPES:
        filters["milk_type"] = milk_type
    if customer_id:
        filters["customer_id"] = customer_id

    entries = list(current_app.db.milk_entries.find(filters).sort("date", -1).limit(200))

    totals = {
        "quantity": sum(e["quantity"] for e in entries),
        "amount": sum(e["total_amount"] for e in entries),
        "count": len(entries),
    }

    customers = list(
        current_app.db.customers.find({}, {"customer_id": 1, "name": 1}).sort("name", 1)
    )

    return render_template(
        "milk_entries/list.html",
        entries=entries,
        totals=totals,
        customers=customers,
        date_from=date_from,
        date_to=date_to,
        shift=shift,
        milk_type=milk_type,
        customer_id=customer_id,
    )


@milk_entries_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_entry():
    active_customers = list(current_app.db.customers.find({"status": "Active"}).sort("name", 1))

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        customer = None
        if not errors:
            customer = current_app.db.customers.find_one({"customer_id": data["customer_id"]})
            if not customer:
                errors.append("Selected customer does not exist.")

        # Automatic mode: the server calculates the rate itself using the
        # current rate_configurations for this milk type. We never trust a
        # client-supplied rate for Automatic entries.
        if not errors and data["rate_mode"] == "Automatic":
            calculated_rate, rate_error = calculate_rate(
                current_app.db, data["milk_type"], data["fat_percentage"], data["snf_percentage"]
            )
            if rate_error:
                errors.append(rate_error)
            else:
                data["rate_per_litre"] = calculated_rate

        if not errors:
            existing = current_app.db.milk_entries.find_one(
                {
                    "customer_id": data["customer_id"],
                    "date": data["date"],
                    "shift": data["shift"],
                }
            )
            if existing:
                flash(
                    f"An entry already exists for {customer['name']} on "
                    f"{data['date_str']} ({data['shift']}). Edit it instead "
                    f"of creating a duplicate.",
                    "warning",
                )
                return redirect(url_for("milk_entries.edit_entry", entry_id=str(existing["_id"])))

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "milk_entries/form.html",
                entry=request.form,
                customers=active_customers,
                mode="add",
                today=date_cls.today().isoformat(),
            )

        total_amount = round(data["quantity"] * data["rate_per_litre"], 2)

        try:
            current_app.db.milk_entries.insert_one(
                {
                    "customer_id": data["customer_id"],
                    "customer_name": customer["name"],
                    "date": data["date"],
                    "shift": data["shift"],
                    "milk_type": data["milk_type"],
                    "quantity": data["quantity"],
                    "fat_percentage": data["fat_percentage"],
                    "snf_percentage": data["snf_percentage"],
                    "rate_per_litre": data["rate_per_litre"],
                    "rate_mode": data["rate_mode"],
                    "total_amount": total_amount,
                    "notes": data["notes"],
                    "created_at": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError:
            # Safety net: the unique index is the real source of truth in
            # case two requests raced past the check above at the same instant.
            flash(
                "An entry for this customer, date, and shift was just created "
                "elsewhere. Please edit the existing entry instead.",
                "warning",
            )
            return redirect(url_for("milk_entries.list_entries"))

        flash(f"Milk entry recorded for {customer['name']} \u2013 \u20b9{total_amount:.2f}", "success")
        return redirect(url_for("milk_entries.list_entries"))

    return render_template(
        "milk_entries/form.html",
        entry={"date": date_cls.today().isoformat()},
        customers=active_customers,
        mode="add",
        today=date_cls.today().isoformat(),
    )


@milk_entries_bp.route("/edit/<entry_id>", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id):
    try:
        oid = ObjectId(entry_id)
    except InvalidId:
        flash("Invalid milk entry link.", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    entry = current_app.db.milk_entries.find_one({"_id": oid})
    if not entry:
        flash("Milk entry not found.", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    # Include inactive customers too, so an old entry tied to a now-inactive
    # customer can still display and be edited correctly.
    all_customers = list(current_app.db.customers.find().sort("name", 1))

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        customer = None
        if not errors:
            customer = current_app.db.customers.find_one({"customer_id": data["customer_id"]})
            if not customer:
                errors.append("Selected customer does not exist.")

        if not errors and data["rate_mode"] == "Automatic":
            calculated_rate, rate_error = calculate_rate(
                current_app.db, data["milk_type"], data["fat_percentage"], data["snf_percentage"]
            )
            if rate_error:
                errors.append(rate_error)
            else:
                data["rate_per_litre"] = calculated_rate

        if not errors:
            duplicate = current_app.db.milk_entries.find_one(
                {
                    "customer_id": data["customer_id"],
                    "date": data["date"],
                    "shift": data["shift"],
                    "_id": {"$ne": oid},
                }
            )
            if duplicate:
                errors.append(
                    f"Another entry already exists for {customer['name']} on "
                    f"{data['date_str']} ({data['shift']})."
                )

        if errors:
            for error in errors:
                flash(error, "danger")
            merged = {**entry, **request.form}
            return render_template(
                "milk_entries/form.html",
                entry=merged,
                customers=all_customers,
                mode="edit",
                entry_id=entry_id,
                today=date_cls.today().isoformat(),
            )

        total_amount = round(data["quantity"] * data["rate_per_litre"], 2)

        current_app.db.milk_entries.update_one(
            {"_id": oid},
            {
                "$set": {
                    "customer_id": data["customer_id"],
                    "customer_name": customer["name"],
                    "date": data["date"],
                    "shift": data["shift"],
                    "milk_type": data["milk_type"],
                    "quantity": data["quantity"],
                    "fat_percentage": data["fat_percentage"],
                    "snf_percentage": data["snf_percentage"],
                    "rate_per_litre": data["rate_per_litre"],
                    "rate_mode": data["rate_mode"],
                    "total_amount": total_amount,
                    "notes": data["notes"],
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        flash("Milk entry updated successfully.", "success")
        return redirect(url_for("milk_entries.list_entries"))

    prefill = dict(entry)
    prefill["date"] = entry["date"].strftime("%Y-%m-%d")
    return render_template(
        "milk_entries/form.html",
        entry=prefill,
        customers=all_customers,
        mode="edit",
        entry_id=entry_id,
        today=date_cls.today().isoformat(),
    )
