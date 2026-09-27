"""
Bulk-import milk entries from a milk analyzer ("milk testing machine") export.

These machines (Lactoscan, EKOMILK, Milkotester, and similar) measure Fat%,
SNF%, and volume per sample and let their own bundled software export the
day's readings as a CSV or Excel file. This app can't talk to the machine
directly (it runs on a cloud server, not next to the machine), so the
supported workflow is: export from the machine's software, then upload that
file here.

Design notes (see also the "Milk Machine Data Import" section of README.md):

- Customers are matched by an admin-assigned `machine_code` (the machine's
  "Card No" / sample ID) rather than by name, since that's what the machine
  actually records.
- Only fat_percentage, snf_percentage, quantity and machine_code are
  required *per row*. date / shift / milk_type are read from the file if
  present, otherwise every row without one falls back to a single batch-wide
  default chosen on the upload form - most machines don't log all three.
- Nothing is written to the database until the admin reviews a preview and
  confirms. The preview is round-tripped through a hidden form field rather
  than server-side session state, which is simple and proportionate for the
  batch sizes a small dairy would realistically import (a day's or a week's
  worth of samples, not millions of rows).
- The rate is always computed server-side from fat/snf via rate_service
  (the exact same formula manual "Automatic" entries use) - never trusted
  from the uploaded file or the round-tripped preview data. This mirrors the
  server-authoritative pattern already used for online payments elsewhere
  in the app: a client-controlled value never determines money.
"""

import csv
import io
from datetime import datetime, timezone, date as date_cls

import openpyxl

from app.services.rate_service import calculate_rate

MILK_TYPES = ("Cow", "Buffalo")
SHIFTS = ("Morning", "Evening")

TEMPLATE_HEADERS = [
    "machine_code",
    "date",
    "shift",
    "milk_type",
    "quantity",
    "fat_percentage",
    "snf_percentage",
]

# Every value here is already normalized (see _normalize_header) - lowercase,
# underscores/percent signs/parentheses stripped, single-spaced. Add more
# aliases here if your machine's software exports different column names.
_HEADER_ALIASES = {
    "machine_code": {"machine code", "card no", "cardno", "code", "sample id", "sampleid", "id"},
    "date": {"date", "entry date", "collection date", "sample date"},
    "shift": {"shift", "session"},
    "milk_type": {"milk type", "type"},
    "quantity": {"quantity", "qty", "volume", "litres", "liters", "qty l", "quantity l"},
    "fat_percentage": {"fat percentage", "fat"},
    "snf_percentage": {"snf percentage", "snf"},
    "notes": {"notes", "remarks", "comment", "comments"},
}

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y")


def _normalize_header(raw):
    h = str(raw or "").strip().lower()
    for ch in ("_", "%", "(", ")"):
        h = h.replace(ch, " ")
    return " ".join(h.split())


def _canonical_field_map(headers):
    """Maps each column index -> canonical field name (or None if unrecognized)."""
    reverse = {}
    for field, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            reverse[alias] = field
        reverse[field.replace("_", " ")] = field

    mapping = {}
    for idx, raw_header in enumerate(headers):
        normalized = _normalize_header(raw_header)
        mapping[idx] = reverse.get(normalized)
    return mapping


