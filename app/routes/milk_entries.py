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
from app.services import notification_service, ledger_service

milk_entries_bp = Blueprint("milk_entries", __name__, url_prefix="/milk-entries")

SHIFTS = ("Morning", "Evening")
MILK_TYPES = ("Cow", "Buffalo")

# Used only to pre-fill the "Send All" shift dropdown with a sensible
# guess: before this hour, assume we're sending the morning collection's
# notifications; at/after it, assume evening. Purely a UI convenience -
# the admin can always override it before sending.
SHIFT_DEFAULT_CUTOFF_HOUR = 14


def _default_notify_shift():
    return "Morning" if datetime.now().hour < SHIFT_DEFAULT_CUTOFF_HOUR else "Evening"


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
        default_notify_date=date_cls.today().isoformat(),
        default_notify_shift=_default_notify_shift(),
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

        entry_doc = {
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
        try:
            current_app.db.milk_entries.insert_one(entry_doc)
            # Common Accounting module: auto-post Dr Milk Purchase Expense / Cr Accounts Payable.
            ledger_service.post_milk_purchase(current_app.db, entry_doc, str(entry_doc["_id"]))
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

        updated_fields = {
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
        current_app.db.milk_entries.update_one({"_id": oid}, {"$set": updated_fields})
        # Common Accounting module: re-post the Milk Purchase entry with the edited amount/customer.
        ledger_service.post_milk_purchase(current_app.db, updated_fields, str(oid))
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


# ---------------------------------------------------------------------------
# Email notifications
# ---------------------------------------------------------------------------


def _summarize_notify_result(result):
    """Turns {"email": "sent"/error} into one short line."""
    status = result.get("email")
    if status == "sent":
        return "Email sent."
    if status:
        return f"Email: {status}"
    return "Nothing to send."


@milk_entries_bp.route("/<entry_id>/notify", methods=["POST"])
@login_required
def notify_entry(entry_id):
    """Sends the email for one milk entry to its customer, on demand."""
    try:
        oid = ObjectId(entry_id)
    except InvalidId:
        flash("Invalid milk entry link.", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    entry = current_app.db.milk_entries.find_one({"_id": oid})
    if not entry:
        flash("Milk entry not found.", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    result = notification_service.notify_milk_entry(current_app.db, entry)
    category = "success" if result.get("email") == "sent" else "warning"
    flash(
        f"{entry['customer_name']} ({entry['date'].strftime('%d %b')}, {entry['shift']}): "
        f"{_summarize_notify_result(result)}",
        category,
    )
    return redirect(request.referrer or url_for("milk_entries.list_entries"))


@milk_entries_bp.route("/notify-bulk", methods=["POST"])
@login_required
def notify_bulk():
    """
    Sends the email for EVERY milk entry on a given date + shift - the
    "Send All" button on the milk entries page. Defaults to today's date
    and a shift guessed from the current time, but the admin can change
    either before submitting.
    """
    date_str = request.form.get("notify_date", "").strip()
    shift = request.form.get("notify_shift", "").strip()

    if shift not in SHIFTS:
        flash("Please select a valid shift (Morning or Evening).", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    try:
        target_date = _parse_date(date_str)
    except ValueError:
        flash("Please select a valid date.", "danger")
        return redirect(url_for("milk_entries.list_entries"))

    entries = list(
        current_app.db.milk_entries.find({"date": target_date, "shift": shift})
    )
    if not entries:
        flash(f"No milk entries found for {date_str} ({shift}).", "warning")
        return redirect(url_for("milk_entries.list_entries"))

    email_sent = email_failed = 0
    for entry in entries:
        result = notification_service.notify_milk_entry(current_app.db, entry)
        if result["email"] == "sent":
            email_sent += 1
        elif result["email"] not in (None, notification_service.NO_EMAIL_ON_FILE):
            email_failed += 1

    flash(
        f"Notified {len(entries)} customer(s) for {date_str} ({shift}) \u2013 "
        f"Email: {email_sent} sent / {email_failed} failed.",
        "info",
    )
    return redirect(url_for("milk_entries.list_entries"))
