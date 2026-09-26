"""CLI entrypoint for Aegis-SQL: Semantic Copilot and Text-to-SQL Engine.

Executes end-to-end semantic resolution, relational join pathfinding, AST validation,
and database execution for analytical queries.
"""

import argparse
import logging
from pathlib import Path
import sys
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from src.agent.pipeline import build_aegis_pipeline
from src.exceptions import AegisError

# Structured engineering log format
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configures structured stream logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def render_results_table(columns: list[str], rows: list[dict]) -> Table:
    """Constructs a Rich terminal table for query output."""
    table = Table(title="Execution Sandbox Results", show_lines=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col, justify="left" if "tier" in col or "status" in col or "name" in col else "right")

    for row in rows:
        formatted_vals = []
        for col in columns:
            val = row.get(col)
            if isinstance(val, float):
                formatted_vals.append(f"{val:,.2f}")
            elif val is None:
                formatted_vals.append("[dim]NULL[/dim]")
            else:
                formatted_vals.append(str(val))
        table.add_row(*formatted_vals)

    return table


def run_query(
    query_str: str,
    catalog_path: Path,
    db_path: Path,
    ddl_path: Path,
    verbose: bool = False,
) -> int:
    """Executes the complete Aegis-SQL workflow for the given query."""
    setup_logging(verbose=verbose)
    logger = logging.getLogger("aegis_sql.cli")

    console.print(Panel(f"[bold white]{query_str}[/bold white]", title="[bold blue]User Analytical Query[/bold blue]"))

    # Ensure warehouse exists
    if not db_path.is_file():
        logger.warning("DuckDB database file '%s' not found. Seeding now...", db_path)
        from data.seed_data import main as seed_main
        seed_main()

    try:
        pipeline = build_aegis_pipeline(
            catalog_path=catalog_path,
            db_path=db_path,
            ddl_path=ddl_path,
        )

        state = pipeline.run(query_str)

        # 1. Semantic Resolution Display
        metrics = state.get("resolved_metrics", [])
        dims = state.get("resolved_dimensions", [])
        join_plan = state.get("join_plan")
        formatted_sql = state.get("formatted_sql")
        results = state.get("query_results")
        result_cols = state.get("result_columns") or []
        val_error = state.get("validation_error")
        exec_error = state.get("execution_error")

        metric_names = [m.name for m in metrics]
        dim_names = [d.name for d in dims]
        logger.info("Semantic Resolution: Metrics=%s, Dimensions=%s", metric_names, dim_names)

        # 2. Identified Join Path Display
        if join_plan:
            path_desc = join_plan.path_description
            logger.info("Identified join path: %s", path_desc)
            console.print(f"\n[bold green]✔ Identified Join Path:[/] [bold cyan]{path_desc}[/bold cyan]")
        else:
            logger.warning("No relational join path could be identified.")

        # 3. Pretty-Printed AST-Validated SQL
        if formatted_sql:
            console.print("\n[bold green]✔ AST-Validated DuckDB SQL Query:[/]")
            syntax = Syntax(formatted_sql, "sql", theme="monokai", line_numbers=True)
            console.print(syntax)
        elif val_error:
            console.print(f"\n[bold red]✖ AST Validation Failed:[/] {val_error}")
            return 1

        # 4. Formatted Table Results
        if results is not None:
            console.print("\n[bold green]✔ Database Results:[/]")
            results_table = render_results_table(result_cols, results)
            console.print(results_table)
            console.print(f"[dim]Total records returned: {len(results)}[/dim]\n")
        elif exec_error:
            console.print(f"\n[bold red]✖ Query Execution Failed:[/] {exec_error}")
            return 1

        return 0

    except AegisError as exc:
        logger.error("Aegis domain error: %s", exc)
        console.print(f"\n[bold red]Domain Error:[/] {exc}")
        return 1
    except Exception as exc:
        logger.exception("Unexpected error executing query: %s", exc)
        console.print(f"\n[bold red]Fatal System Error:[/] {exc}")
        return 1


def main() -> None:
    """CLI argument parser and dispatcher."""
    parser = argparse.ArgumentParser(
        prog="aegis-sql",
        description="Aegis-SQL: Autonomous Semantic Copilot and Text-to-SQL Engine with Graph-Based Schema Linking",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="What is our active MRR broken down by customer tier?",
        help="Natural language analytical question to evaluate.",
    )
    parser.add_argument(
        "--catalog",
        "-c",
        type=Path,
        default=Path("configs/semantic_catalog.yaml"),
        help="Path to declarative semantic catalog YAML file.",
    )
    parser.add_argument(
        "--db",
        "-d",
        type=Path,
        default=Path("data/warehouse.duckdb"),
        help="Path to DuckDB warehouse database file.",
    )
    parser.add_argument(
        "--ddl",
        type=Path,
        default=Path("data/schema.sql"),
        help="Path to SQL DDL schema file.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args = parser.parse_args()
    exit_code = run_query(
        query_str=args.query,
        catalog_path=args.catalog,
        db_path=args.db,
        ddl_path=args.ddl,
        verbose=args.verbose,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
