"""LangGraph execution nodes for Aegis-SQL.

Encapsulates individual stages of query processing:
1. SemanticResolverNode: Extracts governed metrics and dimensions from natural language.
2. GraphPathNode: Resolves minimal relational join paths via SchemaGraph.
3. SQLGeneratorNode: Compiles AST-compliant SQL from semantic formulas and join plans.
4. ASTValidatorNode: Validates syntax, read-only guarantees, and table whitelisting.
5. SandboxExecutorNode: Safely executes queries on the DuckDB warehouse instance.
"""

import logging
import re
from typing import Any
import duckdb
from src.agent.state import AegisState
from src.exceptions import (
    ASTValidationError,
    DimensionNotFoundError,
    MetricNotFoundError,
    QueryExecutionError,
    SchemaLinkError,
)
from src.graph.schema_graph import JoinPlan, SchemaGraph
from src.semantic.models import DimensionDefinition, MetricDefinition, SemanticCatalog
from src.utils.ast_parser import SQLGlotValidator

logger = logging.getLogger("aegis_sql.agent.nodes")


class SemanticResolverNode:
    """Resolves natural language queries into governed catalog metrics and dimensions."""

    def __init__(self, catalog: SemanticCatalog) -> None:
        self.catalog = catalog

    def __call__(self, state: AegisState) -> dict[str, Any]:
        query = state.get("user_query", "").strip()
        logger.info("Resolving semantics for query: '%s'", query)
        logs = list(state.get("logs", []))
        logs.append(f"[SemanticResolver] Ingested user query: '{query}'")

        query_normalized = query.lower()
        # Remove non-alphanumeric characters for clean token matching
        clean_text = re.sub(r"[^\w\s]", " ", query_normalized)
        tokens = set(clean_text.split())

        resolved_metrics: list[MetricDefinition] = []
        resolved_dimensions: list[DimensionDefinition] = []
        filter_predicates: list[str] = []

        # Match Metrics and Dimensions by prioritizing longest multi-word phrase matches
        candidates = []
        for m in self.catalog.metrics.values():
            terms = [m.name, m.display_name.lower()] + [a.lower() for a in m.aliases]
            for term in set(terms):
                candidates.append((len(term), term, "metric", m))

        for d in self.catalog.dimensions.values():
            terms = [d.name, d.display_name.lower(), d.column] + [a.lower() for a in d.aliases]
            for term in set(terms):
                candidates.append((len(term), term, "dimension", d))

        candidates.sort(key=lambda x: x[0], reverse=True)

        claimed_spans: list[tuple[int, int]] = []
        matched_metric_names: set[str] = set()
        matched_dim_names: set[str] = set()

        for _, term, kind, entity in candidates:
            if kind == "metric" and entity.name in matched_metric_names:
                continue
            if kind == "dimension" and entity.name in matched_dim_names:
                continue

            for match in re.finditer(rf"\b{re.escape(term)}\b", clean_text):
                start, end = match.span()
                # Suppress sub-tokens that overlap with already matched longer phrases
                if any(not (end <= cs or start >= ce) for cs, ce in claimed_spans):
                    continue

                claimed_spans.append((start, end))
                if kind == "metric":
                    matched_metric_names.add(entity.name)
                    resolved_metrics.append(entity)
                    logs.append(f"[SemanticResolver] Identified metric: {entity.name} ('{term}')")
                else:
                    matched_dim_names.add(entity.name)
                    resolved_dimensions.append(entity)
                    logs.append(f"[SemanticResolver] Identified dimension: {entity.name} ('{term}')")
                break

        # Fallback heuristic: If no metrics matched, default to active_mrr if 'mrr' appears anywhere
        if not resolved_metrics and "mrr" in tokens:
            mrr_metric = self.catalog.get_metric("active_mrr")
            if mrr_metric:
                resolved_metrics.append(mrr_metric)
                logs.append("[SemanticResolver] Fallback matched 'mrr' -> active_mrr")

        # Extract explicit tier filters if present in query text
        for tier in ["starter", "growth", "enterprise"]:
            if tier in tokens:
                filter_predicates.append(f"customers.tier = '{tier}'")
                logs.append(f"[SemanticResolver] Extracted filter: customers.tier = '{tier}'")

        if not resolved_metrics:
            logger.warning("No metrics resolved from query: '%s'", query)
            logs.append("[SemanticResolver] Warning: No metric identified in user query.")

        # 3. Determine all required tables across metrics, dimensions, and join hints
        required_tables: list[str] = []
        for m in resolved_metrics:
            tbl = m.target_table.lower()
            if tbl not in required_tables:
                required_tables.append(tbl)
            for hint in m.join_hints:
                h_tbl = hint.lower()
                if h_tbl not in required_tables:
                    required_tables.append(h_tbl)

        for d in resolved_dimensions:
            tbl = d.table.lower()
            if tbl not in required_tables:
                required_tables.append(tbl)

        for f in filter_predicates:
            tbl = f.split(".")[0].lower()
            if tbl not in required_tables:
                required_tables.append(tbl)

        logs.append(f"[SemanticResolver] Required warehouse tables: {required_tables}")

        return {
            "resolved_metrics": resolved_metrics,
            "resolved_dimensions": resolved_dimensions,
            "required_tables": required_tables,
            "filter_predicates": filter_predicates,
            "logs": logs,
        }


