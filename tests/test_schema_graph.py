"""Unit tests for the relational SchemaGraph and topological join resolution."""

from pathlib import Path
import pytest
from src.exceptions import SchemaLinkError, UnreachableTableError
from src.graph.inspector import inspect_sql_ddl
from src.graph.schema_graph import SchemaGraph

SCHEMA_SQL_PATH = Path("data/schema.sql")


@pytest.fixture
def warehouse_graph() -> SchemaGraph:
    """Fixture providing a SchemaGraph loaded from data/schema.sql."""
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        ddl = f.read()
    schema = inspect_sql_ddl(ddl)
    return SchemaGraph(schema)


def test_schema_ddl_inspection() -> None:
    """Verifies that all 5 warehouse tables and foreign keys are parsed accurately from DDL."""
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        ddl = f.read()
    schema = inspect_sql_ddl(ddl)

    assert len(schema.tables) == 5
    assert "customers" in schema.tables
    assert "plans" in schema.tables
    assert "subscriptions" in schema.tables
    assert "invoices" in schema.tables
    assert "usage_events" in schema.tables

    # Check key foreign key extractions
    fk_pairs = [(fk.from_table, fk.to_table) for fk in schema.foreign_keys]
    assert ("subscriptions", "customers") in fk_pairs
    assert ("subscriptions", "plans") in fk_pairs
    assert ("invoices", "subscriptions") in fk_pairs
    assert ("invoices", "customers") in fk_pairs


def test_single_table_join_plan(warehouse_graph: SchemaGraph) -> None:
    """Verifies single-table requests produce a 0-join plan with self as base table."""
    plan = warehouse_graph.resolve_join_path(["subscriptions"])
    assert plan.base_table == "subscriptions"
    assert len(plan.join_clauses) == 0
    assert plan.tables_in_plan == ["subscriptions"]
    assert plan.path_description == "subscriptions"


def test_direct_two_table_join_plan(warehouse_graph: SchemaGraph) -> None:
    """Verifies direct neighbor tables resolve with an immediate 1-hop join."""
    plan = warehouse_graph.resolve_join_path(["customers", "subscriptions"], root_table="customers")
    assert plan.base_table == "customers"
    assert len(plan.join_clauses) == 1
    assert plan.join_clauses[0].table == "subscriptions"
    assert "customers.id = subscriptions.customer_id" in plan.join_clauses[0].on_condition
    assert plan.path_description == "customers -> subscriptions"


def test_bridge_table_resolution_customers_to_plans(warehouse_graph: SchemaGraph) -> None:
    """CRITICAL ACCEPTANCE TEST: Resolves customers to plans through subscriptions bridge table.

    customers has no direct foreign key to plans; the graph pathfinder must discover
    that 'subscriptions' is the minimal relational bridge:
    customers -> subscriptions -> plans.
    """
    plan = warehouse_graph.resolve_join_path(["customers", "plans"], root_table="customers")

    assert plan.base_table == "customers"
    assert plan.path_description == "customers -> subscriptions -> plans"
    assert plan.tables_in_plan == ["customers", "subscriptions", "plans"]
    assert len(plan.join_clauses) == 2

    # Step 1: customers to subscriptions
    assert plan.join_clauses[0].table == "subscriptions"
    assert "customers.id = subscriptions.customer_id" in plan.join_clauses[0].on_condition

    # Step 2: subscriptions to plans
    assert plan.join_clauses[1].table == "plans"
    assert "subscriptions.plan_id = plans.id" in plan.join_clauses[1].on_condition


def test_steiner_tree_multi_table_resolution(warehouse_graph: SchemaGraph) -> None:
    """Verifies Steiner-tree discovery across 3 disjoint tables: customers, plans, and invoices."""
    plan = warehouse_graph.resolve_join_path(
        ["customers", "plans", "invoices"],
        root_table="customers",
    )

    # Subscriptions must be included as the intermediate bridge node
    assert "subscriptions" in plan.tables_in_plan
    assert "customers" in plan.tables_in_plan
    assert "plans" in plan.tables_in_plan
    assert "invoices" in plan.tables_in_plan

    # Verify rendered SQL snippet
    from_sql = plan.render_from_clause()
    assert "FROM customers" in from_sql
    assert "JOIN subscriptions" in from_sql
    assert "JOIN plans" in from_sql
    assert "JOIN invoices" in from_sql


def test_unreachable_table_disconnected_component(warehouse_graph: SchemaGraph) -> None:
    """Verifies UnreachableTableError is raised when two tables have no path between them."""
    # Add an isolated table node with no foreign keys
    warehouse_graph.add_table("isolated_audit_log", primary_keys=["id"])

    with pytest.raises(UnreachableTableError, match="cannot be reached"):
        warehouse_graph.resolve_join_path(["customers", "isolated_audit_log"])


def test_case_insensitive_table_handling(warehouse_graph: SchemaGraph) -> None:
    """Verifies table lookup normalization handles mixed-case inputs seamlessly."""
    plan = warehouse_graph.resolve_join_path(["CuStOmErS", "pLaNs"], root_table="cUsToMeRs")
    assert plan.base_table == "customers"
    assert plan.tables_in_plan == ["customers", "subscriptions", "plans"]


def test_empty_or_unknown_tables(warehouse_graph: SchemaGraph) -> None:
    """Verifies validation errors on missing or empty input table lists."""
    with pytest.raises(SchemaLinkError, match="empty table list"):
        warehouse_graph.resolve_join_path([])

    with pytest.raises(SchemaLinkError, match="not found in schema graph"):
        warehouse_graph.resolve_join_path(["customers", "non_existent_table"])
