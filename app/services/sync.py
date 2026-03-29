"""
Offsite sync service.

Picks up unsynced transactions from SQLite and pushes them to the configured
cloud provider.  The CloudProvider reads its settings lazily from the Setting
model so that changes made on the settings page take effect on the next sync
without restarting the process.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class BaseSyncProvider(ABC):
    @abstractmethod
    def push_transactions(self, records: list[dict]) -> list[int]:
        """
        Push a list of transaction dicts.
        Return a list of bar_tx_ids that the remote accepted or already had
        (i.e. can be marked synced locally).  Return [] on failure.
        """


class NullProvider(BaseSyncProvider):
    """No-op provider — offsite sync disabled. Nothing is pushed, nothing is marked synced."""

    def push_transactions(self, records):
        return []  # don't mark anything synced — data stays available for later


# ---------------------------------------------------------------------------
# Cloud provider (HTTP POST to autonom-drinks-cloud)
# ---------------------------------------------------------------------------

class CloudProvider(BaseSyncProvider):
    """
    Pushes transactions to the autonom-drinks-cloud server.
    Settings are read fresh from the Setting table on every sync so that
    web-UI changes are picked up without a restart.
    """

    def __init__(self, app):
        self._app = app

    def _settings(self) -> tuple[str, str, str]:
        """Return (cloud_url, api_key, location) from DB, falling back to env config."""
        from app.models.setting import Setting
        cfg = self._app.config
        url      = Setting.get("CLOUD_URL")      or cfg.get("CLOUD_URL", "")
        api_key  = Setting.get("CLOUD_API_KEY")  or cfg.get("CLOUD_API_KEY", "")
        location = Setting.get("BAR_LOCATION")   or cfg.get("BAR_LOCATION", "Bar")
        return url.rstrip("/"), api_key, location

    def push_transactions(self, records: list[dict]) -> list[int]:
        try:
            import requests as _requests
        except ImportError:
            print("[Sync] 'requests' library not installed — cannot sync to cloud.")
            return []

        url, api_key, location = self._settings()
        if not url or not api_key:
            print("[Sync] Cloud URL or API key not configured — skipping.")
            return []

        try:
            resp = _requests.post(
                f"{url}/api/v1/sync",
                json={"bar_location": location, "transactions": records},
                headers={"X-Api-Key": api_key},
                timeout=15,
            )
        except Exception as exc:
            print(f"[Sync] Cloud request failed: {exc}")
            return []

        if resp.status_code != 200:
            print(f"[Sync] Cloud returned HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        synced_ids = data.get("accepted", []) + data.get("duplicate", [])
        print(
            f"[Sync] Cloud: +{len(data.get('accepted', []))} new, "
            f"{len(data.get('duplicate', []))} dup"
        )
        return synced_ids


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _build_provider(name: str, app) -> BaseSyncProvider:
    if name in ("none", ""):
        return NullProvider()
    if name == "cloud":
        return CloudProvider(app)
    raise ValueError(f"Unknown sync provider: {name!r}")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SyncService:
    def __init__(self, app):
        self._app = app
        self._provider: BaseSyncProvider = _build_provider(
            app.config.get("SYNC_PROVIDER", "none"), app
        )
        self._scheduler = BackgroundScheduler()
        self._lock = threading.Lock()
        self._last_sync: datetime | None = None
        self._last_count: int = 0

    def start(self, interval: int = 300):
        if isinstance(self._provider, NullProvider):
            return
        self._scheduler.add_job(
            self._run_sync,
            trigger="interval",
            seconds=interval,
            id="offsite-sync",
        )
        self._scheduler.start()

    def stop(self):
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def run_now(self):
        """Fire-and-forget background sync (used by scheduler)."""
        threading.Thread(target=self._run_sync_cloud, daemon=True).start()

    def run_now_sync(self) -> dict:
        """Synchronous manual sync — blocks until done, returns a result dict.
        Used by the settings page so the UI can show the actual outcome.
        Always uses CloudProvider regardless of SYNC_PROVIDER setting.
        """
        with self._lock:
            return self._sync_with_result(CloudProvider(self._app))

    def _run_sync_cloud(self):
        with self._lock:
            with self._app.app_context():
                self._sync_with_result(CloudProvider(self._app))

    def status(self) -> dict:
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "last_count": self._last_count,
        }

    # ------------------------------------------------------------------

    def _run_sync(self):
        with self._lock:
            with self._app.app_context():
                self._sync_with_result(self._provider)

    def _sync_with_result(self, provider: BaseSyncProvider) -> dict:
        """Run a sync and return {"ok": bool, "sent": int, "accepted": int, "msg": str}."""
        from app.extensions import db
        from app.models.transaction import Transaction

        unsynced = Transaction.query.filter_by(synced=False).all()
        if not unsynced:
            return {"ok": True, "sent": 0, "accepted": 0, "msg": "Nichts zu synchronisieren."}

        records = [t.to_dict() for t in unsynced]
        try:
            synced_ids = provider.push_transactions(records)
        except Exception as exc:
            msg = f"Fehler: {exc}"
            print(f"[Sync] {msg}")
            return {"ok": False, "sent": len(records), "accepted": 0, "msg": msg}

        if synced_ids:
            id_set = set(synced_ids)
            for t in unsynced:
                if t.id in id_set:
                    t.synced = True
            db.session.commit()
            self._last_sync  = datetime.now(timezone.utc)
            self._last_count = len(synced_ids)
            msg = f"{len(synced_ids)} von {len(records)} Buchungen übertragen."
            print(f"[Sync] {msg}")
            return {"ok": True, "sent": len(records), "accepted": len(synced_ids), "msg": msg}
        else:
            msg = "Cloud nicht erreichbar oder API-Key falsch."
            print(f"[Sync] Push failed — {msg}")
            return {"ok": False, "sent": len(records), "accepted": 0, "msg": msg}
