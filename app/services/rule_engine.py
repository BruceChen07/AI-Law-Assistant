import json
import logging
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("law_assistant")


class RuleCondition(BaseModel):
    field: str = Field(..., description="The entity field to check, e.g., 'taxpayer_type', 'transaction_type'")
    operator: str = Field(
        ..., description="Logical operator: '==', '!=', 'in', 'not_in', 'contains', '>', '<', '>=' ,'<='")
    value: Union[str, int, float, List[Union[str, int, float]]
                 ] = Field(..., description="The value to compare against")


class RuleConstraint(BaseModel):
    field: str = Field(..., description="The constraint field, e.g., 'tax_rate', 'penalty_rate', 'deadline_days'")
    operator: str = Field(
        ..., description="Constraint operator: '==', '!=', '>', '<', '>=', '<=', 'in'")
    value: Union[str, int, float, List[Union[str, int, float]]
                 ] = Field(..., description="The constrained value")


class RuleAction(BaseModel):
    risk_level: str = Field(
        ..., description="Risk level if constraint is violated: 'high', 'medium', 'low'")
    alert_message: str = Field(
        ..., description="Template for the alert message, e.g., '税率应为 {expected}，合同约定为 {actual}'")


class TaxRuleDSL(BaseModel):
    rule_id: str = Field(
        ..., description="Unique identifier for the rule, usually tied to article ID")
    rule_type: str = Field(
        ..., description="Type of rule: 'tax_rate', 'invoice', 'withholding', 'penalty', 'none'")
    tax_category: Optional[str] = Field(
        None, description="Tax category, e.g., 'VAT', 'CIT', 'PIT'")
    trigger_conditions: List[RuleCondition] = Field(
        default_factory=list, description="Conditions that must all be met (AND logic) to trigger this rule")
    numeric_constraints: List[RuleConstraint] = Field(
        default_factory=list, description="Constraints that must be satisfied if triggered")
    action_if_violated: Optional[RuleAction] = Field(
        None, description="Action to take if the constraints are not met")

    def to_json_string(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json_string(cls, json_str: str) -> "TaxRuleDSL":
        return cls.model_validate_json(json_str)


def _evaluate_operator(left: Any, operator: str, right: Any) -> bool:
    try:
        if operator == "==":
            return str(left).lower() == str(right).lower() if isinstance(left, str) else left == right
        elif operator == "!=":
            return str(left).lower() != str(right).lower() if isinstance(left, str) else left != right
        elif operator == "in":
            if not isinstance(right, list):
                return False
            return left in right if not isinstance(left, str) else any(str(left).lower() == str(r).lower() for r in right)
        elif operator == "not_in":
            if not isinstance(right, list):
                return False
            return left not in right if not isinstance(left, str) else all(str(left).lower() != str(r).lower() for r in right)
        elif operator == "contains":
            return str(right).lower() in str(left).lower()

        # Numeric operators
        left_val = float(str(left).replace("%", "")) / \
            100 if isinstance(left, str) and "%" in left else float(left)
        right_val = float(str(right).replace(
            "%", "")) / 100 if isinstance(right, str) and "%" in right else float(right)

        if operator == ">":
            return left_val > right_val
        if operator == "<":
            return left_val < right_val
        if operator == ">=":
            return left_val >= right_val
        if operator == "<=":
            return left_val <= right_val
    except Exception as e:
        logger.warning(
            f"Error evaluating operator {operator} for {left} and {right}: {e}")
        return False
    return False


def evaluate_rule(entities: Dict[str, Any], rule_dsl: TaxRuleDSL) -> Dict[str, Any]:
    """
    Evaluates extracted contract entities against a single TaxRuleDSL.
    Returns a dict with 'matched' (bool), 'passed' (bool), and 'action' (dict).
    - matched: True if trigger_conditions are met (or empty) AND rule is applicable
    - passed: True if matched AND numeric_constraints are met
    """
    if rule_dsl.rule_type == "none":
        return {"matched": False, "passed": True}

    # Check tax_category early exit
    if rule_dsl.tax_category and entities.get("tax_category"):
        if str(rule_dsl.tax_category).lower() != str(entities.get("tax_category")).lower():
            return {"matched": False, "passed": True}

    # 1. Check trigger conditions (AND logic)
    matched = True
    for cond in rule_dsl.trigger_conditions:
        entity_val = entities.get(cond.field)
        if entity_val is None:
            matched = False  # Missing required field to trigger
            break
        if not _evaluate_operator(entity_val, cond.operator, cond.value):
            matched = False
            break

    if not matched:
        return {"matched": False, "passed": True}

    # 2. Check constraints (AND logic)
    passed = True
    violated_field = None
    for const in rule_dsl.numeric_constraints:
        entity_val = entities.get(const.field)
        if entity_val is None:
            # If we matched the conditions but the constraint field is missing, it's a violation (missing obligation)
            passed = False
            violated_field = const.field
            break

        if not _evaluate_operator(entity_val, const.operator, const.value):
            passed = False
            violated_field = const.field
            break

    action_dict = None
    if not passed and rule_dsl.action_if_violated:
        action_dict = rule_dsl.action_if_violated.model_dump()

    return {
        "matched": True,
        "passed": passed,
        "violated_field": violated_field,
        "action": action_dict
    }
