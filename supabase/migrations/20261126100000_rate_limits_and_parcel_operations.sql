-- Preserve the existing API-key counters while allowing separate IP counters.
ALTER TABLE public.rate_limits
    ADD COLUMN IF NOT EXISTS client_ip_hash text;

-- The original primary key made api_key_id mandatory. Replace it with
-- partial unique indexes so exactly one kind of rate-limit subject is used.
DO $$
DECLARE
    primary_key_name name;
BEGIN
    SELECT constraint_row.conname
    INTO primary_key_name
    FROM pg_constraint AS constraint_row
    JOIN pg_attribute AS column_row
      ON column_row.attrelid = constraint_row.conrelid
     AND column_row.attnum = ANY (constraint_row.conkey)
    WHERE constraint_row.conrelid = 'public.rate_limits'::regclass
      AND constraint_row.contype = 'p'
      AND column_row.attname = 'api_key_id'
    LIMIT 1;

    IF primary_key_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.rate_limits DROP CONSTRAINT %I',
            primary_key_name
        );
    END IF;
END;
$$;

ALTER TABLE public.rate_limits
    ALTER COLUMN api_key_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.rate_limits'::regclass
          AND conname = 'rate_limits_exactly_one_subject_check'
    ) THEN
        ALTER TABLE public.rate_limits
            ADD CONSTRAINT rate_limits_exactly_one_subject_check
            CHECK ((api_key_id IS NOT NULL) <> (client_ip_hash IS NOT NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.rate_limits'::regclass
          AND conname = 'rate_limits_client_ip_hash_format_check'
    ) THEN
        ALTER TABLE public.rate_limits
            ADD CONSTRAINT rate_limits_client_ip_hash_format_check
            CHECK (
                client_ip_hash IS NULL
                OR client_ip_hash ~ '^[0-9a-f]{64}$'
            );
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS rate_limits_api_key_window_unique
    ON public.rate_limits (api_key_id, window_start)
    WHERE api_key_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rate_limits_client_ip_window_unique
    ON public.rate_limits (client_ip_hash, window_start)
    WHERE client_ip_hash IS NOT NULL;

-- Incrementing in the database makes concurrent requests count atomically.
CREATE OR REPLACE FUNCTION public.consume_rate_limit(
    p_api_key_id uuid,
    p_client_ip_hash text,
    p_window_start timestamptz,
    p_limit integer
)
RETURNS TABLE (request_count integer, allowed boolean)
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    current_count integer;
BEGIN
    IF (p_api_key_id IS NOT NULL) = (p_client_ip_hash IS NOT NULL) THEN
        RAISE EXCEPTION 'Exactly one rate-limit subject is required'
            USING ERRCODE = '22023';
    END IF;

    IF p_limit < 1 THEN
        RAISE EXCEPTION 'Rate limit must be positive'
            USING ERRCODE = '22023';
    END IF;

    IF p_api_key_id IS NOT NULL THEN
        INSERT INTO public.rate_limits AS rate_limit (
            api_key_id, client_ip_hash, window_start, request_count
        )
        VALUES (p_api_key_id, NULL, p_window_start, 1)
        ON CONFLICT (api_key_id, window_start)
            WHERE api_key_id IS NOT NULL
        DO UPDATE
            SET request_count = rate_limit.request_count + 1
        RETURNING rate_limit.request_count INTO current_count;
    ELSE
        INSERT INTO public.rate_limits AS rate_limit (
            api_key_id, client_ip_hash, window_start, request_count
        )
        VALUES (NULL, p_client_ip_hash, p_window_start, 1)
        ON CONFLICT (client_ip_hash, window_start)
            WHERE client_ip_hash IS NOT NULL
        DO UPDATE
            SET request_count = rate_limit.request_count + 1
        RETURNING rate_limit.request_count INTO current_count;
    END IF;

    RETURN QUERY SELECT current_count, current_count <= p_limit;
END;
$$;

-- Parcel writes and their timeline events must commit or roll back together.
CREATE OR REPLACE FUNCTION public.create_parcel_with_event(
    p_owner_id uuid,
    p_tracking_id text,
    p_destination text,
    p_eta timestamptz DEFAULT NULL
)
RETURNS SETOF public.parcels
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    created_parcel public.parcels%ROWTYPE;
BEGIN
    INSERT INTO public.parcels (
        owner_id, tracking_id, status, eta, destination
    )
    VALUES (
        p_owner_id, p_tracking_id, 'created', p_eta, p_destination
    )
    RETURNING * INTO created_parcel;

    INSERT INTO public.parcel_events (
        parcel_id, status, description, occurred_at
    )
    VALUES (
        created_parcel.id, 'created', 'Parcel created', now()
    );

    RETURN NEXT created_parcel;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_parcel_status_with_event(
    p_owner_id uuid,
    p_tracking_id text,
    p_status text,
    p_description text,
    p_location text DEFAULT NULL
)
RETURNS SETOF public.parcels
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    updated_parcel public.parcels%ROWTYPE;
BEGIN
    IF p_status NOT IN (
        'created',
        'picked_up',
        'in_transit',
        'out_for_delivery',
        'delivered',
        'delayed',
        'failed_delivery'
    ) THEN
        RAISE EXCEPTION 'Unsupported parcel status'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.parcels
    SET status = p_status,
        updated_at = now()
    WHERE owner_id = p_owner_id
      AND tracking_id = p_tracking_id
    RETURNING * INTO updated_parcel;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Parcel not found'
            USING ERRCODE = 'P0002';
    END IF;

    INSERT INTO public.parcel_events (
        parcel_id, status, description, location, occurred_at
    )
    VALUES (
        updated_parcel.id, p_status, p_description, p_location, now()
    );

    RETURN NEXT updated_parcel;
END;
$$;

REVOKE ALL ON FUNCTION public.consume_rate_limit(uuid, text, timestamptz, integer)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.create_parcel_with_event(uuid, text, text, timestamptz)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.update_parcel_status_with_event(uuid, text, text, text, text)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.consume_rate_limit(uuid, text, timestamptz, integer)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.create_parcel_with_event(uuid, text, text, timestamptz)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.update_parcel_status_with_event(uuid, text, text, text, text)
    TO service_role;
