-- Sample parcels and timeline events (owner 20000000-...-0001).
-- Copied unchanged from the original project. Times are relative to now().
-- Safe to re-run: existing rows are skipped (on conflict do nothing).

insert into public.parcels (
  id, owner_id, tracking_id, status, eta, destination, created_at, updated_at
)
values
  (
    '10000000-0000-4000-8000-000000000001',
    '20000000-0000-4000-8000-000000000001',
    'DEMO-PARCEL-001',
    'in_transit',
    now() + interval '2 days',
    'Pune, Maharashtra',
    now() - interval '2 days',
    now() - interval '6 hours'
  ),
  (
    '10000000-0000-4000-8000-000000000002',
    '20000000-0000-4000-8000-000000000001',
    'DEMO-PARCEL-002',
    'out_for_delivery',
    now() + interval '3 hours',
    'Bengaluru, Karnataka',
    now() - interval '1 day',
    now() - interval '1 hour'
  ),
  (
    '10000000-0000-4000-8000-000000000003',
    '20000000-0000-4000-8000-000000000001',
    'DEMO-PARCEL-003',
    'delivered',
    now() - interval '1 day',
    'Mumbai, Maharashtra',
    now() - interval '3 days',
    now() - interval '1 day'
  )
on conflict (tracking_id) do nothing;

insert into public.parcel_events (
  id, parcel_id, status, description, location, occurred_at
)
values
  (
    '30000000-0000-4000-8000-000000000001',
    '10000000-0000-4000-8000-000000000001',
    'created',
    'Shipment information received.',
    'Mumbai, Maharashtra',
    now() - interval '2 days'
  ),
  (
    '30000000-0000-4000-8000-000000000002',
    '10000000-0000-4000-8000-000000000001',
    'picked_up',
    'Parcel picked up from the sender.',
    'Mumbai, Maharashtra',
    now() - interval '1 day 18 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000003',
    '10000000-0000-4000-8000-000000000001',
    'in_transit',
    'Parcel is moving through the delivery network.',
    'Nashik, Maharashtra',
    now() - interval '6 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000004',
    '10000000-0000-4000-8000-000000000002',
    'created',
    'Shipment information received.',
    'Chennai, Tamil Nadu',
    now() - interval '1 day'
  ),
  (
    '30000000-0000-4000-8000-000000000005',
    '10000000-0000-4000-8000-000000000002',
    'picked_up',
    'Parcel picked up from the sender.',
    'Chennai, Tamil Nadu',
    now() - interval '20 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000006',
    '10000000-0000-4000-8000-000000000002',
    'in_transit',
    'Parcel arrived at the local delivery facility.',
    'Bengaluru, Karnataka',
    now() - interval '8 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000007',
    '10000000-0000-4000-8000-000000000002',
    'out_for_delivery',
    'Parcel is with the local courier for delivery.',
    'Bengaluru, Karnataka',
    now() - interval '1 hour'
  ),
  (
    '30000000-0000-4000-8000-000000000008',
    '10000000-0000-4000-8000-000000000003',
    'created',
    'Shipment information received.',
    'Hyderabad, Telangana',
    now() - interval '3 days'
  ),
  (
    '30000000-0000-4000-8000-000000000009',
    '10000000-0000-4000-8000-000000000003',
    'picked_up',
    'Parcel picked up from the sender.',
    'Hyderabad, Telangana',
    now() - interval '2 days 18 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000010',
    '10000000-0000-4000-8000-000000000003',
    'in_transit',
    'Parcel arrived at the local delivery facility.',
    'Mumbai, Maharashtra',
    now() - interval '1 day 6 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000011',
    '10000000-0000-4000-8000-000000000003',
    'out_for_delivery',
    'Parcel is with the local courier for delivery.',
    'Mumbai, Maharashtra',
    now() - interval '1 day 3 hours'
  ),
  (
    '30000000-0000-4000-8000-000000000012',
    '10000000-0000-4000-8000-000000000003',
    'delivered',
    'Parcel delivered to the recipient.',
    'Mumbai, Maharashtra',
    now() - interval '1 day'
  )
on conflict (id) do nothing;
