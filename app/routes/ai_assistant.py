"""
Backs the floating "Business Assistant" chat widget (admin-only - see
app/templates/partials/ai_assistant_widget.html). All the actual AI work
happens in app/services/ai_service.py; this blueprint is just the thin
HTTP layer plus a short per-session chat history so follow-up questions
work.
"""
from flask import Blueprint, current_app, jsonify, request, session

from app.services import ai_service
from app.utils.decorators import login_required

ai_bp = Blueprint("ai_assistant", __name__, url_prefix="/ai")

# Kept small on purpose: this rides in the session cookie, and a long
# history also means a bigger, slower prompt for every follow-up question.
MAX_HISTORY_TURNS = 3


@ai_bp.route("/ask", methods=["POST"])
@login_required
def ask():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "Type a question first."}), 400
    if len(question) > 500:
        return jsonify({"error": "That question is a bit long - please keep it under 500 characters."}), 400

    history = session.get("ai_chat_history", [])
    answer, error = ai_service.ask_business_question(current_app.db, question, history=history)

    if error:
        return jsonify({"error": error}), 200

    updated_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    session["ai_chat_history"] = updated_history[-(MAX_HISTORY_TURNS * 2):]

    return jsonify({"answer": answer})


@ai_bp.route("/reset", methods=["POST"])
@login_required
def reset():
    session.pop("ai_chat_history", None)
    return jsonify({"ok": True})
