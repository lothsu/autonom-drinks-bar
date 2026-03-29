from datetime import datetime, timezone
from app.extensions import db


class Member(db.Model):
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    rfid_uid = db.Column(db.String(64), unique=True, nullable=False)
    balance_cents = db.Column(db.Integer, default=0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "rfid_uid": self.rfid_uid,
            "balance_cents": self.balance_cents,
            "balance": self.balance_cents / 100,
            "active": self.active,
        }
