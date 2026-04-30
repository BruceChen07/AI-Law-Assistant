import re
import json
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from app.core.llm import LLMService
from app.core.embedding import EmbeddingService
from app.services.crud import (
    get_tax_contract_document,
    list_contract_clauses,
    list_tax_rules,
    clear_clause_rule_matches_by_contract,
    create_clause_rule_matches,
)
from app.services.rule_engine import evaluate_rule, TaxRuleDSL
from app.services.tax_common import is_tax_related_text, parse_llm_json_object

logger = logging.getLogger("law_assistant")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _cosine_similarity(vec1, vec2):
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    if np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
        return 0.0
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def _safe_float(v: any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def evaluate_clause_rule_match_llm(clause: dict, rule: dict, cfg: dict, llm: LLMService = None) -> dict:
    clause_text = str(clause.get("clause_text") or "")
    rule_text = " ".join(
        [
            str(rule.get("source_text") or ""),
            str(rule.get("required_action") or ""),
            str(rule.get("prohibited_action") or ""),
            str(rule.get("numeric_constraints") or ""),
            str(rule.get("deadline_constraints") or ""),
        ]
    ).strip()

    if llm is None:
        raise ValueError("llm service is required")

    prompt = f"""
    You are an expert tax and legal auditor. 
    Analyze the relationship between the following contract clause and the tax rule.
    
    Contract Clause:
    "{clause_text}"
    
    Tax Rule:
    "{rule_text}"
    
    Determine if the clause complies with the rule.
    Return ONLY a JSON object with the following structure:
    {{
        "label": "compliant" | "non_compliant" | "not_mentioned",
        "score": float between 0.0 and 1.0 (confidence score),
        "reason": "short explanation of the relationship in English or Chinese"
    }}
    """

    label = "not_mentioned"
    score = 0.12
    reason = "LLM analysis failed or timeout"

    try:
        response, _ = llm.chat([{"role": "user", "content": prompt}])
        result = parse_llm_json_object(response)
        label = result.get("label", "not_mentioned")
        score = float(result.get("score", 0.5))
        reason = result.get("reason", "")
    except Exception as e:
        logger.error(f"LLM match evaluation failed: {e}")

    evidence = {
        "reason": reason,
        "clause_excerpt": clause_text[:300],
        "rule_excerpt": str(rule.get("source_text") or "")[:300],
        "rule_type": rule.get("rule_type", ""),
        "rule_article_no": rule.get("article_no", ""),
        "evaluated_at": _utc_now_iso(),
    }
    return {
        "clause_id": clause.get("id", ""),
        "rule_id": rule.get("id", ""),
        "match_score": round(float(score), 4),
        "match_label": label,
        "evidence_json": json.dumps(evidence, ensure_ascii=False),
    }


def evaluate_clause_rule_match(clause: dict, rule: dict, cfg: dict = None, llm: LLMService = None) -> dict:
    """
    Evaluates if a clause matches a rule. 
    First tries the hard DSL rule engine. If it fails or is inapplicable, falls back to LLM.
    """
    clause_id = clause.get("id")
    rule_id = rule.get("id")

    # 1. Extract entities and try DSL rule engine
    entities = {}
    if clause.get("entities_json"):
        try:
            entities = json.loads(clause.get("entities_json"))
        except Exception:
            pass

    # Build TaxRuleDSL object if DB fields exist
    # Note: the current tax_rule table does not have the JSON fields yet, they are in the article table.
    # We will fallback to LLM if DSL is missing.

    # 2. Fallback to LLM if DSL is not present or inapplicable
    llm_cfg = (cfg or {}).get("llm_config") if isinstance(
        (cfg or {}).get("llm_config"), dict) else {}
    llm_ready = bool(str(llm_cfg.get("api_base") or "").strip()) and bool(
        str(llm_cfg.get("model") or "").strip())
    if cfg and llm_ready:
        return evaluate_clause_rule_match_llm(clause, rule, cfg, llm)

    # Simple fallback for tests without LLM config
    c_text = str(clause.get("clause_text") or "")
    r_text = str(rule.get("source_text") or "")
    num = str(rule.get("numeric_constraints") or "")
    label = "not_mentioned"
    if num and num in c_text:
        label = "compliant"
    elif num and num not in c_text and "%" in c_text:
        label = "non_compliant"

    return {
        "clause_id": clause_id,
        "rule_id": rule_id,
        "match_label": label,
        "match_score": 0.8 if label != "not_mentioned" else 0.2,
        "evidence_json": json.dumps(
            {
                "clause_excerpt": c_text[:100],
                "rule_excerpt": r_text[:100],
                "reason": "Fallback simple match",
            },
            ensure_ascii=False,
        ),
    }


def _pick_matches_for_clause(evaluated: list[dict], top_k: int = 5) -> list[dict]:
    ranked = sorted(
        evaluated,
        key=lambda x: (
            0 if x["match_label"] == "non_compliant" else (
                1 if x["match_label"] == "not_mentioned" else 2),
            -float(x["match_score"]),
        ),
    )
    selected = ranked[:max(1, int(top_k))]
    extra = [x for x in ranked if x["match_label"]
             == "non_compliant" and x not in selected]
    return selected + extra


def match_contract_against_rules(
    cfg,
    contract_id: str,
    operator_id: str = "",
    top_k_per_clause: int = 5,
    llm: LLMService = None,
    embedder: EmbeddingService = None,
) -> dict:
    contract = get_tax_contract_document(cfg, contract_id)
    if not contract:
        raise ValueError("contract document not found")
    clauses = list_contract_clauses(cfg, contract_id, limit=5000)
    if not clauses:
        raise ValueError("contract clauses not found, run analyze first")
    rules = list_tax_rules(cfg, limit=5000)
    if not rules:
        raise ValueError("tax rules not found, run regulation parse first")
    logger.info(
        "tax_match_start contract_id=%s operator=%s clauses=%s rules=%s top_k_per_clause=%s",
        contract_id,
        operator_id,
        len(clauses),
        len(rules),
        int(top_k_per_clause),
    )

    # 1. Initialize services
    if embedder is None:
        raise ValueError("embedding service is required")
    if llm is None:
        raise ValueError("llm service is required")

    # 2. Get embeddings for rules
    rule_texts = [
        " ".join([
            str(r.get("source_text") or ""),
            str(r.get("required_action") or ""),
            str(r.get("prohibited_action") or ""),
            str(r.get("numeric_constraints") or ""),
            str(r.get("deadline_constraints") or ""),
        ]).strip() for r in rules
    ]

    # Simple language detection for embedding
    sample_text = " ".join(rule_texts[:5])
    lang = "en" if len(re.findall(r"[A-Za-z]", sample_text)) > len(
        re.findall(r"[\u4e00-\u9fff]", sample_text)) else "zh"

    rule_embeddings = []
    for text in rule_texts:
        emb = embedder.compute_embedding(text, lang=lang)
        if emb is not None:
            rule_embeddings.append(emb)
        else:
            rule_embeddings.append(
                np.zeros(16, dtype=np.float32))  # Dummy fallback

    all_matches = []

    # 3. Process clauses concurrently
    def process_clause(clause):
        clause_text = str(clause.get("clause_text") or "")
        if not is_tax_related_text(clause_text):
            fallback_rule = rules[0] if rules else {}
            evidence = {
                "reason": "fast_path_not_tax_related_clause",
                "clause_excerpt": clause_text[:300],
                "rule_excerpt": str(fallback_rule.get("source_text") or "")[:300],
                "rule_type": fallback_rule.get("rule_type", ""),
                "rule_article_no": fallback_rule.get("article_no", ""),
                "evaluated_at": _utc_now_iso(),
            }
            return [
                {
                    "clause_id": clause.get("id", ""),
                    "rule_id": fallback_rule.get("id", ""),
                    "match_score": 0.0,
                    "match_label": "not_mentioned",
                    "evidence_json": json.dumps(evidence, ensure_ascii=False),
                }
            ]
        clause_emb = embedder.compute_embedding(clause_text, lang=lang)
        if clause_emb is None:
            clause_emb = np.zeros(16, dtype=np.float32)

        # Calculate similarities
        similarities = []
        for idx, rule_emb in enumerate(rule_embeddings):
            sim = _cosine_similarity(clause_emb, rule_emb)
            similarities.append((idx, sim))

        # Top-K relevant rules by vector similarity
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_indices = [
            # Threshold
            idx for idx, sim in similarities[:top_k_per_clause] if sim > 0.3]

        if not top_indices:
            # If no rule is semantically close, fallback to top 1 just to be safe or skip
            top_indices = [similarities[0][0]] if similarities else []

        evaluated = []
        sim_dict = dict(similarities)
        for idx in top_indices:
            rule = rules[idx]
            match_result = evaluate_clause_rule_match(
                clause, rule, cfg, llm)
            # Add semantic similarity as part of the score or metadata if needed
            match_result["match_score"] = float(sim_dict.get(idx, 0.0))
            evaluated.append(match_result)

        return _pick_matches_for_clause(evaluated, top_k=top_k_per_clause)

    with ThreadPoolExecutor(max_workers=cfg.get("tax_audit_max_workers", 4)) as executor:
        results = executor.map(process_clause, clauses)
        for matches in results:
            all_matches.extend(matches)

    clear_clause_rule_matches_by_contract(cfg, contract_id)
    create_clause_rule_matches(cfg, all_matches, created_by=operator_id)
    compliant = len(
        [x for x in all_matches if x["match_label"] == "compliant"])
    non_compliant = len(
        [x for x in all_matches if x["match_label"] == "non_compliant"])
    not_mentioned = len(
        [x for x in all_matches if x["match_label"] == "not_mentioned"])
    logger.info(
        "tax_match_done contract_id=%s total=%s compliant=%s non_compliant=%s not_mentioned=%s",
        contract_id,
        len(all_matches),
        compliant,
        non_compliant,
        not_mentioned,
    )
    return {
        "contract_id": contract_id,
        "total_matches": len(all_matches),
        "compliant_count": compliant,
        "non_compliant_count": non_compliant,
        "not_mentioned_count": not_mentioned,
    }
