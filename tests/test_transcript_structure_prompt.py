import importlib.util
import sys
import types


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

from app.services.transcript_tree import build_transcript_structure_prompt


def test_transcript_structure_prompt_prefers_natural_paragraphs_over_micro_steps():
    prompt = build_transcript_structure_prompt(
        video_title="demo",
        filename="demo.mp4",
        duration="00:10",
        max_input_chars=4000,
        max_visual_operations_per_section=3,
        source_segments=[
            {"index": 0, "start_seconds": 0.0, "end_seconds": 1.0, "text": "Open the cabinet."},
            {"index": 1, "start_seconds": 1.0, "end_seconds": 2.0, "text": "Check the terminal labels."},
        ],
    )

    assert "自然、清晰的转写段落" in prompt
    assert "不要为了一个小动作" in prompt
    assert "通常每段包含 2-6 个 ASR 句子" in prompt
    assert "不能把一个句子拆到两个段落里" in prompt
    assert "visual_operations" in prompt
    assert "每个段落最多输出 3 个关键操作" in prompt
    assert "operation_text" in prompt
