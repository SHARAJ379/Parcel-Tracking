"""Screenshot every page at phone and desktop widths, in light and dark mode.

Runs the real FastAPI app with in-memory fake data, so it never calls
Supabase or Gemini. The admin username and password are random values made for this run.
Uses the Google Chrome already installed on this computer.

    python scripts/ui_screenshots.py OUTPUT_FOLDER
"""

import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

# Set throwaway values before the app loads; load_dotenv never overrides them.
ADMIN_USERNAME = "preview-admin"
ADMIN_PASSWORD = secrets.token_urlsafe(16)
os.environ["ADMIN_USERNAME"] = ADMIN_USERNAME
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ["SESSION_SECRET"] = secrets.token_urlsafe(32)
os.environ["GEMINI_MODEL"] = "preview-model"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app import app, get_gemini_client, get_supabase_client  # noqa: E402

NOW = datetime.now(timezone.utc)
OWNER = "20000000-0000-4000-8000-000000000001"


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


def ahead(**delta) -> str:
    return (NOW + timedelta(**delta)).isoformat()


def parcel(pid, tracking_id, status, eta, destination, updated):
    return {"id": pid, "owner_id": OWNER, "tracking_id": tracking_id, "status": status,
            "eta": eta, "destination": destination, "created_at": ago(days=3), "updated_at": updated}


PARCELS = [
    parcel("p1", "DEMO-PARCEL-001", "in_transit", ahead(days=1), "Pune, Maharashtra", ago(hours=6)),
    parcel("p2", "DEMO-PARCEL-002", "out_for_delivery", ahead(hours=3), "Bengaluru, Karnataka", ago(hours=1)),
    parcel("p3", "DEMO-PARCEL-003", "delivered", ago(days=1), "Mumbai, Maharashtra", ago(days=1)),
    parcel("p4", "DEMO-DELAYED-004", "delayed", ago(hours=5), "Mysuru, Karnataka", ago(hours=2)),
    parcel("p5", "DEMO-FAILED-005", "failed_delivery", ago(hours=4), "Hubballi, Karnataka", ago(hours=3)),
]


def event(status, description, location, when):
    return {"status": status, "description": description, "location": location, "occurred_at": when}


EVENTS = {
    "p1": [event("created", "Shipment information received.", "Mumbai, Maharashtra", ago(days=2)),
           event("picked_up", "Parcel picked up from the sender.", "Mumbai, Maharashtra", ago(days=1, hours=18)),
           event("in_transit", "Parcel is moving through the delivery network.", "Nashik, Maharashtra", ago(hours=6))],
    "p2": [event("created", "Shipment information received.", "Chennai, Tamil Nadu", ago(days=1)),
           event("picked_up", "Parcel picked up from the sender.", "Chennai, Tamil Nadu", ago(hours=20)),
           event("in_transit", "Parcel arrived at the local delivery facility.", "Bengaluru, Karnataka", ago(hours=8)),
           event("out_for_delivery", "Parcel is with the local courier for delivery.", "Bengaluru, Karnataka", ago(hours=1))],
    "p3": [event("created", "Shipment information received.", "Hyderabad, Telangana", ago(days=3)),
           event("picked_up", "Parcel picked up from the sender.", "Hyderabad, Telangana", ago(days=2, hours=18)),
           event("in_transit", "Parcel arrived at the local delivery facility.", "Mumbai, Maharashtra", ago(days=1, hours=6)),
           event("out_for_delivery", "Parcel is with the local courier for delivery.", "Mumbai, Maharashtra", ago(days=1, hours=3)),
           event("delivered", "Parcel delivered to the recipient.", "Mumbai, Maharashtra", ago(days=1))],
    "p4": [event("created", "Shipment information received.", "Bengaluru, Karnataka", ago(days=2)),
           event("picked_up", "Parcel picked up from the sender.", "Bengaluru, Karnataka", ago(days=1, hours=20)),
           event("in_transit", "Parcel left the sorting hub.", "Mandya, Karnataka", ago(hours=12)),
           event("delayed", "Delayed by heavy rain on the route.", "Mandya, Karnataka", ago(hours=2))],
    "p5": [event("created", "Shipment information received.", "Belagavi, Karnataka", ago(days=2)),
           event("picked_up", "Parcel picked up from the sender.", "Belagavi, Karnataka", ago(days=1, hours=22)),
           event("in_transit", "Parcel arrived at the local delivery facility.", "Hubballi, Karnataka", ago(hours=9)),
           event("out_for_delivery", "Parcel is with the local courier for delivery.", "Hubballi, Karnataka", ago(hours=6)),
           event("failed_delivery", "Recipient not available. Another attempt will be made.", "Hubballi, Karnataka", ago(hours=3))],
}

TABLES = {
    "api_keys": [
        {"id": "k1", "name": "Storefront integration", "key_prefix": "Xk29fLq0", "scopes": ["read"],
         "created_at": ago(days=2), "last_used_at": ago(minutes=40), "revoked_at": None},
        {"id": "k2", "name": "Warehouse scanner", "key_prefix": "p8RtZm1c", "scopes": ["read", "write"],
         "created_at": ago(days=3), "last_used_at": None, "revoked_at": ago(days=1)},
    ],
    "webhooks": [
        {"id": "w1", "url": "https://hooks.example.com/parcels/status", "events": ["parcel.status_updated"],
         "active": True, "created_at": ago(days=1)},
    ],
    "webhook_deliveries": [
        {"id": "d1", "webhook_id": "w1", "parcel_id": "p1", "event_type": "parcel.status_updated", "attempt_number": 1,
         "response_status": 200, "response_body": "received", "error": None, "attempted_at": ago(hours=6)},
        {"id": "d2", "webhook_id": "w1", "parcel_id": "p4", "event_type": "parcel.status_updated", "attempt_number": 1,
         "response_status": 503, "response_body": "", "error": "Webhook endpoint returned HTTP 503.", "attempted_at": ago(hours=2)},
    ],
}


