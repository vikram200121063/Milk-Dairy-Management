"""
Everything that talks to an AI provider lives here, so every other module
just calls a plain Python function and never has to know which provider is
in use, what its SDK looks like, or how tool-calling works for it. Every
public function in this file degrades gracefully when no key is configured
for the active provider (or a call fails): callers get back a clear
(None, error_message) or an unchanged fallback rather than a crash, because
none of the AI features here are required for the rest of the app to work.

Four providers are supported, picked via the AI_PROVIDER setting (see
config.py / .env.example):
    - "anthropic" (default) - Claude.            Paid, no free tier.
    - "openai"               - GPT.                Paid, no free tier.
    - "gemini"               - Google Gemini.      Has a real free tier;
                               supports every feature here, including vision.
    - "groq"                 - Fast open models.   Generous free tier, but
                               no reliable vision support, so the receipt
                               scanner feature isn't available on it.
"openai" and "groq" share one implementation below, since Groq's API is
deliberately OpenAI-compatible.

Four features live behind this one file:
    1. ask_business_question()   - the dashboard "Ask" business assistant
    2. draft_dunning_paragraph() - tone-adjusted overdue-payment reminders
    3. generate_pnl_narrative()  - plain-English Profit & Loss summary
    4. extract_receipt_data()    - photo-of-a-receipt -> expense form fields

Confidence note: the Anthropic and OpenAI/Groq code paths follow their
SDKs' long-stable, well-documented request/response shapes. The Gemini
path follows the documented `google-generativeai` function-calling pattern
just as carefully, but that SDK has churned more across versions - if you
hit an AttributeError on the Gemini path, it's most likely a version
mismatch worth checking against https://ai.google.dev/gemini-api/docs/function-calling
for whatever `google-generativeai` version pip installs for you.
"""
import base64
import json
from datetime import datetime, timezone, timedelta

from flask import current_app

from app.services import accounting_service

MAX_LIMIT = 25

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