class GraphPathNode:
    """Computes the minimal join tree across all required warehouse tables."""

    def __init__(self, schema_graph: SchemaGraph) -> None:
        self.schema_graph = schema_graph

    def __call__(self, state: AegisState) -> dict[str, Any]:
        required_tables = state.get("required_tables", [])
        logs = list(state.get("logs", []))

        if not required_tables:
            logger.error("GraphPathNode received empty table requirements.")
            logs.append("[GraphPathNode] Error: No tables requested for join resolution.")
            return {"join_plan": None, "logs": logs}

        try:
            # We prefer anchoring on customers table if present, else first required table
            preferred_root = "customers" if "customers" in required_tables else required_tables[0]
            join_plan = self.schema_graph.resolve_join_path(required_tables, root_table=preferred_root)
            logs.append(f"[GraphPathNode] Discovered minimal join tree: {join_plan.path_description}")
            logger.info("Join path successfully resolved: %s", join_plan.path_description)
            return {"join_plan": join_plan, "logs": logs}
        except (SchemaLinkError, Exception) as exc:
            logger.error("Relational join path resolution failed: %s", exc)
            logs.append(f"[GraphPathNode] Resolution error: {exc}")
            raise


class SQLGeneratorNode:
    """Assembles governed DuckDB SQL from semantic definitions and the join tree."""

    def __call__(self, state: AegisState) -> dict[str, Any]:
        metrics = state.get("resolved_metrics", [])
        dimensions = state.get("resolved_dimensions", [])
        join_plan: JoinPlan | None = state.get("join_plan")
        filter_predicates = state.get("filter_predicates", [])
        logs = list(state.get("logs", []))

        if not join_plan:
            logs.append("[SQLGenerator] Cannot generate SQL: missing join plan.")
            return {"generated_sql": None, "logs": logs}

        select_clauses: list[str] = []
        group_by_clauses: list[str] = []

        # 1. SELECT dimensions (fully qualified)
        for dim in dimensions:
            # Note: Explicit qualification prevents column ambiguity in DuckDB multi-joins
            select_clauses.append(f"{dim.table}.{dim.column} AS {dim.name}")
            group_by_clauses.append(f"{dim.table}.{dim.column}")

        # 2. SELECT metrics formulas
        for metric in metrics:
            select_clauses.append(f"{metric.formula} AS {metric.name}")

        if not select_clauses:
            # Fallback to count if neither dimension nor metric was specified
            select_clauses.append("COUNT(*) AS total_count")

        # 3. FROM and JOIN clauses
        from_joins_sql = join_plan.render_from_clause()

        # 4. WHERE filters (combining metric constraints and dimensional filters)
        where_conditions: list[str] = []
        for metric in metrics:
            if metric.filter and metric.filter not in where_conditions:
                where_conditions.append(metric.filter)

        for pred in filter_predicates:
            if pred not in where_conditions:
                where_conditions.append(pred)

        where_sql = f"\nWHERE {' AND '.join(where_conditions)}" if where_conditions else ""

        # 5. GROUP BY
        group_sql = f"\nGROUP BY {', '.join(group_by_clauses)}" if group_by_clauses and metrics else ""

        # 6. ORDER BY (metric descending by default for ranking)
        order_sql = ""
        if metrics:
            order_sql = f"\nORDER BY {metrics[0].name} DESC"
        elif group_by_clauses:
            order_sql = f"\nORDER BY {group_by_clauses[0]} ASC"

        select_body = ",\n  ".join(select_clauses)
        generated_sql = f"SELECT\n  {select_body}\n{from_joins_sql}{where_sql}{group_sql}{order_sql};"
        logs.append("[SQLGenerator] Generated raw relational SQL query.")
        logger.debug("Generated SQL:\n%s", generated_sql)

        return {"generated_sql": generated_sql, "logs": logs}


