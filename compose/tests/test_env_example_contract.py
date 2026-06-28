"""Property-based tests for the ``.env.example`` contract with ``check_env.py``.

Feature: docker-compose-cloud-stack-testing

These tests exercise the format-validation logic of ``check_env.py`` (Checks 4
and 5) by generating well-formed and malformed ``.env.example`` content and
asserting that the validator accepts/rejects them appropriately.

The properties cover:
  * **Property 14** — ``.env.example`` has canonical variable-declaration shape.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import check_env.py from the scripts directory
# ---------------------------------------------------------------------------

COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = COMPOSE_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from check_env import (  # noqa: E402
    ComposeReference,
    ValidationResult,
    _DECLARATION_RE,
    parse_compose_references,
    parse_env_example,
    validate,
)

# ---------------------------------------------------------------------------
# Strategies for generating .env.example content
# ---------------------------------------------------------------------------

# Valid uppercase key names: start with [A-Z_], followed by [A-Z0-9_]
_VALID_KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_VALID_KEY_START_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"


@st.composite
def _valid_key(draw: st.DrawFn) -> str:
    """Generate a valid env-var key matching ^[A-Z_][A-Z0-9_]*$."""
    first = draw(st.sampled_from(list(_VALID_KEY_START_CHARS)))
    rest = draw(st.text(alphabet=_VALID_KEY_CHARS, min_size=0, max_size=20))
    return first + rest


@st.composite
def _valid_value(draw: st.DrawFn) -> str:
    """Generate a value part (everything after '=').

    Values can be empty (REQUIRED) or contain printable ASCII. We avoid
    newlines since each declaration is one line.
    """
    # Safe alphabet: printable ASCII without newline
    alphabet = "".join(chr(c) for c in range(32, 127) if chr(c) != "\n")
    return draw(st.text(alphabet=alphabet, min_size=0, max_size=40))


@st.composite
def _comment_line(draw: st.DrawFn) -> str:
    """Generate a valid comment line starting with '#'."""
    # Comment text: printable ASCII without newlines
    alphabet = "".join(chr(c) for c in range(32, 127) if chr(c) != "\n")
    text = draw(st.text(alphabet=alphabet, min_size=0, max_size=60))
    return f"# {text}"


@st.composite
def _declaration_with_preceding_comment(draw: st.DrawFn) -> List[str]:
    """Generate a block: one or more comment lines followed by a KEY=value declaration."""
    num_comments = draw(st.integers(min_value=1, max_value=3))
    comments = [draw(_comment_line()) for _ in range(num_comments)]
    key = draw(_valid_key())
    value = draw(_valid_value())
    return comments + [f"{key}={value}"]


@st.composite
def _well_formed_env_content(draw: st.DrawFn) -> str:
    """Generate a well-formed .env.example file content.

    Rules enforced:
    - Every non-blank, non-comment line matches ^[A-Z_][A-Z0-9_]*=.*$ (Check 4)
    - Every declaration is preceded by a comment or another declaration (Check 5)

    Strategy: generate blocks of [comment(s) + declaration], optionally
    inserting blank lines BETWEEN blocks (not between a comment and its declaration).
    """
    num_blocks = draw(st.integers(min_value=1, max_value=6))
    lines: List[str] = []

    for i in range(num_blocks):
        # Optionally add blank lines between blocks (safe — blank lines between
        # completed blocks don't violate Check 5 because the next block starts
        # with a comment before its declaration).
        if i > 0:
            num_blanks = draw(st.integers(min_value=0, max_value=2))
            lines.extend([""] * num_blanks)

        block = draw(_declaration_with_preceding_comment())
        lines.extend(block)

    return "\n".join(lines) + "\n"


@st.composite
def _malformed_env_content_bad_format(draw: st.DrawFn) -> str:
    """Generate .env.example content with at least one line violating Check 4.

    Injects a line that does NOT match ^[A-Z_][A-Z0-9_]*=.*$ among otherwise
    valid content. Invalid formats include:
    - lowercase key (e.g., "foo=bar")
    - key starting with a digit (e.g., "1VAR=x")
    - missing '=' (e.g., "NOEQUALS")
    - key with special characters (e.g., "MY-VAR=x")
    """
    # Generate some valid content first
    valid_block = draw(_declaration_with_preceding_comment())

    # Generate a malformed line
    malformed_type = draw(st.integers(min_value=0, max_value=3))
    if malformed_type == 0:
        # Lowercase key
        malformed = draw(st.from_regex(r"\A[a-z][a-z0-9_]*=[^\n]*\Z", fullmatch=True))
    elif malformed_type == 1:
        # Key starting with digit
        malformed = draw(st.from_regex(r"\A[0-9][A-Z0-9_]*=[^\n]*\Z", fullmatch=True))
    elif malformed_type == 2:
        # Missing '=' — plain text that isn't a comment or blank
        malformed = draw(st.from_regex(r"\A[A-Z_][A-Z0-9_]+\Z", fullmatch=True))
    else:
        # Key with special characters (hyphen, dot, etc.)
        malformed = draw(st.from_regex(r"\A[A-Z][A-Z0-9]*[-\.][A-Z0-9]+=[^\n]*\Z", fullmatch=True))

    # Place the malformed line after a comment (so Check 5 doesn't mask it)
    lines = valid_block + [f"# Next line is malformed", malformed]
    return "\n".join(lines) + "\n"


@st.composite
def _malformed_env_content_bad_preceding(draw: st.DrawFn) -> str:
    """Generate .env.example content with at least one declaration violating Check 5.

    Inserts a blank line between the comment and its declaration, causing the
    declaration to NOT be preceded by a comment or another declaration.
    """
    key = draw(_valid_key())
    value = draw(_valid_value())
    declaration_line = f"{key}={value}"

    # A comment, then a blank line, then the declaration — violates Check 5
    comment = draw(_comment_line())
    lines = [comment, "", declaration_line]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_checks_4_and_5(env_path: Path) -> ValidationResult:
    """Run check_env.py validation, isolating Checks 4 and 5 results.

    We create a minimal compose_refs list that matches all declared variables
    using :? or :- syntax so that Checks 1-3 pass, and only shape checks
    (4 and 5) can produce violations.
    """
    from check_env import ComposeReference

    declarations = parse_env_example(env_path)

    # Build compose_refs that satisfy Checks 1-3 for every declared var.
    compose_refs: List[ComposeReference] = []
    for decl in declarations:
        if not decl.key:
            continue
        if decl.is_required:
            compose_refs.append(ComposeReference(key=decl.key, syntax="?", modifier_value="required"))
        else:
            compose_refs.append(ComposeReference(key=decl.key, syntax="-", modifier_value=decl.default_value))

    return validate(declarations, compose_refs, env_path)


def _violations_for_checks_4_and_5(result: ValidationResult) -> List[str]:
    """Filter violations to only those from Check 4 and Check 5."""
    check4_pattern = re.compile(r"\.env\.example line \d+ does not match")
    check5_pattern = re.compile(r"is not preceded by a")
    return [v for v in result.violations if check4_pattern.search(v) or check5_pattern.search(v)]


# ---------------------------------------------------------------------------
# Property 14: `.env.example` has canonical variable-declaration shape
# ---------------------------------------------------------------------------


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(content=_well_formed_env_content())
def test_env_example_shape(content: str, tmp_path: Path) -> None:
    """Well-formed .env.example content passes Checks 4 and 5; malformed content fails.

    **Validates: Requirements 11.2, 11.3, 11.4**

    Positive property:
      Any .env.example where every non-blank non-comment line matches
      ``^[A-Z_][A-Z0-9_]*=.*$`` AND every declaration is preceded by a
      comment or another declaration → check_env.py Checks 4 and 5 report
      no violations for those two checks.
    """
    env_file = tmp_path / ".env.example"
    env_file.write_text(content, encoding="utf-8")

    result = _run_checks_4_and_5(env_file)
    shape_violations = _violations_for_checks_4_and_5(result)

    assert shape_violations == [], (
        f"Well-formed .env.example should pass Checks 4 and 5, but got "
        f"violations:\n  {shape_violations}\n\nContent:\n{content}"
    )


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(content=_malformed_env_content_bad_format())
def test_env_example_shape_rejects_bad_format(content: str, tmp_path: Path) -> None:
    """Malformed line format (Check 4 violation) is detected by check_env.py.

    **Validates: Requirements 11.2, 11.3**

    Negative property:
      Any .env.example with at least one non-blank non-comment line that does
      NOT match ``^[A-Z_][A-Z0-9_]*=.*$`` → check_env.py Check 4 reports a
      violation.
    """
    env_file = tmp_path / ".env.example"
    env_file.write_text(content, encoding="utf-8")

    result = _run_checks_4_and_5(env_file)
    shape_violations = _violations_for_checks_4_and_5(result)

    check4_violations = [v for v in shape_violations if "does not match" in v]
    assert len(check4_violations) > 0, (
        f"Malformed .env.example should trigger Check 4 violation, but "
        f"none found.\n\nContent:\n{content}"
    )


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(content=_malformed_env_content_bad_preceding())
def test_env_example_shape_rejects_bad_preceding(content: str, tmp_path: Path) -> None:
    """Missing preceding comment/declaration (Check 5 violation) is detected.

    **Validates: Requirements 11.2, 11.4**

    Negative property:
      Any .env.example with at least one declaration NOT preceded by a
      ``#``-comment or another declaration → check_env.py Check 5 reports a
      violation.
    """
    env_file = tmp_path / ".env.example"
    env_file.write_text(content, encoding="utf-8")

    result = _run_checks_4_and_5(env_file)
    shape_violations = _violations_for_checks_4_and_5(result)

    check5_violations = [v for v in shape_violations if "is not preceded by" in v]
    assert len(check5_violations) > 0, (
        f"Malformed .env.example should trigger Check 5 violation, but "
        f"none found.\n\nContent:\n{content}"
    )


# ---------------------------------------------------------------------------
# Property 15: Compose reference syntax matches the .env.example
#              REQUIRED/default declaration
# ---------------------------------------------------------------------------


@st.composite
def _env_declarations(draw: st.DrawFn) -> List[dict]:
    """Generate a list of variable declarations, each either REQUIRED or with a default.

    Returns a list of dicts with keys: key, is_required, default_value.
    Keys are guaranteed unique.
    """
    num_vars = draw(st.integers(min_value=1, max_value=6))
    keys_seen: set = set()
    declarations: List[dict] = []

    for _ in range(num_vars):
        key = draw(_valid_key())
        # Ensure unique keys
        while key in keys_seen:
            key = draw(_valid_key())
        keys_seen.add(key)

        is_required = draw(st.booleans())
        if is_required:
            default_value = ""
        else:
            # Generate a non-empty default value (printable ASCII, no spaces/newlines/# to avoid comment stripping issues)
            safe_alphabet = "abcdefghijklmnopqrstuvwxyz0123456789.-_/:@"
            default_value = draw(st.text(alphabet=safe_alphabet, min_size=1, max_size=20))

        declarations.append({"key": key, "is_required": is_required, "default_value": default_value})

    return declarations


def _build_env_example(declarations: List[dict]) -> str:
    """Build a well-formed .env.example string from declaration dicts."""
    lines: List[str] = []
    for decl in declarations:
        # Each variable preceded by a comment (satisfies Check 5)
        lines.append(f"# {decl['key']} description")
        if decl["is_required"]:
            lines.append(f"{decl['key']}=")
        else:
            lines.append(f"{decl['key']}={decl['default_value']}")
    return "\n".join(lines) + "\n"


def _build_compose_correct(declarations: List[dict]) -> str:
    """Build compose content where every reference uses the CORRECT syntax."""
    lines: List[str] = []
    for decl in declarations:
        if decl["is_required"]:
            # REQUIRED → use :? syntax
            lines.append(f"      - {decl['key']}=${{{decl['key']}:?set {decl['key']} in .env}}")
        else:
            # Has default → use :- syntax with matching default
            lines.append(f"      - {decl['key']}=${{{decl['key']}:-{decl['default_value']}}}")
    return "services:\n  test-svc:\n    environment:\n" + "\n".join(lines) + "\n"


def _build_compose_wrong(declarations: List[dict]) -> str:
    """Build compose content where every reference uses the WRONG syntax.

    REQUIRED vars use :- (should be :?) and default vars use :? (should be :-).
    """
    lines: List[str] = []
    for decl in declarations:
        if decl["is_required"]:
            # WRONG: REQUIRED var uses :- instead of :?
            lines.append(f"      - {decl['key']}=${{{decl['key']}:-wrongdefault}}")
        else:
            # WRONG: default var uses :? instead of :-
            lines.append(f"      - {decl['key']}=${{{decl['key']}:?error msg}}")
    return "services:\n  test-svc:\n    environment:\n" + "\n".join(lines) + "\n"


def _violations_for_checks_2_and_3(result: ValidationResult) -> List[str]:
    """Filter violations to only those from Check 2 and Check 3."""
    check2_pattern = re.compile(r"does not use \$\{VAR:\?\.\.\.\} syntax")
    check3_pattern = re.compile(r"does not use \$\{VAR:-\.\.\.\} syntax|default mismatch")
    return [v for v in result.violations if check2_pattern.search(v) or check3_pattern.search(v)]


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(declarations=_env_declarations())
def test_compose_reference_syntax_matches(declarations: List[dict], tmp_path: Path) -> None:
    """Correct compose reference syntax produces no Check 2/3 violations;
    incorrect syntax is detected.

    **Validates: Requirements 11.5, 11.6**

    Property 15 — positive case:
      When REQUIRED vars use :? and default vars use :- with matching defaults,
      Checks 2 and 3 produce no violations.

    Property 15 — negative case:
      When REQUIRED vars use :- or default vars use :?, Checks 2 and 3 detect
      violations for every incorrectly-referenced variable.
    """
    from check_env import ComposeReference, parse_compose_references, parse_env_example, validate

    # --- Build files ---
    env_content = _build_env_example(declarations)
    env_file = tmp_path / ".env.example"
    env_file.write_text(env_content, encoding="utf-8")

    # --- POSITIVE CASE: correct syntax ---
    compose_correct_content = _build_compose_correct(declarations)
    compose_correct_file = tmp_path / "docker-compose-correct.yml"
    compose_correct_file.write_text(compose_correct_content, encoding="utf-8")

    decls = parse_env_example(env_file)
    refs_correct = parse_compose_references(compose_correct_file)
    result_correct = validate(decls, refs_correct, env_file)

    violations_correct = _violations_for_checks_2_and_3(result_correct)
    assert violations_correct == [], (
        f"Correct syntax should produce no Check 2/3 violations, but got:\n"
        f"  {violations_correct}\n\n"
        f".env.example:\n{env_content}\n"
        f"compose:\n{compose_correct_content}"
    )

    # --- NEGATIVE CASE: wrong syntax ---
    compose_wrong_content = _build_compose_wrong(declarations)
    compose_wrong_file = tmp_path / "docker-compose-wrong.yml"
    compose_wrong_file.write_text(compose_wrong_content, encoding="utf-8")

    refs_wrong = parse_compose_references(compose_wrong_file)
    result_wrong = validate(decls, refs_wrong, env_file)

    violations_wrong = _violations_for_checks_2_and_3(result_wrong)
    # Every declaration should trigger a violation since we inverted the syntax
    assert len(violations_wrong) >= len(declarations), (
        f"Wrong syntax should produce at least {len(declarations)} Check 2/3 violations "
        f"(one per variable), but got {len(violations_wrong)}:\n"
        f"  {violations_wrong}\n\n"
        f".env.example:\n{env_content}\n"
        f"compose:\n{compose_wrong_content}"
    )


# ---------------------------------------------------------------------------
# Property 16: Declared and referenced env-var sets are equal
# ---------------------------------------------------------------------------


@st.composite
def _env_var_partition(draw: st.DrawFn) -> dict:
    """Generate a partition of variable names into three disjoint sets.

    Returns a dict with:
      - "both": set of keys present in both .env.example and compose
      - "env_only": set of keys declared in .env.example but NOT referenced in compose
      - "compose_only": set of keys referenced in compose but NOT declared in .env.example

    At least one key is always present in "both" to keep the scenario realistic.
    """
    # Generate a pool of unique keys
    num_keys = draw(st.integers(min_value=1, max_value=8))
    keys: list[str] = []
    seen: set[str] = set()
    for _ in range(num_keys):
        k = draw(_valid_key())
        while k in seen:
            k = draw(_valid_key())
        seen.add(k)
        keys.append(k)

    # Partition keys: at least 1 goes to "both"
    both_count = draw(st.integers(min_value=1, max_value=max(1, len(keys))))
    both = set(keys[:both_count])
    remaining = keys[both_count:]

    # Split remaining between env_only and compose_only
    env_only: set[str] = set()
    compose_only: set[str] = set()
    for k in remaining:
        dest = draw(st.sampled_from(["env_only", "compose_only"]))
        if dest == "env_only":
            env_only.add(k)
        else:
            compose_only.add(k)

    return {"both": both, "env_only": env_only, "compose_only": compose_only}


def _build_env_example_from_keys(keys: set[str]) -> str:
    """Build a well-formed .env.example from a set of keys (all with defaults)."""
    lines: List[str] = []
    for key in sorted(keys):
        lines.append(f"# {key} description")
        lines.append(f"{key}=default_value")
    return "\n".join(lines) + "\n"


def _build_compose_from_keys(keys: set[str]) -> str:
    """Build a minimal compose YAML referencing the given keys with ${VAR:-default} syntax."""
    env_lines: List[str] = []
    for key in sorted(keys):
        env_lines.append(f"      - {key}=${{{key}:-default_value}}")
    return "services:\n  test-svc:\n    environment:\n" + "\n".join(env_lines) + "\n"


def _violations_for_check_1(result: ValidationResult) -> List[str]:
    """Filter violations to only those from Check 1 (set equality)."""
    check1_declared = re.compile(r"declared in \.env\.example but NOT referenced")
    check1_referenced = re.compile(r"referenced in docker-compose\.yml but NOT declared")
    return [v for v in result.violations if check1_declared.search(v) or check1_referenced.search(v)]


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(partition=_env_var_partition())
def test_declared_equals_referenced(partition: dict, tmp_path: Path) -> None:
    """set(declared_vars) == set(referenced_vars) produces no Check-1 violations;
    any difference is reported per-variable.

    **Validates: Requirements 11.7**

    Property 16 — positive case:
      When the declared variable set exactly equals the referenced variable set,
      Check 1 produces zero violations.

    Property 16 — negative case:
      When there are extra variables in either .env.example or docker-compose.yml
      (or both), Check 1 reports exactly one violation per extra variable.
    """
    both: set[str] = partition["both"]
    env_only: set[str] = partition["env_only"]
    compose_only: set[str] = partition["compose_only"]

    declared_keys = both | env_only
    referenced_keys = both | compose_only

    # --- Build synthetic files ---
    env_content = _build_env_example_from_keys(declared_keys)
    env_file = tmp_path / ".env.example"
    env_file.write_text(env_content, encoding="utf-8")

    compose_content = _build_compose_from_keys(referenced_keys)
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(compose_content, encoding="utf-8")

    # --- Run validation ---
    decls = parse_env_example(env_file)
    refs = parse_compose_references(compose_file)
    result = validate(decls, refs, env_file)

    check1_violations = _violations_for_check_1(result)

    # --- POSITIVE CASE: when sets are equal, no Check-1 violations ---
    if not env_only and not compose_only:
        assert check1_violations == [], (
            f"When declared == referenced, Check 1 should produce no violations, "
            f"but got:\n  {check1_violations}\n\n"
            f"declared_keys={declared_keys}\nreferenced_keys={referenced_keys}"
        )
    else:
        # --- NEGATIVE CASE: violations for every extra variable ---
        expected_violation_count = len(env_only) + len(compose_only)
        assert len(check1_violations) == expected_violation_count, (
            f"Expected {expected_violation_count} Check-1 violations "
            f"({len(env_only)} env-only + {len(compose_only)} compose-only), "
            f"but got {len(check1_violations)}:\n  {check1_violations}\n\n"
            f"env_only={env_only}\ncompose_only={compose_only}"
        )

        # Verify each env-only var is mentioned in a "declared but NOT referenced" violation
        for key in env_only:
            matching = [
                v
                for v in check1_violations
                if v.endswith(f": {key}") and "declared in .env.example" in v
            ]
            assert len(matching) == 1, (
                f"Expected exactly one 'declared but NOT referenced' violation for '{key}', "
                f"found {len(matching)}: {matching}"
            )

        # Verify each compose-only var is mentioned in a "referenced but NOT declared" violation
        for key in compose_only:
            matching = [
                v
                for v in check1_violations
                if v.endswith(f": {key}") and "referenced in docker-compose.yml" in v
            ]
            assert len(matching) == 1, (
                f"Expected exactly one 'referenced but NOT declared' violation for '{key}', "
                f"found {len(matching)}: {matching}"
            )
