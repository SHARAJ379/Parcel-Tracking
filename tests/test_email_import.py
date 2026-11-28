"""Tests for the email import (CloudMailin endpoint and admin paste box).

Supabase and Gemini are replaced by small in-memory fakes. The fake Gemini
returns whatever JSON a test gives it, which lets the tests check that the
app's own validation catches invented or injected values.
"""

import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError as PostgrestAPIError

import app as app_module
import email_import
import sample_emails as samples
from app import app, find_tracking_ids, get_gemini_client, get_supabase_client

OWNER_ID = "20000000-0000-4000-8000-000000000001"
SECRET = "test-inbound-secret-0123456789abcdef"
SENDER = "test.customer@example.com"


# ---------- Fakes ----------


class MemoryQuery:
    """Supports the subset of the Supabase query builder the app uses."""

    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.operation, self.values = [], "select", None
        self.max_rows, self.sort = None, None

    def select(self, fields):
        return self

    def insert(self, values):
        self.operation, self.values = "insert", values
        return self

    def eq(self, field, value):
        self.filters.append(lambda row: row.get(field) == value)
        return self

    def in_(self, field, values):
        values = list(values)
        self.filters.append(lambda row: row.get(field) in values)
        return self

    def is_(self, field, value):
        self.filters.append(lambda row: row.get(field) is None)
        return self

    def order(self, field, desc=False):
        self.sort = (field, desc)
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.operation == "insert":
            row = {"id": str(uuid4()), **self.values}
            rows.append(row)
            return SimpleNamespace(data=[row])
        matches = [dict(r) for r in rows if all(f(r) for f in self.filters)]
        if self.sort:
            matches.sort(key=lambda r: str(r.get(self.sort[0])), reverse=self.sort[1])
        if self.max_rows is not None:
            matches = matches[: self.max_rows]
        return SimpleNamespace(data=matches)


class MemoryDatabase:
    def __init__(self):
        self.tables = {"parcels": [], "parcel_events": [], "email_imports": []}
        self.rate_limit_allowed = True
        self.rpc_calls = []
        self.rpc_params = []

    def table(self, name):
        return MemoryQuery(self, name)

    def rpc(self, name, params):
        self.rpc_calls.append(name)
        self.rpc_params.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self._rpc(name, params)))

    def add_parcel(self, **fields):
        parcel = {
            "id": str(uuid4()),
            "owner_id": OWNER_ID,
            "status": "created",
            "eta": None,
            "destination": None,
            "courier": None,
            "order_id": None,
            "awb_number": None,
            "items": [],
            "source": "manual",
            "created_at": "2026-11-20T10:00:00+00:00",
            "updated_at": "2026-11-20T10:00:00+00:00",
            **fields,
        }
        self.tables["parcels"].append(parcel)
        return parcel

    def events_for(self, parcel):
        return [e for e in self.tables["parcel_events"] if e["parcel_id"] == parcel["id"]]

    def parcel(self, tracking_id):
        return next(p for p in self.tables["parcels"] if p["tracking_id"] == tracking_id)

    def _rpc(self, name, p):
        if name == "consume_rate_limit":
            return [{"request_count": 1, "allowed": self.rate_limit_allowed}]
        if name == "create_parcel_from_email":
            for existing in self.tables["parcels"]:
                if existing["tracking_id"] == p["p_tracking_id"] or (
                    p["p_awb_number"] and existing["awb_number"] == p["p_awb_number"]
                ):
                    raise PostgrestAPIError({"code": "23505", "message": "duplicate"})
            parcel = self.add_parcel(
                tracking_id=p["p_tracking_id"],
                status=p["p_status"],
                eta=p["p_eta"],
                destination=p["p_destination"],
                courier=p["p_courier"],
                order_id=p["p_order_id"],
                awb_number=p["p_awb_number"],
                items=p["p_items"],
                source="email",
            )
            self.tables["parcel_events"].append(
                {"parcel_id": parcel["id"], "status": p["p_status"], "description": p["p_description"]}
            )
            return [parcel]
        if name == "apply_email_update":
            parcel = next(x for x in self.tables["parcels"] if x["id"] == p["p_parcel_id"])
            for column, key in (("status", "p_status"), ("eta", "p_eta"),
                                ("destination", "p_destination"), ("courier", "p_courier")):
                if p[key] is not None:
                    parcel[column] = p[key]
            parcel["order_id"] = parcel["order_id"] or p["p_order_id"]
            parcel["awb_number"] = parcel["awb_number"] or p["p_awb_number"]
            if p["p_items"]:
                parcel["items"] = p["p_items"]
            if p["p_status"]:
                self.tables["parcel_events"].append(
                    {"parcel_id": parcel["id"], "status": p["p_status"], "description": p["p_description"]}
                )
            return [dict(parcel)]
        raise AssertionError(f"Unexpected RPC {name}")


