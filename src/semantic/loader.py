"""YAML loader for the declarative semantic catalog.

Parses catalog configuration files and instantiates validated SemanticCatalog
Pydantic models with integrity checks.
"""

import logging
from pathlib import Path
import yaml
from src.exceptions import CatalogValidationError
from src.semantic.models import SemanticCatalog

logger = logging.getLogger("aegis_sql.semantic.loader")


def load_semantic_catalog(yaml_path: str | Path) -> SemanticCatalog:
    """Loads and validates a semantic catalog from a YAML specification file.

    Args:
        yaml_path: Filesystem path to the YAML catalog file.

    Returns:
        Validated SemanticCatalog instance with pre-computed lookup indices.

    Raises:
        CatalogValidationError: If YAML is malformed, file is missing, or schema checks fail.
    """
    path = Path(yaml_path)
    if not path.is_file():
        logger.error("Catalog configuration file not found at %s", path)
        raise CatalogValidationError(f"Semantic catalog file not found: {path}")

    logger.debug("Loading semantic catalog from %s", path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        logger.error("YAML syntax error parsing %s: %s", path, exc)
        raise CatalogValidationError(f"Invalid YAML syntax in {path}: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise CatalogValidationError(f"Expected YAML root mapping in {path}, got {type(raw_data).__name__}")

    try:
        catalog = SemanticCatalog.model_validate(raw_data)
        logger.info(
            "Successfully loaded catalog '%s' (v%s) with %d tables, %d metrics, %d dimensions.",
            catalog.catalog_name,
            catalog.version,
            len(catalog.tables),
            len(catalog.metrics),
            len(catalog.dimensions),
        )
        return catalog
    except Exception as exc:
        if isinstance(exc, CatalogValidationError):
            raise
        logger.error("Semantic catalog validation failed: %s", exc)
        raise CatalogValidationError(f"Semantic catalog validation failed: {exc}") from exc
