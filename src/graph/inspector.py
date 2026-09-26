"""Relational schema inspector for DuckDB and SQL DDL.

Extracts tables, columns, primary keys, and foreign-key relational dependencies
from live DuckDB catalogs or declarative SQL DDL syntax trees.
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any
import duckdb
import sqlglot
from sqlglot import exp

logger = logging.getLogger("aegis_sql.graph.inspector")


@dataclass(frozen=True)
class ForeignKeyRelation:
    """Represents a directed foreign key constraint between two tables."""
    from_table: str
    from_col: str
    to_table: str
    to_col: str

    def __repr__(self) -> str:
        return f"{self.from_table}.{self.from_col} -> {self.to_table}.{self.to_col}"


@dataclass
class TableSchema:
    """Represents the schema structure of a warehouse table."""
    name: str
    columns: dict[str, str] = field(default_factory=dict)
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyRelation] = field(default_factory=list)


@dataclass
class DatabaseSchema:
    """Full relational database schema representation."""
    tables: dict[str, TableSchema] = field(default_factory=dict)
    foreign_keys: list[ForeignKeyRelation] = field(default_factory=list)

    def get_table(self, table_name: str) -> TableSchema | None:
        """Case-insensitive table lookup."""
        return self.tables.get(table_name.lower().strip())


def inspect_duckdb_database(db: duckdb.DuckDBPyConnection | str | Path) -> DatabaseSchema:
    """Extracts schema, primary keys, and foreign keys from an active DuckDB database.

    Args:
        db: Open DuckDBPyConnection or filesystem path to .duckdb database file.

    Returns:
        DatabaseSchema with all tables and discovered relationships.
    """
    should_close = False
    if isinstance(db, (str, Path)):
        conn = duckdb.connect(str(db), read_only=True)
        should_close = True
    else:
        conn = db

    try:
        tables: dict[str, TableSchema] = {}
        all_fks: list[ForeignKeyRelation] = []

        # 1. Inspect columns
        cols_query = """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main'
            ORDER BY table_name, ordinal_position;
        """
        for tbl_name, col_name, data_type in conn.execute(cols_query).fetchall():
            t_key = tbl_name.lower()
            if t_key not in tables:
                tables[t_key] = TableSchema(name=tbl_name)
            tables[t_key].columns[col_name.lower()] = data_type

        # 2. Inspect constraints via DuckDB internal catalog functions
        try:
            constraints_query = """
                SELECT constraint_type, table_name, constraint_column_names,
                       referenced_table, referenced_column_names
                FROM duckdb_constraints();
            """
            for c_type, t_name, c_cols, ref_t, ref_cols in conn.execute(constraints_query).fetchall():
                t_key = t_name.lower()
                if t_key not in tables:
                    continue

                if c_type == "PRIMARY KEY":
                    for c_col in (c_cols or []):
                        if c_col.lower() not in tables[t_key].primary_keys:
                            tables[t_key].primary_keys.append(c_col.lower())

                elif c_type == "FOREIGN KEY" and ref_t:
                    ref_key = ref_t.lower()
                    c_col_list = list(c_cols) if c_cols else []
                    ref_col_list = list(ref_cols) if ref_cols else []

                    for src_c, dst_c in zip(c_col_list, ref_col_list):
                        fk = ForeignKeyRelation(
                            from_table=t_key,
                            from_col=src_c.lower(),
                            to_table=ref_key,
                            to_col=dst_c.lower(),
                        )
                        tables[t_key].foreign_keys.append(fk)
                        all_fks.append(fk)
        except Exception as exc:
            logger.warning("Could not extract constraints from duckdb_constraints(): %s", exc)

        logger.info(
            "Inspected DuckDB catalog: %d tables, %d foreign key relations.",
            len(tables),
            len(all_fks),
        )
        return DatabaseSchema(tables=tables, foreign_keys=all_fks)
    finally:
        if should_close:
            conn.close()


def inspect_sql_ddl(ddl_sql: str) -> DatabaseSchema:
    """Parses SQL DDL text via SQLGlot AST to extract tables and foreign key relations.

    Enables offline schema graph extraction without requiring a live database connection.
    """
    tables: dict[str, TableSchema] = {}
    all_fks: list[ForeignKeyRelation] = []

    parsed_stmts = sqlglot.parse(ddl_sql, read="duckdb")

    for stmt in parsed_stmts:
        if not isinstance(stmt, exp.Create):
            continue

        table_expr = stmt.find(exp.Table)
        if not table_expr:
            continue
        table_name = table_expr.name.lower()
        schema = TableSchema(name=table_name)

        # Parse column definitions and inline references
        for col_def in stmt.find_all(exp.ColumnDef):
            col_name = col_def.name.lower()
            data_type = col_def.kind.sql(dialect="duckdb") if col_def.kind else "UNKNOWN"
            schema.columns[col_name] = data_type

            # Check inline PRIMARY KEY
            if any(isinstance(c, exp.PrimaryKeyColumnConstraint) for c in col_def.find_all(exp.ColumnConstraint)):
                schema.primary_keys.append(col_name)

            # Check inline REFERENCES
            for ref in col_def.find_all(exp.Reference):
                tbl_node = ref.find(exp.Table)
                ref_tbl = tbl_node.name.lower() if tbl_node else ""
                col_nodes = ref.expressions or (ref.this.expressions if isinstance(ref.this, exp.Schema) else [])
                ref_cols = [c.name.lower() for c in col_nodes] if col_nodes else ["id"]
                if ref_tbl:
                    fk = ForeignKeyRelation(
                        from_table=table_name,
                        from_col=col_name,
                        to_table=ref_tbl,
                        to_col=ref_cols[0],
                    )
                    schema.foreign_keys.append(fk)
                    all_fks.append(fk)

        # Parse table-level constraints
        for pk_constraint in stmt.find_all(exp.PrimaryKey):
            for col_expr in pk_constraint.expressions:
                pk_col = col_expr.name.lower()
                if pk_col not in schema.primary_keys:
                    schema.primary_keys.append(pk_col)

        for fk_constraint in stmt.find_all(exp.ForeignKey):
            src_cols = [c.name.lower() for c in fk_constraint.expressions]
            ref = fk_constraint.find(exp.Reference)
            if ref:
                tbl_node = ref.find(exp.Table)
                ref_tbl = tbl_node.name.lower() if tbl_node else ""
                col_nodes = ref.expressions or (ref.this.expressions if isinstance(ref.this, exp.Schema) else [])
                ref_cols = [c.name.lower() for c in col_nodes] if col_nodes else ["id"]
                if ref_tbl:
                    for src_c, dst_c in zip(src_cols, ref_cols):
                        fk = ForeignKeyRelation(
                            from_table=table_name,
                            from_col=src_c,
                            to_table=ref_tbl,
                            to_col=dst_c,
                        )
                        schema.foreign_keys.append(fk)
                        all_fks.append(fk)

        tables[table_name] = schema

    logger.info("Inspected DDL: %d tables, %d foreign key relations.", len(tables), len(all_fks))
    return DatabaseSchema(tables=tables, foreign_keys=all_fks)