class Query:
    """Just enough of the Supabase query builder for the pages."""

    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.values = db, name, {}, "select", None

    def select(self, *_):
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def insert(self, values):
        self.op, self.values = "insert", values
        return self

    def eq(self, field, value):
        self.filters[field] = [value]
        return self

    def in_(self, field, values):
        self.filters[field] = list(values)
        return self

    def is_(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        if self.name == "parcels":
            ids = self.filters.get("tracking_id")
            rows = [p for p in PARCELS if ids is None or p["tracking_id"] in ids]
            return SimpleNamespace(data=rows)
        if self.name == "parcel_events":
            return SimpleNamespace(data=EVENTS.get(self.filters["parcel_id"][0], []))
        if self.name == "api_keys" and self.op == "insert":
            return SimpleNamespace(data=[{**self.values, "id": "k3"}])
        return SimpleNamespace(data=TABLES.get(self.name, []))


class FakeDB:
    rate_limit_allowed = True

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        allowed = self.rate_limit_allowed
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[{"allowed": allowed, "request_count": 1}]))


class FakeModels:
    """Answers from the records it is given, like the real prompt asks."""

    delay = 0.0

    def generate_content(self, *, contents, **_):
        time.sleep(self.delay)
        prompt = json.loads(contents)
        records = prompt["parcel_records"]
        question = prompt["untrusted_question"]
        if records is None:
            return SimpleNamespace(text="Please send your tracking ID, for example DEMO-PARCEL-001.")
        parcel_info, latest = records["parcel"], records["timeline"][-1]
        if any("ऀ" <= ch <= "ॿ" for ch in question):
            return SimpleNamespace(text=f"आपका पार्सल {parcel_info['tracking_id']} अभी {latest['location']} में है। अनुमानित डिलीवरी {parcel_info['eta']} तक है।")
        if "arrive" in question.lower():
            return SimpleNamespace(text=f"It is expected by {parcel_info['eta']}.")
        status = parcel_info["status"].replace("_", " ")
        return SimpleNamespace(text=f"{parcel_info['tracking_id']} is {status}. The latest update was at {latest['location']} on {latest['occurred_at']}: {latest['description']}")


DB = FakeDB()
MODELS = FakeModels()
app.dependency_overrides[get_supabase_client] = lambda: DB
app.dependency_overrides[get_gemini_client] = lambda: SimpleNamespace(models=MODELS)

PORT = 8012
BASE = f"http://127.0.0.1:{PORT}"
SIZES = {"phone": (360, 780), "desktop": (1440, 900)}


def check_overflow(page, label, problems):
    width = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if width > 0:
        problems.append(f"{label}: page scrolls sideways by {width}px")


def run(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.1)

    problems = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome")
            for theme in ("light", "dark"):
                for size_name, (width, height) in SIZES.items():
                    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
                    page = context.new_page()
                    page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))

                    def shot(name, full_page=True):
                        label = f"{theme}-{size_name}-{name}"
                        check_overflow(page, label, problems)
                        page.screenshot(path=out / f"{label}.png", full_page=full_page)

                    page.goto(BASE + "/")
                    page.wait_for_load_state("networkidle")
                    shot("home-empty")

                    # A conversation: ID in the message, a follow-up, then Hindi.
                    MODELS.delay = 1.5
                    page.fill("#assistant-question", "Where is demo-parcel-001?")
                    page.keyboard.press("Enter")
                    page.wait_for_selector(".msg-typing")
                    shot("chat-typing", full_page=False)
                    page.wait_for_selector(".msg-typing", state="detached")
                    MODELS.delay = 0
                    for question in ("When will it arrive?", "मेरा पार्सल अभी कहाँ है?"):
                        page.fill("#assistant-question", question)
                        page.keyboard.press("Enter")
                        page.wait_for_selector(".msg-typing", state="detached")
                    page.wait_for_timeout(900)
                    shot("chat-conversation", full_page=False)
                    shot("chat-conversation-full")

                    for tracking_id, name in (("DEMO-PARCEL-003", "delivered"),
                                              ("DEMO-DELAYED-004", "delayed"),
                                              ("DEMO-FAILED-005", "failed"),
                                              ("NOPE-404", "not-found")):
                        page.goto(f"{BASE}/?tracking_id={tracking_id}")
                        page.wait_for_timeout(900)
                        shot(f"parcel-{name}")

                    page.goto(BASE + "/docs-guide")
                    shot("docs-guide")
                    page.goto(BASE + "/admin/login")
                    shot("admin-login")
                    page.fill("#username", ADMIN_USERNAME)
                    page.fill("#password", ADMIN_PASSWORD)
                    page.click("button[type=submit]")
                    page.wait_for_url(BASE + "/admin")
                    shot("admin-parcels")
                    page.click("#tab-keys")
                    shot("admin-keys")
                    page.click("#tab-webhooks")
                    shot("admin-webhooks")
                    page.click("#tab-keys")
                    page.fill("#key-name", "Preview key")
                    page.click("#panel-keys form button[type=submit]")
                    page.wait_for_selector("#created-api-key")
                    shot("api-key-created")
                    page.goto(BASE + "/no-such-page")
                    shot("error-404")
                    DB.rate_limit_allowed = False
                    page.goto(BASE + "/")
                    shot("error-429")
                    DB.rate_limit_allowed = True
                    context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print(f"Saved {len(list(out.glob('*.png')))} screenshots to {out}")
    print("Problems:" if problems else "No sideways scrolling or console errors found.")
    for problem in problems:
        print(" -", problem)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run(Path(sys.argv[1]))
