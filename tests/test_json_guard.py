from app.services.json_guard import parse_json_object, parse_json_object_with_meta


def test_parse_json_object_accepts_fenced_json():
    raw = """```json
{
  "issue_text": "risk",
  "suggestion": "fix"
}
```"""
    out = parse_json_object(raw)
    assert out["issue_text"] == "risk"
    assert out["suggestion"] == "fix"


def test_parse_json_object_repairs_trailing_comma():
    raw = '{"issue_text":"risk","suggestion":"fix",}'
    out = parse_json_object(raw)
    assert out["issue_text"] == "risk"
    assert out["suggestion"] == "fix"


def test_parse_json_object_with_meta_uses_defaults_when_invalid():
    out, meta = parse_json_object_with_meta(
        "not a json object",
        defaults={"issue_text": "fallback", "suggestion": "fallback"},
    )
    assert out["issue_text"] == "fallback"
    assert out["suggestion"] == "fallback"
    assert meta["candidate_count"] >= 1
