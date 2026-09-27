from flask import Blueprint, render_template, request, current_app

from app.utils.decorators import owner_required

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notifications_bp.route("/")
@owner_required
def list_notifications():
    notif_type = request.args.get("type", "")
    channel = request.args.get("channel", "")
    status = request.args.get("status", "")

    filters = {}
    if notif_type in ("milk_entry", "invoice"):
        filters["type"] = notif_type
    if channel == "email":
        filters["channel"] = channel
    if status in ("sent", "failed"):
        filters["status"] = status

    logs = list(
        current_app.db.notifications.find(filters).sort("sent_at", -1).limit(300)
    )

    return render_template(
        "notifications/list.html",
        logs=logs,
        notif_type=notif_type,
        channel=channel,
        status=status,
    )
