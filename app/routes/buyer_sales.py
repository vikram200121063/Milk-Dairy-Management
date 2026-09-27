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

from app.utils.decorators import owner_required
from app.utils.id_generator import get_next_sequence
from app.services.rate_service import MILK_TYPES
from app.services import finance_settings_service, ledger_service

buyer_sales_bp = Blueprint("buyer_sales", __name__, url_prefix="/buyer-sales")

QUALITY_GRADES = ("", "Premium", "A", "B", "Standard")


def _parse_date(date_str):
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _to_float(form, field_name, label, errors, allow_blank=False, default=0.0):
    raw = form.get(field_name, "").strip()
    if not raw and allow_blank:
        return default
    try:
        return float(raw)
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None


def _read_and_validate_form(form):
    errors = []
    buyer_id = form.get("buyer_id", "").strip()
    date_str = form.get("sale_date", "").strip()
    milk_type = form.get("milk_type", "").strip()
    quality_grade = form.get("quality_grade", "").strip()
    notes = form.get("notes", "").strip()

    quantity = _to_float(form, "quantity", "Quantity", errors)
    rate = _to_float(form, "rate_per_litre", "Rate per litre", errors)
    tax_percentage = _to_float(form, "tax_percentage", "Tax percentage", errors, allow_blank=True, default=0.0)
    other_charges = _to_float(form, "other_charges", "Other charges", errors, allow_blank=True, default=0.0)

    if not buyer_id:
        errors.append("Please select a buyer.")

    parsed_date = None
    if not date_str:
        errors.append("Sale date is required.")
    else:
        try:
            parsed_date = _parse_date(date_str)
            if parsed_date.date() > date_cls.today():
                errors.append("Sale date cannot be in the future.")
        except ValueError:
            errors.append("Invalid date format.")

    if milk_type not in MILK_TYPES:
        errors.append("Milk type must be Cow or Buffalo.")
    if quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than 0.")
    if rate is not None and rate < 0:
        errors.append("Rate per litre cannot be negative.")
    if tax_percentage is not None and not (0 <= tax_percentage <= 100):
        errors.append("Tax percentage must be between 0 and 100.")
    if other_charges is not None and other_charges < 0:
        errors.append("Other charges cannot be negative.")

    data = {
        "buyer_id": buyer_id,
        "date_str": date_str,
        "sale_date": parsed_date,
        "milk_type": milk_type,
        "quality_grade": quality_grade,
        "quantity": quantity,
        "rate_per_litre": rate,
        "tax_percentage": tax_percentage,
        "other_charges": other_charges,
        "notes": notes,
    }
    return errors, data


def _compute_amounts(quantity, rate, tax_percentage, other_charges):
    gross_amount = round(quantity * rate, 2)
    tax_amount = round(gross_amount * tax_percentage / 100.0, 2)
    total_amount = round(gross_amount + tax_amount + other_charges, 2)
    return gross_amount, tax_amount, total_amount


@buyer_sales_bp.route("/")
@owner_required
def list_sales():
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    buyer_id = request.args.get("buyer_id", "")
    milk_type = request.args.get("milk_type", "")

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
        filters["sale_date"] = date_filter
    if buyer_id:
        filters["buyer_id"] = buyer_id
    if milk_type in MILK_TYPES:
        filters["milk_type"] = milk_type

    sales = list(current_app.db.buyer_sales.find(filters).sort("sale_date", -1).limit(200))

    totals = {
        "quantity": sum(s["quantity"] for s in sales),
        "amount": sum(s["total_amount"] for s in sales),
        "count": len(sales),
    }

    buyers = list(
        current_app.db.buyers.find({}, {"buyer_id": 1, "company_name": 1}).sort("company_name", 1)
    )

    return render_template(
        "buyer_sales/list.html",
        sales=sales,
        totals=totals,
        buyers=buyers,
        date_from=date_from,
        date_to=date_to,
        buyer_id=buyer_id,
        milk_type=milk_type,
    )


@buyer_sales_bp.route("/add", methods=["GET", "POST"])
@owner_required
def add_sale():
    active_buyers = list(current_app.db.buyers.find({"status": "Active"}).sort("company_name", 1))
    settings = finance_settings_service.get_settings(current_app.db)

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        buyer = None
        if not errors:
            buyer = current_app.db.buyers.find_one({"buyer_id": data["buyer_id"]})
            if not buyer:
                errors.append("Selected buyer does not exist.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "buyer_sales/form.html",
                sale=request.form,
                buyers=active_buyers,
                mode="add",
                today=date_cls.today().isoformat(),
                milk_types=MILK_TYPES,
                quality_grades=QUALITY_GRADES,
                default_tax=settings["default_tax_percentage"],
            )

        gross_amount, tax_amount, total_amount = _compute_amounts(
            data["quantity"], data["rate_per_litre"], data["tax_percentage"], data["other_charges"]
        )

        seq = get_next_sequence(current_app.db, "buyer_sale_id")
        sale_id = f"BSL{seq:05d}"

        sale_doc = {
            "sale_id": sale_id,
            "buyer_id": data["buyer_id"],
            "buyer_name": buyer["company_name"],
            "sale_date": data["sale_date"],
            "milk_type": data["milk_type"],
            "quality_grade": data["quality_grade"],
            "quantity": data["quantity"],
            "rate_per_litre": data["rate_per_litre"],
            "gross_amount": gross_amount,
            "tax_percentage": data["tax_percentage"],
            "tax_amount": tax_amount,
            "other_charges": data["other_charges"],
            "total_amount": total_amount,
            "notes": data["notes"],
            "created_at": datetime.now(timezone.utc),
        }
        current_app.db.buyer_sales.insert_one(sale_doc)
        # Common Accounting module: auto-post Dr Accounts Receivable / Cr Milk Sales Revenue.
        ledger_service.post_buyer_sale(current_app.db, sale_doc)
        flash(
            f"Sale {sale_id} recorded for {buyer['company_name']} \u2013 \u20b9{total_amount:.2f}",
            "success",
        )
        return redirect(url_for("buyer_sales.list_sales"))

    return render_template(
        "buyer_sales/form.html",
        sale={"sale_date": date_cls.today().isoformat(), "tax_percentage": settings["default_tax_percentage"]},
        buyers=active_buyers,
        mode="add",
        today=date_cls.today().isoformat(),
        milk_types=MILK_TYPES,
        quality_grades=QUALITY_GRADES,
        default_tax=settings["default_tax_percentage"],
    )


