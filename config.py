import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'drinks.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
    RFID_MOCK = os.environ.get("RFID_MOCK", "true").lower() == "true"
    # "auto" = detect CP2102 USB bridge first, then fall back to GPIO UART
    RFID_PORT = os.environ.get("RFID_PORT", "auto")
    SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
    # Offsite sync provider: "none" | "cloud"
    SYNC_PROVIDER = os.environ.get("SYNC_PROVIDER", "none")
    # Cloud sync — defaults that can be overridden via the admin settings page
    CLOUD_URL     = os.environ.get("CLOUD_URL", "")
    CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY", "")
    BAR_LOCATION  = os.environ.get("BAR_LOCATION", "Bar")


class DevelopmentConfig(Config):
    DEBUG = True
    # Allow RFID_MOCK=false in .env to override even in dev (e.g. testing with real hardware on PC)
    RFID_MOCK = os.environ.get("RFID_MOCK", "true").lower() == "true"


class ProductionConfig(Config):
    DEBUG = False
    RFID_MOCK = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
