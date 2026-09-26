"""Unit tests for the declarative semantic catalog loader and Pydantic v2 schemas."""

from pathlib import Path
import pytest
import yaml

from src.exceptions import CatalogValidationError
from src.semantic.loader import load_semantic_catalog
from src.semantic.models import DimensionDefinition, MetricDefinition, SemanticCatalog, TableMetadata

FIXTURE_CATALOG_PATH = Path("configs/semantic_catalog.yaml")


def test_load_valid_catalog() -> None:
    """Verifies that the canonical warehouse catalog loads without validation errors."""
    catalog = load_semantic_catalog(FIXTURE_CATALOG_PATH)

    assert catalog.version == "1.0.0"
    assert catalog.catalog_name == "aegis_saas_warehouse"
    assert len(catalog.tables) >= 5
    assert "customers" in catalog.tables
    assert "subscriptions" in catalog.tables
    assert "plans" in catalog.tables
    assert "invoices" in catalog.tables
    assert "usage_events" in catalog.tables


def test_metric_retrieval_and_case_insensitivity() -> None:
    """Verifies case-insensitive metric lookups by canonical name and alias."""
    catalog = load_semantic_catalog(FIXTURE_CATALOG_PATH)

    # Canonical lookup
    metric = catalog.get_metric("active_mrr")
    assert metric is not None
    assert metric.name == "active_mrr"
    assert metric.target_table == "subscriptions"

    # Uppercase
    metric_upper = catalog.get_metric("ACTIVE_MRR")
    assert metric_upper is not None
    assert metric_upper.name == "active_mrr"

    # Alias / Synonym
    mrr_alias = catalog.get_metric("mrr")
    assert mrr_alias is not None
    assert mrr_alias.name == "active_mrr"

    churn_alias = catalog.get_metric("churn rate")
    assert churn_alias is not None
    assert churn_alias.name == "churn_rate"


def test_dimension_retrieval_and_aliases() -> None:
    """Verifies dimension indexing and qualified column generation."""
    catalog = load_semantic_catalog(FIXTURE_CATALOG_PATH)

    dim = catalog.get_dimension("customer_tier")
    assert dim is not None
    assert dim.table == "customers"
    assert dim.column == "tier"
    assert dim.qualified_column == "customers.tier"

    # Alias lookup
    tier_alias = catalog.get_dimension("tier")
    assert tier_alias is not None
    assert tier_alias.name == "customer_tier"


def test_referential_integrity_unknown_table(tmp_path: Path) -> None:
    """Ensures CatalogValidationError is raised if a metric references an undeclared table."""
    bad_data = {
        "version": "1.0.0",
        "catalog_name": "broken_catalog",
        "tables": {
            "customers": {
                "description": "Customer table",
                "primary_key": "id",
                "columns": {"id": "VARCHAR"},
            }
        },
        "metrics": {
            "invalid_metric": {
                "name": "invalid_metric",
                "display_name": "Invalid",
                "formula": "SUM(non_existent_table.amount)",
                "target_table": "non_existent_table",
            }
        },
        "dimensions": {},
    }
    cat_file = tmp_path / "broken_catalog.yaml"
    with open(cat_file, "w", encoding="utf-8") as f:
        yaml.dump(bad_data, f)

    with pytest.raises(CatalogValidationError, match="references unknown table 'non_existent_table'"):
        load_semantic_catalog(cat_file)


def test_referential_integrity_unknown_dimension_column(tmp_path: Path) -> None:
    """Ensures CatalogValidationError is raised if a dimension references an undeclared column."""
    bad_data = {
        "version": "1.0.0",
        "catalog_name": "broken_dim_catalog",
        "tables": {
            "customers": {
                "description": "Customer table",
                "primary_key": "id",
                "columns": {"id": "VARCHAR", "name": "VARCHAR"},
            }
        },
        "metrics": {},
        "dimensions": {
            "ghost_dim": {
                "name": "ghost_dim",
                "display_name": "Ghost Dimension",
                "table": "customers",
                "column": "non_existent_col",
            }
        },
    }
    cat_file = tmp_path / "broken_dim.yaml"
    with open(cat_file, "w", encoding="utf-8") as f:
        yaml.dump(bad_data, f)

    with pytest.raises(CatalogValidationError, match="references unknown column 'non_existent_col'"):
        load_semantic_catalog(cat_file)


def test_missing_file_handling() -> None:
    """Verifies clear error on missing YAML file path."""
    with pytest.raises(CatalogValidationError, match="not found"):
        load_semantic_catalog("non_existent_path_to_catalog.yaml")


def test_malformed_yaml(tmp_path: Path) -> None:
    """Verifies syntax errors in YAML are captured cleanly as CatalogValidationError."""
    corrupted_file = tmp_path / "corrupt.yaml"
    corrupted_file.write_text("version: [unclosed list", encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="Invalid YAML syntax"):
        load_semantic_catalog(corrupted_file)
