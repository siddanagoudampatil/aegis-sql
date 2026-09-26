# Aegis-SQL: Autonomous Semantic Copilot & Relational Text-to-SQL Engine

> **Status:** Production Milestone 1  
> **Dialect Target:** DuckDB / ANSI-SQL

---

## 1. System Overview

Aegis-SQL is an enterprise-grade autonomous Semantic Copilot and Text-to-SQL engine engineered to eliminate LLM hallucinations in database querying. Production Text-to-SQL deployments fail primarily due to three failure modes:

1. **Hallucinated Relational Joins:** LLMs guess join conditions, invent non-existent foreign keys, or generate unintentional Cartesian products ($O(N \times M)$ blowups).
2. **Drift in Metric Definitions:** LLMs guess calculation logic for critical KPIs (e.g., conflating gross contracted revenue with active Monthly Recurring Revenue or miscalculating churn denominators).
3. **Execution Sandbox Escapes:** Unconstrained LLM-generated SQL risks destructive DML/DDL mutations (`DROP`, `UPDATE`, `DELETE`) or Server-Side Request Forgery (SSRF) through database file-reading functions (`read_csv`, `httpfs`).

Aegis-SQL addresses this by **strictly decoupling semantic understanding from relational mechanics**:

- Natural language intent is mapped to **declarative, governed metrics and dimensions** specified in a Pydantic v2 semantic catalog.
- Relational join paths are resolved deterministically using a **NetworkX foreign-key dependency graph** executing Steiner-tree minimal spanning algorithms.
- Queries are compiled into ASTs and passed through an **AST validation sandbox (`sqlglot`)** before reaching the database wire protocol.
- Executed on a high-performance **DuckDB** analytical engine.

---

## 2. Architecture & Pipeline Topology

The execution lifecycle is governed by an orchestrated **LangGraph StateGraph** state machine:

```
[ User Query ]
       │
       ▼
┌───────────────────────────────────────────────────────────┐
│ 1. Semantic Resolver Node                                 │
│    • Tokenizes query & resolves canonical metrics/dims    │
│    • Matches governed business logic & filter predicates  │
│    • Identifies required warehouse table set              │
└──────────────────────────────┬────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│ 2. Topological Graph Pathfinder (NetworkX)                │
│    • Ingests foreign key relational schema graph          │
│    • Computes minimal Steiner tree across terminal tables │
│    • Discovers intermediate bridge tables (BFS traversal) │
│    • Example: customers -> subscriptions -> plans         │
└──────────────────────────────┬────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│ 3. Deterministic SQL Generator                            │
│    • Compiles SELECT, FROM, JOIN, WHERE, GROUP BY         │
│    • Enforces explicit table qualification on columns     │
└──────────────────────────────┬────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│ 4. SQLGlot AST Validation & Sandbox                       │
│    • Rejects non-SELECT queries (no DDL/DML mutations)    │
│    • Enforces table access whitelist                      │
│    • Blocks unsafe functions (read_csv, httpfs, system)   │
│    • Transpiles & pretty-prints DuckDB dialect            │
└──────────────────────────────┬────────────────────────────┘
                               │
                     [ Is AST Green? ]
                      ├── No  ──► [ Abort & Emit Audit Error ]
                      └── Yes ──►
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│ 5. Sandbox Database Executor (DuckDB)                     │
│    • In-memory / OLAP vectorized query execution          │
│    • Serializes rows to structured dictionaries / Rich UI │
└───────────────────────────────────────────────────────────┘
```

---

## 3. Design Decisions & Architectural Trade-offs

### 3.1 NetworkX Steiner Tree vs. LLM Freehand Joins & Recursive SQL CTEs

- **The Problem:** When an analytical question spans multiple tables (e.g., comparing `customers` and `plans`), there is often no direct foreign key between them; an intermediate bridge entity (e.g., `subscriptions`) is required. LLMs frequently fail to infer bridge tables, hallucinate non-existent foreign keys, or select non-optimal join routes with redundant table scans.
- **Why not Recursive SQL CTEs?** Recursive CTEs querying `information_schema.table_constraints` are dialect-dependent, cannot cleanly prune cyclic dependency graphs without vendor-specific workarounds, and introduce query overhead.
- **The Decision:** Aegis-SQL extracts the schema into an undirected graph $G = (V, E)$, where vertices $V$ are warehouse tables and edges $E$ are foreign-key relations. Finding the minimal join tree across a set of requested tables $T \subseteq V$ maps to the **Steiner Minimal Tree in Graphs**. Aegis-SQL solves this via Kou-Markowsky-Berman (KMB) metric closure approximation:
  $$\text{Time Complexity: } \mathcal{O}(|T| \cdot (|V| \log |V| + |E|))$$
  This guarantees a deterministic, cyclic-safe, minimal join sequence in sub-millisecond execution time.

### 3.2 SQLGlot Compiler AST Auditing vs. Regex-Based Sanitization

- **The Problem:** Regex filtering (e.g., searching for `DROP` or `DELETE`) is trivially bypassed in SQL through comments (`/* ... */`), string literals, or obfuscated subqueries.
- **The Decision:** Aegis-SQL parses queries directly into Abstract Syntax Trees using `sqlglot`. We enforce structural invariants at the compiler level:
    1. The AST root node must be an instance of `exp.Select` or `exp.Union`.
    2. All `exp.Table` identifiers must reside in an immutable table whitelist.
    3. All function invocations (`exp.Anonymous`, `exp.Func`) are checked against a blacklist of filesystem and network-capable intrinsics (`read_parquet`, `httpfs`, `shell`, `copy`).