_PROVIDER_KEY_CONFIG = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}
_PROVIDER_MODEL_CONFIG = {
    "anthropic": ("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
    "openai": ("OPENAI_MODEL", "gpt-4o-mini"),
    "gemini": ("GEMINI_MODEL", "gemini-2.0-flash"),
    "groq": ("GROQ_MODEL", "llama-3.3-70b-versatile"),
}


def _active_provider():
    provider = (current_app.config.get("AI_PROVIDER") or "anthropic").strip().lower()
    return provider if provider in _PROVIDER_KEY_CONFIG else "anthropic"


def ai_configured():
    """Whether a key is set for whichever provider is active. Every route
    should check this before showing an AI-powered control, so the UI
    never dead-ends silently."""
    provider = _active_provider()
    return bool(current_app.config.get(_PROVIDER_KEY_CONFIG[provider]))


def _model():
    provider = _active_provider()
    env_key, default = _PROVIDER_MODEL_CONFIG[provider]
    return current_app.config.get(env_key) or default


def _not_configured_message():
    provider = _active_provider()
    env_key = _PROVIDER_KEY_CONFIG[provider]
    return (
        f"AI features aren't configured yet. Set AI_PROVIDER={provider} and {env_key} in "
        f"your .env file (or switch AI_PROVIDER to one you have a key for)."
    )


def _get_client():
    """
    Returns (provider, client) where `client` is that provider's SDK
    object ready to use, or None if AI isn't usable right now (no key
    configured, or that provider's package isn't installed) - callers
    treat None exactly like a failed API call.

    For "gemini", `client` is the configured `google.generativeai` module
    itself (that SDK is used module-level, not through an instance).
    """
    provider = _active_provider()
    api_key = current_app.config.get(_PROVIDER_KEY_CONFIG[provider])
    if not api_key:
        return provider, None

    try:
        if provider == "anthropic":
            import anthropic

            return provider, anthropic.Anthropic(api_key=api_key)

        if provider == "openai":
            import openai

            return provider, openai.OpenAI(api_key=api_key)

        if provider == "groq":
            import openai  # Groq's API is deliberately OpenAI-compatible

            return provider, openai.OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

        if provider == "gemini":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            return provider, genai
    except ImportError:
        current_app.logger.warning(
            f"AI_PROVIDER is '{provider}' but its package isn't installed. Run: pip install -r requirements.txt"
        )
        return provider, None

    return provider, None  # unreachable given _active_provider()'s whitelist, kept for safety


def _clamp_int(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# 1. Business assistant - safe, read-only tools over the dairy's own data
# ---------------------------------------------------------------------------
#
# Claude/GPT/Gemini never query MongoDB directly. They can only pick from
# the small, fixed set of functions below, each with a narrow, whitelisted
# set of parameters - so there's no injection surface and no way for a
# question to accidentally (or deliberately) touch data it shouldn't. The
# spec (TOOLS) is provider-neutral JSON Schema; each provider's adapter
# converts it to whatever shape that SDK wants.


def _tool_business_overview(db, tool_input):
    data = accounting_service.build_dashboard(db, "today")
    return {
        "as_of": "today",
        "active_customers": db.customers.count_documents({"status": "Active"}),
        "active_buyers": db.buyers.count_documents({"status": "Active"}),
        "cash_bank_balance": data["cash_bank_balance"],
        "outstanding_receivables_from_buyers": data["outstanding_receivables"],
        "outstanding_payables_to_customers": data["outstanding_payables"],
        "overdue_buyer_invoices": data["overdue"],
        "pending_customer_payments": data["pending_customer_payments"],
        "todays_revenue": data["revenue"],
        "todays_net_profit": data["net_profit"],
    }


def _tool_revenue_and_expense(db, tool_input):
    range_type = tool_input.get("range", "this_month")
    if range_type not in ("today", "this_week", "this_month"):
        range_type = "this_month"
    data = accounting_service.build_dashboard(db, range_type)
    return {
        "range": data["range_label"],
        "sales_revenue": data["sales_revenue"],
        "other_income": data["other_income"],
        "interest_income": data["interest_income"],
        "total_revenue": data["revenue"],
        "milk_purchase_cost": data["milk_purchase_cost"],
        "other_expenses": data["other_expenses"],
        "net_profit": data["net_profit"],
        "net_cash_flow": data["net_cash_flow"],
        "expense_by_category": data["expense_by_category"],
    }


def _tool_top_customers_by_supply(db, tool_input):
    days = _clamp_int(tool_input.get("days"), 30, 1, 90)
    limit = _clamp_int(tool_input.get("limit"), 5, 1, MAX_LIMIT)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"date": {"$gte": since}}},
        {
            "$group": {
                "_id": "$customer_id",
                "customer_name": {"$last": "$customer_name"},
                "total_quantity": {"$sum": "$quantity"},
                "avg_fat": {"$avg": "$fat_percentage"},
                "avg_snf": {"$avg": "$snf_percentage"},
                "total_amount": {"$sum": "$total_amount"},
                "entries": {"$sum": 1},
            }
        },
        {"$sort": {"total_quantity": -1}},
        {"$limit": limit},
    ]
    rows = list(db.milk_entries.aggregate(pipeline))
    return {
        "period_days": days,
        "customers": [
            {
                "customer_name": r["customer_name"],
                "total_quantity_litres": round(r["total_quantity"], 2),
                "avg_fat_percent": round(r["avg_fat"], 2),
                "avg_snf_percent": round(r["avg_snf"], 2),
                "total_amount": round(r["total_amount"], 2),
                "entries": r["entries"],
            }
            for r in rows
        ],
    }


