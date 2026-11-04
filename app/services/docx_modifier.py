import os
import logging
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher

NAMESPACES = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'ct': 'http://schemas.openxmlformats.org/package/2006/content-types',
    'pr': 'http://schemas.openxmlformats.org/package/2006/relationships'
}

for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


logger = logging.getLogger("law_assistant")


def prepare_docx_for_comments(input_path: str, output_path: str):
    """
    M1: 无损导出骨架
    将 input_path (docx) 复制到 output_path，并确保其包含 word/comments.xml，
    且在 [Content_Types].xml 和 word/_rels/document.xml.rels 中正确注册。
    这保证了后续只修改 document.xml 即可插入批注，不会丢失任何原有的格式、样式和编号。
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with zipfile.ZipFile(input_path, 'r') as zin:
        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            file_list = zin.namelist()

            has_comments = 'word/comments.xml' in file_list

            for item in file_list:
                content = zin.read(item)

                # 1. 确保 [Content_Types].xml 注册了 comments.xml
                if item == '[Content_Types].xml' and not has_comments:
                    root = ET.fromstring(content)
                    existing = root.find(
                        f".//ct:Override[@PartName='/word/comments.xml']", NAMESPACES)
                    if existing is None:
                        override = ET.Element(
                            f"{{{NAMESPACES['ct']}}}Override")
                        override.set('PartName', '/word/comments.xml')
                        override.set(
                            'ContentType', 'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml')
                        root.append(override)
                        content = ET.tostring(
                            root, encoding='utf-8', xml_declaration=True)

                # 2. 确保 word/_rels/document.xml.rels 链接了 comments.xml
                elif item == 'word/_rels/document.xml.rels' and not has_comments:
                    root = ET.fromstring(content)
                    rel_type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments'
                    existing = root.find(
                        f".//pr:Relationship[@Type='{rel_type}']", NAMESPACES)
                    if existing is None:
                        max_id = 0
                        for rel in root.findall(f".//pr:Relationship", NAMESPACES):
                            rid = rel.get('Id', '')
                            if rid.startswith('rId'):
                                try:
                                    num = int(rid[3:])
                                    max_id = max(max_id, num)
                                except ValueError as e:
                                    logger.warning(
                                        "docx_modifier_invalid_relationship_id rid=%s err=%s",
                                        rid,
                                        str(e),
                                    )
                        new_rid = f"rId{max_id + 1}"
                        rel = ET.Element(f"{{{NAMESPACES['pr']}}}Relationship")
                        rel.set('Id', new_rid)
                        rel.set('Type', rel_type)
                        rel.set('Target', 'comments.xml')
                        root.append(rel)
                        content = ET.tostring(
                            root, encoding='utf-8', xml_declaration=True)

                zout.writestr(item, content)

            # 3. 注入空的 word/comments.xml
            if not has_comments:
                comments_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{NAMESPACES['w']}">
</w:comments>"""
                zout.writestr('word/comments.xml',
                              comments_xml.encode('utf-8'))


