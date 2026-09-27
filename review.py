import streamlit as st
from dotenv import load_dotenv

from db import get_client
from review_data import get_review_queue, submit_review

load_dotenv()

st.title("Competitive Intelligence Sentinel — Insight Review")

client = get_client()
queue = get_review_queue(client)

decided = st.session_state.setdefault("decided", {})


def render_header_and_evidence(item):
    st.subheader(f"{item['competitor_name']} — materiality {item['materiality_score']}/10 ({item['confidence']})")
    st.write(item["rationale"])
    with st.expander("Evidence (raw diff)", expanded=True):
        if not item["signals"]:
            st.warning("No evidence linked — cannot verify this insight.")
        for signal in item["signals"]:
            st.caption(f"Theme: {signal['theme']}")
            st.code(signal["diff_text"])
            st.write(signal["summary"])


# Insights whose status update already succeeded but whose feedback save
# failed: rendered from the cached item, since the status change already
# moved them out of get_review_queue()'s pending-only result.
for insight_id, pending in list(decided.items()):
    item = pending["item"]
    with st.container(border=True):
        render_header_and_evidence(item)

        rating = st.selectbox(
            "Feedback (optional)", ["", "useful", "not_useful", "incorrect"], key=f"rating_{insight_id}"
        )
        comment = st.text_input("Comment (optional)", key=f"comment_{insight_id}")

        st.warning(f"Already {pending['decision']}, but feedback wasn't saved.")
        if st.button("Retry saving feedback", key=f"retry_{insight_id}"):
            result = submit_review(
                client,
                insight_id,
                pending["decision"],
                rating=rating or None,
                comment=comment or None,
                skip_status_update=True,
            )
            if result["feedback_saved"] is False:
                st.warning(f"Still failed to save feedback: {result['error']}")
            else:
                st.success("Feedback saved.")
                del decided[insight_id]
                st.rerun()

if not queue and not decided:
    st.info("Nothing to review — no pending insights.")
else:
    for item in queue:
        with st.container(border=True):
            render_header_and_evidence(item)

            rating = st.selectbox(
                "Feedback (optional)",
                ["", "useful", "not_useful", "incorrect"],
                key=f"rating_{item['insight_id']}",
            )
            comment = st.text_input("Comment (optional)", key=f"comment_{item['insight_id']}")

            col1, col2 = st.columns(2)
            if col1.button("Approve", key=f"approve_{item['insight_id']}"):
                result = submit_review(
                    client, item["insight_id"], "approved", rating=rating or None, comment=comment or None
                )
                if not result["status_updated"]:
                    st.error(f"Failed to approve: {result['error']}")
                elif result["feedback_saved"] is False:
                    decided[item["insight_id"]] = {"decision": "approved", "item": item}
                    st.warning(f"Approved, but feedback wasn't saved: {result['error']}")
                else:
                    st.success("Approved.")
                    st.rerun()
            if col2.button("Reject", key=f"reject_{item['insight_id']}"):
                result = submit_review(
                    client, item["insight_id"], "rejected", rating=rating or None, comment=comment or None
                )
                if not result["status_updated"]:
                    st.error(f"Failed to reject: {result['error']}")
                elif result["feedback_saved"] is False:
                    decided[item["insight_id"]] = {"decision": "rejected", "item": item}
                    st.warning(f"Rejected, but feedback wasn't saved: {result['error']}")
                else:
                    st.success("Rejected.")
                    st.rerun()