def _tool_customer_details(db, tool_input):
    name = (tool_input.get("name") or "").strip()
    if not name:
        return {"error": "A customer name is required."}
    customer = db.customers.find_one({"name": {"$regex": name, "$options": "i"}})
    if not customer:
        return {"error": f"No customer found matching '{name}'."}

    days = _clamp_int(tool_input.get("days"), 30, 1, 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    entries = list(
        db.milk_entries.find({"customer_id": customer["customer_id"], "date": {"$gte": since}}).sort("date", -1)
    )
    if entries:
        avg_fat = round(sum(e["fat_percentage"] for e in entries) / len(entries), 2)
        avg_snf = round(sum(e["snf_percentage"] for e in entries) / len(entries), 2)
        total_qty = round(sum(e["quantity"] for e in entries), 2)
    else:
        avg_fat = avg_snf = total_qty = 0

    payment = db.payments.find_one({"customer_id": customer["customer_id"]}, sort=[("cycle_end", -1)])
    return {
        "customer_name": customer["name"],
        "status": customer.get("status"),
        "period_days": days,
        "entries_in_period": len(entries),
        "total_quantity_litres": total_qty,
        "avg_fat_percent": avg_fat,
        "avg_snf_percent": avg_snf,
        "most_recent_payment_cycle": payment.get("payment_period") if payment else None,
        "most_recent_cycle_remaining_balance": payment.get("remaining_amount") if payment else None,
    }


def _tool_overdue_buyers(db, tool_input):
    limit = _clamp_int(tool_input.get("limit"), 10, 1, MAX_LIMIT)
    rows = list(db.buyer_invoices.find({"status": "Overdue"}).sort("remaining_amount", -1).limit(limit))
    return {
        "overdue_invoices": [
            {
                "buyer_name": r["buyer_name"],
                "invoice_id": r["invoice_id"],
                "remaining_amount": r["remaining_amount"],
                "overdue_days": r.get("overdue_days", 0),
                "due_date": r["due_date"].strftime("%Y-%m-%d"),
            }
            for r in rows
        ]
    }


def _tool_pending_customer_payments(db, tool_input):
    limit = _clamp_int(tool_input.get("limit"), 10, 1, MAX_LIMIT)
    rows = list(db.payments.find({"remaining_amount": {"$gt": 0}}).sort("remaining_amount", -1).limit(limit))
    return {
        "pending_payments": [
            {
                "customer_name": r["customer_name"],
                "payment_period": r["payment_period"],
                "remaining_amount": r["remaining_amount"],
            }
            for r in rows
        ]
    }


TOOLS = [
    {
        "name": "get_business_overview",
        "description": (
            "A snapshot of the dairy's state right now: active customer/buyer counts, cash on "
            "hand, outstanding receivables/payables, and today's revenue/profit. Good default "
            "first call for any vague or broad question."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_revenue_and_expense",
        "description": "Revenue, expense, profit, and cash-flow breakdown for a period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": ["today", "this_week", "this_month"],
                    "description": "Defaults to this_month.",
                }
            },
        },
    },
    {
        "name": "get_top_customers_by_supply",
        "description": (
            "Customers ranked by total milk supplied (litres) in the last N days, with their "
            "average fat%/SNF% over that window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookback window, 1-90. Defaults to 30."},
                "limit": {"type": "integer", "description": "Max customers to return, 1-25. Defaults to 5."},
            },
        },
    },
    {
        "name": "get_customer_details",
        "description": "Milk-supply and payment details for one specific customer, looked up by (partial) name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Customer name, or part of it, to search for."},
                "days": {"type": "integer", "description": "Lookback window for supply stats, 1-90. Defaults to 30."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_overdue_buyers",
        "description": "Buyers with overdue invoices, sorted by amount owed, largest first.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max rows, 1-25. Defaults to 10."}},
        },
    },
    {
        "name": "get_pending_customer_payments",
        "description": (
            "Customer payment cycles that still have a remaining balance owed TO the customer, "
            "largest first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max rows, 1-25. Defaults to 10."}},
        },
    },
]

TOOL_IMPL = {
    "get_business_overview": _tool_business_overview,
    "get_revenue_and_expense": _tool_revenue_and_expense,
    "get_top_customers_by_supply": _tool_top_customers_by_supply,
    "get_customer_details": _tool_customer_details,
    "get_overdue_buyers": _tool_overdue_buyers,
    "get_pending_customer_payments": _tool_pending_customer_payments,
}

