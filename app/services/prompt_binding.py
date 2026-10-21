from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, StrictUndefined, TemplateError, TemplateSyntaxError

from app.core.config import get_config
from app.core.database import get_conn


logger = logging.getLogger("law_assistant")


PROMPT_BINDING_SCENES: Dict[str, Dict[str, str]] = {
    "contract_audit.classic_json": {
        "label": "Contract Audit Classic",
        "description": "Classic contract audit JSON prompt used by the admin audit pipeline.",
        "source_module": "app.services.contract_audit._build_classic_audit",
        "default_prompt_key": "contract_audit.classic_json",
    },
    "contract_audit.contract_summary": {
        "label": "Contract Audit Contract Summary",
        "description": "Whole-contract summary prompt used to generate a concise contract overview before the risk summary.",
        "source_module": "app.services.contract_audit._build_contract_summary",
        "default_prompt_key": "contract_audit.contract_summary",
    },
    "contract_audit.contract_summary_map": {
        "label": "Contract Summary Fact Extractor",
        "description": "Map step of the chunked contract summary used for long contracts or local models.",
        "source_module": "app.services.contract_audit._extract_summary_chunk_facts",
        "default_prompt_key": "contract_audit.contract_summary_map",
    }
}


DEFAULT_PROMPT_DATASET_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "default_prompts.json"
)

PROMPT_EDITOR_LOCALES = ("zh", "en")
PROMPT_RUNTIME_LANG_GUARD = (
    "{% if (lang|default('', true) == 'en') or (norm_lang|default('', true) == 'en') %}"
)
PROMPT_RUNTIME_TAX_FOCUS_GUARD = "{% if tax_focus|default(false, true) %}"


def normalize_prompt_editor_lang(lang: Optional[str], default: str = "zh") -> str:
    value = str(lang or "").strip().lower()
    if value.startswith("en"):
        return "en"
    if value.startswith("zh"):
        return "zh"
    return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _parse_json_object(raw: Any) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _normalize_localized_texts(raw: Any, fallback_text: str = "") -> dict:
    parsed = _parse_json_object(raw)
    normalized = {lang: str(parsed.get(lang) or "").strip()
                  for lang in PROMPT_EDITOR_LOCALES}
    fallback = str(fallback_text or "").strip()
    if fallback and not any(normalized.values()):
        normalized["zh"] = fallback
        normalized["en"] = fallback
    if fallback:
        if not normalized["zh"]:
            normalized["zh"] = fallback
        if not normalized["en"]:
            normalized["en"] = fallback
    return normalized


def _normalize_placeholder_map(raw: Any) -> dict:
    parsed = _parse_json_object(raw)
    normalized = {}
    for key, value in parsed.items():
        name = str(key or "").strip()
        if not name:
            continue
        labels = value if isinstance(value, dict) else {}
        normalized[name] = {
            "zh": str(labels.get("zh") or labels.get("cn") or "").strip(),
            "en": str(labels.get("en") or "").strip(),
        }
    return normalized


def _placeholder_label_for_lang(placeholder_map: dict, field_name: str, lang: str) -> str:
    labels = placeholder_map.get(field_name) if isinstance(
        placeholder_map, dict) else {}
    if not isinstance(labels, dict):
        return ""
    locale = normalize_prompt_editor_lang(lang)
    if locale == "en":
        return str(labels.get("en") or labels.get("zh") or f"[{field_name}]").strip()
    return str(labels.get("zh") or labels.get("en") or f"【{field_name}】").strip()


def _replace_business_placeholders(text: str, placeholder_map: dict, lang: str) -> str:
    compiled = str(text or "")
    replacements = []
    for field_name in sorted((placeholder_map or {}).keys(), key=len, reverse=True):
        label = _placeholder_label_for_lang(placeholder_map, field_name, lang)
        if not label:
            continue
        replacements.append((label, "{{ " + field_name + " }}"))
    for label, token in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        compiled = compiled.replace(label, token)
    return compiled


def _replace_runtime_tokens_with_business_labels(text: str, placeholder_map: dict, lang: str) -> str:
    localized = str(text or "")
    for field_name in sorted((placeholder_map or {}).keys(), key=len, reverse=True):
        label = _placeholder_label_for_lang(placeholder_map, field_name, lang)
        localized = localized.replace("{{ " + field_name + " }}", label)
        localized = localized.replace("{{" + field_name + "}}", label)
    return localized


