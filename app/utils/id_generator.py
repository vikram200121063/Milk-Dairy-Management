from pymongo import ReturnDocument


def get_next_sequence(db, sequence_name):
    """
    Atomically returns the next integer in a named sequence.

    MongoDB's _id (ObjectId) is unique but not human-friendly, so for
    things like Customer IDs and Invoice Numbers we maintain our own
    counters in a `counters` collection, e.g.:
        { "_id": "customer_id", "seq": 42 }

    find_one_and_update with $inc is atomic - if two requests call this
    at the exact same moment, MongoDB guarantees each gets a different
    number. This is the standard MongoDB auto-increment pattern.
    """
    result = db.counters.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result["seq"]
