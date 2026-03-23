from __future__ import annotations

from packages.planner.service import create_default_brain_service
from packages.schemas.mock_data import create_mock_session


def test_planner_filters_llm_reply_without_final_wrapper() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()

    def fake_chat(messages, *, temperature=0.2, max_tokens=384):
        return {
            "content": "Okay, let's tackle this user request. The user wants lesion detection and feature extraction.",
            "latency_ms": 12,
        }

    brain.enabled = True
    brain.client.chat = fake_chat  # type: ignore[method-assign]

    result = brain.reply(
        user_message="Inspect this prostate case, detect lesion, feature extract and give me a short report.",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    assert result["planner_metadata"]["llm_status"] == "llm_filtered"
    assert result["reply"]["content"].startswith("I proposed a draft prostate workflow")