ASSISTANT_SYSTEM_PROMPT = (
    "You are the business assistant built into a milk dairy's management system. You answer "
    "the dairy owner's questions about their own customers, buyers, milk collection, payments, "
    "and finances using ONLY the tools provided - never guess or invent figures. Call one or "
    "more tools to gather what you need, then answer in 2-5 short sentences or a brief bullet "
    "list. Amounts are in Indian Rupees (₹). If a tool returns no data or an error, say so "
    "plainly rather than making something up. You cannot take any action (send emails, change "
    "records, etc.) - you can only report information that already exists."
)

_MAX_TOOL_ROUNDS = 4  # hard cap on tool-use round trips for one question
_TOO_MANY_LOOKUPS = "That question needed too many lookups to answer - try asking something more specific."


def _run_tool(db, name, tool_input):
    impl = TOOL_IMPL.get(name)
    try:
        return impl(db, tool_input or {}) if impl else {"error": f"Unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001 - a bad lookup should never 500 the chat
        return {"error": f"Could not run that lookup: {exc}"}


def _ask_anthropic(client, db, question, history):
    import anthropic

    messages = list(history or [])
    messages.append({"role": "user", "content": question})

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            response = client.messages.create(
                model=_model(),
                max_tokens=1024,
                system=ASSISTANT_SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return text or "I couldn't find anything to say about that.", None

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = _run_tool(db, block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, default=str)}
                )
            messages.append({"role": "user", "content": tool_results})

        return None, _TOO_MANY_LOOKUPS
    except anthropic.APIError as exc:
        return None, f"The AI assistant hit an error: {exc}"


def _openai_tool_specs():
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in TOOLS
    ]


def _ask_openai_compatible(client, db, question, history):
    """Shared by AI_PROVIDER "openai" and "groq" - Groq's API is
    deliberately OpenAI-compatible, so one implementation covers both."""
    messages = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})
    tools = _openai_tool_specs()

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=_model(),
                max_tokens=1024,
                messages=messages,
                tools=tools,
            )
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                text = (msg.content or "").strip()
                return text or "I couldn't find anything to say about that.", None

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (TypeError, ValueError):
                    args = {}
                result = _run_tool(db, tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)}
                )

        return None, _TOO_MANY_LOOKUPS
    except Exception as exc:  # noqa: BLE001 - covers openai.APIError and Groq's own subclasses of it
        return None, f"The AI assistant hit an error: {exc}"


_GEMINI_TYPE_MAP_NAMES = {
    "object": "OBJECT",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "array": "ARRAY",
    "boolean": "BOOLEAN",
}


def _json_schema_to_gemini_schema(genai_module, schema):
    """Converts one of our plain JSON-Schema tool definitions into a
    genai.protos.Schema, recursively. See TOOLS above for the input shape."""
    kind = schema.get("type", "object")
    type_enum = getattr(genai_module.protos.Type, _GEMINI_TYPE_MAP_NAMES.get(kind, "STRING"))
    kwargs = {"type_": type_enum}
    if schema.get("description"):
        kwargs["description"] = schema["description"]
    if schema.get("enum"):
        kwargs["enum"] = schema["enum"]
    if kind == "object":
        props = schema.get("properties") or {}
        if props:
            kwargs["properties"] = {k: _json_schema_to_gemini_schema(genai_module, v) for k, v in props.items()}
        if schema.get("required"):
            kwargs["required"] = schema["required"]
    if kind == "array" and schema.get("items"):
        kwargs["items"] = _json_schema_to_gemini_schema(genai_module, schema["items"])
    return genai_module.protos.Schema(**kwargs)


def _gemini_tools(genai_module):
    declarations = [
        genai_module.protos.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_json_schema_to_gemini_schema(genai_module, t["input_schema"]),
        )
        for t in TOOLS
    ]
    return [genai_module.protos.Tool(function_declarations=declarations)]


