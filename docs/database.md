# Database setup

How to build the Supabase database and create local test keys.

The full schema is version-controlled in `supabase/`. To build a new, empty
Supabase project, run these in the SQL Editor in this order:

1. [`supabase/schema.sql`](../supabase/schema.sql): base tables, indexes,
   row-level security and service-role grants.
2. Every file in `supabase/migrations/`, oldest timestamp first.
3. [`supabase/seed.sql`](../supabase/seed.sql): the three `DEMO-PARCEL-*`
   sample parcels. It skips rows that already exist.

If your project already has all of this applied, do not re-run
it there.

## Rate-limit migration

Before testing the parcel API against Supabase, inspect the existing
`rate_limits` columns with this query in the Supabase SQL Editor:

```sql
SELECT
    ordinal_position,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'rate_limits'
ORDER BY ordinal_position;
```

Then run the complete migration at
[`supabase/migrations/20261126100000_rate_limits_and_parcel_operations.sql`](../supabase/migrations/20261126100000_rate_limits_and_parcel_operations.sql)
in the Supabase SQL Editor. It preserves existing API-key counters, adds
hashed-client-IP counters, and uses atomic PostgreSQL upserts. It also adds
transactional database functions used to create parcels with their initial
event and update statuses with their timeline events. It does not recreate or
seed any tables.

To create local test credentials for the seeded sample owner, run this once
after the migration:

```powershell
python scripts\provision_phase2_test_keys.py
```

This generates random read-only and read/write keys, stores only their hashes
in Supabase, and writes the local test keys to the ignored `.env.local` file
without displaying them. These keys are only for local testing; revoke them in
Supabase when finished.
