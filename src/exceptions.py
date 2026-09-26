"""Domain-specific exceptions for Aegis-SQL.

Hierarchical exception taxonomy structured to differentiate between semantic resolution,
relational schema linking, AST validation, and database execution failures.
"""


class AegisError(Exception):
    """Base exception for all domain errors raised by Aegis-SQL."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} (details: {self.details})"
        return self.message


class CatalogValidationError(AegisError):
    """Raised when semantic catalog specifications fail structural or reference integrity."""


class MetricNotFoundError(AegisError):
    """Raised when an extracted query metric does not exist in the semantic catalog."""


class DimensionNotFoundError(AegisError):
    """Raised when an extracted query dimension does not exist in the semantic catalog."""


class SchemaLinkError(AegisError):
    """Base exception for schema linking and relational graph resolution failures."""


class UnreachableTableError(SchemaLinkError):
    """Raised when no valid foreign-key path exists between required relational tables.

    Indicates disconnected components in the schema graph or an impossible join requirement.
    """


class ASTValidationError(AegisError):
    """Raised when an AST check fails security, syntax, or table whitelist validation."""


class QueryExecutionError(AegisError):
    """Raised when physical SQL execution fails within the database sandbox."""
