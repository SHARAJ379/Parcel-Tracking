import hashlib
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv, set_key
from supabase import create_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env.local"
TEST_OWNER_ID = "20000000-0000-4000-8000-000000000001"


def main() -> None:
    load_dotenv(ENV_FILE, override=True)
    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")

    client = create_client(supabase_url, service_role_key)
    keys = {
        "PARCEL_TEST_READ_API_KEY": ("Phase 2 local read test", ["read"]),
        "PARCEL_TEST_WRITE_API_KEY": (
            "Phase 2 local read-write test",
            ["read", "write"],
        ),
    }

    for name, _ in keys.values():
        client.table("api_keys").update(
            {"revoked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("owner_id", TEST_OWNER_ID).eq("name", name).is_(
            "revoked_at", "null"
        ).execute()

    for env_name, (name, scopes) in keys.items():
        raw_key = "pt_test_" + secrets.token_urlsafe(32)
        client.table("api_keys").insert(
            {
                "owner_id": TEST_OWNER_ID,
                "name": name,
                "key_hash": hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
                "key_prefix": raw_key[:12],
                "scopes": scopes,
            }
        ).execute()
        set_key(ENV_FILE, env_name, raw_key)

    print("Local test keys were saved to .env.local; raw keys were not displayed.")


if __name__ == "__main__":
    main()
