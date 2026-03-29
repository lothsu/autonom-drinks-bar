from flask import render_template
from app.blueprints.kiosk import kiosk_bp
from app.models.drink import Drink


@kiosk_bp.get("/")
def index():
    drinks = (
        Drink.query.filter_by(available=True)
        .order_by(Drink.position)
        .all()
    )
    return render_template("kiosk/index.html", drinks=drinks)
