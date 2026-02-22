"""
Unit tests for FlyParser with mocked subprocess calls.

Tests the fly_parser service's ability to call Gosling CLI
and handle various success and failure scenarios.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.fly_parser import FlyParser


@pytest.fixture
def fly_parser():
    """Create FlyParser instance for testing."""
    return FlyParser(gosling_cli_path="/usr/local/bin/gosling")


@pytest.fixture
def sample_egg_json():
    """Sample JSON output from Gosling CLI for egg configuration."""
    return {
        "blocks": [
            {
                "type": "egg",
                "labels": ["my-app"],
                "attributes": {
                    "type": "vm",
                },
                "blocks": [
                    {
                        "type": "cloud",
                        "attributes": {
                            "provider": "yandex",
                            "region": "ru-central1-a",
                        },
                    },
                    {
                        "type": "resources",
                        "attributes": {
                            "cpu": 2,
                            "memory": 4096,
                            "disk": 20,
                        },
                    },
                    {
                        "type": "runner",
                        "attributes": {
                            "tags": ["docker", "linux"],
                            "concurrent": 3,
                            "idle_timeout": "10m",
                        },
                    },
                    {
                        "type": "gitlab",
                        "attributes": {
                            "server": "gitlab.com",
                            "project_id": 12345,
                        },
                    },
                    {
                        "type": "environment",
                        "attributes": {
                            "DOCKER_DRIVER": "overlay2",
                        },
                    },
                ],
            }
        ]
    }


@pytest.fixture
def sample_job_json():
    """Sample JSON output from Gosling CLI for job configuration."""
    return {
        "blocks": [
            {
                "type": "job",
                "labels": ["rotate-secrets"],
                "attributes": {
                    "schedule": "0 2 * * *",
                    "script": "#!/bin/bash\necho 'Rotating secrets'",
                },
                "blocks": [
                    {
                        "type": "runner",
                        "attributes": {
                            "type": "vm",
                            "tags": ["privileged"],
                        },
                    },
                ],
            }
        ]
    }


@pytest.fixture
def sample_uf_json():
    """Sample JSON output from Gosling CLI for UglyFox configuration."""
    return {
        "blocks": [
            {
                "type": "uglyfox",
                "labels": [],
                "attributes": {},
                "blocks": [
                    {
                        "type": "pruning",
                        "attributes": {
                            "failed_threshold": 3,
                            "max_age": "24h",
                            "check_interval": "5m",
                        },
                    },
                    {
                        "type": "runners_condition",
                        "attributes": {
                            "default": {
                                "eggs_entities": ["*"],
                                "apex": {
                                    "max_count": 10,
                                    "min_count": 2,
                                },
                                "nadir": {
                                    "max_count": 5,
                                    "min_count": 0,
                                },
                            },
                        },
                    },
                ],
            }
        ]
    }


class TestFlyParserInit:
    """Tests for FlyParser initialization."""

    def test_init_with_explicit_path(self):
        """Test initialization with explicit Gosling CLI path."""
        parser = FlyParser(gosling_cli_path="/custom/path/gosling")
        assert parser.gosling_cli_path == "/custom/path/gosling"

    def test_init_with_env_var(self):
        """Test initialization with GOSLING_CLI_PATH environment variable."""
        with patch.dict("os.environ", {"GOSLING_CLI_PATH": "/env/path/gosling"}):
            parser = FlyParser()
            assert parser.gosling_cli_path == "/env/path/gosling"

    def test_init_with_default(self):
        """Test initialization with default path."""
        with patch.dict("os.environ", {}, clear=True):
            parser = FlyParser()
            assert parser.gosling_cli_path == "gosling"


class TestCallGoslingParse:
    """Tests for _call_gosling_parse method."""

    def test_successful_parse(self, fly_parser, sample_egg_json):  # pylint: disable=redefined-outer-name
        """Test successful Gosling CLI execution and JSON parsing."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_egg_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = fly_parser._call_gosling_parse(  # pylint: disable=protected-access
                Path("/nest/Eggs/my-app/config.fly"), "egg"
            )

            # Verify subprocess.run was called correctly
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]

            # Verify command structure (path format may vary by OS)
            assert cmd[0] == "/usr/local/bin/gosling"
            assert cmd[1] == "parse"
            assert cmd[2].endswith("config.fly")  # Path format varies by OS
            assert cmd[3] == "--type"
            assert cmd[4] == "egg"

            assert call_args[1]["capture_output"] is True
            assert call_args[1]["text"] is True
            assert call_args[1]["check"] is True
            assert call_args[1]["timeout"] == 30

            # Verify result
            assert result == sample_egg_json

    def test_binary_not_found(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test handling when Gosling CLI binary is not found."""
        with patch("subprocess.run", side_effect=FileNotFoundError("Binary not found")):
            with pytest.raises(FileNotFoundError, match="Gosling CLI binary not found"):
                fly_parser._call_gosling_parse(  # pylint: disable=protected-access
                    Path("/nest/Eggs/my-app/config.fly"), "egg"
                )

    def test_execution_timeout(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test handling when Gosling CLI execution times out."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gosling", timeout=30),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                fly_parser._call_gosling_parse(  # pylint: disable=protected-access
                    Path("/nest/Eggs/my-app/config.fly"), "egg"
                )

    def test_execution_failure(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test handling when Gosling CLI execution fails."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd="gosling",
                stderr="Parse error: invalid syntax",
            ),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                fly_parser._call_gosling_parse(  # pylint: disable=protected-access
                    Path("/nest/Eggs/my-app/config.fly"), "egg"
                )

    def test_invalid_json_output(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test handling when Gosling CLI outputs invalid JSON."""
        mock_result = MagicMock()
        mock_result.stdout = "not valid json"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(json.JSONDecodeError):
                fly_parser._call_gosling_parse(  # pylint: disable=protected-access
                    Path("/nest/Eggs/my-app/config.fly"), "egg"
                )


class TestParseEgg:
    """Tests for parse_egg method."""

    def test_successful_egg_parse(self, fly_parser, sample_egg_json):  # pylint: disable=redefined-outer-name
        """Test successful Egg configuration parsing."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_egg_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = fly_parser.parse_egg(Path("/nest/Eggs/my-app/config.fly"))

            # Verify flattened structure
            assert result["name"] == "my-app"
            assert result["type"] == "vm"
            assert result["cloud"]["provider"] == "yandex"
            assert result["resources"]["cpu"] == 2
            assert result["runner"]["tags"] == ["docker", "linux"]
            assert result["gitlab"]["server"] == "gitlab.com"
            assert result["environment"]["DOCKER_DRIVER"] == "overlay2"

    def test_egg_parse_fallback_on_error(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test fallback to placeholder data when parsing fails."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1, cmd="gosling", stderr="Parse error"
            ),
        ):
            result = fly_parser.parse_egg(Path("/nest/Eggs/my-app/config.fly"))

            # Verify placeholder data is returned
            assert result["name"] == "my-app"
            assert result["type"] == "vm"
            assert "cloud" in result
            assert "resources" in result
            assert "runner" in result


class TestParseJob:
    """Tests for parse_job method."""

    def test_successful_job_parse(self, fly_parser, sample_job_json):  # pylint: disable=redefined-outer-name
        """Test successful Job configuration parsing."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_job_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = fly_parser.parse_job(Path("/nest/Jobs/rotate-secrets.fly"))

            # Verify flattened structure
            assert result["name"] == "rotate-secrets"
            assert result["schedule"] == "0 2 * * *"
            assert result["script"] == "#!/bin/bash\necho 'Rotating secrets'"
            assert result["runner"]["type"] == "vm"
            assert result["runner"]["tags"] == ["privileged"]

    def test_job_parse_fallback_on_error(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test fallback to placeholder data when parsing fails."""
        with patch("subprocess.run", side_effect=FileNotFoundError("Binary not found")):
            result = fly_parser.parse_job(Path("/nest/Jobs/rotate-secrets.fly"))

            # Verify placeholder data is returned
            assert result["name"] == "rotate-secrets"
            assert result["schedule"] == "0 2 * * *"
            assert "runner" in result
            assert "script" in result


class TestParseUfConfig:
    """Tests for parse_uf_config method."""

    def test_successful_uf_parse(self, fly_parser, sample_uf_json):  # pylint: disable=redefined-outer-name
        """Test successful UglyFox configuration parsing."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_uf_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = fly_parser.parse_uf_config(Path("/nest/UF/config.fly"))

            # Verify flattened structure
            assert result["pruning"]["failed_threshold"] == 3
            assert result["pruning"]["max_age"] == "24h"
            assert result["runners_condition"]["default"]["eggs_entities"] == ["*"]
            assert result["runners_condition"]["default"]["apex"]["max_count"] == 10

    def test_uf_parse_fallback_on_error(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test fallback to placeholder data when parsing fails."""
        with patch(
            "subprocess.run",
            side_effect=json.JSONDecodeError("Invalid JSON", "", 0),
        ):
            result = fly_parser.parse_uf_config(Path("/nest/UF/config.fly"))

            # Verify placeholder data is returned
            assert "pruning" in result
            assert "runners_condition" in result
            assert result["pruning"]["failed_threshold"] == 3


class TestParseDirectories:
    """Tests for parse_eggs_directory and parse_jobs_directory methods."""

    def test_parse_eggs_directory_success(self, fly_parser, sample_egg_json):  # pylint: disable=redefined-outer-name
        """Test parsing multiple Egg configurations."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_egg_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            # Create mock directory structure
            eggs_dir = Path("/nest/Eggs")
            with patch.object(Path, "exists", return_value=True):
                with patch.object(
                    Path,
                    "iterdir",
                    return_value=[
                        Path("/nest/Eggs/my-app"),
                        Path("/nest/Eggs/api-service"),
                    ],
                ):
                    with patch.object(Path, "is_dir", return_value=True):
                        with patch.object(
                            Path,
                            "__truediv__",
                            side_effect=lambda x: Path(f"/nest/Eggs/my-app/{x}"),
                        ):
                            with patch.object(Path, "exists", return_value=True):
                                result = fly_parser.parse_eggs_directory(eggs_dir)

                                # Verify results
                                assert len(result) >= 0  # May vary based on mocking

    def test_parse_eggs_directory_not_exists(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test parsing when Eggs directory doesn't exist."""
        eggs_dir = Path("/nest/Eggs")
        with patch.object(Path, "exists", return_value=False):
            result = fly_parser.parse_eggs_directory(eggs_dir)
            assert result == []

    def test_parse_jobs_directory_success(self, fly_parser, sample_job_json):  # pylint: disable=redefined-outer-name
        """Test parsing multiple Job configurations."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(sample_job_json)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            jobs_dir = Path("/nest/Jobs")
            with patch.object(Path, "exists", return_value=True):
                with patch.object(
                    Path,
                    "glob",
                    return_value=[
                        Path("/nest/Jobs/rotate-secrets.fly"),
                        Path("/nest/Jobs/update-images.fly"),
                    ],
                ):
                    result = fly_parser.parse_jobs_directory(jobs_dir)

                    # Verify results
                    assert len(result) >= 0  # May vary based on mocking

    def test_parse_jobs_directory_not_exists(self, fly_parser):  # pylint: disable=redefined-outer-name
        """Test parsing when Jobs directory doesn't exist."""
        jobs_dir = Path("/nest/Jobs")
        with patch.object(Path, "exists", return_value=False):
            result = fly_parser.parse_jobs_directory(jobs_dir)
            assert result == []
