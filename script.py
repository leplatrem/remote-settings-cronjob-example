import os
import sys

import requests
import sentry_sdk
from kinto_http import Client, KintoException
from kinto_http.utils import collection_diff
from sentry_sdk.integrations.gcp import GcpIntegration


# Required environment variables
AUTHORIZATION = os.getenv("AUTHORIZATION", "")
SERVER = os.getenv("SERVER", "http://localhost:8888/v1")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local").lower()
IS_DRY_RUN = os.getenv("DRY_RUN", "0") in "1yY"
SENTRY_DSN = os.getenv("SENTRY_DSN")
SENTRY_ENV = os.getenv("SENTRY_ENV", ENVIRONMENT)

if ENVIRONMENT not in {"local", "dev", "stage", "prod"}:
    raise ValueError(f"'ENVIRONMENT={ENVIRONMENT}' is not a valid value")

# Constants for collection
BUCKET = "main-workspace"
COLLECTION = "fxrelay-allowlist"
ALLOWLIST_INPUT_URL = os.getenv("ALLOWLIST_INPUT_URL")


def fetch_allowlist():
    print(f"📥 Loading new allowlist from {ALLOWLIST_INPUT_URL}")
    response = requests.get(ALLOWLIST_INPUT_URL, timeout=30)
    response.raise_for_status()
    new_allowlist = response.content.decode()
    domains = set(filter(None, new_allowlist.split("\n")))
    print(f"📋 Parsed {len(domains)} domains.")
    return [{"id": domain.replace(".", "-"), "domain": domain} for domain in sorted(domains)]


def main():
    if SENTRY_DSN:
        # Initialize Sentry for error reporting.
        sentry_sdk.init(SENTRY_DSN, integrations=[GcpIntegration()], environment=SENTRY_ENV)
    else:
        print("⚠️ Sentry is not configured. Set SENTRY_DSN environment variable to enable it.")

    client = Client(
        server_url=SERVER,
        auth=AUTHORIZATION,
        bucket=BUCKET,
        collection=COLLECTION,
        dry_mode=IS_DRY_RUN,
    )

    try:
        print("🔐 Checking credentials...", end="")
        server_info = client.server_info()
        print("✅")
        if "user" in server_info:
            print(f"👤 Logged in as {server_info['user']['id']}")
        else:
            print("⚠️ Anonymous access")
    except Exception as e:
        print(f"❌ Failed to connect to Remote Settings server: {e}")
        return 1

    print("📥 Fetching new allowlist records...")
    source_records = fetch_allowlist()

    print("📥 Fetching current destination records...")
    try:
        dest_records = client.get_records()
    except KintoException as e:
        print(f"❌ Failed to fetch existing records: {e}")
        return 1

    # Compute the diff
    to_create, to_update, to_delete = collection_diff(source_records, dest_records)

    has_changes = to_create or to_update or to_delete
    if not has_changes:
        print("✅ Records are already in sync. Nothing to do.")
        return 0

    print(
        f"🔧 Applying {len(to_create)} creates, {len(to_update)} updates, {len(to_delete)} deletes..."
    )
    try:
        with client.batch() as batch:
            for record in to_create:
                batch.create_record(data=record)
            for _, new in to_update:
                new.pop("last_modified", None)
                batch.update_record(data=new)
            for record in to_delete:
                batch.delete_record(id=record["id"])
        print("✅ Batch operations applied.")
    except KintoException as e:
        print(f"❌ Failed to apply changes: {e}")
        return 1

    try:
        if ENVIRONMENT == "dev":
            print("🟢 Self-approving changes on dev...")
            print("✅ Changes self-approved.")
        else:
            print("📤 Requesting review...")
            client.request_review(message="r?")
            print("✅ Review requested.")
    except KintoException as e:
        print(f"❌ Failed to update collection status: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
