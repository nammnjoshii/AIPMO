"""Policy Engine — evaluates proposed agent actions against YAML-configured rules.

Failure mode: FAIL CLOSED.
Any exception during evaluation returns PolicyAction.DENY — never propagates to caller.

Policy precedence (highest → lowest):
  1. Regulatory / compliance  (hardcoded — never overridable)
  2. Organisation-wide        (global scope in YAML)
  3. Portfolio / program
  4. Project-specific
  5. Agent / skill-specific

CLI:
  python -m policy.engine --load configs/policies.yaml
  python -m policy.engine --validate configs/policies.yaml
  python -m policy.engine --reload
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import yaml

from agents.base_agent import PolicyAction
from policy.schemas import PolicyEvaluationResult, ProjectPolicy

logger = logging.getLogger(__name__)

# ---- Hardcoded regulatory rules — these can never be overridden by YAML ----
_REGULATORY_DENY: frozenset[str] = frozenset({
    "delete_audit_record",
    "modify_audit_record",
    "bypass_policy_engine",
    "hardcode_credentials",
})

_REGULATORY_APPROVAL: frozenset[str] = frozenset({
    "bulk_data_export",
    "cross_tenant_data_access",
})


class PolicyEngine:
    """Loads YAML policy configs and evaluates proposed actions.

    Thread-safe for reads. reload() updates state atomically by replacing
    the internal policy dict in a single assignment.
    """

    def __init__(self) -> None:
        self._policies: Dict[str, ProjectPolicy] = {}   # key: project_id or "global"
        self._config_path: Optional[str] = None

    # ---- Loading ----

    def load(self, config_path: str) -> None:
        """Load and validate a YAML policy file. Merges into active policy set."""
        policy = _load_yaml_policy(config_path)
        key = policy.project_id or policy.scope
        self._policies[key] = policy
        self._config_path = config_path
        logger.info(
            "PolicyEngine: loaded policy key=%s version=%s from %s",
            key,
            policy.version,
            config_path,
        )

    def reload(self) -> None:
        """Hot-reload the last-loaded policy file without restarting."""
        if self._config_path is None:
            logger.warning("PolicyEngine.reload: no config path set — nothing to reload.")
            return
        self.load(self._config_path)
        logger.info("PolicyEngine: reloaded policy from %s", self._config_path)

    # ---- Evaluation ----

    def evaluate(
        self,
        action: str,
        project_id: str,
        agent_name: str = "unknown_agent",
        proposed_by: str = "agent",
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyEvaluationResult:
        """Evaluate a proposed action against the active policy set.

        Failure mode: any exception returns DENY — never raises.

        Precedence:
          1. Regulatory hardcoded rules
          2. Project-specific policy
          3. Global policy
          4. unknown_action_default (from loaded policy or DENY)
        """
        try:
            return self._evaluate(action, project_id, agent_name, proposed_by, context or {})
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PolicyEngine.evaluate: unexpected exception for action=%s project=%s: %s — "
                "failing closed with DENY.",
                action,
                project_id,
                exc,
                exc_info=True,
            )
            return PolicyEvaluationResult(
                project_id=project_id,
                agent_name=agent_name,
                action=action,
                proposed_by=proposed_by,
                policy_action=PolicyAction.DENY,
                justification=(
                    f"Policy engine error during evaluation — failing closed. "
                    f"Original error: {exc}"
                ),
                requires_human_approval=False,
            )

    def evaluate_threshold(
        self,
        metric: str,
        value: float,
        project_id: str,
        agent_name: str = "unknown_agent",
    ) -> PolicyEvaluationResult:
        """Evaluate a metric value against threshold rules.

        Failure mode: any exception returns DENY.
        """
        try:
            return self._evaluate_threshold(metric, value, project_id, agent_name)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PolicyEngine.evaluate_threshold: exception for metric=%s value=%s: %s — DENY.",
                metric,
                value,
                exc,
                exc_info=True,
            )
            return PolicyEvaluationResult(
                project_id=project_id,
                agent_name=agent_name,
                action=f"threshold:{metric}",
                proposed_by="engine",
                policy_action=PolicyAction.DENY,
                justification=f"Policy engine error evaluating threshold — failing closed: {exc}",
            )

    # ---- Internal ----

    def _evaluate(
        self,
        action: str,
        project_id: str,
        agent_name: str,
        proposed_by: str,
        context: Dict[str, Any],
    ) -> PolicyEvaluationResult:
        # Step 1 — Regulatory hardcoded rules (cannot be overridden)
        if action in _REGULATORY_DENY:
            return PolicyEvaluationResult(
                project_id=project_id,
                agent_name=agent_name,
                action=action,
                proposed_by=proposed_by,
                policy_action=PolicyAction.DENY,
                justification="Regulatory rule: this action is unconditionally denied.",
                requires_human_approval=False,
            )

        if action in _REGULATORY_APPROVAL:
            return PolicyEvaluationResult(
                project_id=project_id,
                agent_name=agent_name,
                action=action,
                proposed_by=proposed_by,
                policy_action=PolicyAction.APPROVAL_REQUIRED,
                justification="Regulatory rule: this action requires explicit human approval.",
                requires_human_approval=True,
            )

        # Step 2 — Project-specific policy
        project_policy = self._policies.get(project_id)
        if project_policy and action in project_policy.actions:
            outcome = project_policy.actions[action]
            return self._make_result(
                action, project_id, agent_name, proposed_by, outcome,
                justification=f"Project policy v{project_policy.version}: action='{action}' → {outcome}",
            )

        # Step 3 — Global / org-wide policy
        global_policy = self._policies.get("global") or self._policies.get("project")
        if global_policy and action in global_policy.actions:
            outcome = global_policy.actions[action]
            return self._make_result(
                action, project_id, agent_name, proposed_by, outcome,
                justification=f"Global policy v{global_policy.version}: action='{action}' → {outcome}",
            )

        # Step 4 — Default: fail closed
        default = PolicyAction.DENY
        if global_policy:
            default = global_policy.unknown_action_default
        elif project_policy:
            default = project_policy.unknown_action_default

        logger.warning(
            "PolicyEngine: action='%s' not found in any policy for project=%s — "
            "applying default=%s",
            action,
            project_id,
            default,
        )
        return self._make_result(
            action, project_id, agent_name, proposed_by, default,
            justification=f"Action '{action}' not found in policy — applying default: {default}",
        )

    def _evaluate_threshold(
        self,
        metric: str,
        value: float,
        project_id: str,
        agent_name: str,
    ) -> PolicyEvaluationResult:
        # Try project-specific thresholds first, then global
        for key in (project_id, "global", "project"):
            policy = self._policies.get(key)
            if policy and metric in policy.thresholds:
                rule = policy.thresholds[metric]
                outcome, justification = _apply_threshold_rule(metric, value, rule)
                return self._make_result(
                    action=f"threshold:{metric}",
                    project_id=project_id,
                    agent_name=agent_name,
                    proposed_by="engine",
                    outcome=outcome,
                    justification=justification,
                )

        # No threshold rule found — allow
        return self._make_result(
            action=f"threshold:{metric}",
            project_id=project_id,
            agent_name=agent_name,
            proposed_by="engine",
            outcome=PolicyAction.ALLOW,
            justification=f"No threshold rule defined for metric '{metric}' — allowing.",
        )

    @staticmethod
    def _make_result(
        action: str,
        project_id: str,
        agent_name: str,
        proposed_by: str,
        outcome: PolicyAction,
        justification: str,
    ) -> PolicyEvaluationResult:
        requires_approval = outcome in (
            PolicyAction.APPROVAL_REQUIRED,
            PolicyAction.ESCALATE,
        )
        return PolicyEvaluationResult(
            project_id=project_id,
            agent_name=agent_name,
            action=action,
            proposed_by=proposed_by,
            policy_action=outcome,
            justification=justification,
            requires_human_approval=requires_approval,
        )


# ---- Helpers ----

def _load_yaml_policy(config_path: str) -> ProjectPolicy:
    """Load and validate a YAML policy file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Policy config not found: {config_path}")

    with path.open() as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Policy file is empty: {config_path}")

    # Normalise PolicyAction strings in the actions dict
    if "actions" in raw and isinstance(raw["actions"], dict):
        raw["actions"] = {
            k: PolicyAction(v) for k, v in raw["actions"].items()
        }
    if "unknown_action_default" in raw:
        raw["unknown_action_default"] = PolicyAction(raw["unknown_action_default"])

    return ProjectPolicy.model_validate(raw)


