"""Web Push for the ARMED / OPENED heads-up (webapi/push.py).

The last test is end to end without the internet: a local HTTP server stands in
for the browser vendor's push service, and the test decrypts what arrives using
the "device's" keys - proving the key, the VAPID signature and the payload
encryption all line up, which is the part mocks can't show.
"""
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("pywebpush", reason="Web Push is an optional dependency")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from paper_trading import engine  # noqa: E402
from storage import db as storage  # noqa: E402
from webapi import push  # noqa: E402

SUBSCRIPTION = {"endpoint": "https://push.example.test/abc",
                "keys": {"p256dh": "BPubKeyPlaceholder", "auth": "authsecret"}}
CONFIG = {"autopilot": {"profit_target_pct": 50, "stop_loss_pct": -35}}


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "test.db")
    storage.init_db(path)
    storage.ensure_account(path, 10000)
    push.init(path)
    return path


# --- keys -----------------------------------------------------------------

def test_key_is_created_once_beside_the_database(db):
    first = push.load_or_create_key(db)
    assert push.key_path(db).exists()
    assert str(push.key_path(db).parent) == os.path.dirname(db)
    again = push.load_or_create_key(db)
    assert push.public_key_b64(first) == push.public_key_b64(again)


def test_public_key_is_an_uncompressed_p256_point(db):
    encoded = push.public_key_b64(push.load_or_create_key(db))
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    assert len(raw) == 65 and raw[0] == 0x04
    assert "=" not in encoded            # browsers want unpadded base64url


# --- subscriptions --------------------------------------------------------

def test_subscriptions_upsert_by_endpoint_and_can_be_removed(db):
    push.save_subscription(db, SUBSCRIPTION, "phone")
    push.save_subscription(db, dict(SUBSCRIPTION, keys={"p256dh": "new", "auth": "x"}), "phone")
    stored = push.subscriptions(db)
    assert len(stored) == 1 and stored[0]["keys"]["p256dh"] == "new"
    push.remove_subscription(db, SUBSCRIPTION["endpoint"])
    assert push.subscriptions(db) == []


def test_subscription_validation(db):
    assert push.valid_subscription(SUBSCRIPTION)
    assert not push.valid_subscription({"endpoint": "http://insecure", "keys": SUBSCRIPTION["keys"]})
    assert not push.valid_subscription({"endpoint": SUBSCRIPTION["endpoint"], "keys": {}})


# --- sending --------------------------------------------------------------

class FakeResponse:
    def __init__(self, status):
        self.status_code = status


def test_send_prunes_gone_subscriptions_and_counts_failures(db, monkeypatch):
    for index, _status in enumerate((201, 410, 500)):
        push.save_subscription(db, dict(SUBSCRIPTION, endpoint=f"https://push.example.test/{index}"))
    outcomes = {"https://push.example.test/0": None,
                "https://push.example.test/1": 410,
                "https://push.example.test/2": 500}

    def fake_webpush(subscription, data, **kwargs):
        status = outcomes[subscription["endpoint"]]
        assert kwargs["vapid_claims"] == {"sub": "https://example.test"}
        assert kwargs["ttl"] == push.TTL_SECONDS
        if status:
            raise push.WebPushException("nope", response=FakeResponse(status))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    result = push.send(db, push.load_or_create_key(db), "https://example.test", {"title": "t"})
    assert result == {"sent": 1, "removed": 1, "failed": 1}
    remaining = {sub["endpoint"] for sub in push.subscriptions(db)}
    assert remaining == {"https://push.example.test/0", "https://push.example.test/2"}


# --- edge-triggering ------------------------------------------------------

def state(armed=(), opened=()):
    return {
        "armed": {ticker: {"ticker": ticker, "lean": "put", "confidence_pct": 61.0}
                  for ticker in armed},
        "open": {pid: {"ticker": "IWM", "option_type": "put", "strike": 228.0} for pid in opened},
    }


def test_new_events_fire_on_transitions_only():
    assert push.new_events(state(), state(), CONFIG) == []
    armed = push.new_events(state(), state(armed=["SPY"]), CONFIG)
    assert [event["title"] for event in armed] == ["SPY ARMED"]
    assert "puts now (61%)" in armed[0]["body"] and "+50% / -35%" in armed[0]["body"]
    # still armed next tick: no repeat
    assert push.new_events(state(armed=["SPY"]), state(armed=["SPY"]), CONFIG) == []
    opened = push.new_events(state(opened=[7]), state(opened=[7, 9]), CONFIG)
    assert [event["title"] for event in opened] == ["IWM OPENED"]
    assert opened[0]["tag"] == "opened-9" and opened[0]["url"] == "/#trades"


def test_watcher_baselines_first_then_pushes_new_opens(db, monkeypatch):
    sent = []
    monkeypatch.setattr(push, "send", lambda db_path, vapid, subject, message: sent.append(message))
    monkeypatch.setattr(push.payloads, "autopilot_intents", lambda db_path, config: [])
    push.save_subscription(db, SUBSCRIPTION)
    watcher = push.Watcher(db, CONFIG, vapid=None, subject="https://example.test")

    engine.buy(db, "IWM", "put", 228.0, "2026-09-23", 1.4, 1, -0.3, opened_by="auto")
    assert watcher.tick() == []            # restart mid-session: nothing re-announced
    engine.buy(db, "QQQ", "call", 721.0, "2026-09-23", 1.4, 1, 0.3, opened_by="auto")
    engine.buy(db, "SPY", "call", 640.0, "2026-09-23", 1.4, 1, 0.3)   # manual: never alerts
    events = watcher.tick()
    assert [event["title"] for event in events] == ["QQQ OPENED"]
    assert sent == events


def test_watcher_skips_sending_when_no_device_is_subscribed(db, monkeypatch):
    sent = []
    monkeypatch.setattr(push, "send", lambda *args: sent.append(args))
    monkeypatch.setattr(push.payloads, "autopilot_intents", lambda db_path, config: [])
    watcher = push.Watcher(db, CONFIG, vapid=None, subject="https://example.test")
    watcher.tick()
    engine.buy(db, "QQQ", "call", 721.0, "2026-09-23", 1.4, 1, 0.3, opened_by="auto")
    assert watcher.tick()                  # the event is detected...
    assert sent == []                      # ...but there is nobody to send it to


# --- end to end: encrypt, sign, deliver, decrypt ---------------------------

def test_push_arrives_signed_and_decryptable(db):
    import http_ece

    received = {}

    class PushService(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's naming
            received["headers"] = dict(self.headers)
            received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), PushService)
    threading.Thread(target=server.handle_request, daemon=True).start()

    # the "phone": its own P-256 key + auth secret, exactly what a browser makes
    device_key = ec.generate_private_key(ec.SECP256R1())
    device_public = device_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    push.save_subscription(db, {"endpoint": f"http://127.0.0.1:{server.server_port}/push",
                                "keys": {"p256dh": b64(device_public), "auth": b64(auth)}})

    message = {"title": "SPY ARMED", "body": "would buy puts now", "tag": "armed-SPY"}
    result = push.send(db, push.load_or_create_key(db), "https://example.test", message)
    server.server_close()

    assert result == {"sent": 1, "removed": 0, "failed": 0}
    headers = {key.lower(): value for key, value in received["headers"].items()}
    assert headers["authorization"].startswith("vapid t=")      # signed with our key
    assert headers["urgency"] == "high"
    assert headers["ttl"] == str(push.TTL_SECONDS)
    plaintext = http_ece.decrypt(received["body"], private_key=device_key,
                                 auth_secret=auth, version="aes128gcm")
    assert json.loads(plaintext) == message
