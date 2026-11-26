import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import app, get_supabase_client

READ_KEY = "local-read-key"
WRITE_KEY = "local-write-key"
OWNER_ID = "20000000-0000-4000-8000-000000000001"


class FakeTable:
    def __init__(self, database, name):
        self.database = database
        self.name = name
        self.filters = {}
        self.operation = "select"

    def select(self, fields):
        self.fields = fields
        return self

    def update(self, values):
        self.operation = "update"
        self.values = values
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def is_(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, value):
        return self

    def order(self, field, desc=False):
        return self

    def execute(self):
        if self.name == "api_keys" and self.operation == "select":
            keys = {
                hashlib.sha256(READ_KEY.encode()).hexdigest(): {
                    "id": "test-read-key-id",
                    "owner_id": OWNER_ID,
                    "scopes": ["read"],
                },
                hashlib.sha256(WRITE_KEY.encode()).hexdigest(): {
                    "id": "test-write-key-id",
                    "owner_id": OWNER_ID,
                    "scopes": ["read", "write"],
                },
            }
            row = keys.get(self.filters.get("key_hash"))
            return SimpleNamespace(data=[row] if row else [])

        if self.name == "parcels":
            tracking_id = self.filters.get("tracking_id")
            owner_id = self.filters.get("owner_id")
            if tracking_id == "DEMO-PARCEL-001" and owner_id == OWNER_ID:
                return SimpleNamespace(data=[self.database.parcel])
            return SimpleNamespace(data=[])

        if self.name == "parcel_events":
            return SimpleNamespace(data=self.database.events)

        return SimpleNamespace(data=[])


class FakeRPC:
    def __init__(self, database, name, params):
        self.database = database
        self.name = name
        self.params = params

    def execute(self):
        if self.name == "consume_rate_limit":
            return SimpleNamespace(
                data=[
                    {
                        "request_count": 1,
                        "allowed": self.database.rate_limit_allowed,
                    }
                ]
            )
        if self.name == "create_parcel_with_event":
            return SimpleNamespace(
                data=[
                    {
                        "id": "new-parcel-id",
                        "tracking_id": self.params["p_tracking_id"],
                        "status": "created",
                        "eta": self.params["p_eta"],
                        "destination": self.params["p_destination"],
                    }
                ]
            )
        if self.name == "update_parcel_status_with_event":
            return SimpleNamespace(
                data=[
                    {
                        **self.database.parcel,
                        "status": self.params["p_status"],
                    }
                ]
            )
        raise AssertionError(f"Unexpected RPC: {self.name}")


class FakeDatabase:
    def __init__(self):
        self.rate_limit_allowed = True
        self.parcel = {
            "id": "sample-parcel-id",
            "tracking_id": "DEMO-PARCEL-001",
            "status": "in_transit",
            "eta": "2026-12-01T10:00:00+00:00",
            "destination": "Sample destination",
            "created_at": "2026-11-20T10:00:00+00:00",
            "updated_at": "2026-11-25T10:00:00+00:00",
        }
        self.events = [
            {
                "status": "in_transit",
                "description": "Parcel is moving",
                "location": "Sorting facility",
                "occurred_at": "2026-11-25T10:00:00+00:00",
            }
        ]

    def table(self, name):
        return FakeTable(self, name)

    def rpc(self, name, params):
        return FakeRPC(self, name, params)


@pytest.fixture
def api_client():
    database = FakeDatabase()
    app.dependency_overrides[get_supabase_client] = lambda: database
    with TestClient(app) as client:
        yield client, database
    app.dependency_overrides.clear()


def test_health_check(api_client) -> None:
    client, _ = api_client
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_api_key_returns_401_envelope(api_client) -> None:
    client, _ = api_client
    response = client.get("/api/v1/parcels/DEMO-PARCEL-001")

    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "missing_api_key"


def test_invalid_api_key_returns_401_envelope(api_client) -> None:
    client, _ = api_client
    response = client.get(
        "/api/v1/parcels/DEMO-PARCEL-001",
        headers={"x-api-key": "not-a-valid-key"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_read_scope_cannot_create_parcel(api_client) -> None:
    client, _ = api_client
    response = client.post(
        "/api/v1/parcels",
        headers={"x-api-key": READ_KEY},
        json={"tracking_id": "TEST-001", "destination": "Test destination"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_parcel_lookup_is_successful_and_owner_scoped(api_client) -> None:
    client, _ = api_client
    response = client.get(
        "/api/v1/parcels/DEMO-PARCEL-001",
        headers={"x-api-key": READ_KEY},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["tracking_id"] == "DEMO-PARCEL-001"
    assert len(response.json()["data"]["timeline"]) == 1
    assert "owner_id" not in response.json()["data"]


def test_unknown_parcel_returns_404_envelope(api_client) -> None:
    client, _ = api_client
    response = client.get(
        "/api/v1/parcels/DOES-NOT-EXIST",
        headers={"x-api-key": READ_KEY},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "parcel_not_found"


def test_rate_limit_returns_429_envelope(api_client) -> None:
    client, database = api_client
    database.rate_limit_allowed = False
    response = client.get(
        "/api/v1/parcels/DEMO-PARCEL-001",
        headers={"x-api-key": READ_KEY},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


def test_create_and_update_parcel_use_success_envelopes(api_client) -> None:
    client, _ = api_client
    headers = {"x-api-key": WRITE_KEY}
    created = client.post(
        "/api/v1/parcels",
        headers=headers,
        json={"tracking_id": "TEST-001", "destination": "Test destination"},
    )
    updated = client.patch(
        "/api/v1/parcels/DEMO-PARCEL-001/status",
        headers=headers,
        json={"status": "delayed"},
    )

    assert created.status_code == 201
    assert created.json()["success"] is True
    assert created.json()["data"]["tracking_id"] == "TEST-001"
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "delayed"


def test_invalid_body_uses_json_error_envelope(api_client) -> None:
    client, _ = api_client
    response = client.post(
        "/api/v1/parcels",
        headers={"x-api-key": WRITE_KEY},
        json={"tracking_id": "x", "destination": ""},
    )

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "validation_error"