def generate_template_csv():
    """Returns the downloadable blank template as CSV bytes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(["104", "2026-09-27", "Morning", "Cow", "12.5", "4.2", "8.6"])
    writer.writerow(["108", "2026-09-27", "Morning", "Buffalo", "9.0", "6.8", "9.1"])
    return buffer.getvalue().encode("utf-8")


def parse_import_file(file_storage):
    """
    Reads an uploaded .csv/.xlsx/.xls file into a list of raw row dicts
    (canonical field -> raw string/number, unrecognized columns dropped).

    Returns (rows, error). On failure, rows is [] and error is a
    human-readable message; on success error is None (rows may still be
    an empty list if the file had a header but no data).
    """
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        try:
            text = file_storage.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return [], "Could not read the file as text. Please export it as CSV (UTF-8) or Excel."
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
    elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
        try:
            workbook = openpyxl.load_workbook(file_storage, data_only=True, read_only=True)
        except Exception:
            return [], "Could not open this Excel file. Please make sure it's a valid .xlsx export."
        sheet = workbook.active
        all_rows = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
    else:
        return [], "Unsupported file type. Please upload a .csv or .xlsx file."

    if not all_rows:
        return [], "The file appears to be empty."

    header_row = all_rows[0]
    field_map = _canonical_field_map(header_row)

    if "machine_code" not in field_map.values():
        return [], (
            "Couldn't find a machine code column (e.g. 'Machine Code' or 'Card No'). "
            "Download the template below to see the expected format."
        )

    rows = []
    for line_number, raw_row in enumerate(all_rows[1:], start=2):
        if raw_row is None or all(cell in (None, "") for cell in raw_row):
            continue  # skip fully blank rows (common trailing rows in Excel exports)
        row = {"_row_number": line_number}
        for idx, field in field_map.items():
            if field is None or idx >= len(raw_row):
                continue
            value = raw_row[idx]
            row[field] = "" if value is None else str(value).strip()
        rows.append(row)

    return rows, None


def _parse_date_value(value):
    value = (value or "").strip()
    if not value:
        return None, None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc), None
        except ValueError:
            continue
    return None, f"Unrecognized date format: '{value}'."


def build_preview(db, raw_rows, default_date_str, default_shift, default_milk_type):
    """
    Validates and matches every raw row against known customers, computing
    what WOULD happen without writing anything to the database yet.

    Returns a list of preview dicts, each with a "status" of:
      - "ready"          matched, valid, not a duplicate - importable as-is
      - "unknown_code"   no customer has this machine code
      - "duplicate"      this customer already has an entry for that date/shift
      - "invalid"        missing/out-of-range data (bad fat%, no quantity, ...)
    """
    default_date, default_date_error = _parse_date_value(default_date_str)
    today = date_cls.today()

    preview = []
    for row in raw_rows:
        machine_code = row.get("machine_code", "").strip()
        errors = []

        customer = None
        if not machine_code:
            errors.append("No machine code in this row.")
        else:
            customer = db.customers.find_one({"machine_code": machine_code})

        # Date: use the row's own value if the file had a date column,
        # otherwise fall back to the batch default chosen on the upload form.
        row_date_str = row.get("date", "").strip()
        if row_date_str:
            entry_date, date_error = _parse_date_value(row_date_str)
            if date_error:
                errors.append(date_error)
        else:
            entry_date, date_error = default_date, default_date_error
            if date_error:
                errors.append(f"Batch date: {date_error}")
        if entry_date and entry_date.date() > today:
            errors.append("Date cannot be in the future.")

        shift = (row.get("shift", "").strip() or default_shift or "").title()
        if shift not in SHIFTS:
            errors.append("Shift must be Morning or Evening.")

        milk_type = (row.get("milk_type", "").strip() or default_milk_type or "").title()
        if milk_type not in MILK_TYPES:
            errors.append("Milk type must be Cow or Buffalo.")

        quantity = fat = snf = None
        try:
            quantity = float(row.get("quantity", ""))
            if quantity <= 0:
                errors.append("Quantity must be greater than 0.")
        except (TypeError, ValueError):
            errors.append("Quantity is missing or not a number.")
        try:
            fat = float(row.get("fat_percentage", ""))
            if not (0 <= fat <= 100):
                errors.append("Fat% must be between 0 and 100.")
        except (TypeError, ValueError):
            errors.append("Fat% is missing or not a number.")
        try:
            snf = float(row.get("snf_percentage", ""))
            if not (0 <= snf <= 100):
                errors.append("SNF% must be between 0 and 100.")
        except (TypeError, ValueError):
            errors.append("SNF% is missing or not a number.")

        rate = None
        if not errors and customer:
            rate, rate_error = calculate_rate(db, milk_type, fat, snf)
            if rate_error:
                errors.append(rate_error)

        duplicate = False
        if not errors and customer and entry_date:
            duplicate = bool(
                db.milk_entries.find_one(
                    {"customer_id": customer["customer_id"], "date": entry_date, "shift": shift}
                )
            )

        if errors:
            status = "invalid"
        elif not customer:
            status = "unknown_code"
        elif duplicate:
            status = "duplicate"
        else:
            status = "ready"

        preview.append(
            {
                "row_number": row["_row_number"],
                "machine_code": machine_code,
                "customer_id": customer["customer_id"] if customer else None,
                "customer_name": customer["name"] if customer else None,
                "date_str": entry_date.strftime("%Y-%m-%d") if entry_date else (row_date_str or default_date_str),
                "shift": shift,
                "milk_type": milk_type,
                "quantity": quantity,
                "fat_percentage": fat,
                "snf_percentage": snf,
                "rate_per_litre": rate,
                "total_amount": round(quantity * rate, 2) if (quantity and rate is not None) else None,
                "status": status,
                "errors": errors,
            }
        )

    return preview


def commit_import(db, ready_rows, filename, imported_by):
    """
    Inserts the given preview rows (already filtered to the ones the admin
    kept checked on the confirmation screen) as real milk entries.

    Every value that affects money (rate) is recomputed here from fat/snf
    rather than trusted from the posted preview data, and every row is
    re-checked for duplicates immediately before inserting - both as a
    defense against a stale preview (time passed, or the hidden form field
    was tampered with) rather than trusting the browser round-trip.
    """
    from app.services import ledger_service  # local import avoids a circular import at module load

    inserted = skipped_duplicate = skipped_invalid = 0

    for row in ready_rows:
        customer = db.customers.find_one({"customer_id": row.get("customer_id")})
        if not customer:
            skipped_invalid += 1
            continue

        try:
            entry_date = datetime.strptime(row["date_str"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            shift = row["shift"]
            milk_type = row["milk_type"]
            quantity = float(row["quantity"])
            fat = float(row["fat_percentage"])
            snf = float(row["snf_percentage"])
            if shift not in SHIFTS or milk_type not in MILK_TYPES or quantity <= 0:
                raise ValueError("out of range")
        except (KeyError, TypeError, ValueError):
            skipped_invalid += 1
            continue

        if db.milk_entries.find_one(
            {"customer_id": customer["customer_id"], "date": entry_date, "shift": shift}
        ):
            skipped_duplicate += 1
            continue

        rate, rate_error = calculate_rate(db, milk_type, fat, snf)
        if rate_error:
            skipped_invalid += 1
            continue

        entry_doc = {
            "customer_id": customer["customer_id"],
            "customer_name": customer["name"],
            "date": entry_date,
            "shift": shift,
            "milk_type": milk_type,
            "quantity": quantity,
            "fat_percentage": fat,
            "snf_percentage": snf,
            "rate_per_litre": rate,
            "rate_mode": "Automatic",
            "total_amount": round(quantity * rate, 2),
            "notes": "Imported from milk machine file",
            "created_at": datetime.now(timezone.utc),
        }
        db.milk_entries.insert_one(entry_doc)
        ledger_service.post_milk_purchase(db, entry_doc, str(entry_doc["_id"]))
        inserted += 1

    db.milk_imports.insert_one(
        {
            "filename": filename,
            "imported_by": imported_by,
            "imported_at": datetime.now(timezone.utc),
            "inserted": inserted,
            "skipped_duplicate": skipped_duplicate,
            "skipped_invalid": skipped_invalid,
        }
    )

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
    }


def recent_imports(db, limit=20):
    return list(db.milk_imports.find().sort("imported_at", -1).limit(limit))
