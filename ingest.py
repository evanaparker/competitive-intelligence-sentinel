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
            results.append({"url": url, "changed": False, "error": str(e)})
    return results


if __name__ == "__main__":
    load_dotenv()
    client = get_client()
    results = run(client)
    for r in results:
        if r["error"]:
            print(f"ERROR   {r['url']}: {r['error']}")
        elif r["changed"]:
            print(f"CHANGED {r['url']}")
        else:
            print(f"SAME    {r['url']}")
    sys.exit(0)
