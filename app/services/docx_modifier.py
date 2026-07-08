import os
import logging
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
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


def insert_risk_comments(input_path: str, output_path: str, risks: list):
    """
    M2: 单点批注注入
    在保留原文档格式的基础上，根据风险数据向 document.xml 和 comments.xml 中写入批注。
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

        # 5. Insert risks
        for risk in risks:
            issue_text = str(risk.get("issue_text") or risk.get("issue") or "")
            suggestion = str(risk.get("suggestion") or "")
            level = str(risk.get("risk_level")
                        or risk.get("level") or "medium")
            evidence = str(risk.get("evidence_text")
                           or risk.get("evidence") or "")

            if not evidence and "clause" in risk:
                evidence = str(risk["clause"].get("clause_text", ""))

            if not issue_text and not suggestion:
                continue

            max_comment_id += 1
            current_id = str(max_comment_id)

            comment_elem = ET.Element(f"{{{NAMESPACES['w']}}}comment")
            comment_elem.set(f"{{{NAMESPACES['w']}}}id", current_id)
            comment_elem.set(
                f"{{{NAMESPACES['w']}}}author", "Tax Assistant")

            # Use timezone-aware UTC datetime instead of utcnow()
            from datetime import timezone
            comment_elem.set(f"{{{NAMESPACES['w']}}}date", datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

            def add_p_to_comment(text_val):
                p = ET.SubElement(comment_elem, f"{{{NAMESPACES['w']}}}p")
                r = ET.SubElement(p, f"{{{NAMESPACES['w']}}}r")
                t = ET.SubElement(r, f"{{{NAMESPACES['w']}}}t")
                t.text = text_val

            level_cn = {"high": "高风险", "medium": "中风险",
                        "low": "低风险"}.get(level.lower(), "中风险")
            if issue_text:
                add_p_to_comment(f"【{level_cn}】发现问题：{issue_text}")
            if suggestion:
                add_p_to_comment(f"建议：{suggestion}")

            comments_root.append(comment_elem)

            target_p = None
            if evidence:
                norm_evidence = "".join(evidence.split())
                best_score = 0
                for p, p_text in p_texts:
                    if not p_text:
                        continue
                    norm_p_text = "".join(p_text.split())

                    if norm_evidence and norm_evidence in norm_p_text:
                        target_p = p
                        break

                    if norm_evidence and norm_p_text:
                        score = SequenceMatcher(
                            None, norm_evidence[:100], norm_p_text[:100]).ratio()
                        if score > best_score and score > 0.3:
                            best_score = score
                            target_p = p

            if target_p is None and p_texts:
                target_p = p_texts[0][0]

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

        content_map['word/comments.xml'] = ET.tostring(
            comments_root, encoding='utf-8', xml_declaration=True)
        content_map['word/document.xml'] = ET.tostring(
            doc_root, encoding='utf-8', xml_declaration=True)

        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item, content in content_map.items():
                if isinstance(content, str):
                    content = content.encode('utf-8')
                zout.writestr(item, content)
