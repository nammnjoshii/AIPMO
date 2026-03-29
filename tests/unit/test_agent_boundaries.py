"""Test cross-agent import boundaries — T-053.

Dynamically parses every agent file with ast module.
Verifies no agent imports from a sibling agent folder.
Would fail if a bad import were introduced.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

_AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"
_AGENT_MODULES = [
    "execution_monitoring",
    "issue_management",
    "risk_intelligence",
    "communication",
    "knowledge",
    "planning",
    "program_director",
]


def _get_python_files(agent_module: str) -> list[Path]:
    module_dir = _AGENTS_DIR / agent_module
    return list(module_dir.glob("*.py"))


def _extract_imports(file_path: Path) -> list[str]:
    source = file_path.read_text()
    tree = ast.parse(source, filename=str(file_path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _is_cross_agent_import(importing_module: str, import_path: str) -> bool:
    """Return True if import_path is a sibling agent module."""
    for other_module in _AGENT_MODULES:
        if other_module == importing_module:
            continue
        if import_path.startswith(f"agents.{other_module}"):
            return True
    return False


class TestAgentBoundaries:
    @pytest.mark.parametrize("agent_module", _AGENT_MODULES)
    def test_no_cross_agent_imports(self, agent_module: str):
        """Agent must not import from any sibling agent folder."""
        files = _get_python_files(agent_module)
        assert files, f"No Python files found in agents/{agent_module}/"

        violations = []
        for file_path in files:
            imports = _extract_imports(file_path)
            for imp in imports:
                if _is_cross_agent_import(agent_module, imp):
                    violations.append(
                        f"{file_path.name} imports {imp!r} — cross-agent import forbidden"
                    )

        assert not violations, (
            f"Agent '{agent_module}' has cross-agent imports:\n"
            + "\n".join(violations)
        )

    def test_all_agents_have_agent_file(self):
        for module in _AGENT_MODULES:
            agent_file = _AGENTS_DIR / module / "agent.py"
            assert agent_file.exists(), f"Missing agents/{module}/agent.py"

    def test_all_agents_have_prompts_file(self):
        for module in _AGENT_MODULES:
            prompts_file = _AGENTS_DIR / module / "prompts.py"
            assert prompts_file.exists(), f"Missing agents/{module}/prompts.py"

    def test_violation_detection_works(self):
        """Verify the detection logic would catch a bad import."""
        bad_import = "agents.risk_intelligence.agent"
        assert _is_cross_agent_import("execution_monitoring", bad_import) is True

    def test_base_agent_import_is_allowed(self):
        """agents.base_agent is not a sibling — all agents may import it."""
        assert _is_cross_agent_import("execution_monitoring", "agents.base_agent") is False
