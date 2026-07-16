# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Bronze Ingestion Framework
# MAGIC # ==========================================================
# MAGIC # ECO METADATA
# MAGIC # ==========================================================
# MAGIC # NOTEBOOK_NAME=01_bronze_ingestion_framework
# MAGIC # DESCRIPTION=Loads raw source files into the Bronze layer.
# MAGIC # CATALOG=databricks_course_ws
# MAGIC # SCHEMA=bronze
# MAGIC # LAYER=Bronze
# MAGIC # PIPELINE=ECO_Data_Pipeline
# MAGIC # TASK=Bronze_Ingestion
# MAGIC # EXECUTION_ORDER=1
# MAGIC #
# MAGIC # READ_TABLES=
# MAGIC # landing.dim_customer
# MAGIC # landing.fact_internet_sales
# MAGIC # landing.dim_product
# MAGIC # landing.dim_sales_territory
# MAGIC # landing.dim_date
# MAGIC #
# MAGIC # WRITE_TABLES=
# MAGIC # databricks_course_ws.bronze.dim_customer
# MAGIC # databricks_course_ws.bronze.fact_internet_sales
# MAGIC # databricks_course_ws.bronze.dim_product
# MAGIC # databricks_course_ws.bronze.dim_sales_territory
# MAGIC # databricks_course_ws.bronze.dim_date
# MAGIC # END ECO METADATA
# MAGIC # ==========================================================
# MAGIC
# MAGIC
# MAGIC #Execution Order = 1
# MAGIC
# MAGIC #Layer = Bronze
# MAGIC
# MAGIC #Reads:
# MAGIC #landing/*.csv
# MAGIC
# MAGIC #Writes:
# MAGIC #bronze.dim_customer
# MAGIC #bronze.dim_product
# MAGIC #bronze.fact_internet_sales
# MAGIC #bronze.dim_sales_territory
# MAGIC #bronze.dim_date
# MAGIC
# MAGIC
# MAGIC **Catalog:** `databricks_course_ws`  |  **Schema:** `bronze`
# MAGIC
# MAGIC Reads every Parquet file from the landing volume and writes a managed Delta table
# MAGIC in the `bronze` schema using Unity Catalog.  All tables are written in **overwrite**
# MAGIC mode so the notebook is idempotent and safe to re-run.
# MAGIC
# MAGIC | Source file | Target table |
# MAGIC |---|---|
# MAGIC | DimCustomer.parquet | bronze.dim_customer |
# MAGIC | DimProduct.parquet | bronze.dim_product |
# MAGIC | DimDate.parquet | bronze.dim_date |
# MAGIC | DimSalesTerritory.parquet | bronze.dim_sales_territory |
# MAGIC | FactInternetSales.parquet | bronze.fact_internet_sales |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Imports & Configuration

# COMMAND ----------

import logging
import time
from dataclasses import dataclass
from typing import Optional

import pyarrow.parquet as pq
import pyarrow as pa
from pyspark.sql import DataFrame
from pyspark.sql.utils import AnalysisException
from pyspark.sql.types import TimestampType

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bronze_ingestion")

# ── Global constants ────────────────────────────────────────────────────────────
CATALOG        = "databricks_course_ws"
SCHEMA         = "bronze"
LANDING_VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/landing"
WRITE_MODE     = "overwrite"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Helper Functions

# COMMAND ----------

# ── 1.1  load_parquet ──────────────────────────────────────────────────────────

