import os

from supabase import Client, create_client


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example)"
        )
    return create_client(url, key)


def get_active_sources(client: Client) -> list[dict]:
    response = (
        client.table("sources")
        .select("id, url, competitor_id, source_type")
        .eq("is_active", True)
        .execute()
    )
    return response.data


def get_latest_snapshot_hash(client: Client, source_id: str) -> str | None:
    response = (
        client.table("snapshots")
        .select("content_hash")
        .eq("source_id", source_id)
        .order("fetched_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]["content_hash"]


def insert_snapshot(client: Client, source_id: str, content: str, content_hash: str) -> dict:
    response = (
        client.table("snapshots")
        .insert({"source_id": source_id, "content": content, "content_hash": content_hash})
        .execute()
    )
    return response.data[0]


def get_snapshots_for_source(client: Client, source_id: str) -> list[dict]:
    response = (
        client.table("snapshots")
        .select("id, content, content_hash, fetched_at")
        .eq("source_id", source_id)
        .order("fetched_at", desc=True)
        .execute()
    )
    return response.data


def has_signal_for_snapshot(client: Client, snapshot_id: str) -> bool:
    response = (
        client.table("signals")
        .select("id")
        .eq("snapshot_id", snapshot_id)
        .limit(1)
        .execute()
    )
    return len(response.data) > 0


def insert_signal(
    client: Client,
    snapshot_id: str,
    prior_snapshot_id: str,
    diff_text: str,
    classification: str,
    theme: str,
    summary: str,
) -> dict:
    response = (
        client.table("signals")
        .insert(
            {
                "snapshot_id": snapshot_id,
                "prior_snapshot_id": prior_snapshot_id,
                "diff_text": diff_text,
                "classification": classification,
                "theme": theme,
                "summary": summary,
            }
        )
        .execute()
    )
    return response.data[0]
