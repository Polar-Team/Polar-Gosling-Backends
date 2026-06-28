#!/usr/bin/env python3
"""Cross-check .env.example declarations against docker-compose.yml variable references.

Validates the following invariants (Requirements 11.2–11.7):

1. set(declared vars in .env.example) == set(referenced vars in docker-compose.yml).
2. Every REQUIRED var (empty value in .env.example) uses ``${VAR:?...}`` syntax in compose.
3. Every var with a default uses ``${VAR:-default}`` syntax in compose with a matching default.
4. Every non-blank non-comment line in .env.example matches ``^[A-Z_][A-Z0-9_]*=.*$``.
5. Every declaration line is preceded by either another declaration or a ``#``-comment line.

Exit codes:
    0 — all checks passed.
    1 — one or more violations found (printed to stderr).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# --- Constants ----------------------------------------------------------------

# Regex matching a valid .env.example declaration line (before trailing comment stripping).
_DECLARATION_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=.*$")

# Regex to extract KEY and raw value (everything after the first '=').
_KEY_VALUE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")

# Regex for docker-compose.yml variable references: ${VAR}, ${VAR:-default}, ${VAR:?message}.
_COMPOSE_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::([?-])([^}]*))?\}")

# Docker Compose built-in variables that should NOT be declared in .env.example.
_BUILTIN_VARS: frozenset[str] = frozenset({"COMPOSE_PROFILES"})


# --- Data classes -------------------------------------------------------------


@dataclass
class EnvDeclaration:
    """A single variable declaration parsed from .env.example."""

    key: str
    raw_value: str  # everything after '=' on the line (including trailing comment)
    default_value: str  # actual default value (trailing comment stripped), empty means REQUIRED
    is_required: bool
    line_number: int
    has_preceding_comment: bool


@dataclass
class ComposeReference:
    """A variable reference parsed from docker-compose.yml."""

    key: str
    syntax: str  # "", "-", or "?" (from :- or :? or bare)
    modifier_value: str  # the default or error message after :- or :?


@dataclass
class ValidationResult:
    """Accumulates violations across all checks."""

    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.violations) == 0

    def add(self, msg: str) -> None:
        self.violations.append(msg)


# --- Parsing ------------------------------------------------------------------


def parse_env_example(path: Path) -> list[EnvDeclaration]:
    """Parse .env.example and return all KEY=value declarations.

    Only lines that start with a letter matching ^[A-Z_] and contain '=' are
    treated as declarations. Commented-out lines (starting with #) are ignored
    as declarations, even if they contain '=' characters.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    declarations: list[EnvDeclaration] = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip blank lines and full comment lines.
        if not stripped or stripped.startswith("#"):
            continue

        match = _KEY_VALUE_RE.match(stripped)
        if match is None:
            # Non-matching lines are still collected for format validation later,
            # but not as valid declarations. We include them with a sentinel.
            declarations.append(
                EnvDeclaration(
                    key="",
                    raw_value=stripped,
                    default_value="",
                    is_required=False,
                    line_number=i,
                    has_preceding_comment=False,
                )
            )
            continue

        key = match.group(1)
        raw_value = match.group(2)

        # Strip trailing comment: look for ' #' or multiple spaces followed by '#'.
        # The pattern accounts for values like "sqs://test:test@" containing '#' NOT
        # preceded by spaces — we only strip comments after whitespace+#.
        default_value = _strip_trailing_comment(raw_value).strip()

        is_required = default_value == "" or "# REQUIRED" in raw_value

        # Check if preceded by a comment or another declaration.
        has_preceding_comment = _check_preceding_line(lines, i - 1)

        declarations.append(
            EnvDeclaration(
                key=key,
                raw_value=raw_value,
                default_value=default_value,
                is_required=is_required,
                line_number=i,
                has_preceding_comment=has_preceding_comment,
            )
        )

    return declarations


def _strip_trailing_comment(raw_value: str) -> str:
    """Strip trailing inline comment from a .env value.

    Comments are identified by whitespace followed by '#'. This handles cases
    like ``0.1.0                       # default OK`` correctly.
    """
    # Find the first occurrence of whitespace+# that looks like a trailing comment.
    match = re.search(r"\s+#\s*", raw_value)
    if match:
        return raw_value[: match.start()]
    return raw_value


def _check_preceding_line(lines: list[str], current_line_index_0based: int) -> bool:
    """Return True if the line before the current one is a comment or a declaration.

    Parameters
    ----------
    lines : list[str]
        All lines from the file (0-indexed).
    current_line_index_0based : int
        0-based index of the current declaration line.
    """
    if current_line_index_0based <= 0:
        # First line in the file — no preceding line.
        return False

    prev = lines[current_line_index_0based - 1].strip()
    if not prev:
        # Blank line precedes — check is failed.
        return False
    if prev.startswith("#"):
        return True
    if _DECLARATION_RE.match(prev):
        return True
    return False


def parse_compose_references(path: Path) -> list[ComposeReference]:
    """Parse docker-compose.yml raw text for ${VAR}, ${VAR:-default}, ${VAR:?msg} references.

    Only non-comment lines are considered. YAML comment lines (where the first
    non-whitespace character is ``#``) are skipped entirely.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    refs: list[ComposeReference] = []

    for line in lines:
        # Skip YAML comment lines.
        if line.lstrip().startswith("#"):
            continue

        for match in _COMPOSE_VAR_RE.finditer(line):
            key = match.group(1)
            syntax_char = match.group(2) or ""  # "-" or "?" or ""
            modifier_value = match.group(3) or ""

            # Skip built-in Docker Compose variables.
            if key in _BUILTIN_VARS:
                continue

            refs.append(ComposeReference(key=key, syntax=syntax_char, modifier_value=modifier_value))

    return refs


# --- Validation ---------------------------------------------------------------


def validate(
    declarations: list[EnvDeclaration],
    compose_refs: list[ComposeReference],
    env_path: Path,
) -> ValidationResult:
    """Run all validation checks and return accumulated violations."""
    result = ValidationResult()

    # Filter out invalid declarations (key == "") — those are format violations.
    valid_decls = [d for d in declarations if d.key]
    declared_keys = {d.key for d in valid_decls}

    # Build lookup from key → list of compose references.
    refs_by_key: dict[str, list[ComposeReference]] = {}
    for ref in compose_refs:
        refs_by_key.setdefault(ref.key, []).append(ref)
    referenced_keys = set(refs_by_key.keys())

    # --- Check 1: set equality ---
    extra_in_env = declared_keys - referenced_keys
    extra_in_compose = referenced_keys - declared_keys

    if extra_in_env:
        for key in sorted(extra_in_env):
            result.add(f"declared in .env.example but NOT referenced in docker-compose.yml: {key}")
    if extra_in_compose:
        for key in sorted(extra_in_compose):
            result.add(f"referenced in docker-compose.yml but NOT declared in .env.example: {key}")

    # --- Check 2: REQUIRED vars must use :? syntax in compose ---
    for decl in valid_decls:
        if not decl.is_required:
            continue
        if decl.key not in refs_by_key:
            continue  # Already reported in set-equality check.
        refs = refs_by_key[decl.key]
        if not any(r.syntax == "?" for r in refs):
            result.add(
                f"REQUIRED var '{decl.key}' (line {decl.line_number}) does not use "
                f"${{VAR:?...}} syntax in docker-compose.yml"
            )

    # --- Check 3: vars with defaults must use :- syntax with matching default ---
    for decl in valid_decls:
        if decl.is_required:
            continue
        if decl.key not in refs_by_key:
            continue  # Already reported in set-equality check.
        refs = refs_by_key[decl.key]
        dash_refs = [r for r in refs if r.syntax == "-"]
        if not dash_refs:
            result.add(
                f"var '{decl.key}' has default '{decl.default_value}' in .env.example "
                f"but does not use ${{VAR:-...}} syntax in docker-compose.yml"
            )
            continue
        # Check that at least one :- reference has a matching default value.
        for ref in dash_refs:
            if ref.modifier_value != decl.default_value:
                result.add(
                    f"var '{decl.key}' default mismatch: .env.example='{decl.default_value}' "
                    f"vs compose='${{{{...:-{ref.modifier_value}}}}}'"
                )

    # --- Check 4: every non-blank non-comment line matches declaration format ---
    lines = env_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _DECLARATION_RE.match(stripped):
            result.add(
                f".env.example line {i} does not match ^[A-Z_][A-Z0-9_]*=.*$ : {stripped!r}"
            )

    # --- Check 5: every declaration is preceded by a comment or another declaration ---
    for decl in valid_decls:
        if not decl.has_preceding_comment:
            result.add(
                f"var '{decl.key}' (line {decl.line_number}) is not preceded by a "
                f"# comment or another declaration"
            )

    return result


# --- Main ---------------------------------------------------------------------


def main() -> None:
    """Entry point: parse files, run checks, report violations."""
    # Determine paths: accept as CLI args or auto-detect relative to script location.
    script_dir = Path(__file__).resolve().parent
    compose_dir = script_dir.parent

    if len(sys.argv) >= 3:
        env_path = Path(sys.argv[1]).resolve()
        compose_path = Path(sys.argv[2]).resolve()
    elif len(sys.argv) == 2:
        env_path = Path(sys.argv[1]).resolve()
        compose_path = compose_dir / "docker-compose.yml"
    else:
        env_path = compose_dir / ".env.example"
        compose_path = compose_dir / "docker-compose.yml"

    if not env_path.is_file():
        print(f"ERROR: .env.example not found at {env_path}", file=sys.stderr)
        sys.exit(1)

    if not compose_path.is_file():
        print(f"ERROR: docker-compose.yml not found at {compose_path}", file=sys.stderr)
        sys.exit(1)

    declarations = parse_env_example(env_path)
    compose_refs = parse_compose_references(compose_path)

    result = validate(declarations, compose_refs, env_path)

    if result.ok:
        print("env check OK — .env.example and docker-compose.yml are consistent.")
        sys.exit(0)
    else:
        print("env check FAILED — violations found:", file=sys.stderr)
        for v in result.violations:
            print(f"  • {v}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
