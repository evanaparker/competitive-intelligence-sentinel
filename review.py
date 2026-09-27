import streamlit as st
from dotenv import load_dotenv

from db import get_client
from review_data import get_review_queue, submit_review

load_dotenv()

st.title("Competitive Intelligence Sentinel — Insight Review")

client = get_client()
queue = get_review_queue(client)

if not queue:
    st.info("Nothing to review — no pending insights.")
else:
    for item in queue:
        with st.container(border=True):
            st.subheader(
                f"{item['competitor_name']} — materiality {item['materiality_score']}/10 ({item['confidence']})"
            )
            st.write(item["rationale"])
            with st.expander("Evidence (raw diff)", expanded=True):
                for signal in item["signals"]:
                    st.caption(f"Theme: {signal['theme']}")
                    st.code(signal["diff_text"])
                    st.write(signal["summary"])

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
                    st.warning(f"Rejected, but feedback wasn't saved: {result['error']}")
                else:
                    st.success("Rejected.")
                    st.rerun()
