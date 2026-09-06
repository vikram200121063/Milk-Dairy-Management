from datetime import datetime, timezone, date as date_cls

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

expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")

EXPENSE_CATEGORIES = (
    "Transportation/Fuel",
    "Salary",
    "Electricity",
    "Maintenance",
    "Packaging",
    "Other",
)
INCOME_CATEGORY = "Other Income"


def _parse_date(date_str):
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _read_and_validate_form(form):
    errors = []
    entry_type = form.get("entry_type", "Expense").strip()
    if entry_type not in ("Expense", "Income"):
        entry_type = "Expense"

    category = form.get("category", "").strip()
    if entry_type == "Income":
        category = INCOME_CATEGORY
    elif category not in EXPENSE_CATEGORIES:
        errors.append("Please select a valid expense category.")

    description = form.get("description", "").strip()
    date_str = form.get("date", "").strip()

    try:
        amount = float(form.get("amount", "").strip())
        if amount <= 0:
            errors.append("Amount must be greater than 0.")
    except ValueError:
        errors.append("Amount must be a number.")
        amount = None

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

    if not description:
        errors.append("A short description is required.")

    data = {
        "entry_type": entry_type,
        "category": category,
        "description": description,
        "date_str": date_str,
        "date": parsed_date,
        "amount": amount,
    }
    return errors, data


@expenses_bp.route("/")
@login_required
def list_expenses():
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    category = request.args.get("category", "")
    entry_type = request.args.get("entry_type", "")

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
    if category:
        filters["category"] = category
    if entry_type in ("Expense", "Income"):
        filters["entry_type"] = entry_type

    entries = list(current_app.db.expenses.find(filters).sort("date", -1).limit(300))

    totals = {
        "expenses": sum(e["amount"] for e in entries if e.get("entry_type", "Expense") != "Income"),
        "income": sum(e["amount"] for e in entries if e.get("entry_type") == "Income"),
        "count": len(entries),
    }

    return render_template(
        "expenses/list.html",
        entries=entries,
        totals=totals,
        categories=EXPENSE_CATEGORIES,
        date_from=date_from,
        date_to=date_to,
        category=category,
        entry_type=entry_type,
    )


@expenses_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "expenses/form.html", entry=request.form, mode="add",
                categories=EXPENSE_CATEGORIES, today=date_cls.today().isoformat(),
            )

        seq = get_next_sequence(current_app.db, "expense_id")
        expense_id = f"EXP{seq:05d}"

        expense_doc = {
            "expense_id": expense_id,
            "entry_type": data["entry_type"],
            "category": data["category"],
            "description": data["description"],
            "amount": data["amount"],
            "date": data["date"],
            "created_at": datetime.now(timezone.utc),
        }
        current_app.db.expenses.insert_one(expense_doc)
        # Common Accounting module: auto-post the matching Dr/Cr pair.
        ledger_service.post_expense_entry(current_app.db, expense_doc)
        label = "Income entry" if data["entry_type"] == "Income" else "Expense"
        flash(f"{label} {expense_id} recorded \u2013 \u20b9{data['amount']:.2f}", "success")
        return redirect(url_for("expenses.list_expenses"))

    return render_template(
        "expenses/form.html",
        entry={"date": date_cls.today().isoformat(), "entry_type": "Expense"},
        mode="add",
        categories=EXPENSE_CATEGORIES,
        today=date_cls.today().isoformat(),
    )


@expenses_bp.route("/edit/<expense_id>", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    entry = current_app.db.expenses.find_one({"expense_id": expense_id})
    if not entry:
        flash("Entry not found.", "danger")
        return redirect(url_for("expenses.list_expenses"))

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        if errors:
            for error in errors:
                flash(error, "danger")
            merged = {**entry, **request.form}
            return render_template(
                "expenses/form.html", entry=merged, mode="edit", expense_id=expense_id,
                categories=EXPENSE_CATEGORIES, today=date_cls.today().isoformat(),
            )

        updated_fields = {
            "entry_type": data["entry_type"],
            "category": data["category"],
            "description": data["description"],
            "amount": data["amount"],
            "date": data["date"],
            "updated_at": datetime.now(timezone.utc),
        }
        current_app.db.expenses.update_one({"expense_id": expense_id}, {"$set": updated_fields})
        # Common Accounting module: re-post in case the type/amount/date changed.
        ledger_service.post_expense_entry(current_app.db, {"expense_id": expense_id, **updated_fields})
        flash("Entry updated successfully.", "success")
        return redirect(url_for("expenses.list_expenses"))

    prefill = dict(entry)
    prefill["date"] = entry["date"].strftime("%Y-%m-%d")
    return render_template(
        "expenses/form.html", entry=prefill, mode="edit", expense_id=expense_id,
        categories=EXPENSE_CATEGORIES, today=date_cls.today().isoformat(),
    )


@expenses_bp.route("/delete/<expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    current_app.db.expenses.delete_one({"expense_id": expense_id})
    # Common Accounting module: remove the corresponding ledger entries too.
    ledger_service.void_expense_entry(current_app.db, expense_id)
    flash("Entry deleted.", "info")
    return redirect(request.referrer or url_for("expenses.list_expenses"))
