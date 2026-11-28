# Parcels from forwarded emails

Forward an order or shipping email (Amazon, Flipkart, Blue Dart or similar) to
your CloudMailin address and the parcel appears on the site, in admin and in
the chatbot.

```
Your mailbox ──forward──► CloudMailin ──POST JSON──► /api/v1/inbound-email
                                                        │
   secret, size, rate limit, sender, duplicate checks ◄─┘
                                                        │
   email text ─► remove phone/email/card ─► Gemini (JSON schema)
                                                        │
   Pydantic + "is it written in the email?" check ◄─────┘
                                                        │
   create or update parcel + timeline ─► webhook if the status changed
```

The code is in [`email_import.py`](../email_import.py). The routes are
`POST /api/v1/inbound-email` and `POST /admin/email-imports` in `app.py`.

## Setup

1. Run the migration
   [`supabase/migrations/20261128100000_email_imports.sql`](../supabase/migrations/20261128100000_email_imports.sql)
   in the Supabase SQL Editor (see [database.md](./database.md)).
2. Create a secret, for example with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Add these to `.env.local` and to the Vercel project settings, then
   redeploy:

   | Variable | Purpose |
   |---|---|
   | `INBOUND_EMAIL_SECRET` | At least 24 characters. CloudMailin sends it as `Authorization: Bearer <secret>` |
   | `ALLOWED_EMAIL_SENDERS` | Comma-separated addresses that may forward emails, e.g. your Gmail address |
   | `INBOUND_REQUIRE_SPF` | `true` (default) also requires CloudMailin's SPF result to be `pass` |
   | `INBOUND_EMAIL_RATE_LIMIT` | Imports per minute, 1 to 1,000. Default 10. Shared with the admin box |

4. Sign up for the CloudMailin free plan (10,000 emails a month, 512 KB per
   message, no card needed). Create an address and set:
   - **Target URL:** `https://<your-app>.vercel.app/api/v1/inbound-email`
   - **POST format:** JSON (Normalized)
   - **Authorization Settings:** `Bearer <your INBOUND_EMAIL_SECRET>`
   - Attachments are not used, so do not set up attachment storage.
5. Forward an email to the CloudMailin address. Gmail: open the email, choose
   **Forward**, and send it. The admin **Email imports** tab shows the result.

CloudMailin no longer supports `user:password@` in the target URL, which is
why the secret goes in the Authorization header.

## What is extracted

| Field | Stored in | Rule |
|---|---|---|
| Courier or shop | `parcels.courier` | Must be written in the email |
| Order ID | `parcels.order_id` | Must be written in the email, contain a digit |
| Tracking / AWB number | `parcels.awb_number` | Must be written in the email, contain a digit |
| Item names | `parcels.items` | At most 5, each written in the email, cut to 80 characters |
| Status | `parcels.status` + timeline | One of the 7 statuses, never moves backwards |
| ETA | `parcels.eta` | Only a written calendar date, stored as 11:59 pm IST that day and shown as the date only |
| Destination city | `parcels.destination` | City name only: letters, no digits, written in the email |

A missing value stays empty. Gemini is asked to use `null`, and Python then
drops any value it cannot find in the email text, so a value Gemini invents is
never stored.

**Tracking ID shown on the site:** the AWB when the first email has one.
Otherwise a reference such as `EM-7K2Q9P` is created; when a later email adds
the AWB, the same parcel keeps its reference. Users can search by the
tracking ID, the AWB or the order ID, on the home page and in the chat.

## Matching a later email

1. A tracking number finds its parcel.
2. A new tracking number for an order whose parcel has no tracking number
   yet is attached to that parcel (the "order placed" email, then the
   "shipped" one). This avoids a duplicate.
3. An email with only an order ID updates that order's parcel. If the order
   has several parcels, nothing is changed (`ambiguous_order`), because
   choosing one would be a guess.
4. Otherwise a new parcel is created.

A timeline event is added only when the status really changes, and only then
is the `parcel.status_updated` webhook sent. A status never moves backwards:
an old "shipped" email forwarded after "delivered" changes nothing. Delayed and
failed delivery can be reported at any time before delivery.

Emails can change **only parcels created from emails**. Parcels made in admin,
through the API, or by the seed data (such as `DEMO-PARCEL-001`) are skipped
with `parcel_not_from_email`.

An email with several shipments (for example a Flipkart order sent in two
packages) creates or updates one parcel per tracking number.

## Responses