@buyer_sales_bp.route("/edit/<sale_id>", methods=["GET", "POST"])
@owner_required
def edit_sale(sale_id):
    sale = current_app.db.buyer_sales.find_one({"sale_id": sale_id})
    if not sale:
        flash("Sale record not found.", "danger")
        return redirect(url_for("buyer_sales.list_sales"))

    all_buyers = list(current_app.db.buyers.find().sort("company_name", 1))
    settings = finance_settings_service.get_settings(current_app.db)

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form)

        buyer = None
        if not errors:
            buyer = current_app.db.buyers.find_one({"buyer_id": data["buyer_id"]})
            if not buyer:
                errors.append("Selected buyer does not exist.")

        if errors:
            for error in errors:
                flash(error, "danger")
            merged = {**sale, **request.form}
            return render_template(
                "buyer_sales/form.html",
                sale=merged,
                buyers=all_buyers,
                mode="edit",
                sale_id=sale_id,
                today=date_cls.today().isoformat(),
                milk_types=MILK_TYPES,
                quality_grades=QUALITY_GRADES,
                default_tax=settings["default_tax_percentage"],
            )

        gross_amount, tax_amount, total_amount = _compute_amounts(
            data["quantity"], data["rate_per_litre"], data["tax_percentage"], data["other_charges"]
        )

        updated_fields = {
            "buyer_id": data["buyer_id"],
            "buyer_name": buyer["company_name"],
            "sale_date": data["sale_date"],
            "milk_type": data["milk_type"],
            "quality_grade": data["quality_grade"],
            "quantity": data["quantity"],
            "rate_per_litre": data["rate_per_litre"],
            "gross_amount": gross_amount,
            "tax_percentage": data["tax_percentage"],
            "tax_amount": tax_amount,
            "other_charges": data["other_charges"],
            "total_amount": total_amount,
            "notes": data["notes"],
            "updated_at": datetime.now(timezone.utc),
        }
        current_app.db.buyer_sales.update_one({"sale_id": sale_id}, {"$set": updated_fields})
        # Common Accounting module: re-post the Sale entry with the edited amount/buyer.
        ledger_service.post_buyer_sale(current_app.db, {"sale_id": sale_id, **updated_fields})
        flash(
            "Sale updated successfully. If this sale falls in an already-generated "
            "invoice period, regenerate that invoice to pick up the change.",
            "success",
        )
        return redirect(url_for("buyer_sales.list_sales"))

    prefill = dict(sale)
    prefill["sale_date"] = sale["sale_date"].strftime("%Y-%m-%d")
    return render_template(
        "buyer_sales/form.html",
        sale=prefill,
        buyers=all_buyers,
        mode="edit",
        sale_id=sale_id,
        today=date_cls.today().isoformat(),
        milk_types=MILK_TYPES,
        quality_grades=QUALITY_GRADES,
        default_tax=settings["default_tax_percentage"],
    )


@buyer_sales_bp.route("/delete/<sale_id>", methods=["POST"])
@owner_required
def delete_sale(sale_id):
    sale = current_app.db.buyer_sales.find_one({"sale_id": sale_id})
    if not sale:
        flash("Sale record not found.", "danger")
        return redirect(url_for("buyer_sales.list_sales"))

    existing_invoice = current_app.db.buyer_invoices.find_one(
        {"buyer_id": sale["buyer_id"], "period_start": {"$lte": sale["sale_date"]}, "period_end": {"$gte": sale["sale_date"]}}
    )
    current_app.db.buyer_sales.delete_one({"sale_id": sale_id})
    # Common Accounting module: remove the corresponding ledger entries too.
    ledger_service.void_buyer_sale(current_app.db, sale_id)

    if existing_invoice:
        flash(
            "Sale deleted. This sale belonged to an already-generated invoice - "
            "regenerate that invoice to update its totals.",
            "warning",
        )
    else:
        flash("Sale deleted.", "info")
    return redirect(request.referrer or url_for("buyer_sales.list_sales"))
