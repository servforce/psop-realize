from app.services.audit import summarize


def test_summarize_truncates_long_values():
    value = {"text": "x" * 2000}
    result = summarize(value, max_chars=80)
    assert len(result) <= 83
    assert result.endswith("...")

