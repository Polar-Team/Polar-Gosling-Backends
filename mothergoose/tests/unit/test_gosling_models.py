"""
Unit tests for gosling_models.py - GoslingVersionTableYDB and GoslingModelYDB.
No YDB container required; these are pure validation tests.
"""

# pylint: disable=duplicate-code

import pytest
from pydantic import ValidationError

from app.model.gosling_models import GoslingModelYDB, GoslingVersionTableYDB
from app.types.ydb_types import YDBType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ydb_type(ydb_type: str):  # type: ignore[return]
    """Create a YDB type instance."""
    return YDBType({"type": ydb_type}).root  # type: ignore[arg-type]


def default_columns():
    """Return the default valid column tuple."""
    return ("version_id", "version", "source", "downloaded_at", "sha256_hash", "active")


def default_r_type():
    """Return the default valid r_type tuple."""
    return (
        make_ydb_type("Utf8"),
        make_ydb_type("Utf8"),
        make_ydb_type("Utf8"),
        make_ydb_type("Utf8"),
        make_ydb_type("Utf8"),
        make_ydb_type("Bool"),
    )


# ---------------------------------------------------------------------------
# GoslingVersionTableYDB - valid instantiation
# ---------------------------------------------------------------------------


class TestGoslingVersionTableYDBValid:
    """Tests for valid GoslingVersionTableYDB instantiation."""

    def test_default_instantiation_succeeds(self):
        """Valid default instantiation should not raise."""
        table = GoslingVersionTableYDB()
        assert table.table_name == "gosling_version"
        assert table.primary_key == "version_id"
        assert len(table.columns) == 6
        assert len(table.r_type) == 6

    def test_values_for_operate_empty_by_default(self):
        """values_for_operate defaults to empty tuple."""
        table = GoslingVersionTableYDB()
        assert table.values_for_operate == ()

    def test_r_type_last_is_bool(self):
        """The 'active' column r_type must be Bool."""
        table = GoslingVersionTableYDB()
        assert table.r_type[5].type == "Bool"

    def test_r_type_first_five_are_utf8(self):
        """The first five r_types must be Utf8."""
        table = GoslingVersionTableYDB()
        for i in range(5):
            assert table.r_type[i].type == "Utf8"


# ---------------------------------------------------------------------------
# GoslingVersionTableYDB - table_name validation
# ---------------------------------------------------------------------------


class TestGoslingVersionTableYDBTableName:
    """Tests for table_name validation."""

    def test_table_name_not_starting_with_gosling_version_raises(self):
        """table_name not starting with 'gosling_version' must raise ValueError."""
        with pytest.raises(ValueError, match="gosling_version"):
            GoslingVersionTableYDB(table_name="opentofu_version")

    def test_table_name_empty_raises(self):
        """Empty table_name must raise ValueError."""
        with pytest.raises(ValueError):
            GoslingVersionTableYDB(table_name="")

    def test_table_name_with_suffix_is_valid(self):
        """table_name starting with 'gosling_version' with suffix is valid."""
        table = GoslingVersionTableYDB(table_name="gosling_version_v2")
        assert table.table_name == "gosling_version_v2"


# ---------------------------------------------------------------------------
# GoslingVersionTableYDB - primary_key validation
# ---------------------------------------------------------------------------


class TestGoslingVersionTableYDBPrimaryKey:
    """Tests for primary_key validation."""

    def test_wrong_primary_key_raises(self):
        """primary_key other than 'version_id' must raise ValueError."""
        with pytest.raises(ValueError, match="version_id"):
            GoslingVersionTableYDB(primary_key="id")

    def test_correct_primary_key_succeeds(self):
        """primary_key 'version_id' must succeed."""
        table = GoslingVersionTableYDB(primary_key="version_id")
        assert table.primary_key == "version_id"


# ---------------------------------------------------------------------------
# GoslingVersionTableYDB - column name validation
# ---------------------------------------------------------------------------


