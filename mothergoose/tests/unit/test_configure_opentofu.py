"""
Unit tests for OpenTofu configuration template rendering.
Task 17.1: Test OpenTofu template rendering for runner deployment.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.schema.tofu_schemas import TofuBackendS3Options, TofuProvidersVer
from app.services.opentofu_binary import OpenTofuUpdateGithub
from app.services.opentofu_configuration import OpenTofuConfiguration, TofuSetting


@pytest.fixture
def tofu_settings():
    """Create TofuSetting fixture with all required fields."""
    return TofuSetting(
        providers=[
            TofuProvidersVer(
                name="yandex",
                version="0.100.0",
                source="yandex-cloud/yandex",
            ),
            TofuProvidersVer(
                name="aws",
                version="5.0.0",
                source="hashicorp/aws",
            ),
        ],
        backend_s3_options=TofuBackendS3Options(
            bucket="test-bucket",
            key="test-key",
            region="us-east-1",
            endpoint="https://s3.amazonaws.com",
        ),
        worker_module_source={
            "type": "git",
            "url": "https://github.com/example/worker-module",
            "version": "v1.0.0",
        },
        worker_instances={
            "worker-1": {
                "instance_type": "t3.medium",
                "region": "us-east-1",
            },
            "worker-2": {
                "instance_type": "t3.large",
                "region": "us-west-2",
            },
        },
        vm_key_algorithm="RSA",
        vm_key_rsa_bits=4096,
        rift_required=True,
        rift_module_source={
            "type": "registry",
            "url": "registry.terraform.io/example/rift",
            "version": "1.2.0",
        },
        rift_instances={
            "rift-1": {
                "instance_type": "t3.small",
                "region": "us-east-1",
            },
        },
        worker_module_extra_variables={
            "vpc_id": "vpc_id",
            "subnet_id": "subnet_id",
        },
    )


@pytest.fixture
def opentofu_updater():
    """Create OpenTofu updater fixture."""
    updater = MagicMock(spec=OpenTofuUpdateGithub)
    updater.c_version = ["dummy_id", "1.8.0"]
    return updater


def test_tofu_setting_initialization(tofu_settings):
    """Test TofuSetting dataclass initialization with new fields."""
    assert tofu_settings.vm_key_algorithm == "RSA"
    assert tofu_settings.vm_key_rsa_bits == 4096
    assert tofu_settings.rift_required is True
    assert tofu_settings.worker_module_source["type"] == "git"
    assert len(tofu_settings.worker_instances) == 2
    assert tofu_settings.rift_module_source is not None
    assert len(tofu_settings.rift_instances) == 1


def test_opentofu_configuration_initialization(opentofu_updater, tofu_settings):
    """Test OpenTofuConfiguration initialization with new TofuSetting fields."""
    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    assert config.tofu_settings.vm_key_algorithm == "RSA"
    assert config.tofu_settings.vm_key_rsa_bits == 4096
    assert config.tofu_settings.rift_required is True


@patch("app.services.opentofu_configuration.FileSystemLoader")
@patch("app.services.opentofu_configuration.Environment")
def test_template_rendering_providers(
    mock_env,
    mock_loader,
    opentofu_updater,
    tofu_settings,
):
    """Test providers.tf template rendering."""
    # Setup mocks
    mock_template = MagicMock()
    mock_template.render.return_value = "provider yandex {}\nprovider aws {}"
    mock_env_instance = MagicMock()
    mock_env_instance.get_template.return_value = mock_template
    mock_env.return_value = mock_env_instance

    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    # Call the private method through setup
    with tempfile.TemporaryDirectory() as tmpdir:
        config.runtime_config.workspace = tmpdir
        config._OpenTofuConfiguration__create_tofu_configuration_from_templates()  # pylint: disable=protected-access,line-too-long

        # Verify providers.tf was created
        providers_tf_path = os.path.join(tmpdir, "providers.tf")
        assert os.path.exists(providers_tf_path)


@patch("app.services.opentofu_configuration.FileSystemLoader")
@patch("app.services.opentofu_configuration.Environment")
def test_template_rendering_resources(
    mock_env,
    mock_loader,
    opentofu_updater,
    tofu_settings,
):
    """Test resources.tf template rendering."""
    # Setup mocks
    mock_template = MagicMock()
    mock_template.render.return_value = "resource tls_private_key {}"
    mock_env_instance = MagicMock()
    mock_env_instance.get_template.return_value = mock_template
    mock_env.return_value = mock_env_instance

    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    # Call the private method through setup
    with tempfile.TemporaryDirectory() as tmpdir:
        config.runtime_config.workspace = tmpdir
        config._OpenTofuConfiguration__create_tofu_configuration_from_templates()  # pylint: disable=protected-access,line-too-long

        # Verify resources.tf was created
        resources_tf_path = os.path.join(tmpdir, "resources.tf")
        assert os.path.exists(resources_tf_path)


@patch("app.services.opentofu_configuration.FileSystemLoader")
@patch("app.services.opentofu_configuration.Environment")
def test_template_rendering_variables(
    mock_env,
    mock_loader,
    opentofu_updater,
    tofu_settings,
):
    """Test variables.tf template rendering."""
    # Setup mocks
    mock_template = MagicMock()
    mock_template.render.return_value = "variable tofu_worker_instances {}"
    mock_env_instance = MagicMock()
    mock_env_instance.get_template.return_value = mock_template
    mock_env.return_value = mock_env_instance

    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    # Call the private method through setup
    with tempfile.TemporaryDirectory() as tmpdir:
        config.runtime_config.workspace = tmpdir
        config._OpenTofuConfiguration__create_tofu_configuration_from_templates()  # pylint: disable=protected-access,line-too-long

        # Verify variables.tf was created
        variables_tf_path = os.path.join(tmpdir, "variables.tf")
        assert os.path.exists(variables_tf_path)


@patch("app.services.opentofu_configuration.FileSystemLoader")
@patch("app.services.opentofu_configuration.Environment")
def test_template_rendering_data(
    mock_env,
    mock_loader,
    opentofu_updater,
    tofu_settings,
):
    """Test data.tf template rendering (empty for now)."""
    # Setup mocks
    mock_template = MagicMock()
    mock_template.render.return_value = "# Data sources"
    mock_env_instance = MagicMock()
    mock_env_instance.get_template.return_value = mock_template
    mock_env.return_value = mock_env_instance

    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    # Call the private method through setup
    with tempfile.TemporaryDirectory() as tmpdir:
        config.runtime_config.workspace = tmpdir
        config._OpenTofuConfiguration__create_tofu_configuration_from_templates()  # pylint: disable=protected-access,line-too-long

        # Verify data.tf was created
        data_tf_path = os.path.join(tmpdir, "data.tf")
        assert os.path.exists(data_tf_path)


@patch("app.services.opentofu_configuration.FileSystemLoader")
@patch("app.services.opentofu_configuration.Environment")
def test_template_rendering_tofurc(
    mock_env,
    mock_loader,
    opentofu_updater,
    tofu_settings,
):
    """Test .tofurc template rendering."""
    # Setup mocks
    mock_template = MagicMock()
    mock_template.render.return_value = "provider_installation {}"
    mock_env_instance = MagicMock()
    mock_env_instance.get_template.return_value = mock_template
    mock_env.return_value = mock_env_instance

    config = OpenTofuConfiguration(
        updater=opentofu_updater,
        tofu_settings=tofu_settings,
    )

    # Call the private method through setup
    with tempfile.TemporaryDirectory() as tmpdir:
        config.runtime_config.workspace = tmpdir
        config._OpenTofuConfiguration__create_tofu_configuration_from_templates()  # pylint: disable=protected-access,line-too-long

        # Verify .tofurc was created
        tofurc_path = os.path.join(tmpdir, ".tofurc")
        assert os.path.exists(tofurc_path)


def test_tofu_setting_defaults():
    """Test TofuSetting default values."""
    minimal_settings = TofuSetting(
        providers=[
            TofuProvidersVer(
                name="aws",
                version="5.0.0",
                source="hashicorp/aws",
            ),
        ],
        backend_s3_options=TofuBackendS3Options(
            bucket="test-bucket",
            key="test-key",
            region="us-east-1",
        ),
        worker_module_source={
            "type": "registry",
            "url": "registry.terraform.io/example/worker",
            "version": "1.0.0",
        },
        worker_instances={},
    )

    # Check defaults
    assert minimal_settings.vm_key_algorithm == "RSA"
    assert minimal_settings.vm_key_rsa_bits == 4096
    assert minimal_settings.rift_required is False
    assert minimal_settings.rift_module_source is None
    assert minimal_settings.rift_instances is None
    assert minimal_settings.worker_module_extra_variables is None
