"""Web Push for the autopilot heads-up: ARMED / OPENED - and the worker going
down or coming back - reach the phone even when the app is closed.

The in-page alert only runs while the page's JavaScript does, and a locked or
backgrounded phone suspends it within seconds. So the server watches for the
same two transitions itself - a ticker becoming armed, an auto position
appearing - and hands each one to the browser vendor's push service, which
wakes the installed app's service worker to show it.

Self-contained on purpose: this module owns its one table and its key file,
nothing in storage/ or worker.py knows it exists, and without `pywebpush`
installed everything here degrades to "unavailable" while the in-page alerts
carry on.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from storage import db as storage
from webapi import payloads
from worker import _in_premarket_window, is_market_open

try:
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush
    AVAILABLE = True
except ImportError:  # optional dependency
    AVAILABLE = False

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY,          -- the push service URL; unique per device+browser
    subscription_json TEXT NOT NULL,    -- the full PushSubscription (endpoint + keys)
    user_agent TEXT,
    created_at TEXT NOT NULL,
    origin TEXT                         -- the app's https origin the device subscribed from
)
"""

# The VAPID "sub" claim names the sender for the push service's operators. The
# signing library (py_vapid) only accepts `mailto:` or a BARE `https://host` -
# any path fails its check, which is what broke the first real send. So the
# sender is the app's own origin (e.g. https://your-pc.tailXXXX.ts.net), taken
# from the subscribing request: no personal data, and the push service already
# knows that origin from the subscription itself. ZERODTE_PUSH_SUBJECT
# overrides it; FALLBACK_SUBJECT covers a subscription with no origin on record.
FALLBACK_SUBJECT = "https://github.com"
_BARE_HTTPS_ORIGIN = re.compile(r"^https://[\w-]+(\.[\w-]+)+$", re.IGNORECASE)

# An ARMED heads-up is stale within minutes: if the phone is offline longer
# than this, the push service drops it rather than delivering it late.
TTL_SECONDS = 600

# After the watcher starts, how long a stale heartbeat reads as "starting"
# rather than "down" - at least the UI's own 5-minute staleness rule, so a
# worker restarting alongside the API is never reported dead.
WORKER_GRACE_SECONDS = 300
_clock = time.monotonic


def init(db_path: str) -> None:
    with storage.connect(db_path) as conn:
        conn.execute(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(push_subscriptions)")}
        if "origin" not in columns:   # tables created before the origin column existed
            conn.execute("ALTER TABLE push_subscriptions ADD COLUMN origin TEXT")


def normalize_origin(origin: str | None) -> str | None:
    """The origin if it can serve as the VAPID subject (bare https host), else
    None - plain-http and localhost origins can't subscribe to push anyway."""
    origin = (origin or "").strip().rstrip("/")
    return origin if _BARE_HTTPS_ORIGIN.match(origin) else None


def subject_for(origin: str | None, override: str | None = None) -> str:
    return override or normalize_origin(origin) or FALLBACK_SUBJECT


# --- VAPID keys -----------------------------------------------------------

def key_path(db_path: str) -> Path:
    """The private key lives beside the database (gitignored, like the DB).
    Losing it only means every device has to re-enable notifications."""
    return Path(db_path).with_name("vapid_private.pem")


def load_or_create_key(db_path: str) -> "Vapid02":
    path = key_path(db_path)
    if path.exists():
        return Vapid02.from_file(str(path))
    vapid = Vapid02()
    vapid.generate_keys()
    vapid.save_key(str(path))
    log.info("generated a new VAPID key at %s", path)
    return vapid


def public_key_b64(vapid: "Vapid02") -> str:
    """The applicationServerKey the browser subscribes with: the raw
    uncompressed P-256 point, base64url without padding."""
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- subscriptions --------------------------------------------------------

def valid_subscription(subscription: dict) -> bool:
    keys = subscription.get("keys") or {}
    endpoint = subscription.get("endpoint") or ""
    return endpoint.startswith("https://") and bool(keys.get("p256dh")) and bool(keys.get("auth"))


def save_subscription(db_path: str, subscription: dict, user_agent: str | None = None,
                      origin: str | None = None) -> None:
    """Upsert by endpoint. The app re-posts its subscription on every open, so
    this also refreshes the origin of rows saved before it was recorded."""
    with storage.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, subscription_json, user_agent, created_at, origin)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json,
                   user_agent = excluded.user_agent,
                   origin = COALESCE(excluded.origin, push_subscriptions.origin)""",
            (subscription["endpoint"], json.dumps(subscription), user_agent,
             datetime.now(timezone.utc).isoformat(), normalize_origin(origin)),
        )


def remove_subscription(db_path: str, endpoint: str) -> None:
    with storage.connect(db_path) as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def subscriptions(db_path: str) -> list[dict]:
    return [subscription for subscription, _origin in _subscription_rows(db_path)]


def _subscription_rows(db_path: str) -> list[tuple[dict, str | None]]:
    with storage.connect(db_path) as conn:
        rows = conn.execute("SELECT subscription_json, origin FROM push_subscriptions").fetchall()
    return [(json.loads(row["subscription_json"]), row["origin"]) for row in rows]


def send(db_path: str, vapid: "Vapid02", subject_override: str | None, message: dict) -> dict:
    """Push one message to every subscribed device. A 404/410 from the push
    service means that subscription is gone for good (app uninstalled,
    permission revoked), so it is pruned instead of retried forever. Other
    failures are returned in `errors`, since the scheduled task keeps no log."""
    result = {"sent": 0, "removed": 0, "failed": 0, "errors": []}
    for subscription, origin in _subscription_rows(db_path):
        endpoint = subscription["endpoint"]
        try:
            webpush(subscription, json.dumps(message), vapid_private_key=vapid,
                    # a fresh dict per send: webpush mutates the claims it's given
                    vapid_claims={"sub": subject_for(origin, subject_override)},
                    ttl=TTL_SECONDS, headers={"Urgency": "high"}, timeout=10)
            result["sent"] += 1
        except WebPushException as error:
            status = getattr(error.response, "status_code", None)
            if status in (404, 410):
                remove_subscription(db_path, endpoint)
                result["removed"] += 1
                continue
            body = getattr(error.response, "text", "") or ""
            detail = f"{status}: {body.strip()[:200]}" if status else str(error)
            result["failed"] += 1
            result["errors"].append(f"{endpoint[:40]}… {detail}")
            log.warning("push to %s failed: %s", endpoint[:60], detail)
        except Exception as error:  # noqa: BLE001 - a network blip must not kill the watcher
            result["failed"] += 1
            result["errors"].append(f"{endpoint[:40]}… {type(error).__name__}: {error}")
            log.warning("push to %s failed", endpoint[:60], exc_info=True)
    return result


# --- what to alert on -----------------------------------------------------

def in_watch_hours(config: dict, now: datetime | None = None) -> bool:
    """When a dead worker is worth waking a phone for: trading days from the
    pre-market setup window to the close. Nights and weekends stay quiet - a PC
    asleep or rebooting then shouldn't buzz anyone."""
    return is_market_open(config, now) or _in_premarket_window(config, now)