CloudMailin treats a 2xx response as accepted, a 4xx response as a permanent
rejection (the email is refused during delivery, so no bounce goes to a forged
address), and a 5xx response as "try again later".

| Status | `code` / `result` | When |
|---|---|---|
| 200 | `imported`, `updated`, `unchanged` | Parcels created, updated, or already up to date |
| 200 | `no_shipment` | No order or tracking number found (logged in admin) |
| 200 | `failed` | Gemini returned invalid JSON, or every shipment was skipped |
| 200 | `duplicate` | The same `Message-ID` was already processed |
| 400 | `invalid_payload` | Body is not CloudMailin JSON |
| 401 | `invalid_inbound_secret` | Missing or wrong Authorization header |
| 403 | `sender_not_allowed`, `spf_not_passed` | Sender check failed |
| 413 | `email_too_large` | Body over 1 MB |
| 429 | `rate_limit_exceeded` | Over `INBOUND_EMAIL_RATE_LIMIT` this minute |
| 503 | `assistant_timeout`, `assistant_busy`, `assistant_quota_exceeded`, `assistant_upstream_error` | Gemini unavailable (an overload is retried once first): the email is retried later |
| 503 | `inbound_email_not_configured` | `INBOUND_EMAIL_SECRET` missing or too short |

Example success body:

```json
{"success": true, "data": {"result": "imported", "reason": "1 created",
 "shipments": [{"tracking_id": "81234567890", "action": "created", "status": "out_for_delivery"}]}}
```

## Security

- **Secret header.** The main protection. Compared with
  `hmac.compare_digest`. The endpoint returns 503 if the secret is shorter
  than 24 characters, so it cannot run with a weak or empty secret.
- **Sender allowlist.** `envelope.from` must be in `ALLOWED_EMAIL_SENDERS`,
  and by default SPF must pass. **Limits:** a sender address is easy to forge;
  SPF only shows that the sending server may send mail for that domain, not
  who the person is; and Gmail's automatic forwarding can change the envelope
  sender, so an auto-forward rule may need its address added. Treat the
  allowlist as a second layer behind the secret and the CloudMailin address,
  which is hard to guess.
- **Untrusted content.** The email goes to Gemini as labelled data with a
  rule to ignore instructions inside it. Gemini can only return the JSON
  schema; Python checks every value and decides every write. An injected
  "mark DEMO-PARCEL-001 delivered" cannot change that parcel, because emails
  can only change email-created parcels. **Remaining risk:** an email from an
  allowed sender that names another email-created parcel's real tracking
  number could update that parcel's status.
- **Personal data.** Before Gemini, email and UPI addresses, `+91` phone
  numbers, 10-digit numbers on lines that mention a phone, and card endings
  are masked. Street addresses can still reach Gemini, but there is nowhere to
  store them: only a short city name is kept. The raw email, subject, full
  sender address, phone numbers, addresses and payment details are never
  stored. The import log keeps only time, source, result, reason code,
  sender domain, a SHA-256 hash of the `Message-ID`, and tracking IDs.
- **Size and rate.** Bodies over 1 MB are refused before parsing. The text
  sent to Gemini is cut to 20,000 characters. All imports share one
  per-minute counter, which also caps Gemini use.
- **Logs.** Rejected emails are logged with the reason and sender domain
  only, never content.

## Admin "Paste email text" box

Admin, **Email imports** tab. Paste an email (plain text or HTML source, up to
50,000 characters). It runs the same pipeline without the sender check and is
logged with source `admin_paste`. Use it as a demo backup if forwarding is
not set up. The same tab lists the last 25 imports with their result and
reason.

## Test with curl

```bash
curl -i https://<your-app>.vercel.app/api/v1/inbound-email \
  -H "Authorization: Bearer $INBOUND_EMAIL_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"envelope":{"from":"you@example.com","spf":{"result":"pass"}},
       "headers":{"message_id":"<test-1@example.com>"},
       "plain":"Your shipment with Waybill No. 81234567890 is out for delivery to MYSURU. Blue Dart"}'
```

Use an address that is in `ALLOWED_EMAIL_SENDERS`, and a new `message_id` for
each test.

## Known limitations

- Relative dates such as "arriving tomorrow" give no ETA.
- Extraction quality depends on the Gemini model; the grounding check can
  only remove wrong values, not add missing ones.
- A 10-digit tracking number on a line that mentions a phone is masked and
  lost.
- Attachments (PDF invoices) are ignored.
- All imported parcels belong to the admin owner (`SAMPLE_OWNER_ID`).
