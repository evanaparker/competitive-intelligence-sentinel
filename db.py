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


def get_material_signals(client: Client) -> list[dict]:
    response = (
        client.table("signals")
        .select("id, snapshot_id, theme, summary, diff_text")
        .eq("classification", "material")
        .order("created_at")
        .execute()
    )
    return response.data


def get_signal_ids_with_insight(client: Client) -> set[str]:
    response = client.table("insight_signals").select("signal_id").execute()
    return {row["signal_id"] for row in response.data}


def get_snapshot(client: Client, snapshot_id: str) -> dict:
    response = (
        client.table("snapshots")
        .select("id, source_id, content_hash, fetched_at")
        .eq("id", snapshot_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"snapshot {snapshot_id} not found")
    return response.data[0]


def get_source(client: Client, source_id: str) -> dict:
    response = (
        client.table("sources")
        .select("id, url, competitor_id, source_type")
        .eq("id", source_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"source {source_id} not found")
    return response.data[0]


def get_competitor(client: Client, competitor_id: str) -> dict:
    response = (
        client.table("competitors")
        .select("id, name")
        .eq("id", competitor_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"competitor {competitor_id} not found")
    return response.data[0]


def insert_insight(
    client: Client, competitor_id: str, materiality_score: int, confidence: str, rationale: str
) -> dict:
    response = (
        client.table("insights")
        .insert(
            {
                "competitor_id": competitor_id,
                "materiality_score": materiality_score,
                "confidence": confidence,
                "rationale": rationale,
            }
        )
        .execute()
    )
    return response.data[0]


def insert_insight_signal(client: Client, insight_id: str, signal_id: str) -> dict:
    response = (
        client.table("insight_signals")
        .insert({"insight_id": insight_id, "signal_id": signal_id})
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