def worker_state(db_path: str, config: dict, now: datetime | None = None) -> tuple[str, float | None]:
    """('up' | 'down' | 'idle', heartbeat age in seconds). 'down' means the
    heartbeat is stale by the same rule as the UI's worker banner, during watch
    hours; outside them it's 'idle' whatever the heartbeat says."""
    health = payloads.worker_health(db_path)
    age = health.get("age_seconds")
    if not in_watch_hours(config, now):
        return "idle", age
    return ("up" if health["status"] == "up" else "down"), age


def alert_state(db_path: str, config: dict) -> dict:
    """What the heads-up edge-triggers on: which tickers the bot would buy right
    now, which auto positions are open, and whether the worker is alive."""
    armed = {intent["ticker"]: intent
             for intent in payloads.autopilot_intents(db_path, config)
             if intent["would_enter"]}
    opened = {row["id"]: {"ticker": row["ticker"], "option_type": row["option_type"],
                          "strike": row["strike"]}
              for row in storage.get_open_positions(db_path) if row["opened_by"] == "auto"}
    worker, age = worker_state(db_path, config)
    return {"armed": armed, "open": opened, "worker": worker, "worker_age_seconds": age}


def new_events(previous: dict, current: dict, config: dict) -> list[dict]:
    """Transitions INTO armed and newly opened auto positions only - a ticker
    that stays armed alerts once, not every tick. Wording matches the in-page
    alert so the two read the same."""
    ap_cfg = config.get("autopilot", {})
    target = ap_cfg.get("profit_target_pct", 50)
    stop = ap_cfg.get("stop_loss_pct", -35)
    events = []
    for ticker, intent in current["armed"].items():
        if ticker not in previous["armed"]:
            lean = intent["lean"]
            confidence = intent.get("confidence_pct") or 0.0
            events.append({
                "title": f"{ticker} ARMED",
                "body": f"would buy {lean}s now ({confidence:.0f}%) — mirror ATM 0DTE {lean}, "
                        f"+{target:.0f}% / {stop:.0f}%.",
                "tag": f"armed-{ticker}",
                "url": "/#autopilot",
            })
    for position_id, row in current["open"].items():
        if position_id not in previous["open"]:
            events.append({
                "title": f"{row['ticker']} OPENED",
                "body": f"{row['option_type']} {row['strike']:g} just opened — copy now.",
                "tag": f"opened-{position_id}",
                "url": "/#trades",
            })

    # worker liveness: alert on going down, and on coming back from down only -
    # not on down -> idle (the session ending isn't a recovery)
    was, now = previous.get("worker"), current.get("worker")
    age = current.get("worker_age_seconds")
    if now == "down" and was is not None and was != "down":
        stale = f"no heartbeat for {age / 60:.0f} min" if age is not None else "it has never run"
        events.append({
            "title": "Worker DOWN",
            "body": f"{stale} - no signals, no autopilot, no exits. Restart the 0DTE-Worker task.",
            "tag": "worker",
            "url": "/#signals",
        })
    elif was == "down" and now == "up":
        events.append({
            "title": "Worker back",
            "body": "Heartbeat resumed - polling and the autopilot are running again.",
            "tag": "worker",
            "url": "/#signals",
        })
    return events


class Watcher:
    """Called on a timer by api.py. The first tick only takes a baseline, so a
    restart mid-session doesn't re-announce everything already armed/open.

    For the first WORKER_GRACE_SECONDS a stale heartbeat reads as 'starting',
    not 'down': when both tasks restart together the worker hasn't beaten yet.
    A worker still dead after that alerts - including right after a reboot."""

    def __init__(self, db_path: str, config: dict, vapid: "Vapid02",
                 subject_override: str | None = None):
        self.db_path = db_path
        self.config = config
        self.vapid = vapid
        self.subject_override = subject_override
        self.previous: dict | None = None
        self.started = _clock()

    def tick(self) -> list[dict]:
        current = alert_state(self.db_path, self.config)
        if current.get("worker") == "down" and _clock() - self.started < WORKER_GRACE_SECONDS:
            current["worker"] = "starting"
        events = [] if self.previous is None else new_events(self.previous, current, self.config)
        self.previous = current
        if events and subscriptions(self.db_path):
            for event in events:
                send(self.db_path, self.vapid, self.subject_override, event)
        return events
