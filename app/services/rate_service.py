MILK_TYPES = ("Cow", "Buffalo")


def get_rate_config(db, milk_type):
    """Fetch the current rate configuration document for a milk type."""
    return db.rate_configurations.find_one({"milk_type": milk_type})


def calculate_rate(db, milk_type, fat_percentage, snf_percentage):
    """
    Calculates the rate per litre for a given milk type, fat %, and SNF %.

    Formula:  Rate = Base Rate + (Fat% x Fat Rate/Point) + (SNF% x SNF Rate/Point)

    This is the ONLY place the automatic pricing formula lives. If the
    dairy's pricing policy changes later (e.g. switching to a lookup-table
    or a different formula), this is the one function to edit - nothing
    in the routes needs to change.

    Returns (rate, error_message). Exactly one of the two will be set:
    on success `error_message` is None; on failure `rate` is None.
    """
    config = get_rate_config(db, milk_type)
    if not config:
        return None, (
            f"No rate configuration exists for {milk_type} milk. "
            f"Set one up under Rate Configuration first."
        )

    rate = (
        config["base_rate"]
        + fat_percentage * config["fat_rate_per_point"]
        + snf_percentage * config["snf_rate_per_point"]
    )
    # A configuration mistake (e.g. a large negative base rate) should
    # never produce a negative price.
    return round(max(rate, 0), 2), None
