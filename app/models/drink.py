from datetime import datetime, timezone
from app.extensions import db


class Drink(db.Model):
    __tablename__ = "drinks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)  # stored in cents
    available = db.Column(db.Boolean, default=True, nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "price_cents": self.price_cents,
            "price": self.price_cents / 100,
            "available": self.available,
            "position": self.position,
        }
