"""
Regression tests for OpenTofuConfiguration's template-rendering pipeline.

These tests exercise ``OpenTofuConfiguration.setup_tofu_configuration()`` /
``__create_tofu_configuration_from_templates()`` end-to-end with REAL Jinja2
templates and REAL ``TofuSetting`` / ``TofuBackendS3Options`` /
``TofuProvidersVer`` / ``TofuModuleSource`` instances (only the OpenTofu
``Tofu`` binary wrapper and the binary updater are mocked, since those touch
the filesystem/network and are irrelevant to template rendering).

Before this module was filled in, three bugs shipped silently because nothing
actually rendered the templates:

1. ``tofu_versions_tf.j2`` had a malformed ``{% endfor %`` (missing the
   closing brace) which raised ``TemplateSyntaxError`` on every single call.
2. ``tofu_rc.j2`` never closed its ``{% for mirror in morrors %}`` loop,
   which raised ``TemplateSyntaxError`` and, once naively "fixed" by nesting
   the ``direct`` block inside the loop, would have produced one ``direct``
   block per mirror instead of exactly one.
3. ``tofu_cloud_provider`` was never passed into the ``resources.tf`` render
   context, silently producing a broken git module path
   (``modules/?ref=...`` instead of ``modules/aws?ref=...``).

Each bug has a dedicated regression test below.
"""

