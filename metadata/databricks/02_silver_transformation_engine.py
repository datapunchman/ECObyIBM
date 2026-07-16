# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver Transformation Engine
# MAGIC # ECO Metadata
# MAGIC # Notebook: 02_silver_transformation_engine
# MAGIC # Layer: Silver
# MAGIC # Reads:
# MAGIC #   databricks_course_ws.bronze.dim_customer
# MAGIC #   databricks_course_ws.bronze.fact_internet_sales
# MAGIC # Writes:
# MAGIC #   databricks_course_ws.silver.customer
# MAGIC #   databricks_course_ws.silver.sales
# MAGIC # END ECO METADATA
# MAGIC # ======================================================
# MAGIC # ECO METADATA
# MAGIC # ======================================================
# MAGIC # NOTEBOOK_NAME=02_silver_transformation_engine
# MAGIC # DESCRIPTION=Transforms Bronze data into cleansed Silver tables.
# MAGIC # CATALOG=databricks_course_ws
# MAGIC # SCHEMA=silver
# MAGIC # LAYER=Silver
# MAGIC # PIPELINE=ECO_Data_Pipeline
# MAGIC # TASK=Silver_Transformation
# MAGIC # EXECUTION_ORDER=2
# MAGIC #
# MAGIC # READ_TABLES=
# MAGIC # databricks_course_ws.bronze.dim_customer
# MAGIC # databricks_course_ws.bronze.fact_internet_sales
# MAGIC # databricks_course_ws.bronze.dim_product
# MAGIC # databricks_course_ws.bronze.dim_sales_territory
# MAGIC # databricks_course_ws.bronze.dim_date
# MAGIC #
# MAGIC # WRITE_TABLES=
# MAGIC # databricks_course_ws.silver.customer
# MAGIC # databricks_course_ws.silver.sales
# MAGIC # databricks_course_ws.silver.product
# MAGIC # databricks_course_ws.silver.sales_territory
# MAGIC # databricks_course_ws.silver.calendar
# MAGIC # END ECO METADATA
# MAGIC # ======================================================
# MAGIC
# MAGIC
# MAGIC **Catalog:** `databricks_course_ws` | **Source:** `bronze` | **Destination:** `silver`
# MAGIC
# MAGIC Reads cleaned Bronze Delta tables, applies business transformations, and writes
# MAGIC production-ready Silver Delta tables registered in Unity Catalog.
# MAGIC
# MAGIC All tables are written in **overwrite** mode — safe to re-run at any time.
# MAGIC
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Architecture Position
# MAGIC
# MAGIC ```
# MAGIC AdventureWorksDW2025 → Landing Volume → Bronze ✅ → Silver ← HERE → Gold → Power BI → Enterprise Change Orchestrator
# MAGIC ```
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC | Source (Bronze) | Target (Silver) | Key Transformations |
# MAGIC |---|---|---|
# MAGIC | bronze.dim_customer | silver.customer | FullName, Age, AgeGroup, rename keys, dedup |
# MAGIC | bronze.dim_product | silver.product | Rename keys, null standardisation, dedup |
# MAGIC | bronze.fact_internet_sales | silver.sales | Rename, Profit, ProfitMargin |
# MAGIC | bronze.dim_date | silver.calendar | Rename, YearMonth derived column |
# MAGIC | bronze.dim_sales_territory | silver.sales_territory | Rename columns |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Imports & Configuration

# COMMAND ----------

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("silver_transformation")

# ── Global constants ───────────────────────────────────────────────────────────
CATALOG      = "databricks_course_ws"
SRC_SCHEMA   = "bronze"
TGT_SCHEMA   = "silver"
WRITE_MODE   = "overwrite"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Helper Functions

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.1 · `load_table()`

# COMMAND ----------

def load_table(full_table_name: str) -> DataFrame:
    """
    Load a Unity Catalog Delta table into a Spark DataFrame.

    Parameters
    ----------
    full_table_name : str
        Fully-qualified table name, e.g. ``bronze.dim_customer``.

    Returns
    -------
    pyspark.sql.DataFrame
    """
    logger.info("Loading  %-40s", full_table_name)
    try:
        df = spark.read.table(full_table_name)
        logger.info("Loaded   %-40s  cols=%d", full_table_name, len(df.columns))
        return df
    except Exception as exc:
        logger.error("Failed to load %s — %s", full_table_name, exc)
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.2 · `validate_dataframe()`

