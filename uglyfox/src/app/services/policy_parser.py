"""Policy parser for UF/config.fly files.

Calls the Gosling CLI binary (``gosling parse --type=uglyfox``) to parse
UF/config.fly and converts the resulting JSON into UFConfig Pydantic models.
This mirrors the pattern used by MotherGoose's FlyParser service.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.model.policy_models import (
    ApexPoolConfig,
    NadirPoolConfig,
    PruningPolicy,
    RunnerCondition,
    UFConfig,
)
from app.util.base_logging import logged


def _get_gosling_cli_path() -> str:
    """Return the Gosling CLI binary path.

    Reads ``UGLYFOX_GOSLING_CLI_PATH`` env var first, then falls back to
    the settings value (which itself defaults to ``"gosling"``).
    """
    return os.getenv("UGLYFOX_GOSLING_CLI_PATH", os.getenv("GOSLING_CLI_PATH", "gosling"))


def _build_pruning(data: Dict[str, Any]) -> PruningPolicy:
    """Build PruningPolicy from a parsed dict."""
    return PruningPolicy(
        max_age_hours=float(data.get("max_age_hours", 72.0)),
        max_failures=int(data.get("max_failures", 5)),
        idle_timeout_minutes=float(data.get("idle_timeout_minutes", 30.0)),
        check_interval_seconds=float(data.get("check_interval_seconds", 60.0)),
    )


def _build_runner_condition(data: Dict[str, Any]) -> Optional[RunnerCondition]:
    """Build RunnerCondition from a parsed dict. Returns None if egg_name missing."""
    egg_name = data.get("egg_name")
    if not egg_name or not isinstance(egg_name, str):
        return None
    max_age: Optional[float] = float(data["max_age_hours"]) if "max_age_hours" in data else None
    max_fail: Optional[int] = int(data["max_failures"]) if "max_failures" in data else None
    return RunnerCondition(egg_name=egg_name, max_age_hours=max_age, max_failures=max_fail)


def _build_apex_pool(data: Dict[str, Any]) -> ApexPoolConfig:
    """Build ApexPoolConfig from a parsed dict."""
    return ApexPoolConfig(
        min_size=int(data.get("min_size", 1)),
        max_size=int(data.get("max_size", 10)),
        scale_up_threshold=int(data.get("scale_up_threshold", 5)),
    )


def _build_nadir_pool(data: Dict[str, Any]) -> NadirPoolConfig:
    """Build NadirPoolConfig from a parsed dict."""
    return NadirPoolConfig(
        min_size=int(data.get("min_size", 0)),
        max_size=int(data.get("max_size", 5)),
        warmup_time_seconds=float(data.get("warmup_time_seconds", 30.0)),
    )


def _uf_config_from_dict(data: Dict[str, Any]) -> UFConfig:
    """Convert a plain dict (Gosling JSON output or DB cache) into a UFConfig."""
    pruning_data = data.get("pruning", {})
    pruning = _build_pruning(pruning_data) if pruning_data else PruningPolicy()

    conditions: List[RunnerCondition] = []
    for cond_data in data.get("runners_condition", []):
        cond = _build_runner_condition(cond_data)
        if cond is not None:
            conditions.append(cond)

    apex_data = data.get("apex_pool", {})
    apex_pool = _build_apex_pool(apex_data) if apex_data else ApexPoolConfig()

    nadir_data = data.get("nadir_pool", {})
    nadir_pool = _build_nadir_pool(nadir_data) if nadir_data else NadirPoolConfig()

    return UFConfig(
        pruning=pruning,
        runners_condition=conditions,
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

        Args:
            file_path: Path to the UF/config.fly file.

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            FileNotFoundError: Gosling CLI binary not found.
            subprocess.CalledProcessError: Gosling CLI exited non-zero.
            json.JSONDecodeError: Gosling CLI output is not valid JSON.
        """
        cmd = [self.gosling_cli_path, "parse", str(file_path), "--type", "uglyfox"]
        self.debug("Executing Gosling CLI: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

        parsed: Dict[str, Any] = json.loads(result.stdout)
        self.debug("Gosling CLI parsed UF config from %s", file_path)
        return parsed

    def parse_file(self, path: str) -> UFConfig:
        """Parse a UF/config.fly file via the Gosling CLI binary.

        Args:
            path: Filesystem path to the UF/config.fly file.

        Returns:
            Parsed UFConfig model. Falls back to defaults on binary failure.
        """
        self.info("Parsing UF config file: %s", path)
        file_path = Path(path)

        try:
            raw = self._call_gosling_parse(file_path)
        except FileNotFoundError:
            self.warning(
                "Gosling CLI binary not found at '%s'. "
                "Set UGLYFOX_GOSLING_CLI_PATH. Using default UFConfig.",
                self.gosling_cli_path,
            )
            return UFConfig()
        except subprocess.CalledProcessError as exc:
            self.warning(
                "Gosling CLI failed for %s (exit %d): %s. Using default UFConfig.",
                path,
                exc.returncode,
                exc.stderr,
            )
            return UFConfig()
        except (json.JSONDecodeError, ValueError) as exc:
            self.warning(
                "Failed to parse Gosling CLI output for %s: %s. Using default UFConfig.",
                path,
                exc,
            )
            return UFConfig()

        return _uf_config_from_dict(raw)

    def parse_from_dict(self, data: Dict[str, Any]) -> UFConfig:
        """Convert a plain dict (e.g. from DB cache) into a UFConfig model.

        Used when the UF config has already been parsed and stored in the
        database cache — no Gosling CLI call needed.

        Args:
            data: Dictionary representation of UFConfig.

        Returns:
            Parsed UFConfig model.
        """
        self.info("Parsing UF config from dict")
        return _uf_config_from_dict(data)
