"""
Small key-value style settings used by the Buyer Invoicing / Accounting
module: default payment terms, the dunning grace period, and the late
payment interest rate.

Stored as a single document ({"_id": "global", ...}) in the
`finance_settings` collection - the same "one config document" pattern
`rate_configurations` uses per milk type, just with a single fixed _id
since these settings aren't per-anything.
"""
from datetime import datetime, timezone

SETTINGS_ID = "global"

DEFAULT_SETTINGS = {
    # Days after the invoice date a buyer's payment is due, used when a
    # buyer doesn't have their own payment_terms_days set.
    "default_payment_terms_days": 15,
    # How many days after the due date the buyer gets before late payment
    # interest starts accruing (a reminder is still sent immediately).
    "grace_period_days": 7,
    # Annual simple interest rate applied to the remaining balance of an
    # overdue invoice once the grace period has passed.
    "interest_rate_annual_percent": 18.0,
    # Default GST/tax percentage pre-filled on new buyer sales (0 = no tax).
    "default_tax_percentage": 0.0,
}


def get_settings(db):
    """
    Returns the current settings document, creating it with defaults on
    first use and backfilling any keys added in a later version of the
    app that an older saved document wouldn't have.
    """
    doc = db.finance_settings.find_one({"_id": SETTINGS_ID})
    if not doc:
        doc = {"_id": SETTINGS_ID, **DEFAULT_SETTINGS, "updated_at": datetime.now(timezone.utc)}
        db.finance_settings.insert_one(doc)
        return doc

    missing = {k: v for k, v in DEFAULT_SETTINGS.items() if k not in doc}
    if missing:
        db.finance_settings.update_one({"_id": SETTINGS_ID}, {"$set": missing})
        doc.update(missing)
    return doc


def update_settings(db, **fields):
    """Updates one or more settings fields. Unknown keys are ignored."""
    clean = {k: v for k, v in fields.items() if k in DEFAULT_SETTINGS}
    clean["updated_at"] = datetime.now(timezone.utc)
    db.finance_settings.update_one({"_id": SETTINGS_ID}, {"$set": clean}, upsert=True)
