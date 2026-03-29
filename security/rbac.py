"""Role-Based Access Control for Autonomous PMO.

Roles and permissions match README.md definitions.
All permission checks go through has_permission() — do not inline checks in agent code.

Roles:
  EXECUTIVE_SPONSOR, PROGRAM_DIRECTOR, PROJECT_MANAGER,
  TEAM_MEMBER, AUDITOR, AGENT_RUNTIME

Permissions:
  VIEW_PORTFOLIO, VIEW_PROGRAM, VIEW_PROJECT, VIEW_TASK,
  VIEW_AUDIT_LOGS, APPROVE_ESCALATION, GENERATE_REPORT,
  MODIFY_POLICY
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class Role(str, Enum):
    EXECUTIVE_SPONSOR = "executive_sponsor"
    PROGRAM_DIRECTOR = "program_director"
    PROJECT_MANAGER = "project_manager"
    TEAM_MEMBER = "team_member"
    AUDITOR = "auditor"
    AGENT_RUNTIME = "agent_runtime"


class Permission(str, Enum):
    VIEW_PORTFOLIO = "view_portfolio"
    VIEW_PROGRAM = "view_program"
    VIEW_PROJECT = "view_project"
    VIEW_TASK = "view_task"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    APPROVE_ESCALATION = "approve_escalation"
    GENERATE_REPORT = "generate_report"
    MODIFY_POLICY = "modify_policy"


# Static permission matrix
# Principle: grant least-privilege, never elevate implicitly
_PERMISSION_MATRIX: dict[Role, FrozenSet[Permission]] = {
    Role.EXECUTIVE_SPONSOR: frozenset({
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_PROGRAM,
        Permission.VIEW_PROJECT,
        Permission.APPROVE_ESCALATION,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT_LOGS,
    }),
    Role.PROGRAM_DIRECTOR: frozenset({
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_PROGRAM,
        Permission.VIEW_PROJECT,
        Permission.VIEW_TASK,
        Permission.APPROVE_ESCALATION,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT_LOGS,
        Permission.MODIFY_POLICY,
    }),
    Role.PROJECT_MANAGER: frozenset({
        Permission.VIEW_PROGRAM,
        Permission.VIEW_PROJECT,
        Permission.VIEW_TASK,
        Permission.APPROVE_ESCALATION,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT_LOGS,
    }),
    Role.TEAM_MEMBER: frozenset({
        Permission.VIEW_PROJECT,
        Permission.VIEW_TASK,
    }),
    Role.AUDITOR: frozenset({
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_PROGRAM,
        Permission.VIEW_PROJECT,
        Permission.VIEW_TASK,
        Permission.VIEW_AUDIT_LOGS,
    }),
    Role.AGENT_RUNTIME: frozenset({
        Permission.VIEW_PROJECT,
        Permission.VIEW_TASK,
        Permission.GENERATE_REPORT,
        # Agents do NOT get APPROVE_ESCALATION or MODIFY_POLICY
    }),
}


def has_permission(role: Role, permission: Permission) -> bool:
    """Return True if the given role holds the given permission.

    Args:
        role: A Role enum value.
        permission: A Permission enum value.

    Returns:
        True if permitted, False otherwise. Unknown roles return False.
    """
    return permission in _PERMISSION_MATRIX.get(role, frozenset())


def get_permissions(role: Role) -> FrozenSet[Permission]:
    """Return all permissions for a role."""
    return _PERMISSION_MATRIX.get(role, frozenset())
