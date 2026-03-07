"""Policy parser for UF/config.fly files.

Calls the Gosling CLI binary (``gosling parse --type=uglyfox``) to parse
UF/config.fly and converts the resulting JSON into UFConfig Pydantic models.

The Gosling CLI outputs a generic AST structure with a ``blocks`` array.
This parser handles both that raw AST format and a pre-flattened dict
(e.g. from a DB cache).
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.model.policy_models import (
    ApexConditionConfig,
    ApexPoolConfig,
    NadirConditionConfig,
    NadirPoolConfig,
    PoliciesConfig,
    PolicyRule,
    PruningPolicy,
    RunnerCondition,
    UFConfig,
)
from app.util.base_logging import logged


def _get_gosling_cli_path() -> str:
    """Return the Gosling CLI binary path from env vars."""
    return os.getenv(
        "UGLYFOX_GOSLING_CLI_PATH", os.getenv("GOSLING_CLI_PATH", "gosling")
    )


# ---------------------------------------------------------------------------
# Helpers for the raw Gosling AST (blocks array) format
# ---------------------------------------------------------------------------


def _find_block(
    blocks: List[Dict[str, Any]], block_type: str
) -> Optional[Dict[str, Any]]:
    """Return the first block with the given type, or None."""
    for block in blocks:
        if block.get("type") == block_type:
            return block
    return None


def _find_blocks(blocks: List[Dict[str, Any]], block_type: str) -> List[Dict[str, Any]]:
    """Return all blocks with the given type."""
    return [b for b in blocks if b.get("type") == block_type]


# ---------------------------------------------------------------------------
# Builders from canonical .fly field names
# ---------------------------------------------------------------------------


def _build_pruning_from_attrs(attrs: Dict[str, Any]) -> PruningPolicy:
    """Build PruningPolicy from a pruning block attributes dict."""
    return PruningPolicy(
        failed_threshold=int(attrs.get("failed_threshold", 5)),
        max_age=str(attrs.get("max_age", "72h")),
        check_interval=str(attrs.get("check_interval", "5m")),
    )


def _build_apex_condition(attrs: Dict[str, Any]) -> ApexConditionConfig:
    """Build ApexConditionConfig from an apex block attributes dict."""
    cpu = attrs.get("cpu_threshold")
    mem = attrs.get("memory_threshold")
    return ApexConditionConfig(
        max_count=int(attrs.get("max_count", 10)),
        min_count=int(attrs.get("min_count", 1)),
        cpu_threshold=int(cpu) if cpu is not None else None,
        memory_threshold=int(mem) if mem is not None else None,
    )


def _build_nadir_condition(attrs: Dict[str, Any]) -> NadirConditionConfig:
    """Build NadirConditionConfig from a nadir block attributes dict."""
    return NadirConditionConfig(
        max_count=int(attrs.get("max_count", 5)),
        min_count=int(attrs.get("min_count", 0)),
        idle_timeout=str(attrs.get("idle_timeout", "30m")),
    )


def _build_runner_condition_from_block(
    block: Dict[str, Any],
) -> Optional[RunnerCondition]:
    """Build RunnerCondition from a runners_condition AST block."""
    labels = block.get("labels", [])
    name = labels[0] if labels else ""
    if not name:
        return None
    attrs = block.get("attributes", {})
    inner_blocks = block.get("blocks", [])

    eggs_entities = attrs.get("eggs_entities", [])
    if not isinstance(eggs_entities, list):
        eggs_entities = []

    apex_block = _find_block(inner_blocks, "apex")
    apex = _build_apex_condition(apex_block.get("attributes", {}) if apex_block else {})

    nadir_block = _find_block(inner_blocks, "nadir")
    nadir = _build_nadir_condition(
        nadir_block.get("attributes", {}) if nadir_block else {}
    )

    return RunnerCondition(
        name=name, eggs_entities=eggs_entities, apex=apex, nadir=nadir
    )


def _build_policy_rule_from_block(block: Dict[str, Any]) -> Optional[PolicyRule]:
    """Build PolicyRule from a rule AST block."""
    labels = block.get("labels", [])
    name = labels[0] if labels else ""
    if not name:
        return None
    attrs = block.get("attributes", {})
    return PolicyRule(
        name=name,
        condition=str(attrs.get("condition", "")),
        action=str(attrs.get("action", "")),
    )


def _build_policies_from_block(block: Dict[str, Any]) -> PoliciesConfig:
    """Build PoliciesConfig from a policies AST block."""
    rules: List[PolicyRule] = []
    for rule_block in _find_blocks(block.get("blocks", []), "rule"):
        rule = _build_policy_rule_from_block(rule_block)
        if rule is not None:
            rules.append(rule)
    return PoliciesConfig(rules=rules)


def _build_apex_pool(data: Dict[str, Any]) -> ApexPoolConfig:
    """Build ApexPoolConfig from a plain dict (runtime config, not .fly schema)."""
    return ApexPoolConfig(
        min_size=int(data.get("min_size", 1)),
        max_size=int(data.get("max_size", 10)),
        scale_up_threshold=int(data.get("scale_up_threshold", 5)),
    )


def _build_nadir_pool(data: Dict[str, Any]) -> NadirPoolConfig:
    """Build NadirPoolConfig from a plain dict (runtime config, not .fly schema)."""
    return NadirPoolConfig(
        min_size=int(data.get("min_size", 0)),
        max_size=int(data.get("max_size", 5)),
        warmup_time_seconds=float(data.get("warmup_time_seconds", 30.0)),
    )


# ---------------------------------------------------------------------------
# Main conversion: raw Gosling AST or flattened dict -> UFConfig
# ---------------------------------------------------------------------------


def _uf_config_from_ast(data: Dict[str, Any]) -> UFConfig:
    """Convert Gosling CLI AST JSON output into a UFConfig."""
    top_blocks: List[Dict[str, Any]] = data.get("blocks", [])
    uf_block = _find_block(top_blocks, "uglyfox")
    inner: List[Dict[str, Any]] = uf_block.get("blocks", []) if uf_block else []

    pruning_block = _find_block(inner, "pruning")
    pruning = (
        _build_pruning_from_attrs(pruning_block.get("attributes", {}))
        if pruning_block
        else PruningPolicy()
    )

    conditions: List[RunnerCondition] = []
    for rc_block in _find_blocks(inner, "runners_condition"):
        cond = _build_runner_condition_from_block(rc_block)
        if cond is not None:
            conditions.append(cond)

    policies_block = _find_block(inner, "policies")
    policies = (
        _build_policies_from_block(policies_block)
        if policies_block
        else PoliciesConfig()
    )

    apex_data = data.get("apex_pool", {})
    apex_pool = _build_apex_pool(apex_data) if apex_data else ApexPoolConfig()

    nadir_data = data.get("nadir_pool", {})
    nadir_pool = _build_nadir_pool(nadir_data) if nadir_data else NadirPoolConfig()

    return UFConfig(
        pruning=pruning,
        runners_condition=conditions,
        policies=policies,
        apex_pool=apex_pool,
        nadir_pool=nadir_pool,
    )


def _build_conditions_from_list(items: List[Dict[str, Any]]) -> List[RunnerCondition]:
    """Build RunnerCondition list from a flattened dict list."""
    conditions: List[RunnerCondition] = []
    for cond_data in items:
        name = cond_data.get("name", "")
        if not name:
            continue
        eggs_entities = cond_data.get("eggs_entities", [])
        apex = _build_apex_condition(cond_data.get("apex", {}))
        nadir = _build_nadir_condition(cond_data.get("nadir", {}))
        conditions.append(
            RunnerCondition(
                name=name, eggs_entities=eggs_entities, apex=apex, nadir=nadir
            )
        )
    return conditions


def _build_policies_from_rules_list(rules_data: List[Dict[str, Any]]) -> PoliciesConfig:
    """Build PoliciesConfig from a list of rule dicts."""
    rules: List[PolicyRule] = []
    for rule_data in rules_data:
        rule_name = rule_data.get("name", "")
        if rule_name:
            rules.append(
                PolicyRule(
                    name=rule_name,
                    condition=str(rule_data.get("condition", "")),
                    action=str(rule_data.get("action", "")),
                )
            )
    return PoliciesConfig(rules=rules)


def _uf_config_from_dict(data: Dict[str, Any]) -> UFConfig:
    """Convert a dict (Gosling AST or DB cache flattened) into a UFConfig."""
    if "blocks" in data:
        return _uf_config_from_ast(data)

    pruning_data = data.get("pruning", {})
    pruning = (
        _build_pruning_from_attrs(pruning_data) if pruning_data else PruningPolicy()
    )

    conditions = _build_conditions_from_list(data.get("runners_condition", []))
    policies = _build_policies_from_rules_list(
        data.get("policies", {}).get("rules", [])
    )

    apex_data = data.get("apex_pool", {})
    apex_pool = _build_apex_pool(apex_data) if apex_data else ApexPoolConfig()

    nadir_data = data.get("nadir_pool", {})
    nadir_pool = _build_nadir_pool(nadir_data) if nadir_data else NadirPoolConfig()

    return UFConfig(
        pruning=pruning,
        runners_condition=conditions,
        policies=policies,
        apex_pool=apex_pool,
        nadir_pool=nadir_pool,
    )


@logged
class PolicyParser:
    """Parses UF/config.fly by calling the Gosling CLI binary.

    Delegates all .fly parsing to ``gosling parse --type=uglyfox``, which
    outputs JSON to stdout.  The JSON is then mapped to UFConfig models.

    Falls back to an all-defaults UFConfig and logs a warning when the
    binary is unavailable or returns an error, so UglyFox can continue
    operating with safe defaults.
    """

    def __init__(self, gosling_cli_path: Optional[str] = None) -> None:
        """Initialise the parser.

        Args:
            gosling_cli_path: Explicit path to the Gosling CLI binary.
                              If None, resolved from env vars / settings.
        """
        self._gosling_cli_path_override = gosling_cli_path

    @property
    def gosling_cli_path(self) -> str:
        """Resolved path to the Gosling CLI binary."""
        if self._gosling_cli_path_override:
            return self._gosling_cli_path_override
        return _get_gosling_cli_path()

    def _call_gosling_parse(self, file_path: Path) -> Dict[str, Any]:
        """Execute ``gosling parse <file> --type=uglyfox`` and return parsed JSON.

        Raises:
            FileNotFoundError: Gosling CLI binary not found.
            subprocess.CalledProcessError: Gosling CLI exited non-zero.
            json.JSONDecodeError: Gosling CLI output is not valid JSON.
        """
        cmd = [self.gosling_cli_path, "parse", str(file_path), "--type", "uglyfox"]
        self.debug(  # pylint: disable=no-member
            "Executing Gosling CLI: %s", " ".join(cmd)
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        parsed: Dict[str, Any] = json.loads(result.stdout)
        self.debug(  # pylint: disable=no-member
            "Gosling CLI parsed UF config from %s", file_path
        )
        return parsed

    def parse_file(self, path: str) -> UFConfig:
        """Parse a UF/config.fly file via the Gosling CLI binary.

        Returns:
            Parsed UFConfig model. Falls back to defaults on binary failure.
        """
        self.info("Parsing UF config file: %s", path)  # pylint: disable=no-member
        file_path = Path(path)

        try:
            raw = self._call_gosling_parse(file_path)
        except FileNotFoundError:
            self.warning(  # pylint: disable=no-member
                "Gosling CLI binary not found at '%s'. "
                "Set UGLYFOX_GOSLING_CLI_PATH. Using default UFConfig.",
                self.gosling_cli_path,
            )
            return UFConfig()
        except subprocess.CalledProcessError as exc:
            self.warning(  # pylint: disable=no-member
                "Gosling CLI failed for %s (exit %d): %s. Using default UFConfig.",
                path,
                exc.returncode,
                exc.stderr,
            )
            return UFConfig()
        except (json.JSONDecodeError, ValueError) as exc:
            self.warning(  # pylint: disable=no-member
                "Failed to parse Gosling CLI output for %s: %s. Using default UFConfig.",
                path,
                exc,
            )
            return UFConfig()

        return _uf_config_from_dict(raw)

    def parse_from_dict(self, data: Dict[str, Any]) -> UFConfig:
        """Convert a plain dict (e.g. from DB cache) into a UFConfig model.

        Args:
            data: Dictionary representation of UFConfig (flattened or AST).

        Returns:
            Parsed UFConfig model.
        """
        self.info("Parsing UF config from dict")  # pylint: disable=no-member
        return _uf_config_from_dict(data)
