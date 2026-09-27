from db import (
    get_pending_insights,
    get_signal_ids_for_insight,
    get_signal,
    get_competitor,
    update_insight_status,
    insert_feedback,
)


def get_review_queue(client) -> list[dict]:
    queue = []
    for insight in get_pending_insights(client):
        competitor = get_competitor(client, insight["competitor_id"])
        signal_ids = get_signal_ids_for_insight(client, insight["id"])
        signals = [get_signal(client, sid) for sid in signal_ids]
        queue.append(
            {
                "insight_id": insight["id"],
                "competitor_name": competitor["name"],
                "materiality_score": insight["materiality_score"],
                "confidence": insight["confidence"],
                "rationale": insight["rationale"],
                "signals": [
                    {"diff_text": s["diff_text"], "theme": s["theme"], "summary": s["summary"]} for s in signals
                ],
            }
        )
    return queue


def submit_review(
    client, insight_id: str, decision: str, rating: str | None = None, comment: str | None = None
) -> dict:
    try:
        update_insight_status(client, insight_id, decision)
    except Exception as e:
        return {"status_updated": False, "feedback_saved": None, "error": f"{type(e).__name__}: {e}"}

    if rating is None:
        return {"status_updated": True, "feedback_saved": None, "error": None}

    try:
        insert_feedback(client, insight_id, rating, comment)
        return {"status_updated": True, "feedback_saved": True, "error": None}
    except Exception as e:
        return {"status_updated": True, "feedback_saved": False, "error": f"{type(e).__name__}: {e}"}
