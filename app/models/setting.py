from app.extensions import db


class Setting(db.Model):
    """Key-value store for runtime-editable config (cloud sync settings, location, etc.)."""
    __tablename__ = "settings"

    key   = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        row = cls.query.get(key)
        return row.value if row else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        row = cls.query.get(key)
        if row:
            row.value = value
        else:
            db.session.add(cls(key=key, value=value))
        db.session.commit()
