import json
from datetime import datetime, timezone
from app.extensions import db


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    rfid_uid = db.Column(db.String(16), nullable=False)
    total_cents = db.Column(db.Integer, nullable=False)
    items_json = db.Column(db.Text, nullable=False)  # JSON list of {drink_id, name, qty, price_cents}
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    synced = db.Column(db.Boolean, default=False, nullable=False)

    @property
    def items(self):
        return json.loads(self.items_json)

    @items.setter
    def items(self, value):
        self.items_json = json.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "rfid_uid": self.rfid_uid,
            "total_cents": self.total_cents,
            "total": self.total_cents / 100,
            "items": self.items,
            "created_at": self.created_at.isoformat(),
            "synced": self.synced,
        }