def _split_top_level_jinja_if_blocks(text: str) -> Optional[tuple[str, str]]:
    raw = str(text or "").strip()
    if not raw.startswith("{% if"):
        return None
    tags = []
    index = 0
    while True:
        start = raw.find("{%", index)
        if start < 0:
            break
        end = raw.find("%}", start)
        if end < 0:
            return None
        tag_text = raw[start + 2: end].strip()
        tags.append((start, end + 2, tag_text))
        index = end + 2
    if not tags:
        return None
    depth = 0
    root_if = None
    root_else = None
    root_endif = None
    for start, end, tag_text in tags:
        if tag_text.startswith("if "):
            depth += 1
            if depth == 1 and root_if is None:
                root_if = (start, end, tag_text)
            continue
        if tag_text == "else":
            if depth == 1 and root_else is None:
                root_else = (start, end, tag_text)
            continue
        if tag_text.startswith("endif"):
            if depth == 1 and root_if is not None and root_else is not None and root_endif is None:
                root_endif = (start, end, tag_text)
            depth -= 1
            if depth < 0:
                return None
    if depth != 0 or not root_if or not root_else or not root_endif:
        return None
    true_block = raw[root_if[1]: root_else[0]]
    false_block = raw[root_else[1]: root_endif[0]]
    if raw[root_endif[1]:].strip():
        return None
    return true_block.strip(), false_block.strip()


