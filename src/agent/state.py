"""LangGraph pipeline state definition for Aegis-SQL.

Defines the centralized state dict flowing through semantic resolution,
graph pathfinding, SQL compilation, AST verification, and sandbox execution.
"""

from typing import Any, Optional, TypedDict
from src.graph.schema_graph import JoinPlan
from src.semantic.models import DimensionDefinition, MetricDefinition


class AegisState(TypedDict, total=False):
    """Execution state schema passed across LangGraph nodes."""

    # Initial user natural language request
    user_query: str

    # Semantic layer extraction outputs
    resolved_metrics: list[MetricDefinition]
    resolved_dimensions: list[DimensionDefinition]
    required_tables: list[str]
    filter_predicates: list[str]

    # Relational graph resolution outputs
    join_plan: Optional[JoinPlan]

    # SQL generation outputs
    generated_sql: Optional[str]

    # AST validation outputs
    ast_validated: bool
    formatted_sql: Optional[str]
    validation_error: Optional[str]

    # Database sandbox execution outputs
    query_results: Optional[list[dict[str, Any]]]
    result_columns: Optional[list[str]]
    execution_error: Optional[str]

    # Audit log trail
    logs: list[str]
