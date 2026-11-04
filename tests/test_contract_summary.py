"""合同摘要在无外网、无模型服务下的行为测试（使用假 LLM）。"""

import json

from app.services import contract_summary as cs


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, overrides=None):
        self.calls.append((messages, overrides))
        if not self._responses:
            return "", {}
        return self._responses.pop(0), {}


def _cfg(tmp_path):
    return {"data_dir": str(tmp_path), "contract_summary_enabled": True}


def _payload(**overrides):
    data = {
        "title": "测试合同",
        "about": "甲" * 160,
        "core": "乙" * 240,
        "tax": "丙" * 240,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_summary_ok_then_cached(tmp_path):
    llm = _FakeLLM([_payload()])

    result = cs.build_contract_summary(
        _cfg(tmp_path), llm, "合同正文" * 50, "zh", source_filename="c.docx")

    assert result["status"] == "ok"
    assert result["title"] == "测试合同"
    assert result["cache_hit"] is False
    assert len(llm.calls) == 1

    cached = cs.build_contract_summary(
        _cfg(tmp_path), _FakeLLM([]), "合同正文" * 50, "zh", source_filename="c.docx")

    assert cached["cache_hit"] is True
    assert cached["title"] == "测试合同"


def test_summary_disabled(tmp_path):
    cfg = dict(_cfg(tmp_path))
    cfg["contract_summary_enabled"] = False

    result = cs.build_contract_summary(cfg, _FakeLLM([]), "正文", "zh")

    assert result["status"] == "disabled"
    assert result["enabled"] is False


def test_summary_falls_back_when_model_output_is_not_json(tmp_path):
    llm = _FakeLLM(["这不是 JSON", "仍然不是 JSON"])

    result = cs.build_contract_summary(
        _cfg(tmp_path), llm, "合同正文" * 100, "zh", source_filename="f.docx")

    assert result["status"] == "fallback"
    assert result["about"]
    assert result["cache_hit"] is False


def test_summary_uses_map_reduce_for_long_contract(tmp_path):
    map_payload = json.dumps({"parties": "甲方", "tax": ["税率13%"]}, ensure_ascii=False)
    llm = _FakeLLM([map_payload] * 4 + [_payload()])

    result = cs.build_contract_summary(
        _cfg(tmp_path), llm, "条款" * 10000, "zh", source_filename="long.docx")

    assert result["map_reduce"] is True
    assert result["status"] == "ok"
    assert len(llm.calls) >= 5


def test_summary_returns_english_sections(tmp_path):
    llm = _FakeLLM([_payload(title="Contract A")])

    result = cs.build_contract_summary(
        _cfg(tmp_path), llm, "contract text " * 30, "en", source_filename="a.docx")

    assert result["title"] == "Contract A"
    system_text = llm.calls[0][0][0]["content"]
    assert "contract analyst" in system_text.lower()