def _apply_threshold_rule(
    metric: str, value: float, rule: dict
) -> tuple[PolicyAction, str]:
    """Evaluate a single threshold rule dict against a metric value."""
    if rule.get("escalate_if_greater_than") is not None and value > rule["escalate_if_greater_than"]:
        return (
            PolicyAction.ESCALATE,
            f"metric '{metric}'={value:.3f} exceeds escalation threshold "
            f"{rule['escalate_if_greater_than']}",
        )
    if rule.get("approval_required_if_greater_than") is not None and value > rule["approval_required_if_greater_than"]:
        return (
            PolicyAction.APPROVAL_REQUIRED,
            f"metric '{metric}'={value:.3f} exceeds approval threshold "
            f"{rule['approval_required_if_greater_than']}",
        )
    if rule.get("notify_if_greater_than") is not None and value > rule["notify_if_greater_than"]:
        return (
            PolicyAction.ALLOW_WITH_AUDIT,
            f"metric '{metric}'={value:.3f} exceeds notify threshold "
            f"{rule['notify_if_greater_than']} — audit log required",
        )
    if rule.get("sparsity_alert_if_less_than") is not None and value < rule["sparsity_alert_if_less_than"]:
        return (
            PolicyAction.ALLOW_WITH_AUDIT,
            f"metric '{metric}'={value:.3f} below sparsity threshold "
            f"{rule['sparsity_alert_if_less_than']} — sparsity alert",
        )
    return (
        PolicyAction.ALLOW,
        f"metric '{metric}'={value:.3f} within all policy thresholds",
    )


# ---- CLI entry point ----

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Autonomous PMO Policy Engine CLI")
    parser.add_argument("--load", metavar="PATH", help="Load and activate a policy YAML file")
    parser.add_argument("--validate", metavar="PATH", help="Validate a policy YAML file without loading")
    parser.add_argument("--reload", action="store_true", help="Hot-reload the active policy (requires prior --load)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.validate:
        try:
            policy = _load_yaml_policy(args.validate)
            print(f"OK: policy v{policy.version} scope={policy.scope} — validation passed.")
        except Exception as exc:
            print(f"INVALID: {exc}")
            raise SystemExit(1) from exc

    elif args.load:
        engine = PolicyEngine()
        engine.load(args.load)
        print(f"Loaded policy from {args.load}.")

    elif args.reload:
        print("Note: --reload applies to a running engine instance, not a one-off CLI call.")

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