def _cast_timestamp_nanos(df: DataFrame) -> DataFrame:
    """
    Cast any TimestampNTZ / timestamp_ntz columns to standard TimestampType
    (microsecond precision).  Works around PARQUET_TYPE_ILLEGAL for files that
    store timestamps as INT64 NANOS.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import TimestampNTZType
    for field in df.schema.fields:
        if isinstance(field.dataType, (TimestampType, TimestampNTZType)):
            df = df.withColumn(field.name, F.col(field.name).cast(TimestampType()))
    return df


def load_parquet(file_name: str) -> DataFrame:
    """
    Read a single Parquet file from the landing volume.

    Uses PyArrow to read the file first (handles INT64 NANOS timestamps that
    Spark rejects), converts the Arrow table to a pandas DataFrame with
    timestamps downcast to microseconds, then creates a Spark DataFrame.

    Parameters
    ----------
    file_name : str
        File name including the ``.parquet`` extension (e.g. ``DimCustomer.parquet``).

    Returns
    -------
    pyspark.sql.DataFrame
    """
    path = f"{LANDING_VOLUME}/{file_name}"
    logger.info("Reading  %-35s  from  %s", file_name, path)
    try:
        # ── Try native Spark read first (fast path) ──────────────────────────
        df = (
            spark.read
            .format("parquet")
            .option("datetimeRebaseMode", "CORRECTED")
            .option("int96RebaseMode",    "CORRECTED")
            .load(path)
        )
        logger.info("Loaded   %-35s  OK (Spark native)", file_name)
        return df

    except Exception as spark_exc:
        # ── Fallback: PyArrow read → downcast nanos → Spark DataFrame ────────
        logger.warning(
            "Spark native read failed for %s (%s) — falling back to PyArrow",
            file_name, spark_exc,
        )
        try:
            arrow_table = pq.read_table(path)

            # Downcast every timestamp column: NANOS → MICROS (us)
            new_schema_fields = []
            for i, field in enumerate(arrow_table.schema):
                if pa.types.is_timestamp(field.type) and field.type.unit == "ns":
                    new_field = field.with_type(pa.timestamp("us", tz=field.type.tz))
                    new_schema_fields.append((i, new_field))

            for idx, new_field in new_schema_fields:
                col_name = arrow_table.schema.field(idx).name
                arrow_table = arrow_table.set_column(
                    idx,
                    col_name,
                    arrow_table.column(idx).cast(new_field.type),
                )
                logger.info(
                    "  Downcast column %-30s  ns → us  (%s)",
                    col_name, file_name,
                )

            # Convert to pandas then to Spark (preserves all types)
            pandas_df = arrow_table.to_pandas(timestamp_as_object=False)
            df = spark.createDataFrame(pandas_df)
            logger.info("Loaded   %-35s  OK (PyArrow fallback)", file_name)
            return df

        except Exception as arrow_exc:
            logger.error("PyArrow fallback also failed for %s — %s", file_name, arrow_exc)
            raise RuntimeError(
                f"Could not read {file_name}. "
                f"Spark error: {spark_exc}. PyArrow error: {arrow_exc}"
            ) from arrow_exc


# ── 1.2  validate_dataframe ────────────────────────────────────────────────────

def validate_dataframe(df: DataFrame, source: str) -> int:
    """
    Validate that a DataFrame is non-empty and return its row count.

    Parameters
    ----------
    df     : pyspark.sql.DataFrame
    source : str  – human-readable name used in log/print messages

    Returns
    -------
    int  – exact row count
    """
    if df is None:
        raise ValueError(f"[{source}] DataFrame is None")

    col_count = len(df.columns)
    if col_count == 0:
        raise ValueError(f"[{source}] DataFrame has no columns")

    row_count = df.count()
    if row_count == 0:
        raise ValueError(f"[{source}] DataFrame is empty (0 rows)")

    logger.info("Validated %-33s  rows=%-8d  cols=%d", source, row_count, col_count)
    return row_count


# ── 1.3  write_delta ───────────────────────────────────────────────────────────

def write_delta(df: DataFrame, full_table_name: str, mode: str = WRITE_MODE) -> None:
    """
    Write a DataFrame to a Unity Catalog Delta table.

    Parameters
    ----------
    df              : pyspark.sql.DataFrame
    full_table_name : str  – fully-qualified name, e.g. ``bronze.dim_customer``
    mode            : str  – Spark write mode (default: ``overwrite``)
    """
    logger.info("Writing  → %-35s  mode=%s", full_table_name, mode)
    try:
        (
            df.write
            .format("delta")
            .mode(mode)
            .option("overwriteSchema", "true")
            .saveAsTable(full_table_name)
        )
        logger.info("Written  ✓ %-35s", full_table_name)
    except Exception as exc:
        logger.error("Failed to write %s — %s", full_table_name, exc)
        raise


# ── 1.4  log_metrics ──────────────────────────────────────────────────────────

@dataclass
class IngestionMetrics:
    source_file:  str
    target_table: str
    row_count:    int
    elapsed_sec:  float
    status:       str  # "SUCCESS" | "FAILED"
    error_msg:    Optional[str] = None


_metrics_registry: list[IngestionMetrics] = []


def log_metrics(metrics: IngestionMetrics) -> None:
    """
    Append an IngestionMetrics record to the in-memory registry and emit a
    structured log line.
    """
    _metrics_registry.append(metrics)
    if metrics.status == "SUCCESS":
        logger.info(
            "METRICS  %-30s  rows=%-8d  elapsed=%.2fs  status=%s",
            metrics.target_table,
            metrics.row_count,
            metrics.elapsed_sec,
            metrics.status,
        )
    else:
        logger.error(
            "METRICS  %-30s  elapsed=%.2fs  status=%s  error=%s",
            metrics.target_table,
            metrics.elapsed_sec,
            metrics.status,
            metrics.error_msg,
        )


# ── 1.5  ingest_table ─────────────────────────────────────────────────────────

def ingest_table(file_name: str, table_name: str) -> IngestionMetrics:
    """
    End-to-end ingestion for a single Parquet → Delta table.

    Orchestrates load_parquet → validate_dataframe → write_delta → log_metrics
    and prints a human-readable progress summary.

    Parameters
    ----------
    file_name  : str  – Parquet filename in the landing volume
    table_name : str  – Unqualified target table name (schema prefix is added automatically)

    Returns
    -------
    IngestionMetrics
    """
    full_table = f"{SCHEMA}.{table_name}"
    print(f"\n{'─' * 60}")
    print(f"  Loading {file_name} ...")
    print(f"{'─' * 60}")

    t_start = time.perf_counter()
    try:
        # Step 1 — Read
        df = load_parquet(file_name)

        # Step 2 — Validate
        row_count = validate_dataframe(df, file_name)
        print(f"  {row_count:,} rows")

        # Step 3 — Show schema
        print(f"\n  Schema for {full_table}:")
        df.printSchema()

        # Step 4 — Write
        print(f"  Writing {full_table} ...")
        write_delta(df, full_table)

        elapsed = time.perf_counter() - t_start
        metrics = IngestionMetrics(
            source_file=file_name,
            target_table=full_table,
            row_count=row_count,
            elapsed_sec=round(elapsed, 2),
            status="SUCCESS",
        )
        print(f"  ✅  Completed in {elapsed:.2f}s")

    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        metrics = IngestionMetrics(
            source_file=file_name,
            target_table=full_table,
            row_count=0,
            elapsed_sec=round(elapsed, 2),
            status="FAILED",
            error_msg=str(exc),
        )
        print(f"  ❌  FAILED after {elapsed:.2f}s — {exc}")
        logger.exception("Ingestion failed for %s", file_name)

    log_metrics(metrics)
    return metrics

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Catalog / Schema Bootstrap

# COMMAND ----------

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
spark.sql(f"USE SCHEMA {SCHEMA}")
logger.info("Active context: %s.%s", CATALOG, SCHEMA)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Table Ingestion Pipeline

# COMMAND ----------

TABLE_MAP: list[tuple[str, str]] = [
    ("DimCustomer.parquet",      "dim_customer"),
    ("DimProduct.parquet",       "dim_product"),
    ("DimDate.parquet",          "dim_date"),
    ("DimSalesTerritory.parquet","dim_sales_territory"),
    ("FactInternetSales.parquet","fact_internet_sales"),
]

pipeline_start = time.perf_counter()

results = [ingest_table(src, tgt) for src, tgt in TABLE_MAP]

pipeline_elapsed = time.perf_counter() - pipeline_start

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Ingestion Summary

# COMMAND ----------

successes = [m for m in results if m.status == "SUCCESS"]
failures  = [m for m in results if m.status == "FAILED"]

print("\n" + "═" * 60)
print("  BRONZE INGESTION SUMMARY")
print("═" * 60)
print(f"  {'Table':<35} {'Rows':>8}  {'Time':>7}  Status")
print(f"  {'─' * 35} {'─' * 8}  {'─' * 7}  ──────")
for m in results:
    status_icon = "✅" if m.status == "SUCCESS" else "❌"
    print(f"  {m.target_table:<35} {m.row_count:>8,}  {m.elapsed_sec:>6.2f}s  {status_icon}")

print(f"  {'─' * 35} {'─' * 8}  {'─' * 7}  ──────")
total_rows = sum(m.row_count for m in successes)
print(f"  {'TOTAL':<35} {total_rows:>8,}  {pipeline_elapsed:>6.2f}s")
print("═" * 60)

if failures:
    print(f"\n  ⚠️  {len(failures)} table(s) FAILED:")
    for m in failures:
        print(f"     • {m.target_table}: {m.error_msg}")
    raise RuntimeError(
        f"Bronze ingestion completed with {len(failures)} failure(s). "
        "See the summary above for details."
    )

print("\n  ✅  All Bronze Tables Successfully Created")
print("═" * 60 + "\n")

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from bronze.dim_customer 

# COMMAND ----------

# MAGIC %sql
# MAGIC USE CATALOG databricks_course_ws;
# MAGIC USE SCHEMA bronze;
# MAGIC
# MAGIC SHOW TABLES;