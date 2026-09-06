import time
import os

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app

from app.utils.decorators import login_required

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
AVATAR_SIZE = 256  # square thumbnail, in pixels


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _avatar_dir():
    path = os.path.join(current_app.root_path, "static", "uploads", "avatars")
    os.makedirs(path, exist_ok=True)
    return path


@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = current_app.db
    try:
        user = db.users.find_one({"_id": ObjectId(session["user_id"])})
    except (InvalidId, TypeError):
        user = None
    if not user:
        flash("Your account could not be found. Please log in again.", "danger")
        return redirect(url_for("auth.logout"))

    if request.method == "POST":
        action = request.form.get("action", "upload")

        if action == "remove":
            old_path = user.get("profile_photo")
            if old_path:
                full_path = os.path.join(current_app.root_path, "static", old_path)
                if os.path.exists(full_path):
                    os.remove(full_path)
            db.users.update_one({"_id": user["_id"]}, {"$unset": {"profile_photo": ""}})
            session["profile_photo"] = None
            flash("Profile photo removed.", "info")
            return redirect(url_for("profile.index"))

        # action == "upload"
        file = request.files.get("photo")
        if not file or file.filename == "":
            flash("Please choose an image to upload.", "danger")
            return redirect(url_for("profile.index"))
        if not _allowed_file(file.filename):
            flash("Please upload a PNG, JPG, or WEBP image.", "danger")
            return redirect(url_for("profile.index"))

        try:
            from PIL import Image as PILImage, UnidentifiedImageError
        except ImportError:
            flash("Image processing is unavailable on this server.", "danger")
            return redirect(url_for("profile.index"))

        try:
            img = PILImage.open(file.stream)
            img.load()
        except UnidentifiedImageError:
            flash("That file doesn't look like a valid image.", "danger")
            return redirect(url_for("profile.index"))

        # Center-crop to a square, then downscale to a fixed thumbnail size -
        # keeps every avatar a consistent shape/weight regardless of what
        # the person uploaded.
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((AVATAR_SIZE, AVATAR_SIZE), PILImage.LANCZOS)

        filename = f"{user['_id']}.jpg"
        save_path = os.path.join(_avatar_dir(), filename)
        img.save(save_path, format="JPEG", quality=88)

        relative_path = f"uploads/avatars/{filename}"
        db.users.update_one({"_id": user["_id"]}, {"$set": {"profile_photo": relative_path}})
        session["profile_photo"] = relative_path
        session["photo_version"] = int(time.time())
        flash("Profile photo updated.", "success")
        return redirect(url_for("profile.index"))

    return render_template("profile.html", user=user)
