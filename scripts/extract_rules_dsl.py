import os
import sys
import json
import logging
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_config
from app.core.database import get_conn
from app.core.llm import LLMService
from app.services.rule_engine import TaxRuleDSL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("extract_rules_dsl")

EXTRACTION_PROMPT = """你是一个专业的中国财税法专家。
请阅读以下法规条款，如果其中包含明确的适用条件、税率约束或强制动作，请提取并输出为规定的 JSON 格式。
如果是纯概念性描述，或者没有明确数值和强制动作的条款，则返回 rule_type: "none"。

JSON 规范如下（请严格遵守，不要输出其他无关内容）：
{
  "rule_id": "法规条款唯一标识",
  "rule_type": "tax_rate(税率) | invoice(发票) | withholding(代扣代缴) | penalty(违约金) | none(纯概念或无约束)",
  "tax_category": "VAT(增值税) | CIT(企业所得税) | PIT(个人所得税) | null(如果无关)",
  "trigger_conditions": [
    {"field": "taxpayer_type", "operator": "==", "value": "一般纳税人"}
  ],
  "numeric_constraints": [
    {"field": "tax_rate", "operator": "==", "value": 0.13}
  ],
  "action_if_violated": {
    "risk_level": "high | medium | low",
    "alert_message": "例如：一般纳税人增值税税率应为13%"
  }
}

待分析条款：
【条款ID】：{article_id}
【条款内容】：
{content}
"""

def process_batch(cfg: Dict[str, Any], limit: int = 50):
    conn = get_conn(cfg)
    cur = conn.cursor()
    
    # 查找尚未进行 DSL 抽取的条款
    cur.execute("SELECT id, content FROM article WHERE rule_type IS NULL LIMIT ?", (limit,))
    rows = cur.fetchall()
    
    if not rows:
        logger.info("No articles need DSL extraction.")
        conn.close()
        return

    llm = LLMService(cfg)
    
    for row in rows:
        article_id = row["id"]
        content = row["content"]
        
        if not content.strip():
            _mark_empty(conn, article_id)
            continue
            
        logger.info(f"Processing article {article_id}...")
        prompt = EXTRACTION_PROMPT.format(article_id=article_id, content=content)
        
        try:
            # call LLM
            messages = [{"role": "user", "content": prompt}]
            # We enforce JSON output if the model supports it, but standard prompt is usually fine
            text, _ = llm.chat(messages, overrides={"temperature": 0.1})
            
            # Clean text to find JSON block
            text = text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            parsed_json = json.loads(text)
            
            # Validate via Pydantic
            dsl = TaxRuleDSL(**parsed_json)
            
            # Update database
            cur.execute("""
                UPDATE article 
                SET rule_type = ?, conditions_json = ?, constraints_json = ?
                WHERE id = ?
            """, (
                dsl.rule_type,
                json.dumps([c.model_dump() for c in dsl.trigger_conditions], ensure_ascii=False) if dsl.trigger_conditions else "[]",
                json.dumps([c.model_dump() for c in dsl.numeric_constraints], ensure_ascii=False) if dsl.numeric_constraints else "[]",
                article_id
            ))
            conn.commit()
            logger.info(f"Successfully extracted DSL for {article_id}: {dsl.rule_type}")
            
        except Exception as e:
            logger.error(f"Failed to process {article_id}: {e}")
            _mark_error(conn, article_id)
            
    conn.close()

def _mark_empty(conn, article_id: str):
    cur = conn.cursor()
    cur.execute("UPDATE article SET rule_type = 'none', conditions_json = '[]', constraints_json = '[]' WHERE id = ?", (article_id,))
    conn.commit()

def _mark_error(conn, article_id: str):
    cur = conn.cursor()
    cur.execute("UPDATE article SET rule_type = 'error' WHERE id = ?", (article_id,))
    conn.commit()

if __name__ == "__main__":
    config = get_config()
    process_batch(config, limit=200)
