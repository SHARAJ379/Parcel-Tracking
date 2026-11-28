-- Email import: parcels logged from forwarded order and shipping emails.
-- Run once in the Supabase SQL Editor after the 20261126 migration.
-- It only adds columns, one table and two functions. It does not change,
-- delete or reseed existing rows.

-- 1. Extra parcel fields. All are optional, so existing parcels stay valid.
ALTER TABLE public.parcels
    ADD COLUMN IF NOT EXISTS courier text
        CHECK (courier IS NULL OR length(trim(courier)) BETWEEN 1 AND 40),
    ADD COLUMN IF NOT EXISTS order_id text
        CHECK (order_id IS NULL OR order_id ~ '^[A-Z0-9][A-Z0-9-]{4,38}[A-Z0-9]$'),
    ADD COLUMN IF NOT EXISTS awb_number text
        CHECK (awb_number IS NULL OR awb_number ~ '^[A-Z0-9][A-Z0-9-]{6,38}[A-Z0-9]$'),
    ADD COLUMN IF NOT EXISTS items text[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(items) <= 5),
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'email'));

-- One parcel per courier tracking number. This stops duplicates even if two
-- emails about the same shipment arrive at the same moment.
CREATE UNIQUE INDEX IF NOT EXISTS parcels_awb_number_unique
    ON public.parcels (awb_number)
    WHERE awb_number IS NOT NULL;

-- An order can have several shipments, so order_id is indexed but not unique.
CREATE INDEX IF NOT EXISTS parcels_order_id_idx
    ON public.parcels (order_id)
    WHERE order_id IS NOT NULL;

-- 2. Import log. It never stores the email itself: no subject, body, full
-- sender address, phone number, street address or payment detail.
CREATE TABLE IF NOT EXISTS public.email_imports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL CHECK (source IN ('email', 'admin_paste')),
    result text NOT NULL CHECK (
        result IN (
            'imported', 'updated', 'unchanged', 'no_shipment',
            'rejected', 'failed'
        )
    ),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
    -- SHA-256 of the Message-ID header, so a re-delivered email is skipped.
    message_id_hash text UNIQUE
        CHECK (message_id_hash IS NULL OR message_id_hash ~ '^[0-9a-f]{64}$'),
    sender_domain text CHECK (sender_domain IS NULL OR length(sender_domain) <= 255),
    tracking_ids text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS email_imports_received_idx
    ON public.email_imports (received_at DESC);

ALTER TABLE public.email_imports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.email_imports FROM anon, authenticated;
GRANT ALL ON TABLE public.email_imports TO service_role;

-- 3. Create a parcel and its first timeline event in one transaction.
CREATE OR REPLACE FUNCTION public.create_parcel_from_email(
    p_owner_id uuid,
    p_tracking_id text,
    p_status text,
    p_eta timestamptz,
    p_destination text,
    p_courier text,
    p_order_id text,
    p_awb_number text,
    p_items text[],
    p_description text
)
RETURNS SETOF public.parcels
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    created_parcel public.parcels%ROWTYPE;
BEGIN
    INSERT INTO public.parcels (
        owner_id, tracking_id, status, eta, destination,
        courier, order_id, awb_number, items, source
    )
    VALUES (
        p_owner_id, p_tracking_id, p_status, p_eta, p_destination,
        p_courier, p_order_id, p_awb_number, coalesce(p_items, '{}'), 'email'
    )
    RETURNING * INTO created_parcel;

    INSERT INTO public.parcel_events (parcel_id, status, description, occurred_at)
    VALUES (created_parcel.id, p_status, p_description, now());

    RETURN NEXT created_parcel;
END;
$$;

-- 4. Update a parcel from a later email. Only values the email contains are
-- written (NULL means "keep the current value"). A timeline event is added
-- only when p_status is given, which the app does only on a real change.
CREATE OR REPLACE FUNCTION public.apply_email_update(
    p_owner_id uuid,
    p_parcel_id uuid,
    p_status text,
    p_eta timestamptz,
    p_destination text,
    p_courier text,
    p_order_id text,
    p_awb_number text,
    p_items text[],
    p_description text
)
RETURNS SETOF public.parcels
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    updated_parcel public.parcels%ROWTYPE;
BEGIN
    UPDATE public.parcels
    SET status = coalesce(p_status, status),
        eta = coalesce(p_eta, eta),
        destination = coalesce(p_destination, destination),
        courier = coalesce(p_courier, courier),
        order_id = coalesce(order_id, p_order_id),
        awb_number = coalesce(awb_number, p_awb_number),
        items = CASE WHEN cardinality(coalesce(p_items, '{}')) > 0
                     THEN p_items ELSE items END,
        updated_at = now()
    WHERE id = p_parcel_id
      AND owner_id = p_owner_id
    RETURNING * INTO updated_parcel;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Parcel not found'
            USING ERRCODE = 'P0002';
    END IF;

    IF p_status IS NOT NULL THEN
        INSERT INTO public.parcel_events (parcel_id, status, description, occurred_at)
        VALUES (updated_parcel.id, p_status, p_description, now());
    END IF;

    RETURN NEXT updated_parcel;
END;
$$;

REVOKE ALL ON FUNCTION public.create_parcel_from_email(
    uuid, text, text, timestamptz, text, text, text, text, text[], text
) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_email_update(
    uuid, uuid, text, timestamptz, text, text, text, text, text[], text
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.create_parcel_from_email(
    uuid, text, text, timestamptz, text, text, text, text, text[], text
) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_email_update(
    uuid, uuid, text, timestamptz, text, text, text, text, text[], text
) TO service_role;
