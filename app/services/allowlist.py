"""
RFID allowlist service.

Polls the cloud every 5 minutes for the list of permitted RFID card UIDs.
The list is cached in memory so the kiosk can authorise cards offline
between refreshes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler


class AllowlistService:
    def __init__(self, app):
        self._app = app
        self._lock = threading.Lock()
        self._allowlist: set[str] = set()
        self._updated_at: datetime | None = None
        self._scheduler = BackgroundScheduler()

    def start(self, interval: int = 300):
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
        with self._lock:
            return rfid_hex.upper() in self._allowlist

    def status(self) -> dict:
        with self._lock:
            return {
                "count": len(self._allowlist),
                "updated_at": self._updated_at.isoformat() if self._updated_at else None,
            }

    def _fetch(self):
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
