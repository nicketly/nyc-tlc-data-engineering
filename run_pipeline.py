"""One-command entry point for the complete local data pipeline.

Run from the repository root:
    python run_pipeline.py

Starts PostgreSQL with Docker Compose, prepares the raw database, ingests local
Parquet files, builds the warehouse, and validates integrity.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

import ingestion  # noqa: E402
import run_pipeline as warehouse_pipeline  # noqa: E402


def validate_environment() -> None:
    env_path = PROJECT_DIR / ".env"
    example_path = PROJECT_DIR / ".env.example"
    if not env_path.exists():
        if not example_path.is_file():
            raise FileNotFoundError(f"Environment template not found: {example_path}")
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        logging.warning("Created .env from .env.example with local development defaults")

    load_dotenv(env_path)
    required = [
        "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD", "HOST", "PORT"
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(
            "Missing .env settings: " + ", ".join(missing)
            + ". Copy .env.example to .env and configure it."
        )


def start_database() -> None:
    """Start the PostgreSQL service defined in docker-compose.yml."""
    compose_file = PROJECT_DIR / "docker-compose.yml"
    if not compose_file.is_file():
        raise FileNotFoundError(f"Docker Compose file not found: {compose_file}")

    logging.info("Starting PostgreSQL with Docker Compose")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "db"],
        cwd=PROJECT_DIR,
        check=True,
    )


def wait_for_database(timeout_seconds: int = 120) -> None:
    """Wait until PostgreSQL accepts connections using project credentials."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                dbname=os.environ["DATABASE_NAME"],
                user=os.environ["DATABASE_USER"],
                password=os.environ["DATABASE_PASSWORD"],
                host=os.environ["HOST"],
                port=os.environ["PORT"],
                connect_timeout=3,
            ):
                logging.info("PostgreSQL is accepting connections")
                return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(2)

    raise TimeoutError(
        f"PostgreSQL did not become ready within {timeout_seconds} seconds. "
        f"Last connection error: {last_error}"
    )


def prepare_raw_database() -> None:
    """Create required schemas, raw tables, ingestion log, and reference tables."""
    logging.info("Preparing raw database objects")
    with warehouse_pipeline.get_connection() as conn:
        for filename in (
            "01_create_schemas.sql",
            "02_create_raw_yellow_tripdata.sql",
            "03_create_ingestion_log.sql",
            "04_create_index_ingestion_log.sql",
            "05_create_raw_reference_tables.sql",
        ):
            warehouse_pipeline.run_sql_file(conn, filename)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        validate_environment()
        start_database()
        wait_for_database()
        prepare_raw_database()

        logging.info("Starting Parquet ingestion")
        ingestion.main()

        logging.info("Starting warehouse transformations and validation")
        result = warehouse_pipeline.main()
        if result != 0:
            raise RuntimeError("Warehouse pipeline returned a failure exit code")

        logging.info("Complete pipeline finished successfully")
        return 0

    except Exception:
        logging.exception("Complete pipeline failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
