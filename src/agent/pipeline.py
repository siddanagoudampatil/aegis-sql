"""StateGraph workflow assembly and execution pipeline for Aegis-SQL.

Wires together the semantic resolver, relational graph pathfinder, SQL generator,
AST validator, and sandbox executor into a compiled LangGraph state machine.
"""

import logging
from pathlib import Path
from typing import Any
import duckdb
from langgraph.graph import END, StateGraph
from src.agent.nodes import (
    ASTValidatorNode,
    GraphPathNode,
    SandboxExecutorNode,
    SQLGeneratorNode,
    SemanticResolverNode,
)
from src.agent.state import AegisState
from src.graph.inspector import inspect_duckdb_database, inspect_sql_ddl
from src.graph.schema_graph import SchemaGraph
from src.semantic.loader import load_semantic_catalog
from src.semantic.models import SemanticCatalog
from src.utils.ast_parser import SQLGlotValidator

logger = logging.getLogger("aegis_sql.agent.pipeline")


class AegisPipeline:
    """Compiled LangGraph execution pipeline for Aegis-SQL."""

    def __init__(
        self,
        catalog: SemanticCatalog,
        schema_graph: SchemaGraph,
        db_path: str | Path,
    ) -> None:
        self.catalog = catalog
        self.schema_graph = schema_graph
        self.db_path = str(db_path)
        self.app = self._build_graph()

    def _build_graph(self) -> Any:
        """Constructs and compiles the LangGraph StateGraph."""
        workflow = StateGraph(AegisState)

        # 1. Register nodes
        workflow.add_node("semantic_resolver", SemanticResolverNode(self.catalog))
        workflow.add_node("graph_pathfinder", GraphPathNode(self.schema_graph))
        workflow.add_node("sql_generator", SQLGeneratorNode())
        workflow.add_node(
            "ast_validator",
            ASTValidatorNode(SQLGlotValidator(allowed_tables=set(self.catalog.tables.keys()))),
        )
        workflow.add_node("sandbox_executor", SandboxExecutorNode(self.db_path))

        # 2. Define standard linear transitions
        workflow.set_entry_point("semantic_resolver")
        workflow.add_edge("semantic_resolver", "graph_pathfinder")
        workflow.add_edge("graph_pathfinder", "sql_generator")
        workflow.add_edge("sql_generator", "ast_validator")

        # 3. Define conditional transition based on AST verification outcome
        def route_after_validation(state: AegisState) -> str:
            if state.get("ast_validated", False):
                return "sandbox_executor"
            logger.warning("Routing to END: AST validation failed with error: %s", state.get("validation_error"))
            return END

        workflow.add_conditional_edges(
            "ast_validator",
            route_after_validation,
            {
                "sandbox_executor": "sandbox_executor",
                END: END,
            },
        )
        workflow.add_edge("sandbox_executor", END)

        compiled = workflow.compile()
        logger.info("Compiled LangGraph AegisState workflow pipeline.")
        return compiled

    def run(self, user_query: str) -> AegisState:
        """Executes the pipeline for a natural language user query.

        Args:
            user_query: Plain English analytical question.

        Returns:
            Terminal AegisState containing resolved metrics, join path, SQL, and query results.
        """
        initial_state: AegisState = {
            "user_query": user_query,
            "resolved_metrics": [],
            "resolved_dimensions": [],
            "required_tables": [],
            "filter_predicates": [],
            "logs": [],
        }
        return self.app.invoke(initial_state)


def build_aegis_pipeline(
    catalog_path: str | Path,
    db_path: str | Path,
    ddl_path: str | Path | None = None,
) -> AegisPipeline:
    """Factory creating a fully wired AegisPipeline instance.

    Inspects relational schema from DuckDB database or fallback DDL,
    loads the semantic catalog, and constructs the schema graph.
    """
    cat_path = Path(catalog_path)
    database_path = Path(db_path)

    # 1. Ingest Semantic Catalog
    catalog = load_semantic_catalog(cat_path)

    # 2. Extract Relational Schema
    if database_path.is_file():
        logger.info("Inspecting schema from active DuckDB warehouse: %s", database_path)
        schema = inspect_duckdb_database(database_path)
    elif ddl_path and Path(ddl_path).is_file():
        logger.info("Inspecting schema from DDL file: %s", ddl_path)
        with open(ddl_path, "r", encoding="utf-8") as f:
            ddl_sql = f.read()
        schema = inspect_sql_ddl(ddl_sql)
    else:
        raise FileNotFoundError(
            f"Cannot build schema: database '{database_path}' does not exist and no valid DDL provided."
        )

    # 3. Construct Schema Graph
    schema_graph = SchemaGraph(schema)

    return AegisPipeline(
        catalog=catalog,
        schema_graph=schema_graph,
        db_path=database_path,
    )