class FakeGeminiModels:
    def __init__(self):
        self.calls = []
        self.replies = []  # JSON strings or exceptions, used in order

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


def shipment(**fields):
    base = {
        "courier": None,
        "order_id": None,
        "tracking_number": None,
        "items": [],
        "status": "unknown",
        "eta_date": None,
        "destination_city": None,
    }
    return {**base, **fields}


def gemini_reply(*shipments):
    return json.dumps({"shipments": list(shipments)})


def cloudmailin_payload(plain=None, html=None, sender=SENDER, spf="pass", message_id=None):
    return {
        "envelope": {
            "to": "parcels@cloudmailin.example",
            "from": sender,
            "spf": {"result": spf, "domain": sender.rpartition("@")[2]},
        },
        "headers": {
            "from": f"Test Customer <{sender}>",
            "subject": "Fwd: SECRET-SUBJECT-TEXT",
            "message_id": message_id or f"<{uuid4()}@mail.example>",
        },
        "plain": plain,
        "html": html,
        "reply_plain": None,
        "attachments": [],
    }


AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("INBOUND_EMAIL_SECRET", SECRET)
    monkeypatch.setenv("ALLOWED_EMAIL_SENDERS", f"other@example.com, {SENDER.upper()}")
    monkeypatch.delenv("INBOUND_REQUIRE_SPF", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-0123456789")


@pytest.fixture
def email_client(env, monkeypatch):
    db = MemoryDatabase()
    gemini = SimpleNamespace(models=FakeGeminiModels())
    webhook_calls = []
    monkeypatch.setattr(
        email_import,
        "deliver_status_updated",
        lambda db_, owner, parcel: webhook_calls.append(parcel["status"]),
    )
    app.dependency_overrides[get_supabase_client] = lambda: db
    app.dependency_overrides[get_gemini_client] = lambda: gemini
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, gemini=gemini.models, webhooks=webhook_calls)
    app.dependency_overrides.clear()


def post_email(ctx, payload, headers=AUTH):
    return ctx.client.post("/api/v1/inbound-email", json=payload, headers=headers)


# ---------- Gatekeeping ----------


