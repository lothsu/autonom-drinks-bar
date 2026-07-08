"""
RFID allowlist service.

By default, RFID authorisation is based on the local `Member` table (e.g.
populated via the Excel import on the admin page). If the "RFID_ALLOWLIST_CLOUD"
setting is enabled, the service instead polls the cloud every 5 minutes for the
list of permitted RFID card UIDs and caches it in memory, so the kiosk can
authorise cards offline between refreshes — same logic as before this setting
existed.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler


def _cloud_mode_enabled(app) -> bool:
    with app.app_context():
        from app.models.setting import Setting
        return Setting.get("RFID_ALLOWLIST_CLOUD", "0") == "1"


class AllowlistService:
    def __init__(self, app):
        self._app = app
        self._lock = threading.Lock()
        self._allowlist: set[str] = set()
        self._updated_at: datetime | None = None
        self._scheduler = BackgroundScheduler()

    def start(self, interval: int = 300):
        self._interval = interval
        if _cloud_mode_enabled(self._app):
            self._fetch()  # populate immediately on startup
        self._scheduler.add_job(
            self._fetch,
            trigger="interval",
            seconds=interval,
            id="rfid-allowlist",
        )
        self._scheduler.start()

    def stop(self):
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def is_allowed(self, rfid_hex: str) -> bool:
        if not _cloud_mode_enabled(self._app):
            with self._app.app_context():
                from app.models.member import Member
                return (
                    Member.query.filter_by(rfid_uid=rfid_hex.upper(), active=True).first()
                    is not None
                )
        with self._lock:
            return rfid_hex.upper() in self._allowlist

    def status(self) -> dict:
        with self._lock:
            return {
                "cloud_mode": _cloud_mode_enabled(self._app),
                "count": len(self._allowlist),
                "updated_at": self._updated_at.isoformat() if self._updated_at else None,
            }

    def _fetch(self):
        if not _cloud_mode_enabled(self._app):
            return
        from app.services.sync import CloudProvider
        with self._app.app_context():
            provider = CloudProvider(self._app)
            result = provider.fetch_rfid_allowlist()
        if result is None:
            return
        with self._lock:
            self._allowlist = {uid.upper() for uid in result}
            self._updated_at = datetime.now(timezone.utc)
        print(f"[Allowlist] Updated: {len(self._allowlist)} cards")
