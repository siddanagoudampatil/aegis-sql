"""Pydantic v2 schemas defining the declarative semantic catalog.

Governs enterprise metric calculations, dimensional slices, table metadata,
and synonym lookup tables.
"""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.exceptions import CatalogValidationError


class TableMetadata(BaseModel):
    """Metadata describing a relational warehouse table."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    description: str = ""
    primary_key: str = "id"
    columns: dict[str, str] = Field(default_factory=dict)


class MetricDefinition(BaseModel):
    """Represents a standardized, governed business metric formula.

    Centralizing formulas here prevents LLM hallucinations of business logic
    (e.g., ensuring churn rate accurately divides canceled subscriptions by total contracts).
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    display_name: str
    description: str = ""
    formula: str
    target_table: str
    filter: str | None = None
    aggregation_type: str = "sum"
    aliases: list[str] = Field(default_factory=list)
    join_hints: list[str] = Field(default_factory=list)

    @field_validator("name", "target_table")
    @classmethod
    def normalize_identifiers(cls, v: str) -> str:
        return v.strip().lower()


class DimensionDefinition(BaseModel):
    """Represents an analytical slicing dimension tied to a table column."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    display_name: str
    description: str = ""
    table: str
    column: str
    aliases: list[str] = Field(default_factory=list)

    @field_validator("name", "table", "column")
    @classmethod
    def normalize_identifiers(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def qualified_column(self) -> str:
        """Returns the fully qualified SQL identifier e.g. customers.tier."""
        return f"{self.table}.{self.column}"


class SemanticCatalog(BaseModel):
    """Root model for the enterprise semantic catalog.

    Provides indexed O(1) lookups for metrics and dimensions, resolving both
    canonical names and natural-language synonyms.
    """
    model_config = ConfigDict(extra="ignore")

    version: str
    catalog_name: str
    description: str = ""
    tables: dict[str, TableMetadata] = Field(default_factory=dict)
    metrics: dict[str, MetricDefinition] = Field(default_factory=dict)
    dimensions: dict[str, DimensionDefinition] = Field(default_factory=dict)

    # In-memory alias inverted index (lowercased alias -> canonical key)
    _metric_alias_index: dict[str, str] = {}
    _dimension_alias_index: dict[str, str] = {}

    def model_post_init(self, __context: Any) -> None:
        """Builds lookup indices and validates referential integrity."""
        # Index metrics and their synonyms
        metric_index: dict[str, str] = {}
        for key, metric in self.metrics.items():
            k_lower = key.lower()
            metric_index[k_lower] = key
            metric_index[metric.name.lower()] = key
            for alias in metric.aliases:
                metric_index[alias.lower().strip()] = key
        object.__setattr__(self, "_metric_alias_index", metric_index)

        # Index dimensions and their synonyms
        dimension_index: dict[str, str] = {}
        for key, dim in self.dimensions.items():
            k_lower = key.lower()
            dimension_index[k_lower] = key
            dimension_index[dim.name.lower()] = key
            for alias in dim.aliases:
                dimension_index[alias.lower().strip()] = key
        object.__setattr__(self, "_dimension_alias_index", dimension_index)

        self.validate_referential_integrity()

    def validate_referential_integrity(self) -> None:
        """Verifies that all metrics and dimensions reference valid tables and columns.

        Raises CatalogValidationError if any metric or dimension references a table
        not declared in `tables`.
        """
        table_keys = {t.lower() for t in self.tables.keys()}

        for m_name, metric in self.metrics.items():
            if metric.target_table.lower() not in table_keys:
                raise CatalogValidationError(
                    f"Metric '{m_name}' references unknown table '{metric.target_table}'. "
                    f"Available tables: {sorted(list(table_keys))}"
                )

        for d_name, dim in self.dimensions.items():
            if dim.table.lower() not in table_keys:
                raise CatalogValidationError(
                    f"Dimension '{d_name}' references unknown table '{dim.table}'. "
                    f"Available tables: {sorted(list(table_keys))}"
                )
            tbl_meta = self.tables.get(dim.table.lower())
            if tbl_meta and tbl_meta.columns and dim.column.lower() not in [c.lower() for c in tbl_meta.columns]:
                raise CatalogValidationError(
                    f"Dimension '{d_name}' references unknown column '{dim.column}' on table '{dim.table}'."
                )

    def get_metric(self, name_or_alias: str) -> MetricDefinition | None:
        """Finds a metric by canonical name or alias (case-insensitive)."""
        key = self._metric_alias_index.get(name_or_alias.strip().lower())
        if key:
            return self.metrics.get(key)
        return None

    def get_dimension(self, name_or_alias: str) -> DimensionDefinition | None:
        """Finds a dimension by canonical name or alias (case-insensitive)."""
        key = self._dimension_alias_index.get(name_or_alias.strip().lower())
        if key:
            return self.dimensions.get(key)
        return None
