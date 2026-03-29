from flask import Flask
from dotenv import load_dotenv

from config import config
from app.extensions import db
from app.services.rfid import RFIDService
from app.services.sync import SyncService

load_dotenv()

rfid_service: RFIDService | None = None
sync_service: SyncService | None = None


def create_app(env: str = "default") -> Flask:
    global rfid_service, sync_service

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config[env])

    # ensure instance folder exists
    import os
    os.makedirs(app.instance_path, exist_ok=True)

    # extensions
    db.init_app(app)

    # blueprints
    from app.blueprints.kiosk import kiosk_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.api import api_bp

    app.register_blueprint(kiosk_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.template_filter("uid_fmt")
    def uid_fmt(uid: str) -> str:
        try:
            return str(int(uid, 16))
        except (ValueError, TypeError):
            return uid or ""

    with app.app_context():
        # Import all models so db.create_all() sees them
        from app.models import drink, member, transaction, setting  # noqa: F401
        db.create_all()
        _migrate_db()
        _seed_drinks_if_empty()

    # Start background services only in the actual worker process.
    # Flask's debug reloader spawns a parent + child process; both call
    # create_app(), but only the child (WERKZEUG_RUN_MAIN=true) should open
    # hardware ports — otherwise COM4 / ttyUSB0 gets claimed twice.
    import os
    _is_reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    if not _is_reloader_parent:
        rfid_service = RFIDService(mock=app.config["RFID_MOCK"], port=app.config["RFID_PORT"])
        rfid_service.start()

        sync_service = SyncService(app)
        sync_service.start(interval=app.config["SYNC_INTERVAL_SECONDS"])

    return app


def _migrate_db():
    """Apply schema migrations that db.create_all() cannot handle on existing tables."""
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if "transactions" not in inspector.get_table_names():
        return  # fresh DB — create_all already created the correct schema

    col_names = [c["name"] for c in inspector.get_columns("transactions")]

    if "member_id" not in col_names and "rfid_uid" in col_names:
        return  # already on the latest schema

    # Recreate the transactions table: add rfid_uid, drop member_id
    with db.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE transactions_new (
                id          INTEGER PRIMARY KEY,
                rfid_uid    TEXT NOT NULL DEFAULT '',
                total_cents INTEGER NOT NULL,
                items_json  TEXT NOT NULL,
                created_at  DATETIME,
                synced      BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        # Carry over rfid_uid if it already existed, otherwise fall back to empty string
        if "rfid_uid" in col_names:
            conn.execute(text("""
                INSERT INTO transactions_new (id, rfid_uid, total_cents, items_json, created_at, synced)
                SELECT id, COALESCE(rfid_uid, ''), total_cents, items_json, created_at, synced FROM transactions
            """))
        else:
            conn.execute(text("""
                INSERT INTO transactions_new (id, total_cents, items_json, created_at, synced)
                SELECT id, total_cents, items_json, created_at, synced FROM transactions
            """))
        conn.execute(text("DROP TABLE transactions"))
        conn.execute(text("ALTER TABLE transactions_new RENAME TO transactions"))
    print("[DB] Migrated transactions table (rfid_uid only, member_id removed)")


def _seed_drinks_if_empty():
    from app.models.drink import Drink

    if Drink.query.count() == 0:
        defaults = [
            Drink(name="Beer", price_cents=250, position=0),
            Drink(name="Wine", price_cents=300, position=1),
            Drink(name="Water", price_cents=100, position=2),
            Drink(name="Soft Drink", price_cents=200, position=3),
            Drink(name="Coffee", price_cents=150, position=4),
        ]
        db.session.add_all(defaults)
        db.session.commit()