# COMMAND ----------

def validate_dataframe(df: DataFrame, source: str) -> int:
    """
    Validate that a DataFrame is non-empty and return its row count.

    Parameters
    ----------
    df     : pyspark.sql.DataFrame
    source : str  – human-readable label used in log/print messages

    Returns
    -------
    int  – exact row count

    Raises
    ------
    ValueError  – if the DataFrame is None, has no columns, or is empty
    """
    if df is None:
        raise ValueError(f"[{source}] DataFrame is None")
    if len(df.columns) == 0:
        raise ValueError(f"[{source}] DataFrame has no columns")

    row_count = df.count()
    if row_count == 0:
        raise ValueError(f"[{source}] DataFrame is empty (0 rows)")

    logger.info(
        "Validated %-38s  rows=%-8d  cols=%d",
        source, row_count, len(df.columns),
    )
    return row_count

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.3 · `transform_dataframe()`

# COMMAND ----------

def transform_dataframe(
    df: DataFrame,
    transform_fn: Callable[[DataFrame], DataFrame],
    source: str,
) -> DataFrame:
    """
    Apply a named transformation function to a DataFrame with error handling.

    Parameters
    ----------
    df           : pyspark.sql.DataFrame  – input DataFrame
    transform_fn : Callable               – function that accepts and returns a DataFrame
    source       : str                    – label used in log messages

    Returns
    -------
    pyspark.sql.DataFrame  – transformed DataFrame
    """
    logger.info("Transforming %-36s", source)
    try:
        transformed = transform_fn(df)
        logger.info(
            "Transformed  %-36s  cols: %d → %d",
            source, len(df.columns), len(transformed.columns),
        )
        return transformed
    except Exception as exc:
        logger.error("Transformation failed for %s — %s", source, exc)
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.4 · `write_delta()`

# COMMAND ----------

def write_delta(df: DataFrame, full_table_name: str, mode: str = WRITE_MODE) -> None:
    """
    Write a DataFrame to a Unity Catalog Delta table.

    Parameters
    ----------
    df              : pyspark.sql.DataFrame
    full_table_name : str  – fully-qualified target table, e.g. ``silver.customer``
    mode            : str  – Spark write mode (default: ``overwrite``)
    """
    logger.info("Writing  → %-40s  mode=%s", full_table_name, mode)
    try:
        (
            df.write
            .format("delta")
            .mode(mode)
            .option("overwriteSchema", "true")
            .saveAsTable(full_table_name)
        )
        logger.info("Written  ✓ %-40s", full_table_name)
    except Exception as exc:
        logger.error("Failed to write %s — %s", full_table_name, exc)
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.5 · `log_metrics()` & `TransformationMetrics`

# COMMAND ----------

@dataclass
class TransformationMetrics:
    source_table: str
    target_table: str
    row_count:    int
    elapsed_sec:  float
    status:       str           # "SUCCESS" | "FAILED"
    error_msg:    Optional[str] = None


_metrics_registry: list[TransformationMetrics] = []


