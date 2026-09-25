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
    for signal in pending:
        signal_id = signal["id"]
        try:
            snapshot = get_snapshot(client, signal["snapshot_id"])
            # Resolved by id directly, not filtered through active-only
            # sources: a source deactivated after generating a signal must
            # not permanently wedge that signal's scoring.
            source = get_source(client, snapshot["source_id"])
            competitor = get_competitor(client, source["competitor_id"])
            signal_context = {
                "diff_text": signal["diff_text"],
                "theme": signal["theme"],
                "summary": signal["summary"],
                "competitor_name": competitor["name"],
                "source_type": source["source_type"],
            }
            result = score_signal(signal_context, client=openai_client)
            if not 1 <= result["materiality_score"] <= 10:
                raise ValueError(
                    f"materiality_score {result['materiality_score']} outside 1-10"
                )
            insight = insert_insight(
                client,
                source["competitor_id"],
                result["materiality_score"],
                result["confidence"],
                result["rationale"],
            )
            try:
                insert_insight_signal(client, insight["id"], signal_id)
            except Exception as link_error:
                # Not atomic with insert_insight above: the insight above
                # already exists in the DB and is NOT deleted here (known
                # limitation, see spec). Name its id so it's findable.
                raise RuntimeError(
                    f"insight {insight['id']} created but failed to link to signal {signal_id}: "
                    f"{type(link_error).__name__}: {link_error}"
                ) from link_error
            results.append(
                {"signal_id": signal_id, "status": "scored", "materiality_score": result["materiality_score"], "error": None}
            )
        except Exception as e:
            results.append(
                {"signal_id": signal_id, "status": "error", "materiality_score": None, "error": f"{type(e).__name__}: {e}"}
            )
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
