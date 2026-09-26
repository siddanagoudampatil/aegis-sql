"""SQLGlot AST validation, dialect transpilation, and security sandbox checks.

Enforces read-only safety, table access whitelisting, prohibited function screening,
and DuckDB dialect formatting prior to database execution.
"""

import logging
from typing import Set
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from src.exceptions import ASTValidationError

logger = logging.getLogger("aegis_sql.utils.ast_parser")

# Default permitted tables in the warehouse
DEFAULT_ALLOWED_TABLES: Set[str] = {
    "customers",
    "plans",
    "subscriptions",
    "invoices",
    "usage_events",
}

# Functions that could trigger arbitrary local filesystem access or network calls
FORBIDDEN_FUNCTIONS: Set[str] = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "httpfs",
    "copy",
    "system",
    "shell",
    "load",
    "install",
    "duckdb_secrets",
}

FORBIDDEN_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Set,
)


class SQLGlotValidator:
    """Validates and formats SQL statements using SQLGlot AST inspection."""

    def __init__(self, allowed_tables: Set[str] | None = None) -> None:
        self.allowed_tables = {t.lower() for t in (allowed_tables or DEFAULT_ALLOWED_TABLES)}

    def validate(self, sql: str) -> exp.Expression:
        """Parses and runs security, grammar, and table whitelisting checks.

        Args:
            sql: Raw SQL query string.

        Returns:
            Validated sqlglot AST expression.

        Raises:
            ASTValidationError: If the query fails syntax, read-only policy, or table whitelist.
        """
        if not sql or not sql.strip():
            raise ASTValidationError("Empty SQL query provided.")

        # 1. Parse AST with DuckDB dialect
        try:
            # parse_one parses exactly one complete SQL statement
            ast = sqlglot.parse_one(sql, read="duckdb")
        except ParseError as exc:
            logger.error("SQL syntax parse failure: %s", exc)
            raise ASTValidationError(f"SQL syntax parse error: {exc}") from exc

        # 2. Enforce strictly read-only query semantics (Must be Select or Union)
        if isinstance(ast, FORBIDDEN_EXPRESSION_TYPES) or not isinstance(ast, (exp.Select, exp.Union)):
            logger.warning("Rejected non-SELECT statement type: %s", type(ast).__name__)
            raise ASTValidationError(
                f"Security violation: only SELECT queries are permitted (got {type(ast).__name__})."
            )

        # Check for nested mutation expressions
        for forbidden_cls in FORBIDDEN_EXPRESSION_TYPES:
            if list(ast.find_all(forbidden_cls)):
                raise ASTValidationError(
                    f"Security violation: nested {forbidden_cls.__name__} expression detected in query."
                )

        # 3. Enforce Table Whitelist
        referenced_tables = {tbl.name.lower() for tbl in ast.find_all(exp.Table) if tbl.name}
        unauthorized = referenced_tables - self.allowed_tables
        if unauthorized:
            logger.warning("Query referenced unauthorized tables: %s", unauthorized)
            raise ASTValidationError(
                f"Unauthorized table access: tables {sorted(list(unauthorized))} "
                f"are not in the permitted whitelist ({sorted(list(self.allowed_tables))})."
            )

        # 4. Enforce Forbidden Function Screening
        forbidden_keys = {f.replace("_", "") for f in FORBIDDEN_FUNCTIONS}
        for func in ast.find_all((exp.Anonymous, exp.Func)):
            sql_name = func.sql_name().lower() if hasattr(func, "sql_name") else ""
            func_name = getattr(func, "name", "").lower() if isinstance(getattr(func, "name", None), str) else ""
            key = getattr(func, "key", "").lower()
            if (sql_name in FORBIDDEN_FUNCTIONS) or (func_name in FORBIDDEN_FUNCTIONS) or (key in forbidden_keys):
                blocked_id = sql_name or func_name or key
                raise ASTValidationError(
                    f"Security violation: execution of forbidden function '{blocked_id}' is blocked."
                )

        logger.debug("AST validation passed for query referencing tables: %s", referenced_tables)
        return ast

    def format(self, sql: str) -> str:
        """Validates and transpiles the SQL into pretty-printed DuckDB dialect."""
        ast = self.validate(sql)
        # Note: sqlglot's pretty=True produces clean, indented, multi-line SQL
        return ast.sql(dialect="duckdb", pretty=True)


def validate_and_format_sql(sql: str, allowed_tables: Set[str] | None = None) -> str:
    """Convenience function to validate and return formatted DuckDB SQL."""
    validator = SQLGlotValidator(allowed_tables=allowed_tables)
    return validator.format(sql)
