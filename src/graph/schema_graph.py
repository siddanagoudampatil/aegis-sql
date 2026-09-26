"""NetworkX-based relational schema graph and Steiner-tree join resolution.

Ingests table schemas and foreign key constraints to build an undirected multigraph
of relational dependencies. Discovers minimal spanning join paths between arbitrary
warehouse tables with deterministic cycle prevention and disconnected component detection.
"""

from collections import deque
from dataclasses import dataclass, field
import logging
import networkx as nx
from networkx.algorithms.approximation.steinertree import steiner_tree
from src.exceptions import SchemaLinkError, UnreachableTableError
from src.graph.inspector import DatabaseSchema, ForeignKeyRelation

logger = logging.getLogger("aegis_sql.graph.schema_graph")


@dataclass
class JoinClause:
    """Represents a rendered SQL JOIN clause."""
    table: str
    on_condition: str
    join_type: str = "INNER"

    def to_sql(self) -> str:
        return f"{self.join_type} JOIN {self.table} ON {self.on_condition}"


@dataclass
class JoinPlan:
    """The resolved minimal relational join plan between required tables."""
    base_table: str
    join_clauses: list[JoinClause] = field(default_factory=list)
    tables_in_plan: list[str] = field(default_factory=list)
    path_description: str = ""

    def render_from_clause(self) -> str:
        """Renders the FROM and JOIN clauses for a SQL statement."""
        lines = [f"FROM {self.base_table}"]
        for jc in self.join_clauses:
            lines.append(f"  {jc.to_sql()}")
        return "\n".join(lines)


