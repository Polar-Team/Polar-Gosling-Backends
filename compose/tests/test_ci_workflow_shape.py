"""Example-based tests for the CI workflow ``compose-smoke.yml`` shape.

Feature: docker-compose-cloud-stack-testing

These tests parse the GitHub Actions workflow file and assert that trigger
configuration, step ordering, artifact settings, and timeout match the
requirements for CI integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
WORKFLOW_FILE: Path = COMPOSE_DIR.parent / ".github" / "workflows" / "compose-smoke.yml"


@pytest.fixture(scope="module")
def workflow() -> Dict[str, Any]:
    """Load and parse the compose-smoke.yml workflow.

    Note: PyYAML resolves the bare ``on`` key as boolean ``True``.
    We normalise it back to the string ``"on"`` for ergonomic access.
    """
    assert WORKFLOW_FILE.is_file(), f"expected workflow at {WORKFLOW_FILE}"
    content = WORKFLOW_FILE.read_text(encoding="utf-8")
    parsed: Dict[str, Any] = yaml.safe_load(content)
    # PyYAML interprets the YAML 1.1 boolean 'on' as True; remap for clarity.
    if True in parsed and "on" not in parsed:
        parsed["on"] = parsed.pop(True)
    return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_names(steps: List[Dict[str, Any]]) -> List[str]:
    """Extract the 'name' field from each step dict."""
    return [s["name"] for s in steps]


def _step_by_name(steps: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """Find a step by its 'name' field."""
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named '{name}', found {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCIWorkflowTrigger:
    """Tests for PR trigger configuration (Requirement 16.1)."""

    def test_triggers_on_pull_request(self, workflow: Dict[str, Any]) -> None:
        """Workflow triggers on pull_request events.

        **Validates: Requirements 16.1**
        """
        assert "pull_request" in workflow["on"], "workflow must trigger on pull_request"

    def test_pr_event_types(self, workflow: Dict[str, Any]) -> None:
        """PR trigger includes opened, synchronize, reopened event types.

        **Validates: Requirements 16.1**
        """
        pr_config = workflow["on"]["pull_request"]
        expected_types = {"opened", "synchronize", "reopened"}
        actual_types = set(pr_config["types"])
        assert actual_types == expected_types, f"expected types {expected_types}, got {actual_types}"

    def test_pr_branch_filter(self, workflow: Dict[str, Any]) -> None:
        """PR trigger targets the dev-new-features branch.

        **Validates: Requirements 16.1**
        """
        pr_config = workflow["on"]["pull_request"]
        assert "dev-new-features" in pr_config["branches"], (
            f"expected 'dev-new-features' in branches filter, got {pr_config['branches']}"
        )


class TestCIWorkflowStepOrder:
    """Tests for step execution order (Requirement 16.2)."""

    def test_make_targets_in_order(self, workflow: Dict[str, Any]) -> None:
        """Steps execute compose-up, then compose-smoke, then compose-down in order.

        **Validates: Requirements 16.2**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step_names = _step_names(steps)

        up_idx = step_names.index("Start Cloud Stack")
        smoke_idx = step_names.index("Run smoke tests")
        down_idx = step_names.index("Tear down Cloud Stack")

        assert up_idx < smoke_idx < down_idx, (
            f"expected up ({up_idx}) < smoke ({smoke_idx}) < down ({down_idx})"
        )

    def test_up_step_runs_make_compose_up(self, workflow: Dict[str, Any]) -> None:
        """The 'Start Cloud Stack' step runs 'make compose-up'.

        **Validates: Requirements 16.2**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Start Cloud Stack")
        assert step["run"].strip() == "make compose-up"

    def test_smoke_step_runs_make_compose_smoke(self, workflow: Dict[str, Any]) -> None:
        """The 'Run smoke tests' step runs 'make compose-smoke'.

        **Validates: Requirements 16.2**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Run smoke tests")
        assert step["run"].strip() == "make compose-smoke"

    def test_down_step_runs_make_compose_down(self, workflow: Dict[str, Any]) -> None:
        """The 'Tear down Cloud Stack' step runs 'make compose-down'.

        **Validates: Requirements 16.2**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Tear down Cloud Stack")
        assert step["run"].strip() == "make compose-down"


class TestCIWorkflowCleanup:
    """Tests for cleanup behavior on failure (Requirement 16.3)."""

    def test_compose_down_runs_always(self, workflow: Dict[str, Any]) -> None:
        """The compose-down step uses 'if: always()' to ensure cleanup.

        **Validates: Requirements 16.3**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Tear down Cloud Stack")
        assert step.get("if") == "always()", f"expected 'if: always()', got '{step.get('if')}'"


class TestCIWorkflowArtifact:
    """Tests for artifact upload on failure (Requirement 16.4)."""

    def test_artifact_uploaded_on_failure(self, workflow: Dict[str, Any]) -> None:
        """Artifact upload step triggers on failure condition.

        **Validates: Requirements 16.4**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Upload compose logs")
        assert step.get("if") == "failure()", f"expected 'if: failure()', got '{step.get('if')}'"

    def test_artifact_name(self, workflow: Dict[str, Any]) -> None:
        """Artifact is named 'compose-logs'.

        **Validates: Requirements 16.4**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Upload compose logs")
        assert step["with"]["name"] == "compose-logs"

    def test_artifact_retention_days(self, workflow: Dict[str, Any]) -> None:
        """Artifact retention is set to 7 days.

        **Validates: Requirements 16.4**
        """
        steps = workflow["jobs"]["smoke"]["steps"]
        step = _step_by_name(steps, "Upload compose logs")
        assert step["with"]["retention-days"] == 7


class TestCIWorkflowTimeout:
    """Tests for workflow timeout (Requirement 16.5)."""

    def test_timeout_minutes(self, workflow: Dict[str, Any]) -> None:
        """Job timeout is set to 15 minutes.

        **Validates: Requirements 16.5**
        """
        job = workflow["jobs"]["smoke"]
        assert job["timeout-minutes"] == 15, f"expected timeout-minutes=15, got {job.get('timeout-minutes')}"