def test_inbound_email_requires_the_secret(email_client) -> None:
    payload = cloudmailin_payload(plain=samples.amazon_shipped())
    assert post_email(email_client, payload, headers={}).status_code == 401
    wrong = post_email(email_client, payload, headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_inbound_secret"
    assert email_client.gemini.calls == []


def test_inbound_email_is_refused_when_secret_is_not_configured(email_client, monkeypatch) -> None:
    monkeypatch.setenv("INBOUND_EMAIL_SECRET", "short")
    response = post_email(email_client, cloudmailin_payload(plain="x"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "inbound_email_not_configured"


def test_inbound_email_rejects_bodies_over_1_mb(email_client) -> None:
    payload = cloudmailin_payload(plain="a" * 1_100_000)
    response = post_email(email_client, payload)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "email_too_large"


def test_inbound_email_rate_limit(email_client) -> None:
    email_client.db.rate_limit_allowed = False
    response = post_email(email_client, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert response.status_code == 429
    assert email_client.gemini.calls == []


def test_inbound_email_rejects_invalid_json(email_client) -> None:
    response = email_client.client.post(
        "/api/v1/inbound-email", content=b"not json", headers=AUTH
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_payload"


def test_sender_must_be_allowed_and_rejections_store_no_content(email_client, caplog) -> None:
    caplog.set_level(logging.WARNING)
    payload = cloudmailin_payload(plain=samples.amazon_shipped(), sender="attacker@evil.example")
    response = post_email(email_client, payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sender_not_allowed"
    assert email_client.gemini.calls == []
    [row] = email_client.db.tables["email_imports"]
    assert row["result"] == "rejected"
    assert row["sender_domain"] == "evil.example"
    stored = json.dumps(row) + caplog.text
    assert "SECRET-SUBJECT-TEXT" not in stored
    assert "Airdopes" not in stored
    assert "attacker@" not in stored


def test_spf_must_pass_unless_turned_off(email_client, monkeypatch) -> None:
    payload = cloudmailin_payload(plain=samples.newsletter(), spf="fail")
    response = post_email(email_client, payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "spf_not_passed"

    monkeypatch.setenv("INBOUND_REQUIRE_SPF", "false")
    email_client.gemini.replies.append(gemini_reply())
    payload = cloudmailin_payload(plain=samples.newsletter(), spf="fail")
    assert post_email(email_client, payload).status_code == 200


# ---------- Extraction, validation and storage ----------


def test_amazon_shipped_email_creates_parcel_with_personal_data_removed(email_client) -> None:
    email_client.gemini.replies.append(
        gemini_reply(
            shipment(
                courier="Amazon Shipping",
                order_id="403-1234567-7654321",
                tracking_number="361234567890",
                items=["boAt Airdopes 141 Bluetooth Earbuds"],
                status="in_transit",
                eta_date=samples.eta_date().isoformat(),
                destination_city="Bengaluru",
            )
        )
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result"] == "imported"
    assert data["shipments"] == [
        {"tracking_id": "361234567890", "action": "created", "status": "in_transit"}
    ]

    parcel = email_client.db.parcel("361234567890")
    assert parcel["awb_number"] == "361234567890"
    assert parcel["order_id"] == "403-1234567-7654321"
    assert parcel["courier"] == "Amazon Shipping"
    assert parcel["destination"] == "Bengaluru"
    assert parcel["items"] == ["boAt Airdopes 141 Bluetooth Earbuds"]
    assert parcel["eta"].startswith(samples.eta_date().isoformat() + "T23:59")
    assert parcel["source"] == "email"

    call = email_client.gemini.calls[0]
    prompt = json.loads(call["contents"])
    assert "98765" not in prompt["untrusted_email_text"]  # +91 phone masked
    assert "untrusted" in call["config"].system_instruction
    assert call["config"].response_mime_type == "application/json"

    [row] = email_client.db.tables["email_imports"]
    assert row["result"] == "imported"
    assert row["tracking_ids"] == ["361234567890"]
    assert set(row) == {
        "id", "source", "result", "reason", "message_id_hash", "sender_domain", "tracking_ids"
    }


def test_order_email_then_shipped_email_updates_one_parcel(email_client) -> None:
    order = shipment(
        courier="Amazon",
        order_id="403-1234567-7654321",
        items=["boAt Airdopes 141 Bluetooth Earbuds"],
        status="created",
        eta_date=samples.eta_date().isoformat(),
        destination_city="Bengaluru",
    )
    email_client.gemini.replies.append(gemini_reply(order))
    first = post_email(email_client, cloudmailin_payload(plain=samples.amazon_ordered()))
    reference = first.json()["data"]["shipments"][0]["tracking_id"]
    assert reference.startswith("EM-")

    prompt = json.loads(email_client.gemini.calls[0]["contents"])["untrusted_email_text"]
    for private in ("9876543210", "4242", "help@shop.example"):
        assert private not in prompt

    email_client.gemini.replies.append(
        gemini_reply(
            shipment(
                courier="Amazon Shipping",
                order_id="403-1234567-7654321",
                tracking_number="361234567890",
                status="in_transit",
            )
        )
    )
    second = post_email(email_client, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert second.json()["data"]["result"] == "updated"

    [parcel] = email_client.db.tables["parcels"]
    assert parcel["tracking_id"] == reference  # links already shared keep working
    assert parcel["awb_number"] == "361234567890"
    assert parcel["status"] == "in_transit"
    assert [e["status"] for e in email_client.db.events_for(parcel)] == ["created", "in_transit"]
    assert email_client.webhooks == ["in_transit"]


def test_same_status_again_changes_nothing_and_fires_no_webhook(email_client) -> None:
    email_client.db.add_parcel(
        tracking_id="81234567890", awb_number="81234567890", status="out_for_delivery",
        courier="Blue Dart", destination="MYSURU", source="email",
    )
    email_client.gemini.replies.append(
        gemini_reply(shipment(courier="Blue Dart", tracking_number="81234567890",
                              status="out_for_delivery", destination_city="MYSURU"))
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.bluedart_out_for_delivery()))
    assert response.json()["data"]["result"] == "unchanged"
    assert email_client.db.rpc_calls.count("apply_email_update") == 0
    assert email_client.webhooks == []


def test_status_never_moves_backwards() -> None:
    assert email_import.next_status("delivered", "in_transit") is None
    assert email_import.next_status("delivered", "delayed") is None
    assert email_import.next_status("out_for_delivery", "in_transit") is None
    assert email_import.next_status("in_transit", "out_for_delivery") == "out_for_delivery"
    assert email_import.next_status("in_transit", "delayed") == "delayed"
    assert email_import.next_status("delayed", "out_for_delivery") == "out_for_delivery"
    assert email_import.next_status("delayed", "created") is None
    assert email_import.next_status("created", None) is None


def test_manually_forwarded_gmail_message_marks_parcel_delivered(email_client) -> None:
    email_client.db.add_parcel(
        tracking_id="81234567890", awb_number="81234567890", status="out_for_delivery",
        source="email",
    )
    email_client.gemini.replies.append(
        gemini_reply(shipment(courier="Blue Dart", tracking_number="81234567890",
                              status="delivered", destination_city="MYSURU"))
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.gmail_forward_of_bluedart()))
    assert response.json()["data"]["result"] == "updated"
    assert email_client.db.parcel("81234567890")["status"] == "delivered"
    assert email_client.webhooks == ["delivered"]
    prompt = json.loads(email_client.gemini.calls[0]["contents"])["untrusted_email_text"]
    assert "Forwarded message" in prompt
    assert "noreply@bluedart.example" not in prompt


def test_html_only_email_is_converted_to_text(email_client) -> None:
    email_client.gemini.replies.append(
        gemini_reply(shipment(order_id="403-7654321-1234567", tracking_number="362222333444",
                              items=["Prestige Electric Kettle 1.5 L"], status="in_transit"))
    )
    response = post_email(email_client, cloudmailin_payload(html=samples.amazon_html_only()))
    assert response.json()["data"]["result"] == "imported"
    prompt = json.loads(email_client.gemini.calls[0]["contents"])["untrusted_email_text"]
    assert "<td>" not in prompt
    assert "trackOpen" not in prompt and "color: red" not in prompt
    assert "Prestige Electric Kettle 1.5 L" in prompt


def test_email_with_several_shipments_creates_one_parcel_each(email_client) -> None:
    email_client.gemini.replies.append(
        gemini_reply(
            shipment(courier="Ekart", order_id="OD123456789012345678",
                     tracking_number="FMPP1234567890", items=["Milton Steel Water Bottle 1 L"],
                     status="in_transit", destination_city="Hubballi"),
            shipment(courier="Ekart", order_id="OD123456789012345678",
                     tracking_number="FMPP1234567891", items=["Classmate Notebook Pack of 6"],
                     status="in_transit", destination_city="Hubballi"),
            # An extra order-only entry for the same order is dropped.
            shipment(order_id="OD123456789012345678", status="in_transit"),
        )
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.flipkart_two_shipments()))
    data = response.json()["data"]
    assert data["result"] == "imported"
    assert [s["tracking_id"] for s in data["shipments"]] == ["FMPP1234567890", "FMPP1234567891"]
    assert len(email_client.db.tables["parcels"]) == 2


def test_invented_and_injected_values_are_dropped(email_client) -> None:
    demo = email_client.db.add_parcel(tracking_id="DEMO-PARCEL-001", status="in_transit")
    # A Gemini reply that obeyed the injected text and invented values.
    email_client.gemini.replies.append(
        gemini_reply(
            shipment(tracking_number="DEMO-PARCEL-001", status="delivered"),
            shipment(order_id="403-1111111-2222222", tracking_number="99999999999",
                     courier="Ultra Fast Couriers", destination_city="Chennai", status="in_transit"),
        )
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.prompt_injection()))
    assert response.status_code == 200
    data = response.json()["data"]
    assert {"tracking_id": None, "action": "skipped", "reason": "parcel_not_from_email"} in data["shipments"]

    assert demo["status"] == "in_transit"  # protected parcel untouched
    created = [p for p in email_client.db.tables["parcels"] if p["source"] == "email"]
    assert len(created) == 1
    assert created[0]["order_id"] == "403-1111111-2222222"
    assert created[0]["awb_number"] is None  # not written in the email
    assert created[0]["courier"] is None
    assert created[0]["destination"] is None
    assert email_client.webhooks == []


def test_email_without_shipment_returns_200_and_is_logged(email_client) -> None:
    email_client.gemini.replies.append(gemini_reply())
    response = post_email(email_client, cloudmailin_payload(plain=samples.newsletter()))
    assert response.status_code == 200
    assert response.json()["data"]["result"] == "no_shipment"
    assert email_client.db.tables["email_imports"][0]["reason"] == "no_shipment_found"
    assert email_client.db.tables["parcels"] == []


def test_duplicate_message_is_not_processed_twice(email_client) -> None:
    payload = cloudmailin_payload(plain=samples.newsletter(), message_id="<same@mail.example>")
    email_client.gemini.replies.append(gemini_reply())
    assert post_email(email_client, payload).json()["data"]["result"] == "no_shipment"
    again = post_email(email_client, payload)
    assert again.status_code == 200
    assert again.json()["data"]["result"] == "duplicate"
    assert len(email_client.gemini.calls) == 1


def test_gemini_timeout_returns_503_so_the_email_is_retried(email_client) -> None:
    payload = cloudmailin_payload(plain=samples.amazon_shipped(), message_id="<retry@mail.example>")
    email_client.gemini.replies.append(httpx2.TimeoutException("slow"))
    response = post_email(email_client, payload)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "assistant_timeout"
    assert email_client.db.tables["email_imports"][0]["message_id_hash"] is None

    email_client.gemini.replies.append(gemini_reply())
    assert post_email(email_client, payload).json()["data"]["result"] == "no_shipment"


def test_invalid_gemini_json_is_logged_as_failed(email_client) -> None:
    email_client.gemini.replies.append('{"shipments": "not a list"}')
    response = post_email(email_client, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert response.status_code == 200
    assert response.json()["data"] == {"result": "failed", "reason": "extraction_invalid", "shipments": []}


def test_order_id_with_several_email_parcels_is_not_guessed(email_client) -> None:
    for awb in ("FMPP1234567890", "FMPP1234567891"):
        email_client.db.add_parcel(tracking_id=awb, awb_number=awb, order_id="OD123456789012345678",
                                   status="in_transit", source="email")
    email_client.gemini.replies.append(
        gemini_reply(shipment(order_id="OD123456789012345678", status="delayed"))
    )
    response = post_email(email_client, cloudmailin_payload(plain=samples.flipkart_two_shipments()))
    data = response.json()["data"]
    assert data["result"] == "failed"
    assert data["reason"] == "ambiguous_order"
    assert all(p["status"] == "in_transit" for p in email_client.db.tables["parcels"])


# ---------- Units ----------


def test_redact_keeps_tracking_numbers_but_removes_contact_details() -> None:
    text = (
        "AWB 9123456789 shipped\n"
        "Mobile: 9123456789\n"
        "Call +91 91234 56789 or mail a.b@shop.example, UPI test@okaxis\n"
        "Card ending in 4242, XXXX7777, **** 3131\n"
    )
    result = email_import.redact(text)
    assert result.splitlines()[0] == "AWB 9123456789 shipped"
    for private in ("Mobile: 9123456789", "91234 56789", "a.b@shop.example", "test@okaxis",
                    "4242", "7777", "3131"):
        assert private not in result


def test_clean_id_requires_the_value_to_be_in_the_email() -> None:
    text = "Order #403-1234567-7654321, AWB 8123 4567 890"
    pattern = email_import.ORDER_ID_PATTERN
    assert email_import.clean_id("403-1234567-7654321", pattern, text) == "403-1234567-7654321"
    assert email_import.clean_id("81234567890", email_import.TRACKING_NUMBER_PATTERN, text) == "81234567890"
    assert email_import.clean_id("403-0000000-0000000", pattern, text) is None
    assert email_import.clean_id("ABCDEFGH", pattern, "ABCDEFGH") is None  # needs a digit


def test_find_tracking_ids_understands_real_order_and_awb_formats() -> None:
    assert find_tracking_ids("Where is 403-1234567-7654321?") == ["403-1234567-7654321"]
    assert find_tracking_ids("AWB 81234567890 status") == ["81234567890"]
    assert find_tracking_ids("order OD123456789012345678") == ["OD123456789012345678"]
    assert find_tracking_ids("ಪಾರ್ಸೆಲ್ 81234567890 ಎಲ್ಲಿದೆ") == ["81234567890"]
    assert find_tracking_ids("PIN 560038, year 2026, 3 days") == []
    assert find_tracking_ids("DEMO-PARCEL-001") == ["DEMO-PARCEL-001"]


# ---------- Lookup by order ID or AWB ----------


def test_chat_and_page_find_parcels_by_order_id_or_awb(email_client) -> None:
    email_client.db.add_parcel(tracking_id="EM-7K2Q9P", order_id="403-1234567-7654321",
                               awb_number="361234567890", status="in_transit", source="email")
    email_client.gemini.replies.append("Your parcel is in transit.")
    response = email_client.client.post(
        "/ui/assistant", json={"question": "Where is 403-1234567-7654321?"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["tracking_id"] == "EM-7K2Q9P"
    assert "403-1234567-7654321" in response.json()["data"]["panel_html"]

    page = email_client.client.get("/", params={"tracking_id": "361234567890"})
    assert "EM-7K2Q9P" in page.text


def test_order_id_with_several_shipments_asks_which_one(email_client) -> None:
    for awb in ("FMPP1234567890", "FMPP1234567891"):
        email_client.db.add_parcel(tracking_id=awb, awb_number=awb, order_id="OD123456789012345678",
                                   source="email")
    response = email_client.client.post(
        "/ui/assistant", json={"question": "Where is OD123456789012345678?"}
    )
    assert response.status_code == 409
    message = response.json()["error"]["message"]
    assert "FMPP1234567890" in message and "FMPP1234567891" in message
    assert email_client.gemini.calls == []

    page = email_client.client.get("/", params={"tracking_id": "OD123456789012345678"})
    assert page.status_code == 200
    assert "Several shipments found" in page.text


# ---------- Admin paste box ----------


def admin_login(ctx, monkeypatch) -> str:
    csrf = "test-csrf-token"
    ctx.client.cookies.set(app_module.ADMIN_SESSION_COOKIE, app_module.encode_admin_session(csrf))
    return csrf


def test_admin_paste_box_uses_the_same_pipeline(email_client, monkeypatch) -> None:
    csrf = admin_login(email_client, monkeypatch)
    email_client.gemini.replies.append(
        gemini_reply(shipment(courier="Blue Dart", tracking_number="81234567890",
                              status="out_for_delivery", destination_city="MYSURU"))
    )
    response = email_client.client.post(
        "/admin/email-imports",
        data={"csrf_token": csrf, "email_text": samples.bluedart_out_for_delivery()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?notice=email_imported#email"
    [row] = email_client.db.tables["email_imports"]
    assert row["source"] == "admin_paste" and row["tracking_ids"] == ["81234567890"]

    dashboard = email_client.client.get("/admin?notice=email_imported")
    assert "Email imported: a new parcel was created." in dashboard.text
    assert "Recent email imports" in dashboard.text
    assert "81234567890" in dashboard.text


def test_admin_paste_box_requires_csrf_and_login(email_client, monkeypatch) -> None:
    data = {"csrf_token": "wrong", "email_text": "Order 403-1234567-7654321"}
    response = email_client.client.post("/admin/email-imports", data=data, follow_redirects=False)
    assert response.status_code == 303  # not signed in: sent to the login page
    admin_login(email_client, monkeypatch)
    response = email_client.client.post("/admin/email-imports", data=data)
    assert response.status_code == 400
    assert email_client.gemini.calls == []


# ---------- Date-only ETA and decline wording ----------


def test_date_only_eta_is_shown_without_a_time() -> None:
    assert app_module.format_ist_eta("2026-12-04T23:59:00+05:30") == "04 Dec 2026"
    assert app_module.format_ist_eta("2026-12-04T18:29:00+00:00") == "04 Dec 2026"  # same moment in UTC
    assert app_module.format_ist_eta("2026-12-01T10:00:00+00:00") == "01 Dec 2026, 03:30 PM IST"
    assert app_module.format_ist_eta(None) == "Not provided"


def test_email_eta_shows_date_on_panel_admin_and_chat_records(email_client, monkeypatch) -> None:
    email_client.db.add_parcel(tracking_id="81234567890", awb_number="81234567890",
                               status="in_transit", source="email",
                               eta="2026-12-04T18:29:00+00:00")
    page = email_client.client.get("/", params={"tracking_id": "81234567890"})
    assert "04 Dec 2026" in page.text and "11:59 PM" not in page.text

    admin_login(email_client, monkeypatch)
    dashboard = email_client.client.get("/admin")
    assert "04 Dec 2026" in dashboard.text and "11:59 PM" not in dashboard.text

    email_client.gemini.replies.append("It should arrive on 04 Dec 2026.")
    email_client.client.post("/ui/assistant", json={"question": "When will 81234567890 arrive?"})
    records = json.loads(email_client.gemini.calls[0]["contents"])["parcel_records"]
    assert records["parcel"]["eta"] == "04 Dec 2026"


def test_decline_sentence_is_only_for_unrelated_requests() -> None:
    rules = app_module.ASSISTANT_SYSTEM_INSTRUCTION
    assert '"I can only assist with tracking."' in rules
    assert "only\n   when declining" in rules
    assert "never add it to answers about a parcel" in rules


# ---------- Fixes from the live review ----------


def gemini_error(code, status):
    from google.genai import errors as genai_errors

    return genai_errors.APIError(code, {"error": {"message": "test", "status": status}})


@pytest.fixture
def no_retry_wait(monkeypatch):
    import gemini_utils

    monkeypatch.setattr(gemini_utils, "RETRY_DELAY_SECONDS", 0)


def test_chat_retries_once_when_gemini_is_overloaded(email_client, no_retry_wait) -> None:
    email_client.db.add_parcel(tracking_id="DEMO-PARCEL-001", status="in_transit")
    email_client.gemini.replies += [gemini_error(503, "UNAVAILABLE"), "It is in transit."]
    response = email_client.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert response.status_code == 200
    assert len(email_client.gemini.calls) == 2


def test_chat_reports_busy_and_timeout_clearly(email_client, no_retry_wait) -> None:
    email_client.db.add_parcel(tracking_id="DEMO-PARCEL-001", status="in_transit")
    email_client.gemini.replies += [gemini_error(503, "UNAVAILABLE"), gemini_error(503, "UNAVAILABLE")]
    busy = email_client.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert busy.status_code == 503
    assert busy.json()["error"]["code"] == "assistant_busy"

    email_client.gemini.replies.append(gemini_error(504, "DEADLINE_EXCEEDED"))
    calls_before = len(email_client.gemini.calls)
    slow = email_client.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert slow.status_code == 504
    assert slow.json()["error"]["code"] == "assistant_timeout"
    assert len(email_client.gemini.calls) == calls_before + 1  # a 504 is not retried


def test_inbound_email_retries_then_asks_for_redelivery(email_client, no_retry_wait) -> None:
    email_client.gemini.replies += [gemini_error(503, "UNAVAILABLE"), gemini_error(503, "UNAVAILABLE")]
    response = post_email(email_client, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "assistant_busy"
    assert len(email_client.gemini.calls) == 2


def test_security_headers_on_pages_and_api(email_client) -> None:
    page = email_client.client.get("/")
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    api = email_client.client.get("/api/v1/parcels/DEMO-PARCEL-001")
    assert api.headers["x-content-type-options"] == "nosniff"
    docs = email_client.client.get("/docs")
    assert "content-security-policy" not in docs.headers  # Swagger UI needs its CDN
    assert docs.headers["x-frame-options"] == "DENY"


def test_csp_hash_matches_the_inline_theme_script() -> None:
    import base64
    import hashlib
    import re

    html = open("templates/base.html", encoding="utf-8").read()
    [script] = re.findall(r"<script>(.*?)</script>", html, re.S)
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert app_module.THEME_SCRIPT_HASH == f"sha256-{digest}"


def test_admin_sign_in_is_rate_limited_per_ip(email_client, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    wrong = email_client.client.post("/admin/login", data={"username": "x", "password": "y"})
    assert wrong.status_code == 401
    name, params = email_client.db.rpc_params[-1]
    assert name == "consume_rate_limit" and params["p_limit"] == 5
    public_subject = app_module.hashlib.sha256(b"testclient").hexdigest()
    assert params["p_client_ip_hash"] != public_subject  # its own counter

    email_client.db.rate_limit_allowed = False
    blocked = email_client.client.post(
        "/admin/login", data={"username": "test-admin", "password": "test-admin-password"},
        headers={"Accept": "application/json"},
    )
    assert blocked.status_code == 429
    assert "set-cookie" not in blocked.headers
    assert 1 <= int(blocked.headers["retry-after"]) <= 60


def test_rate_limited_responses_say_when_to_retry(email_client) -> None:
    email_client.db.rate_limit_allowed = False
    response = email_client.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_language_rule_keeps_the_question_script() -> None:
    rules = app_module.ASSISTANT_SYSTEM_INSTRUCTION
    assert "language and script of untrusted_question" in rules
    assert "gets a reply in Latin letters" in rules


def test_favicon_is_linked_and_served(email_client) -> None:
    page = email_client.client.get("/")
    assert '<link rel="icon" href="/static/favicon.svg"' in page.text
    icon = email_client.client.get("/static/favicon.svg")
    assert icon.status_code == 200 and "<svg" in icon.text


# ---------- Optional fallback model ----------


@pytest.fixture
def fallback(email_client, monkeypatch):
    """Main model 'test-model', fallback 'fallback-model'; records any sleep."""
    import gemini_utils

    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "fallback-model")
    slept = []
    monkeypatch.setattr(gemini_utils.time, "sleep", lambda seconds: slept.append(seconds))
    email_client.slept = slept
    email_client.db.add_parcel(tracking_id="DEMO-PARCEL-001", status="in_transit")
    return email_client


def models_called(ctx):
    return [call["model"] for call in ctx.gemini.calls]


@pytest.mark.parametrize(
    "failure",
    [
        gemini_error(500, "INTERNAL"),
        gemini_error(503, "UNAVAILABLE"),
        gemini_error(504, "DEADLINE_EXCEEDED"),
        httpx2.TimeoutException("slow"),
    ],
    ids=["500", "503", "504", "timeout"],
)
def test_chat_uses_fallback_model_at_once_when_main_model_is_busy(fallback, failure) -> None:
    fallback.gemini.replies += [failure, "It is in transit."]
    response = fallback.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == "It is in transit."
    assert models_called(fallback) == ["test-model", "fallback-model"]
    assert fallback.slept == []  # no pause before falling back


def test_email_import_uses_fallback_model(fallback) -> None:
    fallback.gemini.replies += [
        gemini_error(504, "DEADLINE_EXCEEDED"),
        gemini_reply(shipment(courier="Blue Dart", tracking_number="81234567890",
                              status="out_for_delivery", destination_city="MYSURU")),
    ]
    response = post_email(fallback, cloudmailin_payload(plain=samples.bluedart_out_for_delivery()))
    assert response.status_code == 200
    assert response.json()["data"]["result"] == "imported"
    assert models_called(fallback) == ["test-model", "fallback-model"]


def test_when_both_models_fail_it_stops_after_two_calls(fallback) -> None:
    fallback.gemini.replies += [gemini_error(503, "UNAVAILABLE"), gemini_error(503, "UNAVAILABLE")]
    chat = fallback.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert chat.status_code == 503 and chat.json()["error"]["code"] == "assistant_busy"

    fallback.gemini.replies += [httpx2.TimeoutException("slow"), httpx2.TimeoutException("slow")]
    mail = post_email(fallback, cloudmailin_payload(plain=samples.amazon_shipped()))
    assert mail.status_code == 503 and mail.json()["error"]["code"] == "assistant_timeout"
    assert models_called(fallback) == ["test-model", "fallback-model"] * 2
    assert fallback.slept == []


@pytest.mark.parametrize("failure, status", [(gemini_error(429, "RESOURCE_EXHAUSTED"), 429),
                                             (gemini_error(400, "INVALID_ARGUMENT"), 502)])
def test_quota_and_request_errors_do_not_use_the_fallback(fallback, failure, status) -> None:
    fallback.gemini.replies.append(failure)
    response = fallback.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
    assert response.status_code == status
    assert models_called(fallback) == ["test-model"]


def test_without_fallback_the_old_behaviour_is_kept(email_client, monkeypatch) -> None:
    import gemini_utils

    slept = []
    monkeypatch.setattr(gemini_utils.time, "sleep", lambda seconds: slept.append(seconds))
    email_client.db.add_parcel(tracking_id="DEMO-PARCEL-001", status="in_transit")
    for unset_or_same in (None, "", "test-model"):  # same as the main model counts as unset
        if unset_or_same is None:
            monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
        else:
            monkeypatch.setenv("GEMINI_FALLBACK_MODEL", unset_or_same)
        email_client.gemini.calls.clear()
        slept.clear()
        email_client.gemini.replies += [gemini_error(503, "UNAVAILABLE"), "It is in transit."]
        response = email_client.client.post("/ui/assistant", json={"question": "Where is DEMO-PARCEL-001?"})
        assert response.status_code == 200
        assert models_called(email_client) == ["test-model", "test-model"]  # same model, once
        assert slept == [gemini_utils.RETRY_DELAY_SECONDS]


def test_worst_case_gemini_time_fits_the_vercel_limit() -> None:
    import gemini_utils

    config = json.load(open("vercel.json", encoding="utf-8"))
    max_duration = config["functions"]["app.py"]["maxDuration"]
    timeout = app_module.GEMINI_REQUEST_TIMEOUT_MS / 1000
    with_fallback = 2 * timeout
    without_fallback = 2 * timeout + gemini_utils.RETRY_DELAY_SECONDS
    assert max(with_fallback, without_fallback) + 10 <= max_duration  # 10 s for database work
