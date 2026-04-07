"""
Offsite sync service.

Picks up unsynced transactions from SQLite and pushes them to the configured
cloud provider.  The CloudProvider reads its settings lazily from the Setting
model so that changes made on the settings page take effect on the next sync
without restarting the process.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
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

    def push_transactions(self, _records):
        return []  # don't mark anything synced — data stays available for later


# ---------------------------------------------------------------------------
# Cloud provider (HTTP POST to autonom-drinks-cloud)
# ---------------------------------------------------------------------------

def _sign_request(api_key: str, bar_uid: str, body: bytes) -> dict:
    """
    Build authentication headers for a signed request.

    Signature: HMAC-SHA256(api_key, "{bar_uid}:{timestamp}:{body}")
    """
    timestamp = str(int(time.time()))
    message = f"{bar_uid}:{timestamp}:".encode() + body
    signature = hmac.new(api_key.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-Bar-UID": bar_uid,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


class CloudProvider(BaseSyncProvider):
    """
    Pushes transactions to the autonom-drinks-cloud server.
    Settings are read fresh from the Setting table on every sync so that
    web-UI changes are picked up without a restart.
    """

    def __init__(self, app):
        self._app = app

    def _settings(self) -> tuple[str, str, str]:
        """Return (api_base, api_key, bar_uid) from DB, falling back to env config.

        CLOUD_URL should be the full API base, e.g. https://host/casino/api/v1
        """
        from app.models.setting import Setting
        cfg = self._app.config
        url     = Setting.get("CLOUD_URL")     or cfg.get("CLOUD_URL", "")
        api_key = Setting.get("CLOUD_API_KEY") or cfg.get("CLOUD_API_KEY", "")
        bar_uid = Setting.get("BAR_UID")       or cfg.get("BAR_UID", "")
        return url.rstrip("/"), api_key, bar_uid

    def _check_url(self, url: str) -> str | None:
        """Return an error string if the URL is unusable, else None."""
        if not url:
            return "Cloud URL not configured"
        is_localhost = url.startswith("http://localhost") or url.startswith("http://127.0.0.1")
        if not is_localhost and (self._app.config.get("ENV") == "production" or not self._app.debug):
            if not url.startswith("https://"):
                return f"CLOUD_URL must use HTTPS in production (got: {url!r})"
        return None

    def send_heartbeat(self):
        try:
            import requests as _requests
        except ImportError:
            return
        url, api_key, bar_uid = self._settings()
        err = self._check_url(url)
        if err or not api_key or not bar_uid:
            return
        try:
            body = json.dumps({}).encode()
            headers = {"Content-Type": "application/json", **_sign_request(api_key, bar_uid, body)}
            _requests.post(
                f"{url}/heartbeat",
                data=body,
                headers=headers,
                timeout=5,
            )
        except Exception:
            pass  # heartbeat is best-effort

    def push_transactions(self, records: list[dict]) -> list[int]:
        try:
            import requests as _requests
        except ImportError:
            print("[Sync] 'requests' library not installed — cannot sync to cloud.")
            return []

        url, api_key, bar_uid = self._settings()

        url_err = self._check_url(url)
        if url_err:
            print(f"[Sync] {url_err} — skipping.")
            return []
        if not api_key:
            print("[Sync] API key not configured — skipping.")
            return []
        if not bar_uid:
            print("[Sync] BAR_UID not configured — skipping.")
            return []

        payload = {"transactions": records}
        body = json.dumps(payload).encode()
        auth_headers = _sign_request(api_key, bar_uid, body)
        headers = {"Content-Type": "application/json", **auth_headers}

        print(f"[Sync] POST {url}/sync  bar_uid={bar_uid!r}  records={len(records)}  ts={auth_headers['X-Timestamp']}")
        print(f"[Sync] payload: {body.decode()[:500]}")

        try:
            resp = _requests.post(
                f"{url}/sync",
                data=body,
                headers=headers,
                timeout=15,
            )
        except Exception as exc:
            print(f"[Sync] Cloud request failed: {exc}")
            return []

        print(f"[Sync] Response HTTP {resp.status_code}: {resp.text[:500]}")

        if resp.status_code != 200:
            return []

        try:
            data = resp.json()
        except Exception as exc:
            print(f"[Sync] Failed to parse cloud response as JSON: {exc} — body: {resp.text[:200]!r}")
            return []
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
                if isinstance(self._provider, CloudProvider):
                    self._provider.send_heartbeat()
                self._sync_with_result(self._provider)

    def _sync_with_result(self, provider: BaseSyncProvider) -> dict:
        """Run a sync and return {"ok": bool, "sent": int, "accepted": int, "msg": str}."""
        from app.extensions import db
        from app.models.transaction import Transaction

        all_unsynced = Transaction.query.filter_by(synced=False).all()
        # Hold back transactions still within the edit window
        unsynced = [t for t in all_unsynced if not t.is_editable()]
        if not unsynced:
            return {"ok": True, "sent": 0, "accepted": 0, "msg": "Nichts zu synchronisieren."}

        def _prepare(t):
            d = t.to_dict()
            # Convert hex RFID (e.g. "126C2B2D") to decimal string ("309078829")
            # to match the format used in member_rfids on the cloud side.
            if d.get("rfid_uid"):
                try:
                    d["rfid_uid"] = str(int(d["rfid_uid"], 16))
                except (ValueError, TypeError):
                    pass
            return d

        records = [_prepare(t) for t in unsynced]
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
            n, total = len(synced_ids), len(records)
            msg = f"{n} von {total} {'Buchung' if n == 1 else 'Buchungen'} übertragen."
            print(f"[Sync] {msg}")
            return {"ok": True, "sent": len(records), "accepted": len(synced_ids), "msg": msg}
        else:
            msg = "Cloud nicht erreichbar oder Authentifizierung fehlgeschlagen."
            print(f"[Sync] Push failed — {msg}")
            return {"ok": False, "sent": len(records), "accepted": 0, "msg": msg}