def _ask_gemini(genai_module, db, question, history):
    try:
        model = genai_module.GenerativeModel(
            _model(),
            system_instruction=ASSISTANT_SYSTEM_PROMPT,
            tools=_gemini_tools(genai_module),
        )
        gemini_history = [
            {"role": "model" if h["role"] == "assistant" else "user", "parts": [h["content"]]}
            for h in (history or [])
        ]
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(question)

        for _ in range(_MAX_TOOL_ROUNDS):
            parts = response.candidates[0].content.parts
            function_calls = [p.function_call for p in parts if getattr(p, "function_call", None) and p.function_call.name]

            if not function_calls:
                text = (getattr(response, "text", "") or "").strip()
                return text or "I couldn't find anything to say about that.", None

            response_parts = []
            for fc in function_calls:
                args = dict(fc.args) if fc.args else {}
                result = _run_tool(db, fc.name, args)
                response_parts.append(
                    genai_module.protos.Part(
                        function_response=genai_module.protos.FunctionResponse(
                            name=fc.name, response={"result": json.dumps(result, default=str)}
                        )
                    )
                )
            response = chat.send_message(genai_module.protos.Content(parts=response_parts))

        return None, _TOO_MANY_LOOKUPS
    except Exception as exc:  # noqa: BLE001
        return None, f"The AI assistant hit an error: {exc}"


def ask_business_question(db, question, history=None):
    """
    Answers one natural-language business question using the small,
    read-only tool set above, through whichever provider AI_PROVIDER
    selects. `history` is an optional list of prior {"role", "content"}
    turns (plain strings) from the same chat widget session, so follow-ups
    ("and last month?") work without resending everything.

    Returns (answer_text, error) - exactly one of the two is set.
    """
    provider, client = _get_client()
    if not client:
        return None, _not_configured_message()

    if provider == "anthropic":
        return _ask_anthropic(client, db, question, history)
    if provider in ("openai", "groq"):
        return _ask_openai_compatible(client, db, question, history)
    if provider == "gemini":
        return _ask_gemini(client, db, question, history)
    return None, f"Unsupported AI provider: {provider}"  # unreachable, kept for safety


# ---------------------------------------------------------------------------
# 2. AI-drafted dunning reminder paragraphs
# ---------------------------------------------------------------------------


def _simple_text_completion(provider, client, prompt, max_tokens):
    """One-shot prompt -> plain text, no tools. Shared by the dunning
    draft and the P&L narrative, which need nothing fancier than that."""
    model = _model()
    if provider == "anthropic":
        response = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in response.content if b.type == "text").strip()

    if provider in ("openai", "groq"):
        response = client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
        )
        return (response.choices[0].message.content or "").strip()

    if provider == "gemini":
        model_obj = client.GenerativeModel(model)
        response = model_obj.generate_content(prompt, generation_config={"max_output_tokens": max_tokens})
        return (getattr(response, "text", "") or "").strip()

    raise RuntimeError(f"Unsupported AI provider: {provider}")