import os
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schema.tofu_schemas import TofuBackendS3Options, TofuProvidersVer
from app.services.opentofu_configuration import (
    OpenTofuConfiguration,
    TofuModuleSource,
    TofuSetting,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend_s3_options(
    bucket: str = "mothergoose-state-bucket",
    key: str = "eggs/test-egg/terraform.tfstate",
    region: str = "us-east-1",
) -> TofuBackendS3Options:
    """Build a minimal, valid ``TofuBackendS3Options`` instance.

    Args:
        bucket: S3 bucket name for the backend.
        key: S3 object key for the state file.
        region: AWS region of the S3 bucket.

    Returns:
        A real (non-mocked) ``TofuBackendS3Options`` instance.
    """
    return TofuBackendS3Options(bucket=bucket, key=key, region=region)


def _providers(*names: str) -> List[TofuProvidersVer]:
    """Build a list of real ``TofuProvidersVer`` instances.

    Args:
        *names: Provider short names (e.g. "aws", "yandex"). A canned
            source/version is generated per known name; unknown names get a
            generic hashicorp-style source.

    Returns:
        List of ``TofuProvidersVer`` instances, one per name.

    Note:
        Built via ``model_construct(...)`` rather than the normal
        constructor. ``TofuProvidersVer.version``'s ``field_validator`` uses
        a triple-quoted regex pattern that (due to an unrelated,
        out-of-scope, pre-existing bug in ``tofu_schemas.py``) embeds literal
        leading/trailing newline+indentation characters without
        ``re.VERBOSE``, so it rejects every possible version string
        (including the exact defaults used in production in
        ``app/tasks/runners.py``). ``model_construct`` yields a genuine
        ``TofuProvidersVer`` instance (not a ``MagicMock``) while sidestepping
        that unrelated validator bug, which is out of this task's scope to fix.
    """
    known_sources = {
        "aws": "hashicorp/aws",
        "yandex": "yandex-cloud/yandex",
    }
    result = []
    for name in names:
        result.append(
            TofuProvidersVer.model_construct(
                name=name,
                version=">= 1.0",
                source=known_sources.get(name, f"hashicorp/{name}"),
            )
        )
    return result


def _make_config(tofu_settings: TofuSetting) -> OpenTofuConfiguration:
    """Construct an ``OpenTofuConfiguration`` with a mocked updater/binary.

    The updater and the ``Tofu`` binary wrapper are mocked because they touch
    the filesystem/network and are irrelevant to template rendering. The
    ``tofu_settings`` are always a REAL dataclass instance so the actual
    Jinja2 templates get rendered — this is what catches template bugs that a
    MagicMock settings object would silently swallow.

    Args:
        tofu_settings: Real ``TofuSetting`` instance to drive rendering.

    Returns:
        A configured ``OpenTofuConfiguration`` instance ready to call
        ``setup_tofu_configuration()`` on.
    """
    updater = MagicMock()
    # Anything other than the literal "dummy_id" skips the binary-update
    # branch inside __update_opentofu_binaries() cleanly.
    updater.c_version = ("real-id-not-dummy", "1.9.0")
    updater.start_update = AsyncMock()

    with patch("app.services.opentofu_configuration.Tofu") as mock_tofu_cls:
        mock_tofu_cls.return_value = MagicMock()
        cfg = OpenTofuConfiguration(
            updater=updater,
            tofu_settings=tofu_settings,
        )
    return cfg


def _workspace_path(cfg: OpenTofuConfiguration) -> str:
    """Get the workspace directory that templates were rendered into.

    ``setup_tofu_configuration()`` sets ``self.tofu.cwd`` to the (private,
    name-mangled) workspace path as its very last step. Since ``self.tofu``
    is a ``MagicMock`` in these tests, reading ``cfg.tofu.cwd`` back out is a
    clean, non-name-mangled way to recover the workspace path instead of
    reaching into ``cfg._OpenTofuConfiguration__tofu_workspace`` directly.

    Args:
        cfg: An ``OpenTofuConfiguration`` on which ``setup_tofu_configuration()``
            has already been awaited.

    Returns:
        Absolute path to the workspace directory containing rendered files.
    """
    return str(cfg.tofu.cwd)


def _read(workspace: str, filename: str) -> str:
    """Read back a rendered file from the workspace directory.

    Args:
        workspace: Workspace directory path.
        filename: Name of the rendered file (e.g. "versions.tf").

    Returns:
        File contents as a string.
    """
    with open(os.path.join(workspace, filename), "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# 1. Regression — setup_tofu_configuration() succeeds with minimal settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_tofu_configuration_minimal_settings_succeeds() -> None:
    """Regression test: minimal settings must render without raising.

    Guards against Bug 1 (malformed ``{% endfor %`` in
    ``tofu_versions_tf.j2``) and Bug 2 (unclosed ``{% for %}`` in
    ``tofu_rc.j2``) — both of which raised ``jinja2.exceptions.
    TemplateSyntaxError`` on literally every call to
    ``setup_tofu_configuration()``, regardless of settings content.
    """
    tofu_settings = TofuSetting(
        providers=_providers("aws"),
        backend_s3_options=_backend_s3_options(),
    )
    cfg = _make_config(tofu_settings)

    # Must not raise TemplateSyntaxError (Bug 1 / Bug 2).
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)

    for expected_file in (
        "versions.tf",
        "providers.tf",
        "variables.tf",
        "data.tf",
        ".tofurc",
    ):
        assert os.path.isfile(
            os.path.join(workspace, expected_file)
        ), f"{expected_file} was not created"

    # Guarded branches must NOT produce files when their settings are absent.
    assert not os.path.isfile(os.path.join(workspace, "resources.tf"))
    assert not os.path.isfile(os.path.join(workspace, "checks.tf"))


# ---------------------------------------------------------------------------
# 2. Regression for Bug 1 — versions.tf content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_versions_tf_content_bug1_regression() -> None:
    """Regression test for Bug 1 (missing ``}`` on ``{% endfor %`` line).

    Beyond "it doesn't crash", assert that versions.tf actually contains the
    S3 backend settings and a required_providers entry per configured
    provider — proving the for-loop body renders correctly once closed.
    """
    tofu_settings = TofuSetting(
        providers=_providers("yandex", "aws"),
        backend_s3_options=_backend_s3_options(
            bucket="my-state-bucket",
            key="path/to/state.tfstate",
            region="eu-central-1",
        ),
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    content = _read(workspace, "versions.tf")

    # S3 backend block values.
    assert 'bucket = "my-state-bucket"' in content
    assert 'key    = "path/to/state.tfstate"' in content
    assert 'region = "eu-central-1"' in content

    # required_providers block: one entry per configured provider, with the
    # correct source/version — this is the loop body that "{% endfor %"
    # (missing brace) prevented from ever rendering at all.
    assert "required_providers {" in content
    assert "yandex = {" in content
    assert 'source  = "yandex-cloud/yandex"' in content
    assert "aws = {" in content
    assert 'source  = "hashicorp/aws"' in content
    assert content.count('version = ">= 1.0"') == 2


# ---------------------------------------------------------------------------
# 3. Regression for Bug 2 — .tofurc mirror / direct block structure
# ---------------------------------------------------------------------------


def _mirrors(count: int) -> List[Dict[str, Any]]:
    """Build ``count`` distinct mirror dicts for ``TofuSetting.mirror_urls``.

    Args:
        count: Number of mirror entries to generate.

    Returns:
        List of mirror dicts, each with a unique ``url``.
    """
    return [{"url": f"https://mirror{i}.example.com/providers/"} for i in range(count)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mirror_count, direct_exclude, expected_mirror_blocks, expected_direct_blocks",
    [
        # (a) No mirrors, no direct exclusion -> nothing rendered inside
        #     provider_installation {}.
        (0, False, 0, 0),
        # (b) A single mirror, no direct exclusion -> exactly one
        #     network_mirror block, no direct block.
        (1, False, 1, 0),
        # (c) Multiple mirrors + direct_exclude=True -> N network_mirror
        #     blocks but the `direct` block must appear EXACTLY ONCE, not
        #     once per mirror. This is the precise defect Bug 2 caused: the
        #     unclosed {% for %} meant the `direct` block was a sibling of
        #     the *last* mirror render, and once naively fixed by nesting
        #     it inside the loop instead of closing the loop before it, it
        #     would render N times (once per mirror iteration) instead of
        #     once for the whole provider_installation block.
        (3, True, 3, 1),
    ],
)
async def test_tofurc_mirror_and_direct_block_counts_bug2_regression(
    mirror_count: int,
    direct_exclude: bool,
    expected_mirror_blocks: int,
    expected_direct_blocks: int,
) -> None:
    """Regression test for Bug 2 (unclosed ``{% for %}`` loop in tofu_rc.j2).

    Args:
        mirror_count: Number of mirrors to configure.
        direct_exclude: Value for ``TofuSetting.direct_exclude``.
        expected_mirror_blocks: Expected count of ``network_mirror {}`` blocks.
        expected_direct_blocks: Expected count of ``direct {}`` blocks
            (must always be 0 or 1, never one-per-mirror).
    """
    tofu_settings = TofuSetting(
        providers=_providers("aws"),
        backend_s3_options=_backend_s3_options(),
        mirror_urls=_mirrors(mirror_count),
        direct_exclude=direct_exclude,
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    content = _read(workspace, ".tofurc")

    mirror_blocks = len(re.findall(r"network_mirror\s*\{", content))
    direct_blocks = len(re.findall(r"direct\s*\{", content))

    assert mirror_blocks == expected_mirror_blocks, (
        f"expected {expected_mirror_blocks} network_mirror block(s), "
        f"found {mirror_blocks} in:\n{content}"
    )
    assert direct_blocks == expected_direct_blocks, (
        f"expected {expected_direct_blocks} direct block(s) (must be a "
        f"single sibling block, not one per mirror), found {direct_blocks} "
        f"in:\n{content}"
    )


# ---------------------------------------------------------------------------
# 4. Regression for Bug 3 — tofu_cloud_provider wiring in resources.tf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resources_tf_git_module_includes_cloud_provider_bug3_regression() -> (
    None
):
    """Regression test for Bug 3 (missing ``tofu_cloud_provider`` context var).

    Before the fix, ``tofu_cloud_provider`` was never passed into the
    ``resources.tf`` render context, so it silently rendered as an empty
    string (no ``StrictUndefined``), producing a broken git module source
    like ``.../modules/?ref=v1.2.3`` instead of ``.../modules/aws?ref=v1.2.3``.
    """
    worker_source = TofuModuleSource(
        url="https://github.com/example/tf-runner-modules",
        version="v1.2.3",
        type="git",
        cloud_provider="aws",
    )
    tofu_settings = TofuSetting(
        providers=_providers("aws"),
        backend_s3_options=_backend_s3_options(),
        worker_module_source=worker_source,
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    assert os.path.isfile(os.path.join(workspace, "resources.tf"))
    content = _read(workspace, "resources.tf")

    source_line = next(
        line for line in content.splitlines() if line.strip().startswith("source =")
    )
    assert "modules/aws" in source_line
    assert "modules/?ref=" not in source_line
    assert 'ref=v1.2.3"' in source_line


@pytest.mark.asyncio
async def test_resources_tf_registry_module_ignores_cloud_provider() -> None:
    """For ``type="registry"`` sources, ``tofu_cloud_provider`` is irrelevant.

    The registry branch of the template never references
    ``tofu_cloud_provider`` — this test confirms the registry-style
    ``source``/``version`` lines still render correctly regardless.
    """
    worker_source = TofuModuleSource(
        url="registry.opentofu.org/example/runner-worker/aws",
        version="1.4.2",
        type="registry",
    )
    tofu_settings = TofuSetting(
        providers=_providers("aws"),
        backend_s3_options=_backend_s3_options(),
        worker_module_source=worker_source,
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    content = _read(workspace, "resources.tf")

    assert 'source  = "registry.opentofu.org/example/runner-worker/aws"' in content
    assert 'version = "1.4.2"' in content
    assert "git::" not in content


# ---------------------------------------------------------------------------
# 5. Rift module rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resources_tf_includes_rift_module_when_required() -> None:
    """When ``tofu_rift_required`` and ``rift_module_source`` are set, a
    ``module "rift"`` block must appear in resources.tf.

    Note: resources.tf is only rendered at all when ``worker_module_source``
    is configured (the module-level guard), so both sources are set here.
    """
    worker_source = TofuModuleSource(
        url="https://github.com/example/tf-runner-modules",
        version="v1.0.0",
        type="git",
        cloud_provider="yandex",
    )
    rift_source = TofuModuleSource(
        url="https://github.com/example/tf-rift-module",
        version="v2.0.0",
        type="git",
    )
    tofu_settings = TofuSetting(
        providers=_providers("yandex"),
        backend_s3_options=_backend_s3_options(),
        worker_module_source=worker_source,
        rift_module_source=rift_source,
        tofu_rift_required=True,
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    content = _read(workspace, "resources.tf")

    assert 'module "rift" {' in content
    assert "tf-rift-module.git?ref=v2.0.0" in content


# ---------------------------------------------------------------------------
# 6. Health checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checks_tf_created_with_health_checks() -> None:
    """When ``health_checks`` is configured, checks.tf must be created and
    contain the check name and URL.
    """
    health_checks: List[Dict[str, Optional[str]]] = [
        {
            "name": "runner_health_check",
            "type": "http",
            "url": "https://example.com/health",
        }
    ]
    tofu_settings = TofuSetting(
        providers=_providers("aws"),
        backend_s3_options=_backend_s3_options(),
        health_checks=health_checks,
    )
    cfg = _make_config(tofu_settings)
    await cfg.setup_tofu_configuration()

    workspace = _workspace_path(cfg)
    assert os.path.isfile(os.path.join(workspace, "checks.tf"))
    content = _read(workspace, "checks.tf")

    assert "runner_health_check" in content
    assert "https://example.com/health" in content
