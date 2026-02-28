"""
Integration tests for FlyParser with real Gosling CLI binary.

These tests require the Gosling CLI binary to be available in PATH
or specified via GOSLING_CLI_PATH environment variable.

Tests are skipped if Gosling CLI is not available.
"""

import os
import shutil
import subprocess
import re

import pytest

from app.services.fly_parser import FlyParser


@pytest.fixture
def gosling_cli_available():
    """Check if Gosling CLI binary is available."""
    gosling_path = os.getenv("GOSLING_CLI_PATH", "gosling")
    return shutil.which(gosling_path) is not None


@pytest.fixture
def temp_nest_dir(tmp_path):
    """Create temporary Nest repository structure."""
    nest_dir = tmp_path / "nest"
    nest_dir.mkdir()

    # Create Eggs directory
    eggs_dir = nest_dir / "Eggs"
    eggs_dir.mkdir()

    # Create Jobs directory
    jobs_dir = nest_dir / "Jobs"
    jobs_dir.mkdir()

    # Create UF directory
    uf_dir = nest_dir / "UF"
    uf_dir.mkdir()

    return nest_dir


@pytest.fixture
def sample_egg_fly(temp_nest_dir):
    """Create sample Egg .fly file."""
    egg_dir = temp_nest_dir / "Eggs" / "my-app"
    egg_dir.mkdir()

    config_file = egg_dir / "config.fly"
    config_file.write_text(
        """
egg "my-app" {
  type = "vm"

  cloud {
    provider = "yandex"
    region   = "ru-central1-a"
  }

  resources {
    cpu    = 2
    memory = 4096
    disk   = 20
  }

  runner {
    tags        = ["docker", "linux"]
    concurrent  = 3
    idle_timeout = "10m"
  }

  gitlab {
    server     = "gitlab.com"
    project_id = 12345
  }

  environment {
    DOCKER_DRIVER = "overlay2"
  }
}
"""
    )

    return config_file


@pytest.fixture
def sample_job_fly(temp_nest_dir):
    """Create sample Job .fly file."""
    job_file = temp_nest_dir / "Jobs" / "rotate-secrets.fly"
    job_file.write_text(
        """
job "rotate-secrets" {
  schedule = "0 2 * * *"
  script   = "#!/bin/bash\\necho 'Rotating secrets'"

  runner {
    type = "vm"
    tags = ["privileged"]
  }
}
"""
    )

    return job_file


@pytest.fixture
def sample_uf_fly(temp_nest_dir):
    """Create sample UglyFox .fly file."""
    uf_file = temp_nest_dir / "UF" / "config.fly"
    uf_file.write_text(
        """
uglyfox {
  pruning {
    failed_threshold = 3
    max_age          = "24h"
    check_interval   = "5m"
  }

  runners_condition {
    default {
      eggs_entities = ["*"]

      apex {
        max_count         = 10
        min_count         = 2
        cpu_threshold     = 80
        memory_threshold  = 70
      }

      nadir {
        max_count    = 5
        min_count    = 0
        idle_timeout = "30m"
      }
    }
  }
}
"""
    )

    return uf_file


