"""
A small but functionally real in-memory MongoDB emulator, used ONLY by the
test suite (tests/conftest.py) so `pytest` can run this app's routes and
services end-to-end with zero setup - no MongoDB Atlas account, no network
access, and no cost. It is test infrastructure, not part of the shipped
app: the real app always talks to real MongoDB via the real `pymongo`
package (see config.py / app/__init__.py) - this module is never imported
outside of tests/.

Supports enough of pymongo's API surface for this codebase: find/find_one
with common query operators ($gte/$lte/$gt/$lt/$ne/$eq/$in/$or/$regex),
cursor .sort()/.limit(), insert_one/insert_many, update_one (with $set/
$unset/$inc, upsert), delete_one/delete_many, count_documents,
create_index (enforces uniqueness, including on _id, exactly like real
MongoDB), and aggregate() with $match/$group (accumulators $sum/$first/
$last/$max/$min, plus a $cond expression)/$sort.
"""
import re
import threading
from copy import deepcopy


# --- bson.ObjectId stand-in -------------------------------------------------

_oid_lock = threading.Lock()
_oid_counter = [0]


class ObjectId:
    def __init__(self, oid=None):
        if oid is None:
            with _oid_lock:
                _oid_counter[0] += 1
                n = _oid_counter[0]
            self._oid = f"{n:024x}"
        else:
            s = str(oid)
            if not re.fullmatch(r"[0-9a-fA-F]{24}", s):
                from bson.errors import InvalidId

                raise InvalidId(f"{s!r} is not a valid ObjectId")
            self._oid = s.lower()

    def __str__(self):
        return self._oid

    def __repr__(self):
        return f"ObjectId('{self._oid}')"

    def __eq__(self, other):
        return isinstance(other, ObjectId) and self._oid == other._oid or str(other) == self._oid

    def __hash__(self):
        return hash(self._oid)


class InvalidId(Exception):
    pass


# --- pymongo errors ----------------------------------------------------------


class PyMongoError(Exception):
    pass


class ConnectionFailure(PyMongoError):
    pass


class ConfigurationError(PyMongoError):
    pass


class DuplicateKeyError(PyMongoError):
    pass


class ReturnDocument:
    BEFORE = 0
    AFTER = 1


# --- Query matching -----------------------------------------------------


def _get_path(doc, path):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _cmp_ok(op, a, b):
    try:
        if op == "$gte":
            return a is not None and a >= b
        if op == "$lte":
            return a is not None and a <= b
        if op == "$gt":
            return a is not None and a > b
        if op == "$lt":
            return a is not None and a < b
        if op == "$ne":
            return a != b
        if op == "$eq":
            return a == b
        if op == "$in":
            return a in b
        if op == "$nin":
            return a not in b
    except TypeError:
        return False
    return False


def _match_value(actual, condition):
    if isinstance(condition, dict) and any(k.startswith("$") for k in condition):
        for op, val in condition.items():
            if op == "$regex":
                flags = re.IGNORECASE if condition.get("$options", "") == "i" else 0
                if not re.search(val, actual or "", flags):
                    return False
            elif op == "$options":
                continue
            else:
                if not _cmp_ok(op, actual, val):
                    return False
        return True
    return actual == condition


def _matches(doc, filt):
    if not filt:
        return True
    for key, condition in filt.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in condition):
                return False
        elif key == "$and":
            if not all(_matches(doc, sub) for sub in condition):
                return False
        else:
            actual = _get_path(doc, key)
            if not _match_value(actual, condition):
                return False
    return True


# --- Aggregation expression evaluation -----------------------------------


def _eval_expr(doc, expr):
    if isinstance(expr, str) and expr.startswith("$"):
        return _get_path(doc, expr[1:])
    if isinstance(expr, dict):
        if "$cond" in expr:
            cond, then_v, else_v = expr["$cond"]
            return _eval_expr(doc, then_v) if _eval_cond(doc, cond) else _eval_expr(doc, else_v)
        return expr
    return expr


def _eval_cond(doc, cond):
    if isinstance(cond, dict):
        for op, args in cond.items():
            a = _eval_expr(doc, args[0])
            b = _eval_expr(doc, args[1])
            if op == "$eq":
                return a == b
            if op == "$ne":
                return a != b
            if op == "$gt":
                return a is not None and a > b
            if op == "$gte":
                return a is not None and a >= b
            if op == "$lt":
                return a is not None and a < b
            if op == "$lte":
                return a is not None and a <= b
    return bool(cond)


# --- Cursor ---------------------------------------------------------------


