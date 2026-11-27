import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from supabase import Client

logger = logging.getLogger(__name__)
WEBHOOK_EVENT_TYPE = "parcel.status_updated"
WEBHOOK_TIMEOUT_SECONDS = 2.0
WEBHOOK_MAX_ATTEMPTS = 3
WEBHOOK_RESPONSE_BODY_MAX_LENGTH = 1000
WEBHOOK_ERROR_MAX_LENGTH = 500


class WebhookTargetError(ValueError):
    pass


def validate_webhook_url(url: str) -> str:
    has_control_characters = any(
        ord(character) < 33 or ord(character) == 127 for character in url
    )
    if url != url.strip() or has_control_characters:
        raise WebhookTargetError(
            "Webhook URL contains invalid whitespace or control characters."
        )

    try:
        parsed_url = urlsplit(url)
        hostname = parsed_url.hostname
        port = parsed_url.port
        if port is None:
            port = 443
        elif port == 0:
            raise WebhookTargetError("Webhook port must be between 1 and 65535.")
    except ValueError as exc:
        if isinstance(exc, WebhookTargetError):
            raise
        raise WebhookTargetError("Webhook URL is invalid.") from exc

    if (
        parsed_url.scheme.lower() != "https"
        or not hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.fragment
    ):
        raise WebhookTargetError(
            "Webhook URL must be HTTPS and must not contain credentials or a fragment."
        )

    hostname = hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
    ):
        raise WebhookTargetError("Local webhook destinations are not allowed.")

    try:
        addresses = {ipaddress.ip_address(hostname.split("%", 1)[0])}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise WebhookTargetError("Webhook host could not be resolved.") from exc
        addresses = {
            ipaddress.ip_address(result[4][0].split("%", 1)[0])
            for result in resolved
        }

    if not addresses or any(not address.is_global for address in addresses):
        raise WebhookTargetError(
            "Webhook host must resolve only to public IP addresses."
        )
    return url


def parcel_event_payload(parcel: dict) -> dict:
    parcel_fields = (
        "id",
        "tracking_id",
        "status",
        "eta",
        "destination",
        "created_at",
        "updated_at",
    )
    return {
        "event": WEBHOOK_EVENT_TYPE,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": {field: parcel.get(field) for field in parcel_fields},
    }


def _log_attempt(
    db: Client,
    webhook: dict,
    payload: dict,
    attempt_number: int,
    response_status: int | None,
    response_body: str | None,
    error: str | None,
) -> None:
    delivery = {
        "webhook_id": webhook["id"],
        "parcel_id": payload["data"]["id"],
        "event_type": WEBHOOK_EVENT_TYPE,
        "payload": payload,
        "attempt_number": attempt_number,
        "response_status": response_status,
        "response_body": (
            response_body[:WEBHOOK_RESPONSE_BODY_MAX_LENGTH]
            if response_body is not None
            else None
        ),
        "error": error[:WEBHOOK_ERROR_MAX_LENGTH] if error else None,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.table("webhook_deliveries").insert(delivery).execute()
    except Exception:
        logger.exception("Could not persist webhook delivery attempt.")


def _deliver_one(db: Client, webhook: dict, payload: dict) -> None:
    serialized_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    body = serialized_payload.encode("utf-8")
    signature = hmac.new(
        webhook["secret"].encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Event-Type": WEBHOOK_EVENT_TYPE,
        "X-Signature": f"sha256={signature}",
    }

    try:
        with httpx.Client(
            timeout=httpx.Timeout(WEBHOOK_TIMEOUT_SECONDS),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt_number in range(1, WEBHOOK_MAX_ATTEMPTS + 1):
                response_status = None
                response_body = None
                error = None
                stop_retrying = False
                try:
                    validate_webhook_url(webhook["url"])
                    response = client.post(
                        webhook["url"],
                        content=body,
                        headers=headers,
                        follow_redirects=False,
                    )
                    response_status = response.status_code
                    response_body = response.text
                    if not 200 <= response_status < 300:
                        error = f"Webhook endpoint returned HTTP {response_status}."
                except WebhookTargetError as exc:
                    error = str(exc)
                    stop_retrying = True
                except httpx.RequestError as exc:
                    error = f"Webhook request failed ({type(exc).__name__})."

                _log_attempt(
                    db,
                    webhook,
                    payload,
                    attempt_number,
                    response_status,
                    response_body,
                    error,
                )
                if error is None or stop_retrying:
                    break
    except Exception:
        logger.exception("Webhook delivery failed before an attempt could complete.")


def deliver_status_updated(db: Client, owner_id: str, parcel: dict) -> None:
    try:
        result = (
            db.table("webhooks")
            .select("id,url,secret,events")
            .eq("owner_id", owner_id)
            .eq("active", True)
            .execute()
        )
    except Exception:
        logger.exception("Could not load active webhooks for parcel status event.")
        return

    payload = parcel_event_payload(parcel)
    for webhook in result.data or []:
        if WEBHOOK_EVENT_TYPE not in (webhook.get("events") or []):
            continue
        try:
            _deliver_one(db, webhook, payload)
        except Exception:
            logger.exception(
                "Unexpected error while delivering webhook ID %s.",
                webhook.get("id"),
            )
