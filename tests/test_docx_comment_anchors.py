import json
import zipfile
from pathlib import Path

from docx import Document

from app.services.docx_modifier import insert_risk_comments


def _build_docx(path: Path):
    doc = Document()
    doc.add_paragraph("WAREHOUSE MANAGEMENT SERVICES AGREEMENT")
    doc.add_paragraph("本合同项下设备销售价格为不含税价，双方另行约定税率与发票类型。")
    doc.add_paragraph("乙方应于收款后十日内向甲方开具增值税专用发票，逾期承担违约责任。")
    doc.add_paragraph("争议解决适用中华人民共和国法律。")
    doc.save(str(path))


def _paragraph_texts(path: Path):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return xml


def test_comments_are_anchored_to_the_clause_paragraph(tmp_path: Path):
    source = tmp_path / "contract.docx"
    _build_docx(source)
    output = tmp_path / "out.docx"

    risks = [
        {
            "issue_id": "r1",
            "risk_level": "high",
            "issue_text": "开票时限缺失",
            "suggestion": "明确开票期限",
            "location": {
                "risk_id": "classic-r1",
                "clause_id": "c2",
                "clause_path": "第二条",
                "page_no": 1,
                "paragraph_no": "3",
                "quote": "乙方应于收款后十日内向甲方开具增值税专用发票",
                "clause_text": "乙方应于收款后十日内向甲方开具增值税专用发票，逾期承担违约责任。",
            },
            "clause": {
                "clause_id": "c2",
                "clause_path": "第二条",
                "clause_text": "乙方应于收款后十日内向甲方开具增值税专用发票，逾期承担违约责任。",
                "page_no": 1,
                "paragraph_no": "3",
            },
        },
        {
            "issue_id": "r2",
            "risk_level": "medium",
            "issue_text": "含税价约定不明",
            "suggestion": "注明含税价与税率",
            "location": {
                "risk_id": "classic-r2",
                "clause_id": "c1",
                "clause_path": "第一条",
                "page_no": 1,
                "paragraph_no": "2",
                # paraphrase: must fall back to the clause text tier
                "quote": "价格没有写清楚是否含税",
                "clause_text": "本合同项下设备销售价格为不含税价，双方另行约定税率与发票类型。",
            },
        },
        {
            "issue_id": "r3",
            "risk_level": "low",
            "issue_text": "争议解决条款缺失",
            "suggestion": "补充争议解决方式",
            "location": {
                "risk_id": "classic-r3",
                "clause_id": "c3",
                "page_no": 1,
                "paragraph_no": "4",
                "locate_source": "preview_clause",
                "quote": "",
                "clause_text": "",
            },
        },
        {
            "issue_id": "r5",
            "risk_level": "low",
            "issue_text": "历史结果只有 chunk 内序号",
            "suggestion": "请人工定位",
            "location": {
                "risk_id": "classic-r5",
                "clause_id": "c9",
                "page_no": 0,
                "paragraph_no": "2",
                "quote": "",
                "clause_text": "",
            },
        },
        {
            "issue_id": "r4",
            "risk_level": "medium",
            "issue_text": "无定位信息的风险",
            "suggestion": "请人工定位",
            "location": {},
        },
    ]

    anchors = insert_risk_comments(str(source), str(output), risks, locale="zh-CN")

    assert len(anchors) == 5
    by_risk = {item["risk_id"]: item for item in anchors}

    assert by_risk["classic-r1"]["strategy"] == "quote_exact"
    assert by_risk["classic-r1"]["paragraph_index"] == 2
    assert by_risk["classic-r1"]["miss"] is False

    assert by_risk["classic-r2"]["strategy"] in {"clause_exact", "clause_fuzzy"}
    assert by_risk["classic-r2"]["paragraph_index"] == 1
    assert by_risk["classic-r2"]["miss"] is False

    assert by_risk["classic-r3"]["strategy"] == "paragraph_index"
    assert by_risk["classic-r3"]["paragraph_index"] == 3

    # untrusted (chunk-local) paragraph number must not be used as a position hint
    assert by_risk["classic-r5"]["strategy"] == "first_paragraph"
    assert by_risk["classic-r5"]["miss"] is True

    # no location at all -> identity falls back to issue_id
    assert by_risk["r4"]["strategy"] == "first_paragraph"
    assert by_risk["r4"]["miss"] is True

    document_xml = _paragraph_texts(output)
    assert document_xml.count("commentRangeStart") == 5

    with zipfile.ZipFile(output) as archive:
        comments_xml = archive.read("word/comments.xml").decode("utf-8")
    assert "定位：" in comments_xml
    assert "摘录：" in comments_xml
    assert "第二条" in comments_xml
    assert "自动定位未命中" in comments_xml


def test_anchor_records_are_json_serialisable(tmp_path: Path):
    source = tmp_path / "contract.docx"
    _build_docx(source)
    output = tmp_path / "out.docx"
    risks = [
        {
            "issue_id": "r1",
            "risk_level": "high",
            "issue_text": "开票时限缺失",
            "suggestion": "明确开票期限",
            "location": {"quote": "逾期承担违约责任", "paragraph_no": "3"},
        }
    ]

    anchors = insert_risk_comments(str(source), str(output), risks, locale="en-US")

    payload = json.dumps(anchors, ensure_ascii=False)
    assert "策略" not in payload
    assert anchors[0]["strategy"].endswith("_exact") or anchors[0]["strategy"] == "quote_exact"

    with zipfile.ZipFile(output) as archive:
        comments_xml = archive.read("word/comments.xml").decode("utf-8")
    assert "locate:" in comments_xml
    assert "excerpt:" in comments_xml
