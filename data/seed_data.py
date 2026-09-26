"""Reproducible warehouse database seeder for Aegis-SQL.

Populates DuckDB with a realistic B2B SaaS operational dataset containing
heterogeneous entity states, edge cases (churned accounts, null cancellation
timestamps, partial payments, open invoices), and strict foreign key relations.
"""

from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("aegis_sql.data.seed_data")

DB_DIR = Path(__file__).resolve().parent
DB_PATH = DB_DIR / "warehouse.duckdb"
SCHEMA_PATH = DB_DIR / "schema.sql"


def get_connection(db_path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Creates a DuckDB connection for warehouse ingestion and querying."""
    return duckdb.connect(str(db_path))


def init_schema(conn: duckdb.DuckDBPyConnection, schema_path: Path = SCHEMA_PATH) -> None:
    """Executes the DDL schema against the database connection."""
    logger.info("Applying DDL schema from %s", schema_path)
    with open(schema_path, "r", encoding="utf-8") as f:
        ddl = f.read()
    conn.execute(ddl)


def seed_database(conn: duckdb.DuckDBPyConnection) -> None:
    """Seeds ~100 realistic rows covering B2B SaaS entities and relational edge cases.

    Scenarios modeled:
    - Active enterprise customers with high MRR and recurring monthly usage.
    - Churned customers with canceled subscriptions and null vs. populated canceled_at.
    - Past-due customers with open, unpaid invoices.
    - Multi-subscription customers (historical upgrade path).
    """
    logger.info("Beginning database seeding transaction...")

    # Clear existing data in reverse dependency order
    conn.execute("DELETE FROM usage_events;")
    conn.execute("DELETE FROM invoices;")
    conn.execute("DELETE FROM subscriptions;")
    conn.execute("DELETE FROM plans;")
    conn.execute("DELETE FROM customers;")

    base_time = datetime(2025, 1, 1, 0, 0, 0)

    # 1. Plans (5 tiers & intervals)
    plans = [
        ("plan_str_mo", "Starter Monthly", "starter", 49.00, "monthly", '{"seats": 3, "api_limit": 10000}'),
        ("plan_str_yr", "Starter Annual", "starter", 39.00, "annual", '{"seats": 3, "api_limit": 120000}'),
        ("plan_gro_mo", "Growth Monthly", "growth", 199.00, "monthly", '{"seats": 10, "api_limit": 50000}'),
        ("plan_gro_yr", "Growth Annual", "growth", 159.00, "annual", '{"seats": 15, "api_limit": 600000}'),
        ("plan_ent_yr", "Enterprise Annual", "enterprise", 999.00, "annual", '{"seats": 100, "api_limit": 5000000}'),
    ]
    conn.executemany(
        "INSERT INTO plans (id, name, tier, monthly_fee, billing_interval, features_json) VALUES (?, ?, ?, ?, ?, ?);",
        plans,
    )
    logger.info("Seeded %d plan records.", len(plans))

    # 2. Customers (16 diverse entities across tiers and lifecycle states)
    customers = [
        ("cust_001", "Acme Corporation", "billing@acme.com", "enterprise", "active", base_time - timedelta(days=360)),
        ("cust_002", "Globex Industries", "finance@globex.org", "enterprise", "active", base_time - timedelta(days=280)),
        ("cust_003", "Soylent Logistics", "ops@soylent.io", "growth", "active", base_time - timedelta(days=210)),
        ("cust_004", "Initech Software", "peter@initech.com", "growth", "active", base_time - timedelta(days=190)),
        ("cust_005", "Umbrella Corp", "albert@umbrella.com", "enterprise", "active", base_time - timedelta(days=150)),
        ("cust_006", "Hooli Tech", "gavin@hooli.com", "growth", "suspended", base_time - timedelta(days=120)),
        ("cust_007", "Massive Dynamic", "nina@massivedynamic.com", "enterprise", "active", base_time - timedelta(days=110)),
        ("cust_008", "Stark Industries", "tony@stark.io", "enterprise", "active", base_time - timedelta(days=95)),
        ("cust_009", "Wayne Enterprises", "bruce@wayne.org", "growth", "active", base_time - timedelta(days=90)),
        ("cust_010", "Cyberdyne Systems", "miles@cyberdyne.ai", "starter", "active", base_time - timedelta(days=80)),
        ("cust_011", "Pied Piper", "richard@piedpiper.com", "starter", "active", base_time - timedelta(days=60)),
        ("cust_012", "Dunder Mifflin", "michael@dundermifflin.com", "starter", "churned", base_time - timedelta(days=250)),
        ("cust_013", "Vandelay Imports", "george@vandelay.com", "starter", "churned", base_time - timedelta(days=180)),
        ("cust_014", "Oscorp Bio", "norman@oscorp.com", "growth", "active", base_time - timedelta(days=45)),
        ("cust_015", "Aperture Science", "glados@aperture.labs", "starter", "active", base_time - timedelta(days=30)),
        ("cust_016", "LexCorp", "lex@lexcorp.net", "enterprise", "active", base_time - timedelta(days=20)),
    ]
    conn.executemany(
        "INSERT INTO customers (id, name, email, tier, status, created_at) VALUES (?, ?, ?, ?, ?, ?);",
        customers,
    )
    logger.info("Seeded %d customer records.", len(customers))

    # 3. Subscriptions (20 records, including canceled subscriptions with timestamps)
    subscriptions = [
        # cust_001 (Acme - Enterprise Annual)
        ("sub_001", "cust_001", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=360), None, base_time, base_time + timedelta(days=365)),
        # cust_002 (Globex - Enterprise Annual)
        ("sub_002", "cust_002", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=280), None, base_time, base_time + timedelta(days=365)),
        # cust_003 (Soylent - Growth Monthly)
        ("sub_003", "cust_003", "plan_gro_mo", "active", 199.00, base_time - timedelta(days=210), None, base_time, base_time + timedelta(days=30)),
        # cust_004 (Initech - Growth Annual)
        ("sub_004", "cust_004", "plan_gro_yr", "active", 159.00, base_time - timedelta(days=190), None, base_time, base_time + timedelta(days=365)),
        # cust_005 (Umbrella - Enterprise Annual)
        ("sub_005", "cust_005", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=150), None, base_time, base_time + timedelta(days=365)),
        # cust_006 (Hooli - Growth Monthly, past due)
        ("sub_006", "cust_006", "plan_gro_mo", "past_due", 199.00, base_time - timedelta(days=120), None, base_time - timedelta(days=30), base_time),
        # cust_007 (Massive Dynamic - Enterprise Annual)
        ("sub_007", "cust_007", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=110), None, base_time, base_time + timedelta(days=365)),
        # cust_008 (Stark Industries - Enterprise Annual)
        ("sub_008", "cust_008", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=95), None, base_time, base_time + timedelta(days=365)),
        # cust_009 (Wayne Enterprises - Growth Monthly)
        ("sub_009", "cust_009", "plan_gro_mo", "active", 199.00, base_time - timedelta(days=90), None, base_time, base_time + timedelta(days=30)),
        # cust_010 (Cyberdyne - Starter Monthly)
        ("sub_010", "cust_010", "plan_str_mo", "active", 49.00, base_time - timedelta(days=80), None, base_time, base_time + timedelta(days=30)),
        # cust_011 (Pied Piper - Starter Annual)
        ("sub_011", "cust_011", "plan_str_yr", "active", 39.00, base_time - timedelta(days=60), None, base_time, base_time + timedelta(days=365)),
        # cust_012 (Dunder Mifflin - Starter Monthly, canceled)
        ("sub_012", "cust_012", "plan_str_mo", "canceled", 49.00, base_time - timedelta(days=250), base_time - timedelta(days=100), base_time - timedelta(days=130), base_time - timedelta(days=100)),
        # cust_013 (Vandelay - Starter Monthly, canceled with null canceled_at edge-case)
        ("sub_013", "cust_013", "plan_str_mo", "canceled", 49.00, base_time - timedelta(days=180), None, base_time - timedelta(days=150), base_time - timedelta(days=120)),
        # cust_014 (Oscorp - Growth Monthly)
        ("sub_014", "cust_014", "plan_gro_mo", "active", 199.00, base_time - timedelta(days=45), None, base_time, base_time + timedelta(days=30)),
        # cust_015 (Aperture - Starter Monthly)
        ("sub_015", "cust_015", "plan_str_mo", "active", 49.00, base_time - timedelta(days=30), None, base_time, base_time + timedelta(days=30)),
        # cust_016 (LexCorp - Enterprise Annual)
        ("sub_016", "cust_016", "plan_ent_yr", "active", 999.00, base_time - timedelta(days=20), None, base_time, base_time + timedelta(days=365)),
        # Upgraded/Superseded subscriptions
        ("sub_017", "cust_001", "plan_gro_yr", "canceled", 159.00, base_time - timedelta(days=700), base_time - timedelta(days=360), base_time - timedelta(days=700), base_time - timedelta(days=360)),
        ("sub_018", "cust_002", "plan_gro_mo", "canceled", 199.00, base_time - timedelta(days=500), base_time - timedelta(days=280), base_time - timedelta(days=310), base_time - timedelta(days=280)),
        ("sub_019", "cust_007", "plan_gro_yr", "canceled", 159.00, base_time - timedelta(days=400), base_time - timedelta(days=110), base_time - timedelta(days=400), base_time - timedelta(days=110)),
        ("sub_020", "cust_004", "plan_str_mo", "canceled", 49.00, base_time - timedelta(days=300), base_time - timedelta(days=190), base_time - timedelta(days=220), base_time - timedelta(days=190)),
    ]
    conn.executemany(
        """INSERT INTO subscriptions (
            id, customer_id, plan_id, status, mrr_amount,
            started_at, canceled_at, current_period_start, current_period_end
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        subscriptions,
    )
    logger.info("Seeded %d subscription records.", len(subscriptions))

    # 4. Invoices (36 records, paid, open, overdue, uncollectible)
    invoices = [
        # Acme invoices
        ("inv_001", "sub_001", "cust_001", 11988.00, 11988.00, "paid", date(2025, 1, 15), base_time + timedelta(days=2), base_time),
        ("inv_002", "sub_017", "cust_001", 1908.00, 1908.00, "paid", date(2024, 1, 15), base_time - timedelta(days=350), base_time - timedelta(days=360)),
        # Globex invoices
        ("inv_003", "sub_002", "cust_002", 11988.00, 11988.00, "paid", date(2025, 1, 20), base_time + timedelta(days=5), base_time),
        # Soylent invoices (Monthly)
        ("inv_004", "sub_003", "cust_003", 199.00, 199.00, "paid", date(2024, 11, 15), base_time - timedelta(days=45), base_time - timedelta(days=47)),
        ("inv_005", "sub_003", "cust_003", 199.00, 199.00, "paid", date(2024, 12, 15), base_time - timedelta(days=15), base_time - timedelta(days=16)),
        ("inv_006", "sub_003", "cust_003", 199.00, 0.00, "open", date(2025, 1, 15), None, base_time),
        # Initech invoices
        ("inv_007", "sub_004", "cust_004", 1908.00, 1908.00, "paid", date(2024, 7, 1), base_time - timedelta(days=180), base_time - timedelta(days=190)),
        # Umbrella invoices
        ("inv_008", "sub_005", "cust_005", 11988.00, 11988.00, "paid", date(2024, 8, 15), base_time - timedelta(days=145), base_time - timedelta(days=150)),
        # Hooli invoices (Delinquent / Past Due)
        ("inv_009", "sub_006", "cust_006", 199.00, 199.00, "paid", date(2024, 11, 1), base_time - timedelta(days=60), base_time - timedelta(days=61)),
        ("inv_010", "sub_006", "cust_006", 199.00, 0.00, "open", date(2024, 12, 1), None, base_time - timedelta(days=30)),
        ("inv_011", "sub_006", "cust_006", 199.00, 0.00, "uncollectible", date(2025, 1, 1), None, base_time),
        # Massive Dynamic
        ("inv_012", "sub_007", "cust_007", 11988.00, 11988.00, "paid", date(2024, 9, 20), base_time - timedelta(days=105), base_time - timedelta(days=110)),
        # Stark Industries
        ("inv_013", "sub_008", "cust_008", 11988.00, 11988.00, "paid", date(2024, 10, 5), base_time - timedelta(days=90), base_time - timedelta(days=95)),
        # Wayne Enterprises
        ("inv_014", "sub_009", "cust_009", 199.00, 199.00, "paid", date(2024, 11, 1), base_time - timedelta(days=60), base_time - timedelta(days=62)),
        ("inv_015", "sub_009", "cust_009", 199.00, 199.00, "paid", date(2024, 12, 1), base_time - timedelta(days=30), base_time - timedelta(days=31)),
        ("inv_016", "sub_009", "cust_009", 199.00, 199.00, "paid", date(2025, 1, 1), base_time + timedelta(days=1), base_time),
        # Cyberdyne
        ("inv_017", "sub_010", "cust_010", 49.00, 49.00, "paid", date(2024, 12, 10), base_time - timedelta(days=20), base_time - timedelta(days=21)),
        ("inv_018", "sub_010", "cust_010", 49.00, 0.00, "open", date(2025, 1, 10), None, base_time),
        # Pied Piper
        ("inv_019", "sub_011", "cust_011", 468.00, 468.00, "paid", date(2024, 11, 5), base_time - timedelta(days=50), base_time - timedelta(days=60)),
        # Dunder Mifflin (Churned historical)
        ("inv_020", "sub_012", "cust_012", 49.00, 49.00, "paid", date(2024, 6, 1), base_time - timedelta(days=210), base_time - timedelta(days=212)),
        ("inv_021", "sub_012", "cust_012", 49.00, 0.00, "void", date(2024, 9, 1), None, base_time - timedelta(days=120)),
        # Vandelay Imports
        ("inv_022", "sub_013", "cust_013", 49.00, 49.00, "paid", date(2024, 8, 1), base_time - timedelta(days=150), base_time - timedelta(days=151)),
        # Oscorp
        ("inv_023", "sub_014", "cust_014", 199.00, 199.00, "paid", date(2024, 12, 15), base_time - timedelta(days=15), base_time - timedelta(days=16)),
        # Aperture
        ("inv_024", "sub_015", "cust_015", 49.00, 49.00, "paid", date(2025, 1, 2), base_time + timedelta(days=1), base_time),
        # LexCorp
        ("inv_025", "sub_016", "cust_016", 11988.00, 11988.00, "paid", date(2025, 1, 10), base_time + timedelta(days=3), base_time),
        # Additional historical invoices to hit ~100 total rows across warehouse
        ("inv_026", "sub_001", "cust_001", 11988.00, 11988.00, "paid", date(2023, 1, 15), base_time - timedelta(days=720), base_time - timedelta(days=730)),
        ("inv_027", "sub_002", "cust_002", 11988.00, 11988.00, "paid", date(2023, 1, 20), base_time - timedelta(days=640), base_time - timedelta(days=645)),
        ("inv_028", "sub_003", "cust_003", 199.00, 199.00, "paid", date(2024, 10, 15), base_time - timedelta(days=75), base_time - timedelta(days=76)),
        ("inv_029", "sub_007", "cust_007", 1908.00, 1908.00, "paid", date(2023, 9, 20), base_time - timedelta(days=470), base_time - timedelta(days=475)),
        ("inv_030", "sub_008", "cust_008", 11988.00, 11988.00, "paid", date(2023, 10, 5), base_time - timedelta(days=455), base_time - timedelta(days=460)),
        ("inv_031", "sub_009", "cust_009", 199.00, 199.00, "paid", date(2024, 10, 1), base_time - timedelta(days=90), base_time - timedelta(days=91)),
        ("inv_032", "sub_014", "cust_014", 199.00, 0.00, "open", date(2025, 1, 15), None, base_time),
        ("inv_033", "sub_015", "cust_015", 49.00, 0.00, "open", date(2025, 2, 2), None, base_time + timedelta(days=1)),
        ("inv_034", "sub_016", "cust_016", 5000.00, 5000.00, "paid", date(2025, 1, 15), base_time + timedelta(days=5), base_time + timedelta(days=1)),
        ("inv_035", "sub_018", "cust_002", 199.00, 199.00, "paid", date(2023, 11, 1), base_time - timedelta(days=420), base_time - timedelta(days=425)),
        ("inv_036", "sub_019", "cust_007", 1908.00, 1908.00, "paid", date(2022, 9, 20), base_time - timedelta(days=840), base_time - timedelta(days=845)),
    ]
    conn.executemany(
        """INSERT INTO invoices (
            id, subscription_id, customer_id, amount_due, amount_paid,
            status, due_date, paid_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        invoices,
    )
    logger.info("Seeded %d invoice records.", len(invoices))

    # 5. Usage Events (40 telemetry records across customers and event types)
    usage_events = [
        ("evt_001", "cust_001", "sub_001", "api_call", 45000, base_time - timedelta(days=5)),
        ("evt_002", "cust_001", "sub_001", "compute_hour", 320, base_time - timedelta(days=4)),
        ("evt_003", "cust_001", "sub_001", "export_gb", 150, base_time - timedelta(days=3)),
        ("evt_004", "cust_002", "sub_002", "api_call", 89000, base_time - timedelta(days=5)),
        ("evt_005", "cust_002", "sub_002", "compute_hour", 640, base_time - timedelta(days=4)),
        ("evt_006", "cust_003", "sub_003", "api_call", 12000, base_time - timedelta(days=6)),
        ("evt_007", "cust_003", "sub_003", "export_gb", 25, base_time - timedelta(days=2)),
        ("evt_008", "cust_004", "sub_004", "api_call", 34000, base_time - timedelta(days=7)),
        ("evt_009", "cust_004", "sub_004", "compute_hour", 180, base_time - timedelta(days=6)),
        ("evt_010", "cust_005", "sub_005", "api_call", 120000, base_time - timedelta(days=8)),
        ("evt_011", "cust_005", "sub_005", "compute_hour", 920, base_time - timedelta(days=7)),
        ("evt_012", "cust_005", "sub_005", "export_gb", 540, base_time - timedelta(days=5)),
        ("evt_013", "cust_006", "sub_006", "api_call", 1500, base_time - timedelta(days=25)),
        ("evt_014", "cust_007", "sub_007", "api_call", 95000, base_time - timedelta(days=10)),
        ("evt_015", "cust_007", "sub_007", "compute_hour", 410, base_time - timedelta(days=9)),
        ("evt_016", "cust_008", "sub_008", "api_call", 145000, base_time - timedelta(days=3)),
        ("evt_017", "cust_008", "sub_008", "compute_hour", 1200, base_time - timedelta(days=2)),
        ("evt_018", "cust_008", "sub_008", "export_gb", 800, base_time - timedelta(days=1)),
        ("evt_019", "cust_009", "sub_009", "api_call", 18500, base_time - timedelta(days=4)),
        ("evt_020", "cust_009", "sub_009", "compute_hour", 95, base_time - timedelta(days=3)),
        ("evt_021", "cust_010", "sub_010", "api_call", 4200, base_time - timedelta(days=5)),
        ("evt_022", "cust_011", "sub_011", "api_call", 8900, base_time - timedelta(days=4)),
        ("evt_023", "cust_011", "sub_011", "compute_hour", 45, base_time - timedelta(days=3)),
        ("evt_024", "cust_014", "sub_014", "api_call", 16400, base_time - timedelta(days=2)),
        ("evt_025", "cust_014", "sub_014", "compute_hour", 110, base_time - timedelta(days=1)),
        ("evt_026", "cust_015", "sub_015", "api_call", 3200, base_time - timedelta(days=6)),
        ("evt_027", "cust_016", "sub_016", "api_call", 210000, base_time - timedelta(days=2)),
        ("evt_028", "cust_016", "sub_016", "compute_hour", 1450, base_time - timedelta(days=1)),
        ("evt_029", "cust_016", "sub_016", "export_gb", 950, base_time),
        ("evt_030", "cust_001", "sub_001", "api_call", 51000, base_time - timedelta(days=1)),
        ("evt_031", "cust_002", "sub_002", "api_call", 94000, base_time - timedelta(days=2)),
        ("evt_032", "cust_003", "sub_003", "compute_hour", 75, base_time - timedelta(days=1)),
        ("evt_033", "cust_004", "sub_004", "export_gb", 40, base_time - timedelta(days=2)),
        ("evt_034", "cust_007", "sub_007", "export_gb", 210, base_time - timedelta(days=1)),
        ("evt_035", "cust_008", "sub_008", "api_call", 88000, base_time),
        ("evt_036", "cust_009", "sub_009", "export_gb", 15, base_time - timedelta(days=1)),
        ("evt_037", "cust_010", "sub_010", "compute_hour", 12, base_time - timedelta(days=1)),
        ("evt_038", "cust_011", "sub_011", "export_gb", 8, base_time - timedelta(days=1)),
        ("evt_039", "cust_014", "sub_014", "export_gb", 30, base_time),
        ("evt_040", "cust_015", "sub_015", "compute_hour", 18, base_time),
    ]
    conn.executemany(
        """INSERT INTO usage_events (
            id, customer_id, subscription_id, event_type, quantity, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?);""",
        usage_events,
    )
    logger.info("Seeded %d usage event records.", len(usage_events))

    total_rows = len(plans) + len(customers) + len(subscriptions) + len(invoices) + len(usage_events)
    logger.info("Database seeding successfully completed. Total warehouse rows: %d", total_rows)


def main() -> None:
    """CLI entrypoint to initialize and seed data/warehouse.duckdb."""
    logger.info("Initializing DuckDB warehouse at: %s", DB_PATH)
    conn = get_connection(DB_PATH)
    try:
        init_schema(conn, SCHEMA_PATH)
        seed_database(conn)
    finally:
        conn.close()
    logger.info("Warehouse is ready for queries.")


if __name__ == "__main__":
    main()