def _strip_leading_tax_focus_block(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    suffix = "{% endif %}"
    if not raw.startswith("{% if"):
        return "", raw
    prefix_end = raw.find("%}")
    if prefix_end < 0:
        return "", raw
    prefix = raw[: prefix_end + 2]
    if "tax_focus" not in prefix:
        return "", raw
    end_idx = raw.find(suffix, len(prefix))
    if end_idx < 0:
        return "", raw
    focus_text = raw[len(prefix): end_idx].strip()
    body_text = raw[end_idx + len(suffix):].strip()
    return focus_text, body_text


def _extract_localized_runtime_variants(runtime_text: str) -> dict:
    raw = str(runtime_text or "").strip()
    split_blocks = _split_top_level_jinja_if_blocks(raw)
    if not split_blocks:
        return {"zh": raw, "en": raw}
    true_block, false_block = split_blocks
    tag_prefix = raw[: raw.find("%}") + 2] if raw.startswith("{% if") else ""
    guard_text = tag_prefix.lower()
    if "lang" in guard_text and "'en'" in guard_text:
        return {
            "en": true_block,
            "zh": false_block,
        }
    return {
        "zh": true_block,
        "en": false_block,
    }


def _build_runtime_text(localized_texts: dict, placeholder_map: dict, focus_texts: Optional[dict] = None) -> str:
    focus_map = focus_texts or {}
    compiled_by_lang = {}
    for lang in PROMPT_EDITOR_LOCALES:
        body = _replace_business_placeholders(
            localized_texts.get(lang, ""), placeholder_map, lang).strip()
        focus_text = _replace_business_placeholders(
            focus_map.get(lang, ""), placeholder_map, lang).strip()
        if focus_text:
            body = PROMPT_RUNTIME_TAX_FOCUS_GUARD + \
                focus_text + "{% endif %}" + body
        compiled_by_lang[lang] = body
    zh_text = compiled_by_lang.get("zh", "")
    en_text = compiled_by_lang.get("en", "")
    if zh_text and en_text:
        if zh_text == en_text:
            return zh_text
        return f"{PROMPT_RUNTIME_LANG_GUARD}{en_text}{{% else %}}{zh_text}{{% endif %}}"
    return zh_text or en_text


def _extract_localized_texts_from_runtime(
    runtime_text: str,
    placeholder_map: dict,
    fallback_lang: str = "zh",
    focus_runtime_text: str = "",
) -> tuple[dict, dict]:
    runtime_variants = _extract_localized_runtime_variants(runtime_text)
    focus_variants = _extract_localized_runtime_variants(focus_runtime_text)
    localized = {}
    localized_focus = {}
    for lang in PROMPT_EDITOR_LOCALES:
        focus_text = str(focus_variants.get(lang) or "").strip()
        body_text = str(runtime_variants.get(lang) or "").strip()
        extracted_focus, extracted_body = _strip_leading_tax_focus_block(
            body_text)
        if extracted_focus:
            focus_text = extracted_focus
            body_text = extracted_body
        localized[lang] = _replace_runtime_tokens_with_business_labels(
            body_text,
            placeholder_map,
            lang,
        ).strip()
        localized_focus[lang] = _replace_runtime_tokens_with_business_labels(
            focus_text,
            placeholder_map,
            lang,
        ).strip()
    default_lang = normalize_prompt_editor_lang(fallback_lang)
    other_lang = "en" if default_lang == "zh" else "zh"
    if localized[default_lang] and not localized[other_lang]:
        localized[other_lang] = localized[default_lang]
    if localized_focus[default_lang] and not localized_focus[other_lang]:
        localized_focus[other_lang] = localized_focus[default_lang]
    return localized, localized_focus


def get_prompt_editor_content(prompt_row: Optional[dict], editor_lang: str = "zh") -> dict:
    row = dict(prompt_row or {})
    locale = normalize_prompt_editor_lang(editor_lang)
    placeholder_map = _normalize_placeholder_map(
        row.get("placeholder_map_json"))
    localized_system = _normalize_localized_texts(
        row.get("localized_system_texts"))
    if not any(localized_system.values()) and str(row.get("system_text") or "").strip():
        localized_system, _ = _extract_localized_texts_from_runtime(
            str(row.get("system_text") or ""),
            placeholder_map,
            fallback_lang=locale,
        )
    localized_system = _normalize_localized_texts(localized_system)
    localized_template = _normalize_localized_texts(
        row.get("localized_template_texts"))
    localized_focus = _normalize_localized_texts(
        row.get("localized_focus_texts"))
    if (
        (not any(localized_template.values()) or not any(localized_focus.values()))
        and str(row.get("template_text") or "").strip()
    ):
        extracted_template, extracted_focus = _extract_localized_texts_from_runtime(
            str(row.get("template_text") or ""),
            placeholder_map,
            fallback_lang=locale,
        )
        if not any(localized_template.values()):
            localized_template = extracted_template
        if not any(localized_focus.values()):
            localized_focus = extracted_focus
    localized_template = _normalize_localized_texts(localized_template)
    localized_focus = _normalize_localized_texts(localized_focus)
    return {
        "editor_lang": locale,
        "display_system_text": str(localized_system.get(locale) or ""),
        "display_focus_text": str(localized_focus.get(locale) or ""),
        "display_template_text": str(localized_template.get(locale) or ""),
        "localized_system_texts": localized_system,
        "localized_focus_texts": localized_focus,
        "localized_template_texts": localized_template,
        "placeholder_map_json": _json_dumps(placeholder_map),
    }


def build_prompt_storage_payload(
    *,
    existing_row: Optional[dict] = None,
    default_item: Optional[dict] = None,
    editor_lang: str = "zh",
    display_system_text: str = "",
    display_focus_text: str = "",
    display_template_text: str = "",
) -> dict:
    row = dict(existing_row or {})
    default = dict(default_item or {})
    locale = normalize_prompt_editor_lang(editor_lang)
    other_locale = "en" if locale == "zh" else "zh"
    placeholder_map = _normalize_placeholder_map(
        row.get("placeholder_map_json") or default.get("placeholder_map")
    )
    localized_system = _normalize_localized_texts(
        row.get("localized_system_texts") or default.get(
            "localized_system_texts"),
    )
    if not any(localized_system.values()):
        localized_system, _ = _extract_localized_texts_from_runtime(
            str(row.get("system_text") or default.get("system_text") or ""),
            placeholder_map,
            fallback_lang=locale,
        )
    localized_focus = _normalize_localized_texts(
        row.get("localized_focus_texts") or default.get(
            "localized_focus_texts"),
    )
    localized_template = _normalize_localized_texts(
        row.get("localized_template_texts") or default.get(
            "localized_template_texts"),
    )
    if not any(localized_template.values()) or not any(localized_focus.values()):
        extracted_template, extracted_focus = _extract_localized_texts_from_runtime(
            str(row.get("template_text") or default.get("template_text") or ""),
            placeholder_map,
            fallback_lang=locale,
        )
        if not any(localized_template.values()):
            localized_template = extracted_template
        if not any(localized_focus.values()):
            localized_focus = extracted_focus
    localized_system = _normalize_localized_texts(localized_system)
    localized_focus = _normalize_localized_texts(localized_focus)
    localized_template = _normalize_localized_texts(localized_template)
    localized_system[locale] = str(display_system_text or "").strip()
    localized_focus[locale] = str(display_focus_text or "").strip()
    localized_template[locale] = str(display_template_text or "").strip()
    if not localized_system[other_locale] and localized_system[locale]:
        localized_system[other_locale] = localized_system[locale]
    if not localized_template[other_locale] and localized_template[locale]:
        localized_template[other_locale] = localized_template[locale]
    runtime_system_text = _build_runtime_text(
        localized_system, placeholder_map)
    runtime_template_text = _build_runtime_text(
        localized_template,
        placeholder_map,
        localized_focus,
    )
    return {
        "editor_lang": locale,
        "system_text": runtime_system_text,
        "template_text": runtime_template_text,
        "localized_system_texts": _json_dumps(localized_system),
        "localized_focus_texts": _json_dumps(localized_focus),
        "localized_template_texts": _json_dumps(localized_template),
        "placeholder_map_json": _json_dumps(placeholder_map),
    }


@lru_cache(maxsize=1)
def _load_default_prompt_dataset() -> List[dict]:
    with DEFAULT_PROMPT_DATASET_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("default prompt dataset must be a list")
    return [dict(item) for item in data]


def _default_prompt_map() -> Dict[str, dict]:
    mapping: Dict[str, dict] = {}
    for item in _load_default_prompt_dataset():
        prompt_key = str(item.get("prompt_key") or "").strip()
        if not prompt_key:
            continue
        mapping[prompt_key] = dict(item)
    return mapping


def list_prompt_binding_scenes() -> List[Dict[str, str]]:
    items = []
    for scene_key, meta in PROMPT_BINDING_SCENES.items():
        items.append(
            {
                "scene_key": scene_key,
                "label": str(meta.get("label") or scene_key),
                "description": str(meta.get("description") or ""),
                "source_module": str(meta.get("source_module") or ""),
            }
        )
    return items


def get_prompt_binding_scene(scene_key: str) -> Optional[Dict[str, str]]:
    return PROMPT_BINDING_SCENES.get(str(scene_key or "").strip())


def get_default_prompt_definition(prompt_key: str) -> Optional[dict]:
    item = _default_prompt_map().get(str(prompt_key or "").strip())
    return dict(item) if item else None


def sync_default_prompts(cfg: Optional[dict] = None) -> None:
    runtime_cfg = cfg or get_config()
    defaults = _load_default_prompt_dataset()
    if not defaults:
        return
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("SELECT id, prompt_key, managed_scope FROM audit_prompt")
    rows = cur.fetchall()
    existing_by_key = {
        str(row["prompt_key"] or "").strip(): dict(row)
        for row in rows
        if str(row["prompt_key"] or "").strip()
    }
    for item in defaults:
        prompt_key = str(item.get("prompt_key") or "").strip()
        if not prompt_key:
            continue
        existing = existing_by_key.get(prompt_key)
        localized_system_texts = _normalize_localized_texts(
            item.get("localized_system_texts"),
            str(item.get("system_text") or ""),
        )
        localized_focus_texts = _normalize_localized_texts(
            item.get("localized_focus_texts"),
        )
        localized_template_texts = _normalize_localized_texts(
            item.get("localized_template_texts"),
            str(item.get("template_text") or ""),
        )
        placeholder_map = _normalize_placeholder_map(
            item.get("placeholder_map"))
        runtime_system_text = _build_runtime_text(
            localized_system_texts,
            placeholder_map,
        ) or str(item.get("system_text") or "")
        runtime_template_text = _build_runtime_text(
            localized_template_texts,
            placeholder_map,
            localized_focus_texts,
        ) or str(item.get("template_text") or "")
        sample_input_json = item.get("sample_input_json") or {}
        if not isinstance(sample_input_json, dict):
            sample_input_json = {}
        sample_input_text = json.dumps(
            sample_input_json, ensure_ascii=False, indent=2, sort_keys=True
        )
        if existing:
            managed_scope = str(existing.get("managed_scope") or "").strip()
            is_system_managed = managed_scope in ("", "system")
            if is_system_managed:
                cur.execute(
                    """
                    UPDATE audit_prompt
                    SET prompt_key = ?,
                        source_module = ?,
                        managed_scope = 'system',
                        system_text = ?,
                        template_text = ?,
                        localized_system_texts = ?,
                        localized_focus_texts = ?,
                        localized_template_texts = ?,
                        placeholder_map_json = ?,
                        sample_input_json = ?
                    WHERE id = ?
                    """,
                    (
                        prompt_key,
                        str(item.get("source_module") or ""),
                        runtime_system_text,
                        runtime_template_text,
                        _json_dumps(localized_system_texts),
                        _json_dumps(localized_focus_texts),
                        _json_dumps(localized_template_texts),
                        _json_dumps(placeholder_map),
                        sample_input_text,
                        str(existing.get("id") or prompt_key),
                    ),
                )
            else:
                # User-customized prompt: only fill empty metadata, preserve content.
                cur.execute(
                    """
                    UPDATE audit_prompt
                    SET prompt_key = COALESCE(prompt_key, ?),
                        source_module = CASE WHEN source_module = '' THEN ? ELSE source_module END
                    WHERE id = ?
                    """,
                    (
                        prompt_key,
                        str(item.get("source_module") or ""),
                        str(existing.get("id") or prompt_key),
                    ),
                )
            continue
        cur.execute(
            """
            INSERT INTO audit_prompt(
                id, name, prompt_key, description, system_text, template_text,
                localized_system_texts, localized_focus_texts, localized_template_texts,
                placeholder_map_json, sample_input_json, source_module, managed_scope, status,
                created_by, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_key,
                str(item.get("name") or prompt_key),
                prompt_key,
                str(item.get("description") or ""),
                runtime_system_text,
                runtime_template_text,
                _json_dumps(localized_system_texts),
                _json_dumps(localized_focus_texts),
                _json_dumps(localized_template_texts),
                _json_dumps(placeholder_map),
                sample_input_text,
                str(item.get("source_module") or ""),
                "system",
                "enabled",
                "system",
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def list_builtin_prompts(cfg: Optional[dict] = None) -> List[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_prompt
        WHERE managed_scope = 'system'
        ORDER BY name ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_prompt_record(prompt_id: str, cfg: Optional[dict] = None) -> Optional[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?",
                (str(prompt_id or "").strip(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_prompt_record_by_key(prompt_key: str, cfg: Optional[dict] = None) -> Optional[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM audit_prompt WHERE prompt_key = ?",
        (str(prompt_key or "").strip(),),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_builtin_prompt(prompt_id: str, cfg: Optional[dict] = None) -> Optional[dict]:
    row = get_prompt_record(prompt_id, cfg)
    if row and str(row.get("managed_scope") or "") == "system":
        return row
    return None


def list_prompt_bindings(cfg: Optional[dict] = None) -> List[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            b.scene_key,
            b.prompt_id,
            b.updated_by,
            b.updated_at,
            p.prompt_key,
            p.name,
            p.description,
            p.system_text,
            p.template_text,
            p.sample_input_json,
            p.source_module,
            p.managed_scope,
            p.status,
            p.created_by,
            p.created_at,
            p.updated_at AS prompt_updated_at
        FROM audit_prompt_binding b
        LEFT JOIN audit_prompt p ON p.id = b.prompt_id
        ORDER BY b.scene_key ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def map_prompt_bindings_by_prompt_id(cfg: Optional[dict] = None) -> Dict[str, List[str]]:
    prompt_map: Dict[str, List[str]] = {}
    for row in list_prompt_bindings(cfg):
        prompt_id = str(row.get("prompt_id") or "").strip()
        scene_key = str(row.get("scene_key") or "").strip()
        if not prompt_id or not scene_key:
            continue
        prompt_map.setdefault(prompt_id, []).append(scene_key)
    return prompt_map


def get_prompt_binding(scene_key: str, cfg: Optional[dict] = None) -> Optional[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            b.scene_key,
            b.prompt_id,
            b.updated_by,
            b.updated_at,
            p.prompt_key,
            p.name,
            p.description,
            p.system_text,
            p.template_text,
            p.sample_input_json,
            p.source_module,
            p.managed_scope,
            p.status,
            p.created_by,
            p.created_at,
            p.updated_at AS prompt_updated_at
        FROM audit_prompt_binding b
        LEFT JOIN audit_prompt p ON p.id = b.prompt_id
        WHERE b.scene_key = ?
        """,
        (scene_key,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_default_scene_prompt(scene_key: str, cfg: Optional[dict] = None) -> Optional[dict]:
    scene = get_prompt_binding_scene(scene_key)
    if not scene:
        return None
    default_key = str(scene.get("default_prompt_key") or "").strip()
    if not default_key:
        return None
    row = get_prompt_record_by_key(default_key, cfg)
    if row:
        return row
    default_item = get_default_prompt_definition(default_key)
    if not default_item:
        return None
    return _default_item_to_row(default_item)


def set_prompt_binding(scene_key: str, prompt_id: Optional[str], updated_by: str, cfg: Optional[dict] = None) -> dict:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    scene = get_prompt_binding_scene(scene_key)
    if not scene:
        conn.close()
        raise ValueError("Prompt binding scene is not supported")

    normalized_prompt_id = str(prompt_id or "").strip()
    now = datetime.utcnow().isoformat()
    if normalized_prompt_id:
        cur.execute(
            "SELECT id, status FROM audit_prompt WHERE id = ?",
            (normalized_prompt_id,),
        )
        prompt = cur.fetchone()
        if not prompt:
            conn.close()
            raise ValueError("Prompt not found")
        if str(prompt["status"] or "") != "enabled":
            conn.close()
            raise ValueError("Only enabled prompts can be bound")
        cur.execute(
            """
            INSERT INTO audit_prompt_binding(scene_key, prompt_id, updated_by, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scene_key)
            DO UPDATE SET prompt_id = excluded.prompt_id, updated_by = excluded.updated_by, updated_at = excluded.updated_at
            """,
            (scene_key, normalized_prompt_id, updated_by, now),
        )
    else:
        cur.execute(
            "DELETE FROM audit_prompt_binding WHERE scene_key = ?", (scene_key,))
    conn.commit()
    conn.close()
    return get_prompt_binding(scene_key, runtime_cfg) or {}


def clear_prompt_bindings_for_prompt(prompt_id: str, cfg: Optional[dict] = None) -> None:
    normalized_prompt_id = str(prompt_id or "").strip()
    if not normalized_prompt_id:
        return
    runtime_cfg = cfg or get_config()
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM audit_prompt_binding WHERE prompt_id = ?",
                (normalized_prompt_id,))
    conn.commit()
    conn.close()


def restore_default_prompt(prompt_id: str, updated_by: str, cfg: Optional[dict] = None) -> Optional[dict]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    conn = get_conn(runtime_cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, prompt_key, managed_scope FROM audit_prompt WHERE id = ?",
        (str(prompt_id or "").strip(),),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    prompt_key = str(row["prompt_key"] or "").strip()
    managed_scope = str(row["managed_scope"] or "").strip()
    default_item = get_default_prompt_definition(prompt_key)
    if managed_scope != "system" or not default_item:
        conn.close()
        raise ValueError("Only system prompts can be restored to defaults")
    sample_input_json = json.dumps(
        default_item.get("sample_input_json") or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    localized_system_texts = _normalize_localized_texts(
        default_item.get("localized_system_texts"),
        str(default_item.get("system_text") or ""),
    )
    localized_focus_texts = _normalize_localized_texts(
        default_item.get("localized_focus_texts"),
    )
    localized_template_texts = _normalize_localized_texts(
        default_item.get("localized_template_texts"),
        str(default_item.get("template_text") or ""),
    )
    placeholder_map = _normalize_placeholder_map(
        default_item.get("placeholder_map"))
    runtime_system_text = _build_runtime_text(
        localized_system_texts,
        placeholder_map,
    ) or str(default_item.get("system_text") or "")
    runtime_template_text = _build_runtime_text(
        localized_template_texts,
        placeholder_map,
        localized_focus_texts,
    ) or str(default_item.get("template_text") or "")
    now = datetime.utcnow().isoformat()
    cur.execute(
        """
        UPDATE audit_prompt
        SET name = ?, description = ?, system_text = ?, template_text = ?,
            localized_system_texts = ?, localized_focus_texts = ?, localized_template_texts = ?,
            placeholder_map_json = ?, sample_input_json = ?, source_module = ?, status = 'enabled',
            updated_at = ?, created_by = CASE WHEN created_by = 'system' THEN ? ELSE created_by END
        WHERE id = ?
        """,
        (
            str(default_item.get("name") or prompt_key),
            str(default_item.get("description") or ""),
            runtime_system_text,
            runtime_template_text,
            _json_dumps(localized_system_texts),
            _json_dumps(localized_focus_texts),
            _json_dumps(localized_template_texts),
            _json_dumps(placeholder_map),
            sample_input_json,
            str(default_item.get("source_module") or ""),
            now,
            updated_by or "system",
            str(prompt_id or "").strip(),
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?",
                (str(prompt_id or "").strip(),))
    restored = cur.fetchone()
    conn.close()
    return dict(restored) if restored else None


def render_prompt_messages(
    prompt_key: str,
    context: Dict[str, Any],
    cfg: Optional[dict] = None,
    fallback_system_text: str = "",
    fallback_template_text: str = "",
) -> Dict[str, Any]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    prompt_row = get_prompt_record_by_key(prompt_key, runtime_cfg)
    default_item = get_default_prompt_definition(prompt_key)
    return _render_prompt_record(
        prompt_key=prompt_key,
        prompt_row=prompt_row,
        default_item=default_item,
        context=context,
        fallback_system_text=fallback_system_text,
        fallback_template_text=fallback_template_text,
    )


def resolve_bound_prompt_text(
    scene_key: str,
    fallback_template: str,
    context: Dict[str, Any],
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    runtime_cfg = cfg or get_config()
    sync_default_prompts(runtime_cfg)
    binding = get_prompt_binding(scene_key, runtime_cfg)
    if binding:
        prompt_id = str(binding.get("prompt_id") or "").strip()
        if prompt_id:
            prompt_row = get_prompt_record(prompt_id, runtime_cfg)
            if prompt_row and str(prompt_row.get("status") or "") == "enabled":
                rendered = _render_prompt_record(
                    prompt_key=str(prompt_row.get("prompt_key") or ""),
                    prompt_row=prompt_row,
                    default_item=get_default_prompt_definition(
                        str(prompt_row.get("prompt_key") or "")),
                    context=context,
                    fallback_system_text="",
                    fallback_template_text=fallback_template,
                )
                if rendered.get("error"):
                    default_prompt = get_default_scene_prompt(
                        scene_key, runtime_cfg)
                    default_item = get_default_prompt_definition(
                        str(default_prompt.get("prompt_key") or "")
                    ) if default_prompt else None
                    fallback_rendered = _render_prompt_record(
                        prompt_key=str(default_prompt.get("prompt_key") or ""),
                        prompt_row=default_prompt,
                        default_item=default_item,
                        context=context,
                        fallback_system_text="",
                        fallback_template_text=fallback_template,
                    )
                    fallback_rendered.update(
                        {"source": "system_default", "scene_key": scene_key})
                    return fallback_rendered
                resolved_prompt_id = str(
                    rendered.get("prompt_id") or "").strip()
                rendered.update(
                    {
                        "source": "bound_prompt" if resolved_prompt_id == prompt_id else "system_default",
                        "scene_key": scene_key,
                        "updated_at": str(binding.get("updated_at") or ""),
                        "updated_by": str(binding.get("updated_by") or ""),
                    }
                )
                return rendered
    default_prompt = get_default_scene_prompt(scene_key, runtime_cfg)
    default_item = get_default_prompt_definition(
        str(default_prompt.get("prompt_key") or "")) if default_prompt else None
    rendered = _render_prompt_record(
        prompt_key=str(default_prompt.get("prompt_key") or ""),
        prompt_row=default_prompt,
        default_item=default_item,
        context=context,
        fallback_system_text="",
        fallback_template_text=fallback_template,
    )
    rendered.update({"source": "system_default", "scene_key": scene_key})
    return rendered


def _default_item_to_row(item: dict) -> dict:
    prompt_key = str(item.get("prompt_key") or "").strip()
    localized_system_texts = _normalize_localized_texts(
        item.get("localized_system_texts"),
        str(item.get("system_text") or ""),
    )
    localized_focus_texts = _normalize_localized_texts(
        item.get("localized_focus_texts"),
    )
    localized_template_texts = _normalize_localized_texts(
        item.get("localized_template_texts"),
        str(item.get("template_text") or ""),
    )
    placeholder_map = _normalize_placeholder_map(item.get("placeholder_map"))
    sample_input_json = json.dumps(
        item.get("sample_input_json") or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {
        "id": prompt_key,
        "prompt_key": prompt_key,
        "name": str(item.get("name") or prompt_key),
        "description": str(item.get("description") or ""),
        "system_text": _build_runtime_text(localized_system_texts, placeholder_map) or str(item.get("system_text") or ""),
        "template_text": _build_runtime_text(localized_template_texts, placeholder_map, localized_focus_texts) or str(item.get("template_text") or ""),
        "localized_system_texts": _json_dumps(localized_system_texts),
        "localized_focus_texts": _json_dumps(localized_focus_texts),
        "localized_template_texts": _json_dumps(localized_template_texts),
        "placeholder_map_json": _json_dumps(placeholder_map),
        "sample_input_json": sample_input_json,
        "source_module": str(item.get("source_module") or ""),
        "managed_scope": "system",
        "status": "enabled",
        "created_by": "system",
        "created_at": "",
        "updated_at": "",
    }


def _render_prompt_record(
    *,
    prompt_key: str,
    prompt_row: Optional[dict],
    default_item: Optional[dict],
    context: Dict[str, Any],
    fallback_system_text: str,
    fallback_template_text: str,
) -> Dict[str, Any]:
    env = Environment(undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    row = prompt_row or (_default_item_to_row(
        default_item) if default_item else None)
    if not row:
        return {
            "prompt_id": "",
            "prompt_key": prompt_key,
            "prompt_name": "",
            "rendered_system_text": fallback_system_text,
            "rendered_text": fallback_template_text,
            "system_text": fallback_system_text,
            "template_text": fallback_template_text,
            "source_module": "",
        }
    prompt_id = str(row.get("id") or "")
    prompt_name = str(row.get("name") or "")
    system_text = str(row.get("system_text") or "")
    template_text = str(row.get("template_text") or "")
    if str(row.get("status") or "") != "enabled" or not template_text.strip():
        fallback_row = _default_item_to_row(
            default_item) if default_item else None
        if fallback_row and fallback_row.get("id") != prompt_id:
            return _render_prompt_record(
                prompt_key=prompt_key,
                prompt_row=fallback_row,
                default_item=None,
                context=context,
                fallback_system_text=fallback_system_text,
                fallback_template_text=fallback_template_text,
            )
        return {
            "prompt_id": prompt_id,
            "prompt_key": prompt_key,
            "prompt_name": prompt_name,
            "rendered_system_text": fallback_system_text,
            "rendered_text": fallback_template_text,
            "system_text": system_text or fallback_system_text,
            "template_text": template_text or fallback_template_text,
            "source_module": str(row.get("source_module") or ""),
        }
    try:
        rendered_system_text = (
            env.from_string(system_text).render(**dict(context or {}))
            if system_text.strip()
            else ""
        )
        rendered_text = env.from_string(
            template_text).render(**dict(context or {}))
    except (TemplateSyntaxError, TemplateError, TypeError, ValueError) as exc:
        logger.warning(
            "managed_prompt_render_failed prompt_key=%s prompt_id=%s err=%s",
            prompt_key,
            prompt_id,
            str(exc),
        )
        fallback_row = _default_item_to_row(
            default_item) if default_item else None
        if fallback_row and fallback_row.get("id") != prompt_id:
            return _render_prompt_record(
                prompt_key=prompt_key,
                prompt_row=fallback_row,
                default_item=None,
                context=context,
                fallback_system_text=fallback_system_text,
                fallback_template_text=fallback_template_text,
            )
        return {
            "prompt_id": prompt_id,
            "prompt_key": prompt_key,
            "prompt_name": prompt_name,
            "rendered_system_text": fallback_system_text,
            "rendered_text": fallback_template_text,
            "system_text": system_text or fallback_system_text,
            "template_text": template_text or fallback_template_text,
            "source_module": str(row.get("source_module") or ""),
            "error": str(exc),
        }
    return {
        "prompt_id": prompt_id,
        "prompt_key": str(row.get("prompt_key") or prompt_key),
        "prompt_name": prompt_name,
        "rendered_system_text": rendered_system_text or fallback_system_text,
        "rendered_text": rendered_text or fallback_template_text,
        "system_text": system_text,
        "template_text": template_text,
        "source_module": str(row.get("source_module") or ""),
        "managed_scope": str(row.get("managed_scope") or "custom"),
        "status": str(row.get("status") or "enabled"),
    }
