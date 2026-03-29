from flask import Blueprint

kiosk_bp = Blueprint("kiosk", __name__, template_folder="../../templates/kiosk")

from app.blueprints.kiosk import routes  # noqa: E402, F401
