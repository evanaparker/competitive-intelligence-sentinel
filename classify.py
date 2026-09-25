import sys

from dotenv import load_dotenv

from classifier import classify_diff, get_client as get_openai_client
from db import (
    get_client as get_supabase_client,
    get_active_sources,
    get_two_latest_snapshots,
    has_signal_for_snapshot,
    insert_signal,
)
from diffing import compute_diff


def run(client) -> list[dict]:
    results = []
    for source in get_active_sources(client):
        url = source["url"]
        try:
            snaps = get_two_latest_snapshots(client, source["id"])
            if len(snaps) < 2:
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            newest, prior = snaps[0], snaps[1]
            if newest["content_hash"] == prior["content_hash"]:
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            if has_signal_for_snapshot(client, newest["id"]):
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            diff_text = compute_diff(prior["content"], newest["content"])
            result = classify_diff(diff_text, source["source_type"])
            insert_signal(
                client,
                newest["id"],
                prior["id"],
                diff_text,
                result["classification"],
                result["theme"],
                result["summary"],
            )
            results.append(
                {"source_url": url, "status": "classified", "classification": result["classification"], "error": None}
            )
        except Exception as e:
            results.append(
                {"source_url": url, "status": "error", "classification": None, "error": f"{type(e).__name__}: {e}"}
            )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR      {r['source_url']}: {r['error']}"
    elif r["status"] == "skipped":
        return f"SKIPPED    {r['source_url']}"
    else:
        return f"CLASSIFIED {r['source_url']}: {r['classification']}"


if __name__ == "__main__":
    load_dotenv()
    get_openai_client()  # precondition: fail fast, before touching any source
    supabase_client = get_supabase_client()
    results = run(supabase_client)
    for r in results:
        print(format_line(r))
    sys.exit(1 if any(r["error"] is not None for r in results) else 0)
