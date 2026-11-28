# API reference notes

Longer notes on the `/api/v1` routes. Interactive docs are at `/docs` and a short guide is at `/docs-guide` on the running app.

## Parcels, assistant and responses

All `/api/v1` routes require the `x-api-key` header. Keys are SHA-256 hashed
before lookup, checked for revocation, and restricted to their stored owner and
scopes. Read and write requests default to 60 per API key per UTC minute. Set
`RATE_LIMIT_REQUESTS` to a positive integer from 1 through 10,000 to tune that
limit; the production default remains 60. Each request window is aligned to a
UTC minute. The Supabase client is cached for the process lifetime. API-key
validation and `last_used_at` update share one database call; the limiter and
parcel/timeline queries remain separate.

- `POST /api/v1/parcels` (write): create a parcel and its initial timeline event.
- `GET /api/v1/parcels/{tracking_id}` (read): get an owner-scoped parcel and
  chronological timeline.
- `PATCH /api/v1/parcels/{tracking_id}/status` (write): update status and add a
  timeline event in one database transaction.
- `POST /api/v1/assistant` (read): multi-turn chat about one owner's parcel and
  its timeline using Gemini.
- `POST /api/v1/inbound-email`: CloudMailin target for forwarded emails. It
  uses `Authorization: Bearer <INBOUND_EMAIL_SECRET>` instead of an API key.
  See [inbound-email.md](./inbound-email.md) for the payload and status codes.

`{tracking_id}` and IDs in chat may also be a courier AWB number or a shop
order ID. Lookup tries the tracking ID, then the AWB, then the order ID. If an
order ID matches several parcels, the response is 409 `ambiguous_order_id` and
the message lists their tracking IDs. Chat recognises IDs with letters and
digits (`DEMO-PARCEL-001`, `OD123456789012345678`), Amazon order IDs
(`403-1234567-7654321`) and digit-only numbers of 10 to 20 digits (Blue Dart
AWBs). Parcel responses also include `courier`, `order_id`, `awb_number` and
`items`, which are `null` or empty for parcels not imported from email.

The assistant request body is `{"question": "...", "tracking_id": "...",
"history": [{"role": "user" | "assistant", "text": "..."}]}`. Only `question`
is required. The parcel is chosen in this order: a tracking ID written in the
question, then `tracking_id`, then the latest ID the user wrote in `history`.
IDs are matched as typed or in uppercase. `history` holds at most 10 earlier
turns; each text is limited to 1,000 characters.

The model receives only that owner's parcel and timeline (times converted to
IST), the history and the question, all labelled as untrusted data. Its rules
say to use only those records, reply in English, Hindi or Kannada, decline
unrelated requests, and never act on instructions inside the data. With no
tracking ID it is sent no records and asks for one. An unknown tracking ID
returns 404 without calling Gemini. Gemini timeouts return 504, upstream quota
limits return 429, and other upstream or empty responses return 502 without
exposing provider details. The response data is `{"answer", "tracking_id",
"parcel", "timeline"}`.

API responses use `{"success": true, "data": ...}` or
`{"success": false, "error": {"code": ..., "message": ...}}`. Invalid input is
returned as HTTP 400; missing/invalid keys as 401; missing scope as 403;
unknown or other-owner parcels as 404; duplicate tracking IDs as 409; and
rate-limited requests as 429.

## Webhooks

The webhook API uses write scope for registration/deletion and read scope for
listing:

- `POST /api/v1/webhooks` with `{"url":"https://..."}` registers an endpoint.
  The response includes a signing secret once.
- `GET /api/v1/webhooks` lists only the authenticated owner's endpoints and
  never returns their secrets.
- `DELETE /api/v1/webhooks/{id}` deletes only an endpoint owned by the
  authenticated API key's owner.

Registration accepts only HTTPS URLs that resolve exclusively to public IP
addresses; localhost, private/link-local/reserved addresses, credentials in
URLs, and URL fragments are rejected. Delivery rechecks the destination, uses
HTTPX with redirects disabled and a two-second timeout, and logs each attempt
(up to three). Status updates wait for delivery, but a delivery or logging
failure does not fail the parcel status response.

The registration response includes a randomly generated signing secret once.
Store it privately; list responses never include it. Requests use the exact
UTF-8 JSON body sent over the wire and the header
`X-Signature: sha256=<lowercase-hex-HMAC-SHA256>`.
