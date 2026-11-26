import hashlib
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError as PostgrestAPIError
from pydantic import BaseModel, ConfigDict, Field
from supabase import Client, create_client

load_dotenv(".env.local")

logger = logging.getLogger(__name__)
RATE_LIMIT_REQUESTS = 60
app = FastAPI(title="Parcel and Delivery Tracking Assistant")


class ParcelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracking_id: str = Field(min_length=3, max_length=80)
    destination: str = Field(min_length=1, max_length=200)
    eta: datetime | None = None


class ParcelStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "created",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "delayed",
        "failed_delivery",
    ]
    description: str | None = Field(default=None, min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=200)


class APIKeyPrincipal(BaseModel):
    id: str
    owner_id: str
    scopes: frozenset[str]


@lru_cache
def get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "server_configuration_error",
                "message": "The database service is not configured.",
            },
        )
    return create_client(supabase_url, service_role_key)


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    del request
    if isinstance(exc.detail, dict):
        error = exc.detail
    else:
        error = {"code": "request_error", "message": str(exc.detail)}
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": error},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    errors = [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error": {
                "code": "validation_error",
                "message": "The request contains invalid fields.",
                "details": errors,
            },
        },
    )


@app.exception_handler(PostgrestAPIError)
async def database_error_handler(
    request: Request, exc: PostgrestAPIError
) -> JSONResponse:
    del request
    status_errors = {
        "22023": (400, "invalid_database_request", "The requested data is invalid."),
        "23505": (409, "resource_conflict", "A resource with that value already exists."),
        "P0002": (404, "parcel_not_found", "No parcel matches that tracking ID."),
    }
    status_code, code, message = status_errors.get(
        exc.code,
        (500, "database_error", "The database request could not be completed."),
    )
    if status_code == 500:
        logger.error("Supabase request failed with database code %s.", exc.code)
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    del request, exc
    logger.exception("Unhandled request error")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "internal_server_error",
                "message": "An unexpected server error occurred.",
            },
        },
    )


def authenticate_api_key(
    api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    db: Client = Depends(get_supabase_client),
) -> APIKeyPrincipal:
    if not api_key:
        raise api_error(401, "missing_api_key", "An API key is required.")

    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    result = (
        db.table("api_keys")
        .select("id,owner_id,scopes")
        .eq("key_hash", key_hash)
        .is_("revoked_at", "null")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise api_error(401, "invalid_api_key", "The API key is invalid or revoked.")

    key_record = result.data[0]
    db.table("api_keys").update(
        {"last_used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", key_record["id"]).execute()

    return APIKeyPrincipal(
        id=str(key_record["id"]),
        owner_id=str(key_record["owner_id"]),
        scopes=frozenset(key_record.get("scopes") or []),
    )


def require_scope(required_scope: str):
    def check_scope(
        principal: APIKeyPrincipal = Depends(authenticate_api_key),
        db: Client = Depends(get_supabase_client),
    ) -> APIKeyPrincipal:
        if required_scope not in principal.scopes:
            raise api_error(
                403,
                "insufficient_scope",
                f"This API key requires the '{required_scope}' scope.",
            )

        now = datetime.now(timezone.utc)
        window_start = now.replace(second=0, microsecond=0).isoformat()
        result = db.rpc(
            "consume_rate_limit",
            {
                "p_api_key_id": principal.id,
                "p_client_ip_hash": None,
                "p_window_start": window_start,
                "p_limit": RATE_LIMIT_REQUESTS,
            },
        ).execute()
        rate_limit = result.data[0]
        if not rate_limit["allowed"]:
            raise api_error(
                429,
                "rate_limit_exceeded",
                "The request limit has been reached. Try again next minute.",
            )
        return principal

    return check_scope


def parcel_data(parcel: dict) -> dict:
    visible_fields = (
        "id",
        "tracking_id",
        "status",
        "eta",
        "destination",
        "created_at",
        "updated_at",
    )
    return {field: parcel.get(field) for field in visible_fields}


def rpc_parcel_data(result_data: object) -> dict:
    if isinstance(result_data, list) and result_data:
        return result_data[0]
    if isinstance(result_data, dict):
        return result_data
    raise api_error(500, "database_error", "The database returned an invalid result.")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/parcels", status_code=201)
def create_parcel(
    payload: ParcelCreate,
    principal: APIKeyPrincipal = Depends(require_scope("write")),
    db: Client = Depends(get_supabase_client),
) -> dict:
    result = db.rpc(
        "create_parcel_with_event",
        {
            "p_owner_id": principal.owner_id,
            "p_tracking_id": payload.tracking_id,
            "p_destination": payload.destination,
            "p_eta": payload.eta.isoformat() if payload.eta else None,
        },
    ).execute()
    parcel = rpc_parcel_data(result.data)
    return {"success": True, "data": parcel_data(parcel)}


@app.get("/api/v1/parcels/{tracking_id}")
def get_parcel(
    tracking_id: str,
    principal: APIKeyPrincipal = Depends(require_scope("read")),
    db: Client = Depends(get_supabase_client),
) -> dict:
    result = (
        db.table("parcels")
        .select("id,tracking_id,status,eta,destination,created_at,updated_at")
        .eq("owner_id", principal.owner_id)
        .eq("tracking_id", tracking_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise api_error(404, "parcel_not_found", "No parcel matches that tracking ID.")

    parcel = result.data[0]
    event_result = (
        db.table("parcel_events")
        .select("status,description,location,occurred_at")
        .eq("parcel_id", parcel["id"])
        .order("occurred_at", desc=False)
        .execute()
    )
    return {
        "success": True,
        "data": {
            **parcel_data(parcel),
            "timeline": event_result.data or [],
        },
    }


@app.patch("/api/v1/parcels/{tracking_id}/status")
def update_parcel_status(
    tracking_id: str,
    payload: ParcelStatusUpdate,
    principal: APIKeyPrincipal = Depends(require_scope("write")),
    db: Client = Depends(get_supabase_client),
) -> dict:
    description = payload.description or (
        "Status updated to " + payload.status.replace("_", " ")
    )
    result = db.rpc(
        "update_parcel_status_with_event",
        {
            "p_owner_id": principal.owner_id,
            "p_tracking_id": tracking_id,
            "p_status": payload.status,
            "p_description": description,
            "p_location": payload.location,
        },
    ).execute()
    parcel = rpc_parcel_data(result.data)
    return {"success": True, "data": parcel_data(parcel)}
