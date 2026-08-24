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
        source_segments=[
            {"index": 0, "start_seconds": 0.0, "end_seconds": 1.0, "text": "Open the cabinet."},
            {"index": 1, "start_seconds": 1.0, "end_seconds": 2.0, "text": "Check the terminal labels."},
        ],
    )

    assert "完整业务步骤" in prompt
    assert "不要按每颗螺丝" in prompt
    assert "机械臂组件介绍、组装底座与大臂" in prompt
    assert "正文由系统根据 source_segment_indices 从 ASR 原句拼接生成" in prompt
    assert "每个段落必须输出 polished_text" in prompt
    assert "每个段落必须输出 business_frame_text" in prompt
    assert "作为待匹配文本" in prompt
    assert "适合匹配单张关键帧" in prompt
    assert "不是本段完整操作流程的复述" in prompt
    assert "对象、对象关系和完成/安装状态" in prompt
    assert "不要枚举连续动作" in prompt
    assert "每个段落必须直接输出 section.query_graph" in prompt
    assert "围绕 business_frame_text 这张核心关键帧画面生成" in prompt
    assert "不要输出 query_graph.state" in prompt
    assert "不要输出 visual_operations" in prompt
    assert "completion_state" not in prompt
    assert '"query_graph"' in prompt
    assert "polished_text" in prompt
    assert "business_frame_text" in prompt
