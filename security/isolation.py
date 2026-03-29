"""Tenant and project isolation enforcement.

Cross-project access is denied by default. Portfolio-level grants must be explicit.
No cross-project data leaks through agent context — enforced at context_assembly layer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)

# Portfolio-level grants: {tenant_id: {(source_project, target_project)}}
# Only portfolio managers may add entries here via policy configuration.
_PORTFOLIO_GRANTS: Dict[str, Set[tuple]] = {}


class TenantIsolation:
    """Enforces tenant and project data isolation.

    Default: cross-project access is denied.
    Exception: explicit portfolio-level grants registered via allow_cross_project().
    """

    def __init__(self) -> None:
        # Instance-level grants (override class-level for testing)
        self._grants: Dict[str, Set[tuple]] = {}

    def get_project_scope(self, tenant_id: str, project_id: str) -> Dict[str, Any]:
        """Return the allowed data scope for this tenant/project combination.

        Args:
            tenant_id: The tenant identifier.
            project_id: The project identifier.

        Returns:
            Dict with:
              - tenant_id (str)
              - project_id (str)
              - allowed_projects (List[str]): projects this context may read
              - cross_project_allowed (bool): True only with explicit portfolio grant
        """
        allowed_projects = [project_id]  # always allowed to read own project

        # Add projects for which a portfolio grant exists
        grants = self._grants.get(tenant_id, _PORTFOLIO_GRANTS.get(tenant_id, set()))
        for source, target in grants:
            if source == project_id and target not in allowed_projects:
                allowed_projects.append(target)

        return {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "allowed_projects": allowed_projects,
            "cross_project_allowed": len(allowed_projects) > 1,
        }

    def is_cross_project_allowed(
        self,
        tenant_id: str,
        source_project: str,
        target_project: str,
    ) -> bool:
        """Return True only if there is an explicit portfolio-level grant.

        Default is False. Never elevate access without a registered grant.
        """
        if source_project == target_project:
            return True  # same project is always allowed

        grants = self._grants.get(tenant_id, _PORTFOLIO_GRANTS.get(tenant_id, set()))
        allowed = (source_project, target_project) in grants

        if not allowed:
            logger.debug(
                "TenantIsolation: cross-project access DENIED "
                "tenant=%s source=%s target=%s",
                tenant_id,
                source_project,
                target_project,
            )
        return allowed

    def allow_cross_project(
        self,
        tenant_id: str,
        source_project: str,
        target_project: str,
    ) -> None:
        """Register an explicit portfolio-level cross-project grant.

        This should only be called by portfolio-level policy configuration,
        never by agent code.
        """
        if tenant_id not in self._grants:
            self._grants[tenant_id] = set()
        self._grants[tenant_id].add((source_project, target_project))
        logger.info(
            "TenantIsolation: registered cross-project grant "
            "tenant=%s %s → %s",
            tenant_id,
            source_project,
            target_project,
        )

    def revoke_cross_project(
        self,
        tenant_id: str,
        source_project: str,
        target_project: str,
    ) -> None:
        """Remove a portfolio-level grant."""
        self._grants.get(tenant_id, set()).discard((source_project, target_project))