def insert_risk_comments(
    input_path: str,
    output_path: str,
    risks: list,
    locale: str = "zh-CN",
):
    """
    M2: 单点批注注入
    在保留原文档格式的基础上，根据风险数据向 document.xml 和 comments.xml 中写入批注。

    锚定策略（从强到弱）：quote 精确匹配 → quote 模糊匹配 → 条款正文精确/模糊 →
    原始段落序号 → 首个非空段落（标记 miss）。返回每个风险的锚定记录，便于核对。
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with zipfile.ZipFile(input_path, 'r') as zin:
        file_list = zin.namelist()
        has_comments = 'word/comments.xml' in file_list

        content_map = {}
        for item in file_list:
            content_map[item] = zin.read(item)

        # 1. Ensure [Content_Types].xml is updated
        if not has_comments:
            root = ET.fromstring(content_map['[Content_Types].xml'])
            existing = root.find(
                f".//ct:Override[@PartName='/word/comments.xml']", NAMESPACES)
            if existing is None:
                override = ET.Element(f"{{{NAMESPACES['ct']}}}Override")
                override.set('PartName', '/word/comments.xml')
                override.set(
                    'ContentType', 'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml')
                root.append(override)
                content_map['[Content_Types].xml'] = ET.tostring(
                    root, encoding='utf-8', xml_declaration=True)

        # 2. Ensure word/_rels/document.xml.rels is updated
        if not has_comments:
            if 'word/_rels/document.xml.rels' in content_map:
                root = ET.fromstring(
                    content_map['word/_rels/document.xml.rels'])
                rel_type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments'
                existing = root.find(
                    f".//pr:Relationship[@Type='{rel_type}']", NAMESPACES)
                if existing is None:
                    max_id = 0
                    for rel in root.findall(f".//pr:Relationship", NAMESPACES):
                        rid = rel.get('Id', '')
                        if rid.startswith('rId'):
                            try:
                                max_id = max(max_id, int(rid[3:]))
                            except ValueError as e:
                                logger.warning(
                                    "docx_modifier_invalid_relationship_id rid=%s err=%s",
                                    rid,
                                    str(e),
                                )
                    new_rid = f"rId{max_id + 1}"
                    rel = ET.Element(f"{{{NAMESPACES['pr']}}}Relationship")
                    rel.set('Id', new_rid)
                    rel.set('Type', rel_type)
                    rel.set('Target', 'comments.xml')
                    root.append(rel)
                    content_map['word/_rels/document.xml.rels'] = ET.tostring(
                        root, encoding='utf-8', xml_declaration=True)

        # 3. Handle comments.xml
        if has_comments:
            comments_root = ET.fromstring(content_map['word/comments.xml'])
        else:
            comments_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{NAMESPACES['w']}"></w:comments>"""
            comments_root = ET.fromstring(comments_xml.encode('utf-8'))

        max_comment_id = -1
        for c in comments_root.findall(f".//w:comment", NAMESPACES):
            cid = c.get(f"{{{NAMESPACES['w']}}}id")
            if cid is not None:
                try:
                    max_comment_id = max(max_comment_id, int(cid))
                except ValueError as e:
                    logger.warning(
                        "docx_modifier_invalid_comment_id comment_id=%s err=%s",
                        str(cid),
                        str(e),
                    )

        # 4. Handle document.xml
        doc_root = ET.fromstring(content_map['word/document.xml'])
        paragraphs = doc_root.findall(f".//w:p", NAMESPACES)

        p_texts = []
        for p in paragraphs:
            text = "".join(t.text for t in p.findall(
                f".//w:t", NAMESPACES) if t.text)
            p_texts.append((p, text))

        # 5. Insert risks — anchors resolved from the locator carried on each risk
        def _norm_ws(value: str) -> str:
            return "".join(str(value or "").split())

        def _similarity(left: str, right: str) -> float:
            if not left or not right:
                return 0.0
            return SequenceMatcher(None, left[:120], right[:120]).ratio()

        def _resolve_anchor(candidates, texts, threshold=0.62):
            for kind, needle in candidates:
                if len(needle) < 4:
                    continue
                for idx, (paragraph, paragraph_text) in enumerate(texts):
                    normalized = _norm_ws(paragraph_text)
                    if normalized and needle in normalized:
                        return paragraph, idx, f"{kind}_exact"
            best_score = 0.0
            best = (None, -1, "")
            for kind, needle in candidates:
                if len(needle) < 8:
                    continue
                for idx, (paragraph, paragraph_text) in enumerate(texts):
                    normalized = _norm_ws(paragraph_text)
                    if not normalized:
                        continue
                    score = _similarity(needle, normalized)
                    if score > best_score:
                        best_score = score
                        best = (paragraph, idx, f"{kind}_fuzzy")
            if best[0] is not None and best_score >= threshold:
                return best
            return None, -1, ""

        is_zh_comment = not str(locale or "zh").lower().startswith("en")
        anchors = []
        for risk in risks:
            issue_text = str(risk.get("issue_text") or risk.get("issue") or "")
            suggestion = str(risk.get("suggestion") or "")
            level = str(risk.get("risk_level")
                        or risk.get("level") or "medium")
            location = risk.get("location") if isinstance(
                risk.get("location"), dict) else {}
            clause = risk.get("clause") if isinstance(
                risk.get("clause"), dict) else {}
            quote = str(
                location.get("quote") or risk.get("evidence_text")
                or risk.get("evidence") or clause.get("quote") or "")
            clause_text = str(
                location.get("clause_text") or clause.get("clause_text") or "")
            clause_path = str(
                location.get("clause_path") or clause.get("clause_path") or "")
            page_no = int(location.get("page_no") or clause.get("page_no") or 0)
            paragraph_no = str(
                location.get("paragraph_no") or clause.get("paragraph_no") or "")

            if not issue_text and not suggestion:
                continue

            target_p, target_index, strategy = _resolve_anchor(
                [("quote", _norm_ws(quote)), ("clause", _norm_ws(clause_text)[:160])],
                p_texts,
            )
            locate_source = str(location.get("locate_source") or "")
            # Legacy results stored a chunk-local ordinal here: only the parser-derived
            # numbering may be used as a positional hint.
            if target_p is None and paragraph_no.isdigit() and locate_source == "preview_clause":
                candidate_index = int(paragraph_no) - 1
                if 0 <= candidate_index < len(p_texts) and _norm_ws(p_texts[candidate_index][1]):
                    target_p = p_texts[candidate_index][0]
                    target_index = candidate_index
                    strategy = "paragraph_index"
            anchor_miss = False
            if target_p is None:
                for idx, (paragraph, paragraph_text) in enumerate(p_texts):
                    if _norm_ws(paragraph_text):
                        target_p, target_index, strategy = paragraph, idx, "first_paragraph"
                        break
                if target_p is None and p_texts:
                    target_p, target_index = p_texts[0][0], 0
                    strategy = "first_paragraph"
                anchor_miss = target_p is not None

            max_comment_id += 1
            current_id = str(max_comment_id)

            comment_elem = ET.Element(f"{{{NAMESPACES['w']}}}comment")
            comment_elem.set(f"{{{NAMESPACES['w']}}}id", current_id)
            comment_elem.set(
                f"{{{NAMESPACES['w']}}}author", "Tax Assistant")
            comment_elem.set(f"{{{NAMESPACES['w']}}}date", datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

            def add_p_to_comment(text_val):
                p = ET.SubElement(comment_elem, f"{{{NAMESPACES['w']}}}p")
                r = ET.SubElement(p, f"{{{NAMESPACES['w']}}}r")
                t = ET.SubElement(r, f"{{{NAMESPACES['w']}}}t")
                t.text = text_val

            snippet = (quote or clause_text).strip()
            locator_bits = []
            if clause_path:
                locator_bits.append(clause_path)
            elif paragraph_no:
                locator_bits.append(
                    f"第{paragraph_no}段" if is_zh_comment else f"para {paragraph_no}")
            if page_no:
                locator_bits.append(
                    f"第{page_no}页" if is_zh_comment else f"page {page_no}")
            if snippet:
                locator_bits.append(
                    ("摘录：" if is_zh_comment else "excerpt: ") + snippet[:60])
            if anchor_miss:
                locator_bits.append(
                    "自动定位未命中，请按摘录在原文检索" if is_zh_comment
                    else "auto-anchor missed; search the excerpt in the document")
            if locator_bits:
                add_p_to_comment(
                    ("定位：" if is_zh_comment else "locate: ") + " · ".join(locator_bits))

            level_cn = {"high": "高风险", "medium": "中风险",
                        "low": "低风险"}.get(level.lower(), "中风险")
            if issue_text:
                add_p_to_comment(f"【{level_cn}】发现问题：{issue_text}")
            if suggestion:
                add_p_to_comment(f"建议：{suggestion}")

            comments_root.append(comment_elem)

            if target_p is not None:
                start_elem = ET.Element(
                    f"{{{NAMESPACES['w']}}}commentRangeStart")
                start_elem.set(f"{{{NAMESPACES['w']}}}id", current_id)

                end_elem = ET.Element(f"{{{NAMESPACES['w']}}}commentRangeEnd")
                end_elem.set(f"{{{NAMESPACES['w']}}}id", current_id)

                r_elem = ET.Element(f"{{{NAMESPACES['w']}}}r")
                ref_elem = ET.SubElement(
                    r_elem, f"{{{NAMESPACES['w']}}}commentReference")
                ref_elem.set(f"{{{NAMESPACES['w']}}}id", current_id)

                target_p.insert(0, start_elem)
                target_p.append(end_elem)
                target_p.append(r_elem)

            anchors.append(
                {
                    "risk_id": str(
                        location.get("risk_id") or risk.get("issue_id") or current_id),
                    "comment_id": current_id,
                    "clause_id": str(
                        location.get("clause_id") or clause.get("clause_id") or ""),
                    "clause_path": clause_path,
                    "page_no": page_no,
                    "paragraph_no": paragraph_no,
                    "strategy": strategy or "unresolved",
                    "paragraph_index": int(target_index),
                    "miss": anchor_miss,
                    "snippet": snippet[:120],
                }
            )


        content_map['word/comments.xml'] = ET.tostring(
            comments_root, encoding='utf-8', xml_declaration=True)
        content_map['word/document.xml'] = ET.tostring(
            doc_root, encoding='utf-8', xml_declaration=True)

        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item, content in content_map.items():
                if isinstance(content, str):
                    content = content.encode('utf-8')
                zout.writestr(item, content)
    return anchors