class SchemaGraph:
    """Graph representation of database schema using NetworkX.

    Nodes represent warehouse tables.
    Edges represent foreign-key relationships.
    Weights represent path traversal penalties (default 1.0 per hop).
    """

    def __init__(self, schema: DatabaseSchema | None = None) -> None:
        self.graph = nx.Graph()
        self.schema = schema or DatabaseSchema()
        if schema:
            self._build_graph(schema)

    def _build_graph(self, schema: DatabaseSchema) -> None:
        """Populates nodes and edges from the inspected schema."""
        for tbl_name, tbl_meta in schema.tables.items():
            self.graph.add_node(
                tbl_name.lower(),
                schema=tbl_meta,
                columns=list(tbl_meta.columns.keys()),
                primary_keys=tbl_meta.primary_keys,
            )

        for fk in schema.foreign_keys:
            src = fk.from_table.lower()
            dst = fk.to_table.lower()
            if self.graph.has_node(src) and self.graph.has_node(dst):
                # An undirected graph edge connects the referencing and referenced tables
                existing_fks = []
                if self.graph.has_edge(src, dst):
                    existing_fks = self.graph[src][dst].get("fks", [])
                existing_fks.append(fk)
                self.graph.add_edge(src, dst, weight=1.0, fks=existing_fks)

        logger.info(
            "Constructed SchemaGraph with %d table nodes and %d foreign key edges.",
            self.graph.number_of_nodes(),
            self.graph.number_of_edges(),
        )

    def add_table(self, table_name: str, primary_keys: list[str] | None = None) -> None:
        """Explicitly registers a table node into the schema graph."""
        self.graph.add_node(
            table_name.lower(),
            primary_keys=[pk.lower() for pk in (primary_keys or [])],
        )

    def add_relation(self, from_table: str, from_col: str, to_table: str, to_col: str) -> None:
        """Explicitly registers a foreign key relation into the graph."""
        src = from_table.lower()
        dst = to_table.lower()
        fk = ForeignKeyRelation(
            from_table=src,
            from_col=from_col.lower(),
            to_table=dst,
            to_col=to_col.lower(),
        )
        if not self.graph.has_node(src):
            self.add_table(src)
        if not self.graph.has_node(dst):
            self.add_table(dst)

        existing_fks = []
        if self.graph.has_edge(src, dst):
            existing_fks = self.graph[src][dst].get("fks", [])
        existing_fks.append(fk)
        self.graph.add_edge(src, dst, weight=1.0, fks=existing_fks)

    def resolve_join_path(self, required_tables: list[str], root_table: str | None = None) -> JoinPlan:
        """Resolves the minimal join tree connecting all required warehouse tables.

        Uses Steiner-tree minimal spanning algorithms to bridge arbitrary disjoint tables
        (e.g., finding that 'customers' connects to 'plans' via intermediate 'subscriptions').

        Args:
            required_tables: List of table names referenced by metrics, dimensions, or filters.
            root_table: Optional preferred base table for the FROM clause.

        Returns:
            JoinPlan with deterministic BFS-ordered join clauses.

        Raises:
            SchemaLinkError: If table names are invalid or no tables are provided.
            UnreachableTableError: If tables reside in disconnected graph components.
        """
        if not required_tables:
            raise SchemaLinkError("Cannot resolve join plan for an empty table list.")

        # Normalize and deduplicate preserving order
        normalized: list[str] = []
        for t in required_tables:
            tn = t.strip().lower()
            if tn not in normalized:
                normalized.append(tn)

        # Validate node existence
        missing_tables = [t for t in normalized if not self.graph.has_node(t)]
        if missing_tables:
            raise SchemaLinkError(
                f"Tables not found in schema graph: {missing_tables}. "
                f"Available tables: {sorted(list(self.graph.nodes))}"
            )

        # Case: Single table query (no joins required)
        if len(normalized) == 1:
            table = normalized[0]
            return JoinPlan(
                base_table=table,
                join_clauses=[],
                tables_in_plan=[table],
                path_description=table,
            )

        # Validate component connectivity
        first_table = normalized[0]
        component = nx.node_connected_component(self.graph, first_table)
        unreachable = [t for t in normalized if t not in component]
        if unreachable:
            raise UnreachableTableError(
                f"Disconnected relational schema: tables {unreachable} cannot be reached from '{first_table}'."
            )

        # Discover minimal spanning tree across terminal tables
        if len(normalized) == 2:
            try:
                shortest_path = nx.shortest_path(self.graph, source=normalized[0], target=normalized[1])
                join_tree = self.graph.subgraph(shortest_path).copy()
            except nx.NetworkXNoPath as exc:
                raise UnreachableTableError(
                    f"No relational foreign key path between '{normalized[0]}' and '{normalized[1]}'."
                ) from exc
        else:
            # Steiner tree approximation connects 3+ terminal nodes with minimal intermediate bridge tables
            join_tree = steiner_tree(self.graph, normalized, weight="weight")

        # Determine the root/base table for the SQL FROM clause
        if root_table and root_table.lower() in join_tree:
            base_table = root_table.lower()
        elif normalized[0] in join_tree:
            base_table = normalized[0]
        else:
            base_table = sorted(list(join_tree.nodes))[0]

        # Traverse the join tree using BFS from the base table to generate valid join sequences
        join_clauses: list[JoinClause] = []
        visited = {base_table}
        bfs_queue: deque[str] = deque([base_table])
        path_sequence: list[str] = [base_table]

        while bfs_queue:
            parent = bfs_queue.popleft()
            # Sort neighbors for deterministic query output across runs
            for child in sorted(list(join_tree.neighbors(parent))):
                if child not in visited:
                    visited.add(child)
                    bfs_queue.append(child)
                    path_sequence.append(child)

                    on_condition = self._build_on_condition(parent, child)
                    join_clauses.append(JoinClause(table=child, on_condition=on_condition))

        path_desc = " -> ".join(path_sequence)
        logger.info("Resolved minimal join tree: %s", path_desc)

        return JoinPlan(
            base_table=base_table,
            join_clauses=join_clauses,
            tables_in_plan=path_sequence,
            path_description=path_desc,
        )

    def _build_on_condition(self, tbl_a: str, tbl_b: str) -> str:
        """Determines the explicit SQL ON join condition between two adjacent tables."""
        edge_data = self.graph.get_edge_data(tbl_a, tbl_b)
        if not edge_data or "fks" not in edge_data:
            raise SchemaLinkError(f"No foreign key constraint found linking '{tbl_a}' and '{tbl_b}'.")

        fks: list[ForeignKeyRelation] = edge_data["fks"]
        # Use first declared foreign key relation between these tables
        fk = fks[0]

        # Format ON condition based on which table is referencing which
        if fk.from_table == tbl_b and fk.to_table == tbl_a:
            return f"{tbl_a}.{fk.to_col} = {tbl_b}.{fk.from_col}"
        if fk.from_table == tbl_a and fk.to_table == tbl_b:
            return f"{tbl_a}.{fk.from_col} = {tbl_b}.{fk.to_col}"

        # Fallback to canonical order
        return f"{fk.to_table}.{fk.to_col} = {fk.from_table}.{fk.from_col}"