def log_metrics(metrics: TransformationMetrics) -> None:
    """
    Append a TransformationMetrics record to the in-memory registry
    and emit a structured log line.
    """
    _metrics_registry.append(metrics)
    if metrics.status == "SUCCESS":
        logger.info(
            "METRICS  %-35s  rows=%-8d  elapsed=%.2fs  status=%s",
            metrics.target_table,
            metrics.row_count,
            metrics.elapsed_sec,
            metrics.status,
        )
    else:
        logger.error(
            "METRICS  %-35s  elapsed=%.2fs  status=%s  error=%s",
            metrics.target_table,
            metrics.elapsed_sec,
            metrics.status,
            metrics.error_msg,
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.6 · `process_table()`

# COMMAND ----------

def process_table(
    source_table: str,
    target_table: str,
    transform_fn: Callable[[DataFrame], DataFrame],
) -> TransformationMetrics:
    """
    End-to-end pipeline for a single Bronze → Silver transformation.

    Orchestrates: load_table → validate_dataframe → transform_dataframe
                  → validate_dataframe (post) → write_delta → log_metrics

    Parameters
    ----------
    source_table : str      – fully-qualified Bronze table name
    target_table : str      – fully-qualified Silver table name
    transform_fn : Callable – table-specific transformation function

    Returns
    -------
    TransformationMetrics
    """
    print(f"\n{'─' * 65}")
    print(f"  Processing  {source_table}  →  {target_table}")
    print(f"{'─' * 65}")

    t_start = time.perf_counter()
    try:
        # Step 1 — Load
        df_raw = load_table(source_table)

        # Step 2 — Validate source
        src_rows = validate_dataframe(df_raw, source_table)
        print(f"  Source rows : {src_rows:,}")

        # Step 3 — Transform
        df_transformed = transform_dataframe(df_raw, transform_fn, source_table)

        # Step 4 — Validate result
        tgt_rows = validate_dataframe(df_transformed, target_table)
        print(f"  Target rows : {tgt_rows:,}")

        # Step 5 — Show schema
        print(f"\n  Schema → {target_table}:")
        df_transformed.printSchema()

        # Step 6 — Write
        print(f"  Writing {target_table} ...")
        write_delta(df_transformed, target_table)

        elapsed = time.perf_counter() - t_start
        metrics = TransformationMetrics(
            source_table=source_table,
            target_table=target_table,
            row_count=tgt_rows,
            elapsed_sec=round(elapsed, 2),
            status="SUCCESS",
        )
        print(f"  ✅  Completed in {elapsed:.2f}s")

    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        metrics = TransformationMetrics(
            source_table=source_table,
            target_table=target_table,
            row_count=0,
            elapsed_sec=round(elapsed, 2),
            status="FAILED",
            error_msg=str(exc),
        )
        print(f"  ❌  FAILED after {elapsed:.2f}s — {exc}")
        logger.exception("Transformation failed: %s → %s", source_table, target_table)

    log_metrics(metrics)
    return metrics

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Catalog / Schema Bootstrap

# COMMAND ----------

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {TGT_SCHEMA}")
spark.sql(f"USE SCHEMA {TGT_SCHEMA}")
logger.info("Active context: %s  |  source=%s  target=%s", CATALOG, SRC_SCHEMA, TGT_SCHEMA)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Transformation Functions
# MAGIC
# MAGIC One dedicated function per Silver table. Each function accepts a raw Bronze
# MAGIC DataFrame and returns a fully transformed Silver DataFrame.

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.1 · `transform_customer()`
# MAGIC
# MAGIC **Transformations applied**
# MAGIC - Rename `CustomerKey` → `CustomerID`, `CustomerAlternateKey` → `CustomerCode`
# MAGIC - Derive `FullName` = `FirstName + ' ' + LastName`
# MAGIC - Derive `Age` = current year − birth year
# MAGIC - Derive `AgeGroup` bucket: 18-25 / 26-35 / 36-45 / 46-60 / 60+
# MAGIC - Drop duplicate records

# COMMAND ----------

def transform_customer(df: DataFrame) -> DataFrame:
    """
    Transform bronze.dim_customer → silver.customer.

    Derived columns: FullName, Age, AgeGroup.
    Renamed columns: CustomerKey → CustomerID, CustomerAlternateKey → CustomerCode.
    Deduplication applied on CustomerID.
    """
    current_year = F.year(F.current_date())

    age_group_expr = (
        F.when(F.col("Age").between(18, 25), F.lit("18-25"))
         .when(F.col("Age").between(26, 35), F.lit("26-35"))
         .when(F.col("Age").between(36, 45), F.lit("36-45"))
         .when(F.col("Age").between(46, 60), F.lit("46-60"))
         .otherwise(F.lit("60+"))
    )

    return (
        df
        .withColumnRenamed("CustomerKey",          "CustomerID")
        .withColumnRenamed("CustomerAlternateKey", "CustomerCode")
        .withColumn(
            "FullName",
            F.concat_ws(" ", F.col("FirstName"), F.col("LastName")),
        )
        .withColumn(
            "Age",
            (current_year - F.year(F.col("BirthDate"))).cast(IntegerType()),
        )
        .withColumn("AgeGroup", age_group_expr)
        .dropDuplicates(["CustomerID"])
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.2 · `transform_product()`
# MAGIC
# MAGIC **Transformations applied**
# MAGIC - Rename `ProductKey` → `ProductID`, `EnglishProductName` → `ProductName`
# MAGIC - Standardise `NULL` string literals to actual `NULL` values
# MAGIC - Drop duplicate records

# COMMAND ----------

def transform_product(df: DataFrame) -> DataFrame:
    """
    Transform bronze.dim_product → silver.product.

    Renamed columns: ProductKey → ProductID, EnglishProductName → ProductName.
    Null standardisation: replaces string literal 'NULL' / 'N/A' with null.
    Deduplication applied on ProductID.
    """
    # Columns to standardise nulls for (string columns only)
    string_cols = [f.name for f in df.schema.fields if str(f.dataType) == "StringType()"]

    df_renamed = (
        df
        .withColumnRenamed("ProductKey",         "ProductID")
        .withColumnRenamed("EnglishProductName", "ProductName")
    )

    # Replace sentinel null strings with actual SQL NULL
    for col_name in string_cols:
        # Rename may have changed the column name — use updated name if applicable
        active_name = (
            "ProductName" if col_name == "EnglishProductName"
            else col_name
        )
        if active_name in df_renamed.columns:
            df_renamed = df_renamed.withColumn(
                active_name,
                F.when(
                    F.upper(F.trim(F.col(active_name))).isin("NULL", "N/A", "NA", "NONE", ""),
                    F.lit(None),
                ).otherwise(F.col(active_name)),
            )

    return df_renamed.dropDuplicates(["ProductID"])

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.3 · `transform_sales()`
# MAGIC
# MAGIC **Transformations applied**
# MAGIC - Rename `SalesOrderNumber` → `OrderNumber`, `SalesAmount` → `Revenue`,
# MAGIC   `TaxAmt` → `Tax`, `Freight` → `FreightCost`, `TotalProductCost` → `ProductCost`
# MAGIC - Derive `Profit` = `Revenue − ProductCost`
# MAGIC - Derive `ProfitMargin` = `((Revenue − ProductCost) / Revenue) * 100` rounded to 2 dp

# COMMAND ----------

def transform_sales(df: DataFrame) -> DataFrame:
    """
    Transform bronze.fact_internet_sales → silver.sales.

    Renamed columns: SalesOrderNumber → OrderNumber, SalesAmount → Revenue,
                     TaxAmt → Tax, Freight → FreightCost, TotalProductCost → ProductCost.
    Derived columns: Profit, ProfitMargin (rounded to 2 dp).
    """
    return (
        df
        .withColumnRenamed("SalesOrderNumber",  "OrderNumber")
        .withColumnRenamed("SalesAmount",        "Revenue")
        .withColumnRenamed("TaxAmt",             "Tax")
        .withColumnRenamed("Freight",            "FreightCost")
        .withColumnRenamed("TotalProductCost",   "ProductCost")
        .withColumn(
            "Profit",
            F.round(F.col("Revenue") - F.col("ProductCost"), 2),
        )
        .withColumn(
            "ProfitMargin",
            F.round(
                ((F.col("Revenue") - F.col("ProductCost")) / F.col("Revenue")) * 100,
                2,
            ),
        )
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.4 · `transform_calendar()`
# MAGIC
# MAGIC **Transformations applied**
# MAGIC - Rename `DateKey` → `CalendarKey`, `FullDateAlternateKey` → `CalendarDate`,
# MAGIC   `EnglishMonthName` → `MonthName`, `CalendarQuarter` → `Quarter`,
# MAGIC   `EnglishDayNameOfWeek` → `DayName`
# MAGIC - Derive `YearMonth` in `YYYY-MM` format

# COMMAND ----------

def transform_calendar(df: DataFrame) -> DataFrame:
    """
    Transform bronze.dim_date → silver.calendar.

    Renamed columns: DateKey → CalendarKey, FullDateAlternateKey → CalendarDate,
                     EnglishMonthName → MonthName, CalendarQuarter → Quarter,
                     EnglishDayNameOfWeek → DayName.
    Derived column: YearMonth (format: YYYY-MM).
    """
    return (
        df
        .withColumnRenamed("DateKey",               "CalendarKey")
        .withColumnRenamed("FullDateAlternateKey",  "CalendarDate")
        .withColumnRenamed("EnglishMonthName",      "MonthName")
        .withColumnRenamed("CalendarQuarter",       "Quarter")
        .withColumnRenamed("EnglishDayNameOfWeek",  "DayName")
        .withColumn(
            "YearMonth",
            F.date_format(F.col("CalendarDate"), "yyyy-MM"),
        )
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.5 · `transform_sales_territory()`
# MAGIC
# MAGIC **Transformations applied**
# MAGIC - Rename `SalesTerritoryKey` → `TerritoryID`, `SalesTerritoryCountry` → `Country`,
# MAGIC   `SalesTerritoryRegion` → `Region`, `SalesTerritoryGroup` → `TerritoryGroup`

# COMMAND ----------

def transform_sales_territory(df: DataFrame) -> DataFrame:
    """
    Transform bronze.dim_sales_territory → silver.sales_territory.

    Renamed columns: SalesTerritoryKey → TerritoryID,
                     SalesTerritoryCountry → Country,
                     SalesTerritoryRegion → Region,
                     SalesTerritoryGroup → TerritoryGroup.
    """
    return (
        df
        .withColumnRenamed("SalesTerritoryKey",     "TerritoryID")
        .withColumnRenamed("SalesTerritoryCountry", "Country")
        .withColumnRenamed("SalesTerritoryRegion",  "Region")
        .withColumnRenamed("SalesTerritoryGroup",   "TerritoryGroup")
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Transformation Pipeline
# MAGIC
# MAGIC Defines the ordered pipeline manifest and executes every transformation.
# MAGIC Each entry is a tuple of `(source_table, target_table, transform_function)`.

# COMMAND ----------

PIPELINE: list[tuple[str, str, Callable]] = [
    (f"{SRC_SCHEMA}.dim_customer",       f"{TGT_SCHEMA}.customer",        transform_customer),
    (f"{SRC_SCHEMA}.dim_product",        f"{TGT_SCHEMA}.product",         transform_product),
    (f"{SRC_SCHEMA}.fact_internet_sales",f"{TGT_SCHEMA}.sales",           transform_sales),
    (f"{SRC_SCHEMA}.dim_date",           f"{TGT_SCHEMA}.calendar",        transform_calendar),
    (f"{SRC_SCHEMA}.dim_sales_territory",f"{TGT_SCHEMA}.sales_territory", transform_sales_territory),
]

pipeline_start = time.perf_counter()

results = [process_table(src, tgt, fn) for src, tgt, fn in PIPELINE]

pipeline_elapsed = time.perf_counter() - pipeline_start

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Transformation Summary

# COMMAND ----------

successes = [m for m in results if m.status == "SUCCESS"]
failures  = [m for m in results if m.status == "FAILED"]

print("\n" + "═" * 65)
print("  SILVER TRANSFORMATION SUMMARY")
print("═" * 65)
print(f"  {'Table':<35} {'Rows':>8}  {'Time':>7}  Status")
print(f"  {'─' * 35} {'─' * 8}  {'─' * 7}  ──────")
for m in results:
    status_icon = "✅" if m.status == "SUCCESS" else "❌"
    print(f"  {m.target_table:<35} {m.row_count:>8,}  {m.elapsed_sec:>6.2f}s  {status_icon}")

print(f"  {'─' * 35} {'─' * 8}  {'─' * 7}  ──────")
total_rows = sum(m.row_count for m in successes)
print(f"  {'TOTAL':<35} {total_rows:>8,}  {pipeline_elapsed:>6.2f}s")
print("═" * 65)

if failures:
    print(f"\n  ⚠️  {len(failures)} table(s) FAILED:")
    for m in failures:
        print(f"     • {m.target_table}: {m.error_msg}")
    raise RuntimeError(
        f"Silver transformation completed with {len(failures)} failure(s). "
        "See the summary above for details."
    )

print("\n  ✅  All Silver Tables Successfully Created")
print("═" * 65 + "\n")