### 3.3 Declarative Semantic Layer vs. Direct Prompt-to-SQL

- **The Problem:** Prompts containing raw DDLs require the LLM to invent formulas for business concepts on the fly. For instance, calculating "Active MRR" requires filtering `subscriptions.status = 'active'`, and "Churn Rate" requires careful division by non-null denominators.
- **The Decision:** Business metrics and analytical dimensions are defined declaratively in YAML and validated via Pydantic v2. The semantic layer governs the calculation formula, while the LLM or semantic parser only resolves user intent to catalog keys.

---

## 4. B2B SaaS Warehouse Schema

The DuckDB operational warehouse schema models a real-world multi-tenant B2B SaaS platform:

| Table           | Primary Key | Foreign Keys                                                           | Key Edge-Case Modeling                                                                                            |
| :-------------- | :---------- | :--------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------- |
| `customers`     | `id`        | -                                                                      | Active, churned, and suspended lifecycle states across Starter, Growth, and Enterprise tiers.                     |
| `plans`         | `id`        | -                                                                      | Monthly and annual cadences; JSON entitlement flags.                                                              |
| `subscriptions` | `id`        | `customer_id -> customers.id`<br>`plan_id -> plans.id`                 | Historical upgrades, active contracts, and churned records with both null and populated `canceled_at` timestamps. |
| `invoices`      | `id`        | `subscription_id -> subscriptions.id`<br>`customer_id -> customers.id` | Paid, open, void, and uncollectible states; partial payments and overdue statements.                              |
| `usage_events`  | `id`        | `customer_id -> customers.id`<br>`subscription_id -> subscriptions.id` | High-frequency telemetry events (`api_call`, `compute_hour`, `export_gb`).                                        |

---

## 5. Local Setup & Quickstart

### Prerequisites

- Python 3.10+
- Linux / macOS / WSL

### 1. Initialize Virtual Environment & Install Dependencies

```bash
# Create local virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -e .
```

### 2. Seed the DuckDB Warehouse

Execute the reproducible database seeder to create `data/warehouse.duckdb`:

```bash
python -m data.seed_data
```

Output:

```
[INFO] Applying DDL schema from data/schema.sql
[INFO] Seeded 5 plan records.
[INFO] Seeded 16 customer records.
[INFO] Seeded 20 subscription records.
[INFO] Seeded 36 invoice records.
[INFO] Seeded 40 usage event records.
[INFO] Database seeding successfully completed. Total warehouse rows: 117
```

### 3. Run the Test Suite

Aegis-SQL includes a comprehensive test suite with 100% passing tests across unit, integration, and security boundaries:

```bash
pytest tests/ -v
```

### 4. Execute the End-to-End CLI Engine

Run analytical queries against the warehouse:

```bash
python main.py --query "What is our active MRR broken down by customer tier?"
```

Sample CLI output:

```
✔ Identified Join Path: customers -> subscriptions -> plans

✔ AST-Validated DuckDB SQL Query:
SELECT
  customers.tier AS customer_tier,
  SUM(subscriptions.mrr_amount) AS active_mrr
FROM customers
INNER JOIN subscriptions
  ON customers.id = subscriptions.customer_id
WHERE
  subscriptions.status = 'active'
GROUP BY
  customers.tier
ORDER BY
  active_mrr DESC;

✔ Database Results:
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ customer_tier ┃ active_mrr ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ enterprise    │   5,994.00 │
│ growth        │     756.00 │
│ starter       │     186.00 │
└───────────────┴────────────┘
```

---

## 6. Project Layout

```
aegis-sql/
├── pyproject.toml              # Dependencies & build metadata
├── README.md                   # Systems architecture & design RFC
├── main.py                     # CLI entrypoint with Rich terminal rendering
├── configs/
│   └── semantic_catalog.yaml   # Governed metrics, dimensions, and synonym indices
├── data/
│   ├── __init__.py
│   ├── schema.sql              # DuckDB DDL with relational constraints
│   ├── seed_data.py            # Reproducible B2B SaaS dataset seeder
│   └── warehouse.duckdb        # Seeded DuckDB database file
├── src/
│   ├── __init__.py
│   ├── exceptions.py           # Domain exception taxonomy
│   ├── semantic/               # Semantic Catalog Layer
│   │   ├── __init__.py
│   │   ├── models.py           # Pydantic v2 schemas
│   │   └── loader.py           # YAML ingestion and referential integrity check
│   ├── graph/                  # Relational Schema Graph Layer
│   │   ├── __init__.py
│   │   ├── inspector.py        # DuckDB catalog & DDL AST constraint extractor
│   │   └── schema_graph.py     # NetworkX Steiner-tree & shortest pathfinder
│   ├── agent/                  # LangGraph Workflow Layer
│   │   ├── __init__.py
│   │   ├── state.py            # AegisState TypedDict definition
│   │   ├── nodes.py            # Resolver, Pathfinder, SQLGen, Validator, Sandbox
│   │   └── pipeline.py         # StateGraph assembly & conditional compilation
│   └── utils/                  # AST Sandboxing & Transpilation
│       ├── __init__.py
│       └── ast_parser.py       # SQLGlot AST validation & query sanitation
└── tests/
    ├── test_semantic_loader.py # Semantic catalog unit tests
    ├── test_schema_graph.py    # Topological join resolution tests
    ├── test_ast_parser.py      # AST sandbox & security boundary tests
    └── test_pipeline_e2e.py    # End-to-end integration tests
```