class TestFlyParserIntegration:
    """Integration tests with real Gosling CLI binary."""

    @pytest.fixture(autouse=True)
    def require_gosling_cli(self, gosling_cli_binary):
        """Skip tests if Gosling CLI binary is not available."""
        gosling_path = os.getenv("GOSLING_CLI_PATH", "gosling")
        if not shutil.which(gosling_path):
            pytest.skip("Gosling CLI binary not available")

    def _check_parse_command_available(self):
        """Check if Gosling CLI has parse command."""
        gosling_path = os.getenv("GOSLING_CLI_PATH", "gosling")
        try:
            result = subprocess.run(
                [gosling_path, "parse", "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ):
            return False

    def test_parse_egg_with_real_cli(self, sample_egg_fly):
        """Test parsing Egg configuration with real Gosling CLI."""
        parser = FlyParser()

        result = parser.parse_egg(sample_egg_fly)

        # Verify parsed configuration (may be placeholder if parse command not available)
        assert result["name"] == "my-app"
        assert result["type"] == "vm"
        assert "cloud" in result
        assert "resources" in result
        assert "runner" in result
        assert "gitlab" in result

        # If parse command is available, verify detailed parsing
        if self._check_parse_command_available():
            assert result["cloud"]["provider"] == "yandex"
            assert result["cloud"]["region"] == "ru-central1-a"
            assert result["resources"]["cpu"] == 2
            assert result["resources"]["memory"] == 4096
            assert result["resources"]["disk"] == 20
            assert result["runner"]["tags"] == ["docker", "linux"]
            assert result["runner"]["concurrent"] == 3
            assert result["runner"]["idle_timeout"] == "10m"
            assert result["gitlab"]["server"] == "gitlab.com"
            assert result["gitlab"]["project_id"] == 12345
            assert result["environment"]["DOCKER_DRIVER"] == "overlay2"

    def test_parse_job_with_real_cli(self, sample_job_fly):
        """Test parsing Job configuration with real Gosling CLI."""
        parser = FlyParser()

        result = parser.parse_job(sample_job_fly)

        # Verify parsed configuration (may be placeholder if parse command not available)
        assert result["name"] == "rotate-secrets"
        assert "schedule" in result
        assert "runner" in result
        assert "script" in result

        # If parse command is available, verify detailed parsing
        if self._check_parse_command_available():
            assert result["schedule"] == "0 2 * * *"
            assert re.search(
                r"#!/bin/bash\s+echo 'Rotating secrets'", result["script"]
            ), f"Not expected value. Got: {result['script']}"
            assert result["runner"]["type"] == "vm"
            assert result["runner"]["tags"] == ["privileged"]

    def test_parse_uf_config_with_real_cli(self, sample_uf_fly):
        """Test parsing UglyFox configuration with real Gosling CLI."""
        parser = FlyParser()

        result = parser.parse_uf_config(sample_uf_fly)

        # Verify parsed configuration (may be placeholder if parse command not available)
        assert "pruning" in result
        assert "runners_condition" in result

        # If parse command is available, verify detailed parsing
        if self._check_parse_command_available():
            assert result["pruning"]["failed_threshold"] == 3
            assert result["pruning"]["max_age"] == "24h"
            assert result["pruning"]["check_interval"] == "5m"
            assert result["runners_condition"]["default"]["eggs_entities"] == ["*"]
            assert result["runners_condition"]["default"]["apex"]["max_count"] == 10
            assert result["runners_condition"]["default"]["apex"]["min_count"] == 2
            assert result["runners_condition"]["default"]["nadir"]["max_count"] == 5
            assert result["runners_condition"]["default"]["nadir"]["min_count"] == 0

    def test_parse_eggs_directory_with_real_cli(self, temp_nest_dir, sample_egg_fly):
        """Test parsing Eggs directory with real Gosling CLI."""
        parser = FlyParser()

        # Create another egg
        egg2_dir = temp_nest_dir / "Eggs" / "api-service"
        egg2_dir.mkdir()
        config2_file = egg2_dir / "config.fly"
        config2_file.write_text(
            """
egg "api-service" {
  type = "serverless"

  cloud {
    provider = "aws"
    region   = "us-east-1"
  }

  resources {
    cpu    = 1
    memory = 2048
  }

  runner {
    tags = ["api", "serverless"]
  }

  gitlab {
    server     = "gitlab.company.com"
    project_id = 67890
  }
}
"""
        )

        eggs_dir = temp_nest_dir / "Eggs"
        result = parser.parse_eggs_directory(eggs_dir)

        # Verify results
        assert len(result) == 2
        egg_names = {egg["name"] for egg in result}
        assert "my-app" in egg_names
        assert "api-service" in egg_names

    def test_parse_jobs_directory_with_real_cli(self, temp_nest_dir, sample_job_fly):
        """Test parsing Jobs directory with real Gosling CLI."""
        parser = FlyParser()

        # Create another job
        job2_file = temp_nest_dir / "Jobs" / "update-images.fly"
        job2_file.write_text(
            """
job "update-images" {
  schedule = "0 3 * * 0"
  script   = "#!/bin/bash\\necho 'Updating images'"

  runner {
    type = "serverless"
    tags = ["maintenance"]
  }
}
"""
        )

        jobs_dir = temp_nest_dir / "Jobs"
        result = parser.parse_jobs_directory(jobs_dir)

        # Verify results
        assert len(result) == 2
        job_names = {job["name"] for job in result}
        assert "rotate-secrets" in job_names
        assert "update-images" in job_names

    def test_parse_invalid_fly_file(self, temp_nest_dir):
        """Test parsing invalid .fly file falls back to placeholder."""
        parser = FlyParser()

        # Create invalid .fly file
        egg_dir = temp_nest_dir / "Eggs" / "invalid-app"
        egg_dir.mkdir()
        config_file = egg_dir / "config.fly"
        config_file.write_text("invalid syntax {{{")

        result = parser.parse_egg(config_file)

        # Should fall back to placeholder data
        assert result["name"] == "invalid-app"
        assert result["type"] == "vm"
        assert "cloud" in result
        assert "resources" in result

    def test_parse_nonexistent_file(self, temp_nest_dir):
        """Test parsing nonexistent file falls back to placeholder."""
        parser = FlyParser()

        nonexistent_file = temp_nest_dir / "Eggs" / "missing" / "config.fly"

        result = parser.parse_egg(nonexistent_file)

        # Should fall back to placeholder data
        assert result["name"] == "missing"
        assert result["type"] == "vm"

    def test_custom_gosling_cli_path(self, sample_egg_fly):
        """Test using custom Gosling CLI path."""
        # Get actual Gosling CLI path
        gosling_path = shutil.which(os.getenv("GOSLING_CLI_PATH", "gosling"))

        if gosling_path:
            parser = FlyParser(gosling_cli_path=gosling_path)

            result = parser.parse_egg(sample_egg_fly)

            # Verify parsing succeeded
            assert result["name"] == "my-app"
            assert result["type"] == "vm"


class TestFlyParserFallback:
    """Tests for fallback behavior when Gosling CLI is not available."""

    @pytest.fixture(autouse=True)
    def skip_if_gosling_available(self, gosling_cli_binary):
        """Skip fallback tests if Gosling CLI binary IS available."""
        gosling_path = os.getenv("GOSLING_CLI_PATH", "gosling")
        if shutil.which(gosling_path) is not None:
            pytest.skip("Gosling CLI binary is available - testing fallback behavior")

    def test_parse_egg_fallback_when_cli_missing(self, sample_egg_fly):
        """Test fallback to placeholder when Gosling CLI is missing."""
        parser = FlyParser(gosling_cli_path="/nonexistent/gosling")

        result = parser.parse_egg(sample_egg_fly)

        # Should return placeholder data
        assert result["name"] == "my-app"
        assert result["type"] == "vm"
        assert "cloud" in result
        assert "resources" in result
        assert "runner" in result

    def test_parse_job_fallback_when_cli_missing(self, sample_job_fly):
        """Test fallback to placeholder when Gosling CLI is missing."""
        parser = FlyParser(gosling_cli_path="/nonexistent/gosling")

        result = parser.parse_job(sample_job_fly)

        # Should return placeholder data
        assert result["name"] == "rotate-secrets"
        assert "schedule" in result
        assert "runner" in result
        assert "script" in result

    def test_parse_uf_config_fallback_when_cli_missing(self, sample_uf_fly):
        """Test fallback to placeholder when Gosling CLI is missing."""
        parser = FlyParser(gosling_cli_path="/nonexistent/gosling")

        result = parser.parse_uf_config(sample_uf_fly)

        # Should return placeholder data
        assert "pruning" in result
        assert "runners_condition" in result