class ASTValidatorNode:
    """Validates the generated SQL statement using SQLGlot AST inspection."""

    def __init__(self, validator: SQLGlotValidator | None = None) -> None:
        self.validator = validator or SQLGlotValidator()

    def __call__(self, state: AegisState) -> dict[str, Any]:
        sql = state.get("generated_sql")
        logs = list(state.get("logs", []))

        if not sql:
            logs.append("[ASTValidator] No SQL found in state to validate.")
            return {"ast_validated": False, "validation_error": "No SQL generated", "logs": logs}

        try:
            formatted_sql = self.validator.format(sql)
            logs.append("[ASTValidator] AST verification succeeded; query passed security sandbox.")
            logger.info("SQL statement validated and formatted successfully.")
            return {
                "ast_validated": True,
                "formatted_sql": formatted_sql,
                "validation_error": None,
                "logs": logs,
            }
        except ASTValidationError as exc:
            logger.error("AST validation rejected query: %s", exc)
            logs.append(f"[ASTValidator] Rejection: {exc}")
            return {
                "ast_validated": False,
                "formatted_sql": None,
                "validation_error": str(exc),
                "logs": logs,
            }


class SandboxExecutorNode:
    """Executes the validated SQL against DuckDB and captures structured tabular results."""

    def __init__(self, db_path_or_conn: str | duckdb.DuckDBPyConnection) -> None:
        self.db_target = db_path_or_conn

    def __call__(self, state: AegisState) -> dict[str, Any]:
        logs = list(state.get("logs", []))
        if not state.get("ast_validated", False):
            logs.append("[SandboxExecutor] Execution skipped: AST validation was not green.")
            return {"execution_error": "Skipped due to AST validation failure", "logs": logs}

        sql = state.get("formatted_sql")
        if not sql:
            return {"execution_error": "Missing formatted SQL", "logs": logs}

        should_close = False
        if isinstance(self.db_target, str):
            conn = duckdb.connect(self.db_target, read_only=True)
            should_close = True
        else:
            conn = self.db_target

        try:
            logger.info("Executing query on DuckDB sandbox...")
            cursor = conn.execute(sql)
            col_names = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            # Convert row tuples to list of dictionaries
            results = [dict(zip(col_names, row)) for row in rows]
            logs.append(f"[SandboxExecutor] Execution succeeded: retrieved {len(results)} rows.")
            logger.info("Query returned %d rows.", len(results))

            return {
                "query_results": results,
                "result_columns": col_names,
                "execution_error": None,
                "logs": logs,
            }
        except Exception as exc:
            logger.error("Query execution error: %s", exc)
            logs.append(f"[SandboxExecutor] Execution failed: {exc}")
            return {
                "query_results": None,
                "result_columns": None,
                "execution_error": str(exc),
                "logs": logs,
            }
        finally:
            if should_close:
                conn.close()
