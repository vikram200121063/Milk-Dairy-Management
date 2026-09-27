import os
from dotenv import load_dotenv

# Load variables from a .env file into the environment (only affects local dev;
# in production, real environment variables are set by the hosting platform)
load_dotenv()


class Config:
    """
    Central configuration object.
    Every value here is read from an environment variable rather than
    hard-coded, so secrets never live in the source code.
    """

    # Used by Flask to sign session cookies. Without this, login sessions
    # would not be secure.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # MongoDB Atlas connection string, e.g.
    # mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/milk_dairy
    MONGO_URI = os.environ.get("MONGO_URI")

    # Name of the database inside the Atlas cluster
    MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "milk_dairy")

    # Whether Flask runs in debug mode (auto-reload, detailed error pages).
    # Should always be False in production.
    DEBUG = os.environ.get("FLASK_DEBUG", "False").lower() in ("true", "1")

    # Caps any single request body (e.g. a profile photo upload) at 8MB,
    # so a mistakenly-huge file can't tie up the server or fill the disk.
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024

    # Business details shown on printed/PDF invoices
    DAIRY_NAME = os.environ.get("DAIRY_NAME", "OM Dairy")
    DAIRY_ADDRESS = os.environ.get("DAIRY_ADDRESS", "Village Road, Your Town")
    DAIRY_CONTACT = os.environ.get("DAIRY_CONTACT", "+91 90000 00000")

    # --- SMTP (Email) ---
    # Works with Gmail (use an App Password, not your login password) or
    # any other SMTP provider.
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = os.environ.get("SMTP_PORT", "587")
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "True")
    SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL")
    SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", DAIRY_NAME)

    # --- AI features ---
    # Powers the dashboard "Ask" business assistant, AI-drafted dunning
    # reminders, the P&L narrative summary, and receipt/photo auto-fill on
    # the expense form (app/services/ai_service.py). Every one of those
    # features checks ai_service.ai_configured() first and falls back to
    # its previous non-AI behavior (or a plain "not configured" message)
    # when no key is set for the active provider - nothing here is
    # required for the rest of the app to work.
    #
    # AI_PROVIDER picks which service the four features above call:
    #   "anthropic" (default) - Claude. Paid, no free tier. console.anthropic.com
    #   "openai"              - GPT. Paid, no free tier. platform.openai.com
    #   "gemini"              - Google Gemini. Has a genuinely free tier and
    #                           supports both vision and tool-calling, so it's
    #                           the closest free drop-in for every feature
    #                           here. Get a key at aistudio.google.com/apikey
    #   "groq"                - Runs open models (Llama, etc.) very fast with
    #                           a generous free tier, but its models don't
    #                           reliably support the vision call the receipt
    #                           auto-fill feature needs - that one feature
    #                           will show a "not supported" message under
    #                           this provider. Get a key at console.groq.com
    # Only the key for whichever provider you pick actually needs to be set.
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").strip().lower()

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    # Google retires older Gemini model names periodically - if this default
    # ever 404s with a "model no longer available" error, that error message
    # itself names the current replacement; put that name in GEMINI_MODEL.
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Where the "flask send-pl-summary" CLI command emails its monthly
    # AI-written Profit & Loss narrative. Leave blank to skip that email
    # (the on-demand "AI Summary" button on the Accounting dashboard works
    # regardless of this setting).
    ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL")

    # --- Online payments (Razorpay) ---
    # Powers the customer portal's "Pay Now" button and the shareable
    # buyer invoice payment link (app/services/payment_gateway_service.py).
    # Leave both blank to disable online payments entirely - every payment
    # screen falls back to the existing manual "record a payment"
    # form, exactly as before this feature existed.
    #
    # Get free TEST-mode keys (no KYC, no fees, no real money moves) at
    # https://dashboard.razorpay.com/app/keys -> toggle "Test Mode" on,
    # then "Generate Test Key". Switching to LIVE keys later (real money,
    # requires business KYC, ~2% transaction fee) needs no code change -
    # just replace these two values.
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

    # Optional but recommended once RAZORPAY_KEY_ID/SECRET are set: a
    # webhook lets Razorpay confirm a payment even if the buyer/customer's
    # browser never makes it back to our "verify" call (closed tab, lost
    # connection). Get this from Razorpay Dashboard -> Settings -> Webhooks
    # after adding https://<your-app-domain>/pay/webhook there, subscribed
    # to the "payment.captured" event - it's a different value from
    # RAZORPAY_KEY_SECRET. Leave blank and the app works exactly as before
    # (browser-only confirmation) - see app/routes/pay.py's webhook() route.
    RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    @staticmethod
    def validate():
        """
        Fail fast and loud if critical config is missing, instead of
        letting the app start in a broken state.
        """
        missing = []
        if not Config.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not Config.MONGO_URI:
            missing.append("MONGO_URI")
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Did you create a .env file from .env.example?"
            )