def draft_dunning_paragraph(buyer, invoice, overdue_days, prior_reminder_count):
    """
    Drafts the 2-4 sentence body paragraph for an overdue-invoice reminder
    email, tuned in tone to how many reminders this buyer has already had
    (a warm nudge on the first one, noticeably firmer on repeat lateness).

    Returns None on any failure (no key, API error, empty response) so the
    caller falls back to the static template in
    email_service.send_dunning_reminder_email - a reminder always goes out
    one way or another, AI-drafted or not.
    """
    provider, client = _get_client()
    if not client:
        return None

    if prior_reminder_count == 0:
        tone = "This is their first late payment with us, so keep it warm and give them the benefit of the doubt."
    else:
        tone = (
            f"This buyer has already been sent {prior_reminder_count} reminder(s) before on past "
            "invoices, so be noticeably firmer and more direct this time, without being rude."
        )

    prompt = (
        "Write ONLY the body paragraph (2-4 sentences, no greeting, no signoff, no subject line) "
        f"of a payment reminder email to {buyer.get('contact_person') or buyer['company_name']} "
        f"of {buyer['company_name']}. Invoice {invoice['invoice_id']} for billing period "
        f"{invoice['billing_period']} was due on {invoice['due_date'].strftime('%d %b %Y')} and is "
        f"now {overdue_days} day(s) overdue, with ₹{invoice['remaining_amount']:.2f} still "
        f"outstanding. {tone} Return plain text only - no markdown, no HTML."
    )

    try:
        return _simple_text_completion(provider, client, prompt, max_tokens=200) or None
    except Exception:  # noqa: BLE001 - any failure here just means "use the static template"
        current_app.logger.warning("AI dunning draft failed; falling back to the static template.", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 3. Monthly Profit & Loss narrative
# ---------------------------------------------------------------------------


def generate_pnl_narrative(data, range_label, dairy_name):
    """
    Turns an accounting_service figures dict (build_dashboard() or
    profit_and_loss()) into a short (3-5 sentence) plain-English summary
    for a non-technical owner. Returns (narrative, error) - exactly one is
    set. Never invents figures beyond what's in `data`; it's told not to.
    """
    provider, client = _get_client()
    if not client:
        return None, _not_configured_message()

    payload = {
        "range": range_label,
        "sales_revenue": data.get("sales_revenue"),
        "other_income": data.get("other_income"),
        "interest_income": data.get("interest_income"),
        "total_revenue": data.get("revenue", data.get("total_revenue")),
        "milk_purchase_cost": data.get("milk_purchase_cost"),
        "other_expenses": data.get("other_expenses"),
        "net_profit": data.get("net_profit"),
        "net_cash_flow": data.get("net_cash_flow"),
        "outstanding_receivables": data.get("outstanding_receivables"),
        "outstanding_payables": data.get("outstanding_payables"),
        "overdue": data.get("overdue"),
        "expense_by_category": data.get("expense_by_category"),
    }
    prompt = (
        f"Here are {dairy_name}'s financial figures for {range_label}, in Indian Rupees, as JSON "
        f"(a null field just means that figure isn't available for this range - don't mention "
        f"the JSON or the word 'null'):\n{json.dumps(payload, default=str)}\n\n"
        "Write a plain-English summary for the dairy's owner in 3-5 short sentences: how the "
        "period went (profit or loss, and by how much), the single biggest cost driver, and one "
        "thing worth their attention (e.g. an overdue buyer, low cash, a rising expense category) "
        "if the data suggests one. No markdown, no bullet points, just prose. Be concrete with "
        "numbers, not vague."
    )

    try:
        text = _simple_text_completion(provider, client, prompt, max_tokens=400)
        return (text, None) if text else (None, "The AI assistant returned an empty summary.")
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not generate the AI summary: {exc}"


# ---------------------------------------------------------------------------
# 4. Receipt / expense-document photo auto-fill
# ---------------------------------------------------------------------------


def _receipt_schema():
    from app.routes.expenses import EXPENSE_CATEGORIES  # deferred: avoids an import cycle at app start

    return {
        "name": "record_receipt_data",
        "description": "Records the structured data extracted from a receipt/expense document image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Total amount on the receipt, a plain number with no currency symbol.",
                },
                "date": {
                    "type": "string",
                    "description": "Date on the receipt as YYYY-MM-DD. Omit if no date is visible.",
                },
                "description": {
                    "type": "string",
                    "description": "Under 10 words: the vendor/item this expense was for.",
                },
                "category": {"type": "string", "enum": list(EXPENSE_CATEGORIES)},
                "entry_type": {
                    "type": "string",
                    "enum": ["Expense", "Income"],
                    "description": "'Income' only if this is clearly money coming IN to the dairy.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "low"],
                    "description": "'low' if the image is blurry/unclear or key fields had to be guessed.",
                },
            },
            "required": ["amount", "description", "category", "entry_type", "confidence"],
        },
    }


