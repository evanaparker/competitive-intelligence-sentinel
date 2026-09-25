import sys

from dotenv import load_dotenv

from classifier import classify_diff, get_client as get_openai_client
from db import (
    get_client as get_supabase_client,
    get_active_sources,
    get_snapshots_for_source,
    has_signal_for_snapshot,
    insert_signal,
)
from diffing import compute_diff


def run(client, openai_client=None) -> list[dict]:
    results = []
    for source in get_active_sources(client):
        url = source["url"]
        try:
            snaps = get_snapshots_for_source(client, source["id"])
            if len(snaps) < 2:
                results.append(
                    {"source_url": url, "status": "skipped", "reason": "insufficient_history", "classification": None, "error": None}
                )
                continue
            newest = snaps[0]
            if has_signal_for_snapshot(client, newest["id"]):
                results.append(
                    {"source_url": url, "status": "skipped", "reason": "already_classified", "classification": None, "error": None}
                )
                continue
            # Walk back to the last snapshot that was already used as the
            # "newest" side of a signal — not just the immediately-prior
            # snapshot — so a change is never silently lost because a
            # classify.py run was missed between two ingest.py runs. If
            # nothing has ever been classified for this source, the oldest
            # snapshot anchors the very first comparison.
            baseline = None
            for snap in snaps[1:]:
                if has_signal_for_snapshot(client, snap["id"]):
                    baseline = snap
                    break
            if baseline is None:
                baseline = snaps[-1]
            if newest["content_hash"] == baseline["content_hash"]:
                results.append(
                    {"source_url": url, "status": "skipped", "reason": "unchanged", "classification": None, "error": None}
                )
                continue
            diff_text = compute_diff(baseline["content"], newest["content"])
            result = classify_diff(diff_text, source["source_type"], client=openai_client)
            insert_signal(
                client,
                newest["id"],
                baseline["id"],
                diff_text,
                result["classification"],
                result["theme"],
                result["summary"],
            )
            results.append(
                {"source_url": url, "status": "classified", "reason": None, "classification": result["classification"], "error": None}
            )
        except Exception as e:
            results.append(
                {"source_url": url, "status": "error", "reason": None, "classification": None, "error": f"{type(e).__name__}: {e}"}
            )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR      {r['source_url']}: {r['error']}"
    elif r["status"] == "skipped":
        return f"SKIPPED    {r['source_url']} ({r['reason']})"
    else:
        return f"CLASSIFIED {r['source_url']}: {r['classification']}"


if __name__ == "__main__":
    load_dotenv()
    openai_client = get_openai_client()  # precondition: fail fast, before touching any source
    supabase_client = get_supabase_client()
    try:
        results = run(supabase_client, openai_client=openai_client)
        for r in results:
            print(format_line(r))
        sys.exit(1 if any(r["error"] is not None for r in results) else 0)
    finally:
        openai_client.close()
