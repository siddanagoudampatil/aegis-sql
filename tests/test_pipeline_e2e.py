"""End-to-end integration tests for the Aegis-SQL LangGraph execution pipeline."""

from pathlib import Path
import duckdb
import pytest
from data.seed_data import DB_PATH, SCHEMA_PATH, init_schema, seed_database
from src.agent.pipeline import AegisPipeline, build_aegis_pipeline

CATALOG_PATH = Path("configs/semantic_catalog.yaml")


@pytest.fixture(scope="module")
def seeded_test_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Creates a temporary, populated DuckDB warehouse instance for integration testing."""
    test_db_dir = tmp_path_factory.mktemp("warehouse")
    db_file = test_db_dir / "test_warehouse.duckdb"

    conn = duckdb.connect(str(db_file))
    try:
        init_schema(conn, SCHEMA_PATH)
        seed_database(conn)
    finally:
        conn.close()

    return db_file


@pytest.fixture
def pipeline(seeded_test_db: Path) -> AegisPipeline:
    """Fixture providing an AegisPipeline wired to the temporary warehouse."""
    return build_aegis_pipeline(
        catalog_path=CATALOG_PATH,
        db_path=seeded_test_db,
        ddl_path=SCHEMA_PATH,
    )


def test_primary_mrr_by_tier_query(pipeline: AegisPipeline) -> None:
    """ACCEPTANCE TEST: Verifies complete execution of 'active MRR broken down by customer tier'.

    Checks:
    - Semantic resolution: 'active_mrr' metric and 'customer_tier' dimension.
    - Graph pathfinding: minimal join tree between customers and subscriptions.
    - AST validation: clean DuckDB SQL query.
    - Sandbox execution: non-empty results grouping active MRR by tier.
    """
    query = "What is our active MRR broken down by customer tier?"
    state = pipeline.run(query)

    # 1. Verify semantic resolution
    resolved_metrics = [m.name for m in state.get("resolved_metrics", [])]
    resolved_dims = [d.name for d in state.get("resolved_dimensions", [])]
    assert "active_mrr" in resolved_metrics
    assert "customer_tier" in resolved_dims

    # 2. Verify relational join plan
    join_plan = state.get("join_plan")
    assert join_plan is not None
    assert "customers" in join_plan.tables_in_plan
    assert "subscriptions" in join_plan.tables_in_plan

    # 3. Verify AST validation
    assert state.get("ast_validated") is True
    formatted_sql = state.get("formatted_sql")
    assert formatted_sql is not None
    assert "SELECT" in formatted_sql
    assert "FROM customers" in formatted_sql
    assert "GROUP BY" in formatted_sql
    assert "customers.tier" in formatted_sql

    # 4. Verify Database Execution Sandbox results
    results = state.get("query_results")
    assert results is not None
    assert len(results) > 0

    # Ensure tiers are present and have positive MRR numbers
    tiers_returned = {row["customer_tier"] for row in results}
    assert "enterprise" in tiers_returned
    assert "growth" in tiers_returned
    assert "starter" in tiers_returned

    for row in results:
        assert row["active_mrr"] > 0


def test_total_revenue_by_tier(pipeline: AegisPipeline) -> None:
    """Verifies multi-table join and aggregation for settled invoice revenue by customer tier."""
    query = "What is our total revenue broken down by customer tier?"
    state = pipeline.run(query)

    assert state.get("ast_validated") is True
    results = state.get("query_results")
    assert results is not None
    assert len(results) > 0

    # Every tier should have recorded settled revenue in our seed data
    tiers_returned = {row["customer_tier"] for row in results}
    assert "enterprise" in tiers_returned
    for row in results:
        assert row["total_revenue"] > 0


def test_churn_rate_aggregation(pipeline: AegisPipeline) -> None:
    """Verifies calculation of non-linear formula metrics (subscription churn rate)."""
    query = "What is our subscription churn rate?"
    state = pipeline.run(query)

    assert state.get("ast_validated") is True
    results = state.get("query_results")
    assert results is not None
    assert len(results) == 1
    churn = results[0]["churn_rate"]
    assert 0.0 < churn < 100.0


def test_usage_events_by_event_type(pipeline: AegisPipeline) -> None:
    """Verifies telemetry consumption slicing by event_type."""
    query = "What is the total usage broken down by event type?"
    state = pipeline.run(query)

    assert state.get("ast_validated") is True
    results = state.get("query_results")
    assert results is not None
    assert len(results) == 3  # api_call, compute_hour, export_gb
    event_types = {row["event_type"] for row in results}
    assert event_types == {"api_call", "compute_hour", "export_gb"}