_RECEIPT_PROMPT_TEMPLATE = (
    "Extract the expense data from this receipt for a dairy's bookkeeping. Today's date is "
    "{today} if you need it for context. Pick the closest matching category even if it's not "
    "a perfect fit."
)


def _extract_receipt_anthropic(client, image_bytes, media_type, schema, today_str):
    tool = dict(schema)
    response = client.messages.create(
        model=_model(),
        max_tokens=500,
        tool_choice={"type": "tool", "name": tool["name"]},
        tools=[tool],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": _RECEIPT_PROMPT_TEMPLATE.format(today=today_str)},
                ],
            }
        ],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            result = dict(block.input or {})
            result.setdefault("date", today_str)
            return result, None
    return None, "The AI assistant couldn't extract anything usable from that image."


def _extract_receipt_openai(client, image_bytes, media_type, schema, today_str):
    b64 = base64.b64encode(image_bytes).decode("ascii")
    tool_spec = {
        "type": "function",
        "function": {"name": schema["name"], "description": schema["description"], "parameters": schema["input_schema"]},
    }
    response = client.chat.completions.create(
        model=_model(),
        max_tokens=500,
        tool_choice={"type": "function", "function": {"name": schema["name"]}},
        tools=[tool_spec],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _RECEIPT_PROMPT_TEMPLATE.format(today=today_str)},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }
        ],
    )
    msg = response.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        return None, "The AI assistant couldn't extract anything usable from that image."
    try:
        result = json.loads(tool_calls[0].function.arguments or "{}")
    except (TypeError, ValueError):
        return None, "The AI assistant returned data in an unexpected format."
    result.setdefault("date", today_str)
    return result, None


def _extract_receipt_gemini(genai_module, image_bytes, media_type, schema, today_str):
    declaration = genai_module.protos.FunctionDeclaration(
        name=schema["name"],
        description=schema["description"],
        parameters=_json_schema_to_gemini_schema(genai_module, schema["input_schema"]),
    )
    model = genai_module.GenerativeModel(
        _model(),
        tools=[genai_module.protos.Tool(function_declarations=[declaration])],
        tool_config={"function_calling_config": {"mode": "ANY", "allowed_function_names": [schema["name"]]}},
    )
    response = model.generate_content(
        [
            {"mime_type": media_type, "data": image_bytes},
            _RECEIPT_PROMPT_TEMPLATE.format(today=today_str),
        ]
    )
    parts = response.candidates[0].content.parts
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc and fc.name == schema["name"]:
            result = dict(fc.args) if fc.args else {}
            result.setdefault("date", today_str)
            return result, None
    return None, "The AI assistant couldn't extract anything usable from that image."


def extract_receipt_data(image_bytes, media_type):
    """
    Reads a photographed/scanned receipt or expense document and returns a
    best-guess dict - {amount, date, description, category, entry_type,
    confidence} - to prefill the Add Expense form. The admin still reviews
    and submits the form themselves; this never writes to the database.

    Returns (data, error) - exactly one is set. Not available on
    AI_PROVIDER=groq (no reliable vision + tool-calling support there).
    """
    provider, client = _get_client()
    if not client:
        return None, _not_configured_message()

    if media_type not in _ALLOWED_IMAGE_TYPES:
        return None, "Please upload a JPEG, PNG, WEBP, or GIF image of the receipt."

    if provider == "groq":
        return None, (
            "Receipt scanning needs vision support that isn't reliably available through Groq. "
            "Switch AI_PROVIDER to gemini, openai, or anthropic in your .env for this feature."
        )

    schema = _receipt_schema()
    today_str = datetime.now(timezone.utc).date().isoformat()

    try:
        if provider == "anthropic":
            return _extract_receipt_anthropic(client, image_bytes, media_type, schema, today_str)
        if provider == "openai":
            return _extract_receipt_openai(client, image_bytes, media_type, schema, today_str)
        if provider == "gemini":
            return _extract_receipt_gemini(client, image_bytes, media_type, schema, today_str)
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not read the receipt: {exc}"

    return None, f"Unsupported AI provider: {provider}"  # unreachable, kept for safety
