import sys
import uuid

from dotenv import load_dotenv

from correlator import cluster_by_time_window, judge_correlation, window_has_closed
from db import (
    get_client as get_supabase_client,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_source,
    get_competitor,
    assign_correlation_group,
)
from llm import get_client as get_openai_client


def run(client, openai_client=None) -> list[dict]:
    results = []
    already_insighted = get_signal_ids_with_insight(client)
    pending = [
        s
        for s in get_material_signals(client)
        if s["id"] not in already_insighted and s["correlation_group_id"] is None
    ]

    # Resolve each signal's competitor and source_type up front — cluster_by_time_window
    # and judge_correlation both need competitor_name/source_type already attached,
    # and grouping-by-competitor needs competitor_id regardless of cluster size.
    by_competitor: dict[str, list[dict]] = {}
    for signal in pending:
        try:
            snapshot = get_snapshot(client, signal["snapshot_id"])
            source = get_source(client, snapshot["source_id"])
            competitor = get_competitor(client, source["competitor_id"])
            competitor_id = source["competitor_id"]
            enriched = {**signal, "competitor_name": competitor["name"], "source_type": source["source_type"]}
            by_competitor.setdefault(competitor_id, []).append(enriched)
        except Exception as e:
            results.append(
                {
                    "competitor_id": None,
                    "signal_ids": [signal["id"]],
                    "status": "error",
                    "group_id": None,
                    "error": f"{type(e).__name__}: {e}",
                }
            )

    for competitor_id, comp_signals in by_competitor.items():
        for cluster in cluster_by_time_window(comp_signals):
            signal_ids = [s["id"] for s in cluster]
            # Every DB write below is inside this try, not just the LLM call:
            # a transient write failure must produce an "error" result for
            # THIS cluster only, never abort the run and discard results
            # already computed (and already committed) for earlier clusters.
            try:
                if len(cluster) == 1:
                    signal = cluster[0]
                    if not window_has_closed(signal):
                        # A companion could still arrive within the window —
                        # deciding now would permanently foreclose grouping
                        # it with a signal from a later run. Leave it NULL;
                        # it's picked up again (and re-clustered) next run.
                        results.append(
                            {
                                "competitor_id": competitor_id,
                                "signal_ids": signal_ids,
                                "status": "pending_window",
                                "group_id": None,
                                "error": None,
                            }
                        )
                        continue
                    assign_correlation_group(client, signal_ids, signal_ids[0])
                    results.append(
                        {
                            "competitor_id": competitor_id,
                            "signal_ids": signal_ids,
                            "status": "singleton",
                            "group_id": signal_ids[0],
                            "error": None,
                        }
                    )
                    continue
                correlated = judge_correlation(cluster, client=openai_client)
                if correlated:
                    group_id = str(uuid.uuid4())
                    assign_correlation_group(client, signal_ids, group_id)
                    results.append(
                        {
                            "competitor_id": competitor_id,
                            "signal_ids": signal_ids,
                            "status": "grouped",
                            "group_id": group_id,
                            "error": None,
                        }
                    )
                else:
                    # Each signal gets its OWN id — a decision, not a group.
                    # Never a shared id: they were judged NOT to belong together.
                    for signal_id in signal_ids:
                        assign_correlation_group(client, [signal_id], signal_id)
                    results.append(
                        {
                            "competitor_id": competitor_id,
                            "signal_ids": signal_ids,
                            "status": "ungrouped",
                            "group_id": None,
                            "error": None,
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "competitor_id": competitor_id,
                        "signal_ids": signal_ids,
                        "status": "error",
                        "group_id": None,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR     {r['competitor_id']}: {r['error']}"
    elif r["status"] == "grouped":
        return f"GROUPED   {r['competitor_id']}: {len(r['signal_ids'])} signals ({r['group_id']})"
    elif r["status"] == "ungrouped":
        return f"UNGROUPED {r['competitor_id']}: {len(r['signal_ids'])} signals (not correlated)"
    elif r["status"] == "pending_window":
        return f"PENDING   {r['competitor_id']}: 1 signal (window still open)"
    else:
        return f"SINGLETON {r['competitor_id']}: 1 signal"


if __name__ == "__main__":
    load_dotenv()
    openai_client = get_openai_client()  # precondition: fail fast, before touching any competitor
    supabase_client = get_supabase_client()
    try:
        results = run(supabase_client, openai_client=openai_client)
        for r in results:
            print(format_line(r))
        sys.exit(1 if any(r["error"] is not None for r in results) else 0)
    finally:
        openai_client.close()
