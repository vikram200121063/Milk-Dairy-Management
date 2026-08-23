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

    # Business details shown on printed/PDF invoices
    DAIRY_NAME = os.environ.get("DAIRY_NAME", "My Milk Dairy")
    DAIRY_ADDRESS = os.environ.get("DAIRY_ADDRESS", "Village Road, Your Town")
    DAIRY_CONTACT = os.environ.get("DAIRY_CONTACT", "+91 90000 00000")

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
