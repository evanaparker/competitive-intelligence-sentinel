import sys

from dotenv import load_dotenv

from db import (
    get_client as get_supabase_client,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_source,
    get_competitor,
    insert_insight,
    insert_insight_signal,
)
from llm import get_client as get_openai_client
from scorer import score_signal


def run(client, openai_client=None) -> list[dict]:
    results = []
    already_insighted = get_signal_ids_with_insight(client)
    pending = [s for s in get_material_signals(client) if s["id"] not in already_insighted]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for signal in pending:
        # A null correlation_group_id means correlate.py never decided this
        # signal (never ran, or errored on it) — falls back to the signal's
        # OWN id as its group key, so it becomes its own singleton group.
        # Two different null-group signals therefore get two different
        # fallback keys (their own distinct ids), never merged together.
        key = signal["correlation_group_id"] or signal["id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(signal)

    for key in order:
        group_signals = groups[key]
        signal_ids = [s["id"] for s in group_signals]
        try:
            contexts = []
            competitor_id = None
            for signal in group_signals:
                snapshot = get_snapshot(client, signal["snapshot_id"])
                # Resolved by id directly, not filtered through active-only
                # sources: a source deactivated after generating a signal
                # must not permanently wedge that signal's scoring.
                source = get_source(client, snapshot["source_id"])
                competitor = get_competitor(client, source["competitor_id"])
                competitor_id = source["competitor_id"]
                contexts.append(
                    {
                        "diff_text": signal["diff_text"],
                        "theme": signal["theme"],
                        "summary": signal["summary"],
                        "competitor_name": competitor["name"],
                        "source_type": source["source_type"],
                    }
                )
            result = score_signal(contexts, client=openai_client)
            if not 1 <= result["materiality_score"] <= 10:
                raise ValueError(f"materiality_score {result['materiality_score']} outside 1-10")
            insight = insert_insight(
                client,
                competitor_id,
                result["materiality_score"],
                result["confidence"],
                result["rationale"],
            )
            linked = []
            try:
                for signal_id in signal_ids:
                    insert_insight_signal(client, insight["id"], signal_id)
                    linked.append(signal_id)
            except Exception as link_error:
                unlinked = [sid for sid in signal_ids if sid not in linked]
                raise RuntimeError(
                    f"insight {insight['id']} created and linked to {linked} but failed to link "
                    f"{unlinked[0]}: {type(link_error).__name__}: {link_error}"
                ) from link_error
            for signal_id in signal_ids:
                results.append(
                    {
                        "signal_id": signal_id,
                        "status": "scored",
                        "materiality_score": result["materiality_score"],
                        "error": None,
                    }
                )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            for signal_id in signal_ids:
                results.append({"signal_id": signal_id, "status": "error", "materiality_score": None, "error": err})
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR  {r['signal_id']}: {r['error']}"
    else:
        return f"SCORED {r['signal_id']}: {r['materiality_score']}"


if __name__ == "__main__":
    load_dotenv()
    openai_client = get_openai_client()  # precondition: fail fast, before touching any signal
    supabase_client = get_supabase_client()
    try:
        results = run(supabase_client, openai_client=openai_client)
        for r in results:
            print(format_line(r))
        sys.exit(1 if any(r["error"] is not None for r in results) else 0)
    finally:
        openai_client.close()
