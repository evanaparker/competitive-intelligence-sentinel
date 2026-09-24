import sys

from dotenv import load_dotenv

from db import get_client, get_active_sources, get_latest_snapshot_hash, insert_snapshot
from extract import extract_text
from fetch import fetch
from hashing import compute_hash


def run(client) -> list[dict]:
    results = []
    for source in get_active_sources(client):
        url = source["url"]
        try:
            html = fetch(url)
            text = extract_text(html)
            content_hash = compute_hash(text)
            prior_hash = get_latest_snapshot_hash(client, source["id"])
            changed = prior_hash is None or prior_hash != content_hash
            insert_snapshot(client, source["id"], text, content_hash)
            results.append({"url": url, "changed": changed, "error": None})
        except Exception as e:
            results.append({"url": url, "changed": False, "error": f"{type(e).__name__}: {e}"})
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR   {r['url']}: {r['error']}"
    elif r["changed"]:
        return f"CHANGED {r['url']}"
    else:
        return f"SAME    {r['url']}"


if __name__ == "__main__":
    load_dotenv()
    client = get_client()
    results = run(client)
    for r in results:
        print(format_line(r))
    sys.exit(1 if any(r["error"] is not None for r in results) else 0)