class Cursor(list):
    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            keys = [(key_or_list, direction if direction is not None else 1)]
        else:
            keys = list(key_or_list)

        result = list(self)
        for field, dirn in reversed(keys):
            result.sort(key=lambda d, f=field: _sort_value(_get_path(d, f)), reverse=(dirn == -1))
        self[:] = result
        return self

    def limit(self, n):
        self[:] = list(self)[:n]
        return self


def _sort_value(v):
    # Make None sort consistently instead of raising on mixed types.
    if v is None:
        return (0, "")
    if isinstance(v, (int, float)):
        return (1, v)
    return (2, str(v))


# --- Collection -------------------------------------------------------------


class InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class UpdateResult:
    def __init__(self, matched_count, modified_count, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class Collection:
    def __init__(self):
        self._docs = []
        self._unique_indexes = []  # list of (fields_tuple, sparse)

    # -- indexes --

    def create_index(self, keys, unique=False, sparse=False, **kwargs):
        if unique:
            if isinstance(keys, str):
                fields = (keys,)
            else:
                fields = tuple(k for k, _ in keys)
            self._unique_indexes.append((fields, sparse))
        return "index_created"

    def _check_unique(self, doc, exclude_id=None):
        for fields, sparse in self._unique_indexes:
            values = tuple(doc.get(f) for f in fields)
            if sparse and any(v is None for v in values):
                continue
            for existing in self._docs:
                if exclude_id is not None and existing.get("_id") == exclude_id:
                    continue
                existing_values = tuple(existing.get(f) for f in fields)
                if existing_values == values:
                    raise DuplicateKeyError(f"duplicate key on {fields}")

    # -- reads --

    def find(self, filt=None, projection=None):
        return Cursor(deepcopy(d) for d in self._docs if _matches(d, filt or {}))

    def find_one(self, filt=None, projection=None):
        for d in self._docs:
            if _matches(d, filt or {}):
                return deepcopy(d)
        return None

    def count_documents(self, filt=None):
        return sum(1 for d in self._docs if _matches(d, filt or {}))

    # -- writes --

    def insert_one(self, doc):
        # Real PyMongo mutates the caller's dict in place, adding "_id" -
        # some app code (e.g. milk_entries.add_entry, milk_import_service)
        # relies on that to get the generated id right after inserting.
        # Match that behavior here instead of inserting a copy.
        if "_id" not in doc or doc["_id"] is None:
            doc["_id"] = ObjectId()
        else:
            # Real MongoDB always enforces uniqueness on _id, even with no
            # explicit index declared for it (app code relies on this for
            # idempotency - e.g. payment_gateway_service.claim_payment_event
            # uses a caller-supplied _id specifically to make a second
            # insert of the same id fail instead of silently duplicating).
            for existing in self._docs:
                if existing.get("_id") == doc["_id"]:
                    raise DuplicateKeyError("duplicate key on _id")
        self._check_unique(doc)
        self._docs.append(doc)
        return InsertOneResult(doc["_id"])

    def insert_many(self, docs):
        ids = []
        for doc in docs:
            result = self.insert_one(doc)
            ids.append(result.inserted_id)
        return InsertManyResult(ids)

    def _apply_update(self, doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                doc[k] = v
        if "$unset" in update:
            for k in update["$unset"]:
                doc.pop(k, None)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k) or 0) + v
        # If update has no operators, treat as a full replace (rare in this app).
        if not any(k.startswith("$") for k in update):
            new_doc = dict(update)
            new_doc["_id"] = doc["_id"]
            doc.clear()
            doc.update(new_doc)

    def update_one(self, filt, update, upsert=False):
        for d in self._docs:
            if _matches(d, filt or {}):
                self._apply_update(d, update)
                self._check_unique(d, exclude_id=d["_id"])
                return UpdateResult(1, 1)
        if upsert:
            new_doc = {k: v for k, v in (filt or {}).items() if not k.startswith("$") and not isinstance(v, dict)}
            new_doc["_id"] = ObjectId()
            self._apply_update(new_doc, update)
            self._check_unique(new_doc, exclude_id=new_doc["_id"])
            self._docs.append(new_doc)
            return UpdateResult(0, 0, upserted_id=new_doc["_id"])
        return UpdateResult(0, 0)

    def update_many(self, filt, update):
        count = 0
        for d in self._docs:
            if _matches(d, filt or {}):
                self._apply_update(d, update)
                count += 1
        return UpdateResult(count, count)

    def find_one_and_update(self, filt, update, upsert=False, return_document=None):
        for d in self._docs:
            if _matches(d, filt or {}):
                before = deepcopy(d)
                self._apply_update(d, update)
                return deepcopy(d) if return_document == ReturnDocument.AFTER else before
        if upsert:
            new_doc = {k: v for k, v in (filt or {}).items() if not k.startswith("$") and not isinstance(v, dict)}
            new_doc["_id"] = filt.get("_id", ObjectId())
            self._apply_update(new_doc, update)
            self._docs.append(new_doc)
            return deepcopy(new_doc) if return_document == ReturnDocument.AFTER else None
        return None

    def delete_one(self, filt):
        for i, d in enumerate(self._docs):
            if _matches(d, filt or {}):
                del self._docs[i]
                return DeleteResult(1)
        return DeleteResult(0)

    def delete_many(self, filt=None):
        keep = [d for d in self._docs if not _matches(d, filt or {})]
        removed = len(self._docs) - len(keep)
        self._docs = keep
        return DeleteResult(removed)

    # -- aggregation --

    def aggregate(self, pipeline):
        docs = [deepcopy(d) for d in self._docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                docs = self._do_group(docs, stage["$group"])
            elif "$sort" in stage:
                for field, dirn in reversed(list(stage["$sort"].items())):
                    docs.sort(key=lambda d, f=field: _sort_value(d.get(f)), reverse=(dirn == -1))
            elif "$limit" in stage:
                docs = docs[: stage["$limit"]]
            elif "$project" in stage:
                pass  # not needed by this codebase's pipelines
        return docs

    @staticmethod
    def _do_group(docs, group_spec):
        id_expr = group_spec["_id"]
        groups = {}
        order = []
        for doc in docs:
            key = _eval_expr(doc, id_expr) if isinstance(id_expr, str) else id_expr
            if key not in groups:
                groups[key] = {"_id": key}
                order.append(key)
                # track "seen first" per accumulator field for $first
                groups[key]["__first_done"] = set()
            g = groups[key]
            for field, accum in group_spec.items():
                if field == "_id":
                    continue
                (op, expr), = accum.items()
                val = _eval_expr(doc, expr)
                if op == "$sum":
                    g[field] = (g.get(field) or 0) + (val or 0)
                elif op == "$first":
                    if field not in g["__first_done"]:
                        g[field] = val
                        g["__first_done"].add(field)
                elif op == "$last":
                    g[field] = val
                elif op == "$max":
                    g[field] = val if field not in g or g.get(field) is None else max(g[field], val)
                elif op == "$min":
                    g[field] = val if field not in g or g.get(field) is None else min(g[field], val)
        result = []
        for key in order:
            g = groups[key]
            g.pop("__first_done", None)
            result.append(g)
        return result


# --- Database / Client ------------------------------------------------------


class Database:
    def __init__(self, name):
        self.name = name
        self._collections = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = Collection()
        return self._collections[name]

    def command(self, *a, **k):
        return {"ok": 1.0}


class _FakeAdmin:
    def command(self, *a, **k):
        return {"ok": 1.0}


class MongoClient:
    def __init__(self, *a, **k):
        self.admin = _FakeAdmin()
        self._dbs = {}

    def __getitem__(self, name):
        if name not in self._dbs:
            self._dbs[name] = Database(name)
        return self._dbs[name]


def install():
    """
    Injects this module in place of the real `pymongo`/`bson` packages in
    sys.modules, so `from pymongo import MongoClient` etc. anywhere in the
    app resolves to these fakes instead of trying to reach real MongoDB.
    Must be called BEFORE the app package is imported.
    """
    import sys
    import types

    pymongo_mod = types.ModuleType("pymongo")
    pymongo_mod.MongoClient = MongoClient
    pymongo_mod.ReturnDocument = ReturnDocument

    pymongo_errors_mod = types.ModuleType("pymongo.errors")
    pymongo_errors_mod.PyMongoError = PyMongoError
    pymongo_errors_mod.ConnectionFailure = ConnectionFailure
    pymongo_errors_mod.ConfigurationError = ConfigurationError
    pymongo_errors_mod.DuplicateKeyError = DuplicateKeyError
    pymongo_mod.errors = pymongo_errors_mod

    bson_mod = types.ModuleType("bson")
    bson_mod.ObjectId = ObjectId

    bson_errors_mod = types.ModuleType("bson.errors")
    bson_errors_mod.InvalidId = InvalidId
    bson_mod.errors = bson_errors_mod

    sys.modules["pymongo"] = pymongo_mod
    sys.modules["pymongo.errors"] = pymongo_errors_mod
    sys.modules["bson"] = bson_mod
    sys.modules["bson.errors"] = bson_errors_mod