class TestGoslingVersionTableYDBColumns:
    """Tests for column name validation."""

    def test_wrong_column_0_raises(self):
        """Wrong first column name must raise ValueError."""
        cols = (
            "wrong_id",
            "version",
            "source",
            "downloaded_at",
            "sha256_hash",
            "active",
        )
        with pytest.raises(ValueError, match="version_id"):
            GoslingVersionTableYDB(columns=cols)

    def test_wrong_column_1_raises(self):
        """Wrong second column name must raise ValueError."""
        cols = (
            "version_id",
            "wrong",
            "source",
            "downloaded_at",
            "sha256_hash",
            "active",
        )
        with pytest.raises(ValueError, match="version"):
            GoslingVersionTableYDB(columns=cols)

    def test_wrong_column_2_raises(self):
        """Wrong third column name must raise ValueError."""
        cols = (
            "version_id",
            "version",
            "wrong",
            "downloaded_at",
            "sha256_hash",
            "active",
        )
        with pytest.raises(ValueError, match="source"):
            GoslingVersionTableYDB(columns=cols)

    def test_wrong_column_3_raises(self):
        """Wrong fourth column name must raise ValueError."""
        cols = ("version_id", "version", "source", "wrong", "sha256_hash", "active")
        with pytest.raises(ValueError, match="downloaded_at"):
            GoslingVersionTableYDB(columns=cols)

    def test_wrong_column_4_raises(self):
        """Wrong fifth column name must raise ValueError."""
        cols = ("version_id", "version", "source", "downloaded_at", "wrong", "active")
        with pytest.raises(ValueError, match="sha256_hash"):
            GoslingVersionTableYDB(columns=cols)

    def test_wrong_column_5_raises(self):
        """Wrong sixth column name must raise ValueError."""
        cols = (
            "version_id",
            "version",
            "source",
            "downloaded_at",
            "sha256_hash",
            "wrong",
        )
        with pytest.raises(ValueError, match="active"):
            GoslingVersionTableYDB(columns=cols)


# ---------------------------------------------------------------------------
# GoslingVersionTableYDB - r_type validation
# ---------------------------------------------------------------------------


class TestGoslingVersionTableYDBRType:
    """Tests for r_type validation."""

    def test_non_utf8_for_first_column_raises(self):
        """Non-Utf8 r_type for first column must raise ValueError."""
        r = (
            make_ydb_type("Bool"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Bool"),
        )
        with pytest.raises(ValueError):
            GoslingVersionTableYDB(r_type=r)

    def test_non_bool_for_active_column_raises(self):
        """Non-Bool r_type for 'active' column must raise ValueError."""
        r = (
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
        )
        with pytest.raises(ValueError, match="Bool"):
            GoslingVersionTableYDB(r_type=r)

    def test_mismatched_columns_r_type_count_raises(self):
        """Mismatched columns and r_type count must raise ValueError."""
        cols = (
            "version_id",
            "version",
            "source",
            "downloaded_at",
            "sha256_hash",
            "active",
        )
        r = (
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Bool"),
        )
        with pytest.raises(ValueError, match="columns must match"):
            GoslingVersionTableYDB(columns=cols, r_type=r)


# ---------------------------------------------------------------------------
# GoslingModelYDB - valid instantiation
# ---------------------------------------------------------------------------


class TestGoslingModelYDBValid:
    """Tests for valid GoslingModelYDB instantiation."""

    def test_instantiation_with_table_succeeds(self):
        """GoslingModelYDB with a valid table list must succeed."""
        model = GoslingModelYDB(tables=[GoslingVersionTableYDB()])
        assert model.model_name == "GoslingModel"
        assert model.version == "1.0.0"
        assert len(model.tables) == 1

    def test_instantiation_with_empty_tables_succeeds(self):
        """GoslingModelYDB with empty tables list must succeed (mirrors OpenTofuModelYDB)."""
        model = GoslingModelYDB(tables=[])
        assert model.tables == []

    def test_custom_model_name(self):
        """Custom model_name must be accepted."""
        model = GoslingModelYDB(
            tables=[GoslingVersionTableYDB()], model_name="MyGosling"
        )
        assert model.model_name == "MyGosling"

    def test_custom_version(self):
        """Custom valid semver version must be accepted."""
        model = GoslingModelYDB(tables=[], version="2.3.1")
        assert model.version == "2.3.1"


# ---------------------------------------------------------------------------
# GoslingModelYDB - semver validation
# ---------------------------------------------------------------------------


class TestGoslingModelYDBSemver:
    """Tests for semver validation in GoslingModelYDB."""

    def test_invalid_semver_raises_validation_error(self):
        """Invalid semver string must raise ValidationError."""
        with pytest.raises(ValidationError):
            GoslingModelYDB(tables=[], version="not-semver")

    def test_two_part_version_raises_validation_error(self):
        """Two-part version string must raise ValidationError."""
        with pytest.raises(ValidationError):
            GoslingModelYDB(tables=[], version="1.0")

    def test_empty_version_raises_validation_error(self):
        """Empty version string must raise ValidationError."""
        with pytest.raises(ValidationError):
            GoslingModelYDB(tables=[], version="")

    def test_version_with_letters_raises_validation_error(self):
        """Version with non-digit parts must raise ValidationError."""
        with pytest.raises(ValidationError):
            GoslingModelYDB(tables=[], version="1.0.a")
