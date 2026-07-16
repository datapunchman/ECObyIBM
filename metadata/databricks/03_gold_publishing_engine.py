# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold Publishing Engine
# MAGIC # ECO METADATA
# MAGIC # ==========================================================
# MAGIC # NOTEBOOK_NAME=03_gold_publishing_engine
# MAGIC # DESCRIPTION=Publishes curated Gold datasets for analytics and reporting.
# MAGIC # CATALOG=databricks_course_ws
# MAGIC # SCHEMA=gold
# MAGIC # LAYER=Gold
# MAGIC # PIPELINE=ECO_Data_Pipeline
# MAGIC # TASK=Gold_Publishing
# MAGIC # EXECUTION_ORDER=3
# MAGIC #
# MAGIC # READ_TABLES=
# MAGIC # databricks_course_ws.silver.customer
# MAGIC # databricks_course_ws.silver.sales
# MAGIC # databricks_course_ws.silver.product
# MAGIC # databricks_course_ws.silver.sales_territory
# MAGIC # databricks_course_ws.silver.calendar
# MAGIC #
# MAGIC # WRITE_TABLES=
# MAGIC # databricks_course_ws.gold.customer_360
# MAGIC # databricks_course_ws.gold.customer_segmentation
# MAGIC # databricks_course_ws.gold.executive_summary
# MAGIC # databricks_course_ws.gold.monthly_sales
# MAGIC # databricks_course_ws.gold.product_performance
# MAGIC # databricks_course_ws.gold.sales_dashboard
# MAGIC # databricks_course_ws.gold.sales_forecasting_base
# MAGIC # databricks_course_ws.gold.territory_performance
# MAGIC # databricks_course_ws.gold.metadata_catalog
# MAGIC # databricks_course_ws.gold.executive_kpi
# MAGIC
# MAGIC # Execution Order = 3
# MAGIC
# MAGIC # Reads:
# MAGIC # silver.*
# MAGIC
# MAGIC # Writes:
# MAGIC # gold.customer_360
# MAGIC # gold.customer_segmentation
# MAGIC # gold.executive_summary
# MAGIC # gold.monthly_sales
# MAGIC # gold.product_performance
# MAGIC # gold.sales_dashboard
# MAGIC # gold.sales_forecasting_base
# MAGIC # gold.territory_performance
# MAGIC # gold.metadata_catalog
# MAGIC # gold.executive_kpi
# MAGIC
# MAGIC **Catalog:** `databricks_course_ws` | **Source:** `silver` | **Destination:** `gold`
# MAGIC
# MAGIC Reads clean Silver Delta tables, applies business-level aggregations and joins,
# MAGIC and publishes production-ready Gold tables optimised for Power BI, executive
# MAGIC dashboards, and AI reasoning via the **Enterprise Change Orchestrator**.
# MAGIC
# MAGIC All tables are written in **overwrite** mode — safe to re-run at any time.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Architecture Position
# MAGIC
# MAGIC ```
# MAGIC AdventureWorksDW2025 → Landing → Bronze ✅ → Silver ✅ → Gold ← HERE → Power BI → Enterprise Change Orchestrator
# MAGIC ```
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC | Gold Table | Purpose |
# MAGIC |---|---|
# MAGIC | gold.sales_dashboard | Primary Power BI fact table — fully denormalised |
# MAGIC | gold.customer_360 | Single customer profile with lifetime metrics |
# MAGIC | gold.product_performance | Product revenue, profit and ranking |
# MAGIC | gold.territory_performance | Geographical revenue and profit analysis |
# MAGIC | gold.monthly_sales | Monthly trend series for time-series dashboards |
# MAGIC | gold.executive_kpi | Single-row KPI snapshot for executive dashboards |
# MAGIC | gold.customer_segmentation | Platinum / Gold / Silver / Bronze segments |
# MAGIC | gold.sales_forecasting_base | Historical monthly base table for ML forecasting |
# MAGIC | gold.executive_summary | Combined executive narrative summary |
# MAGIC | gold.metadata_catalog | AI-readable metadata for Enterprise Change Orchestrator |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Imports & Configuration

# COMMAND ----------

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)
from pyspark.sql.window import Window

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gold_publishing")

# ── Global constants ───────────────────────────────────────────────────────────
CATALOG    = "databricks_course_ws"
SRC_SCHEMA = "silver"
TGT_SCHEMA = "gold"
WRITE_MODE = "overwrite"
REFRESH_TS = datetime.utcnow()

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
        Fully-qualified table name, e.g. ``silver.customer``.

    Returns
    -------
    pyspark.sql.DataFrame
    """
    logger.info("Loading  %-45s", full_table_name)
    try:
        df = spark.read.table(full_table_name)
        logger.info("Loaded   %-45s  cols=%d", full_table_name, len(df.columns))
        return df
    except Exception as exc:
        logger.error("Failed to load %s — %s", full_table_name, exc)
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.2 · `validate_dataframe()`

# COMMAND ----------

def validate_dataframe(df: DataFrame, label: str) -> int:
    """
    Validate that a DataFrame is non-empty and return its row count.

    Parameters
    ----------
    df    : pyspark.sql.DataFrame
    label : str – human-readable label for logging

    Returns
    -------
    int – exact row count

    Raises
    ------
    ValueError – if DataFrame is None, has no columns, or is empty
    """
    if df is None:
        raise ValueError(f"[{label}] DataFrame is None")
    if len(df.columns) == 0:
        raise ValueError(f"[{label}] DataFrame has no columns")

    row_count = df.count()
    if row_count == 0:
        raise ValueError(f"[{label}] DataFrame is empty (0 rows)")

    logger.info("Validated %-43s  rows=%-8d  cols=%d", label, row_count, len(df.columns))
    return row_count

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.3 · `build_business_table()`

# COMMAND ----------

def build_business_table(
    build_fn: Callable[[], DataFrame],
    table_label: str,
) -> DataFrame:
    """
    Execute a Gold build function with structured error handling and logging.

    Parameters
    ----------
    build_fn    : Callable – zero-argument function that returns the Gold DataFrame
    table_label : str      – label used in log messages

    Returns
    -------
    pyspark.sql.DataFrame
    """
    logger.info("Building %-45s", table_label)
    try:
        df = build_fn()
        logger.info("Built    %-45s  cols=%d", table_label, len(df.columns))
        return df
    except Exception as exc:
        logger.error("Build failed for %s — %s", table_label, exc)
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
    full_table_name : str  – fully-qualified target, e.g. ``gold.sales_dashboard``
    mode            : str  – Spark write mode (default: ``overwrite``)
    """
    logger.info("Writing  → %-45s  mode=%s", full_table_name, mode)
    try:
        (
            df.write
            .format("delta")
            .mode(mode)
            .option("overwriteSchema", "true")
            .saveAsTable(full_table_name)
        )
        logger.info("Written  ✓ %-45s", full_table_name)
    except Exception as exc:
        logger.error("Failed to write %s — %s", full_table_name, exc)
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.5 · `log_metrics()` & `PublishingMetrics`

# COMMAND ----------

@dataclass
class PublishingMetrics:
    target_table: str
    row_count:    int
    elapsed_sec:  float
    status:       str            # "SUCCESS" | "FAILED"
    error_msg:    Optional[str] = None


_metrics_registry: list[PublishingMetrics] = []


def log_metrics(metrics: PublishingMetrics) -> None:
    """
    Append a PublishingMetrics record to the in-memory registry and emit
    a structured log line.
    """
    _metrics_registry.append(metrics)
    if metrics.status == "SUCCESS":
        logger.info(
            "METRICS  %-40s  rows=%-8d  elapsed=%.2fs  status=%s",
            metrics.target_table, metrics.row_count,
            metrics.elapsed_sec, metrics.status,
        )
    else:
        logger.error(
            "METRICS  %-40s  elapsed=%.2fs  status=%s  error=%s",
            metrics.target_table, metrics.elapsed_sec,
            metrics.status, metrics.error_msg,
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1.6 · `publish_table()`

# COMMAND ----------

def publish_table(
    target_table: str,
    build_fn: Callable[[], DataFrame],
    sample_rows: int = 3,
) -> PublishingMetrics:
    """
    End-to-end pipeline for a single Gold table publication.

    Orchestrates: build_business_table → validate_dataframe → write_delta
                  → display schema → display sample → log_metrics

    Parameters
    ----------
    target_table : str      – fully-qualified Gold table, e.g. ``gold.sales_dashboard``
    build_fn     : Callable – zero-argument function returning the Gold DataFrame
    sample_rows  : int      – number of sample rows to display (default: 3)

    Returns
    -------
    PublishingMetrics
    """
    print(f"\n{'─' * 70}")
    print(f"  Publishing  →  {target_table}")
    print(f"{'─' * 70}")

    t_start = time.perf_counter()
    try:
        # Step 1 — Build
        df = build_business_table(build_fn, target_table)

        # Step 2 — Validate
        row_count = validate_dataframe(df, target_table)
        print(f"  Rows        : {row_count:,}")
        print(f"  Columns     : {len(df.columns)}")

        # Step 3 — Show schema
        print(f"\n  Schema → {target_table}:")
        df.printSchema()

        # Step 4 — Show sample
        print(f"  Sample ({sample_rows} rows):")
        df.show(sample_rows, truncate=False)

        # Step 5 — Write
        print(f"  Writing {target_table} ...")
        write_delta(df, target_table)

        elapsed = time.perf_counter() - t_start
        metrics = PublishingMetrics(
            target_table=target_table,
            row_count=row_count,
            elapsed_sec=round(elapsed, 2),
            status="SUCCESS",
        )
        print(f"  ✅  Published in {elapsed:.2f}s")

    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        metrics = PublishingMetrics(
            target_table=target_table,
            row_count=0,
            elapsed_sec=round(elapsed, 2),
            status="FAILED",
            error_msg=str(exc),
        )
        print(f"  ❌  FAILED after {elapsed:.2f}s — {exc}")
        logger.exception("Publishing failed for %s", target_table)

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
# MAGIC ## 3 · Load Silver Tables
# MAGIC
# MAGIC All Silver tables are loaded once here and reused across every Gold build
# MAGIC function, avoiding redundant reads.
# MAGIC
# MAGIC ### Actual Silver column reference
# MAGIC
# MAGIC | Silver Table | Key columns used in Gold |
# MAGIC |---|---|
# MAGIC | silver.customer | `CustomerID`, `CustomerCode`, `FullName`, `Age`, `AgeGroup`, `GeographyKey`, `BirthDate` |
# MAGIC | silver.sales | `CustomerKey`, `ProductKey`, `SalesTerritoryKey`, `OrderNumber`, `Revenue`, `ProductCost`, `Profit`, `ProfitMargin`, `OrderQuantity`, `OrderDateKey` |
# MAGIC | silver.product | `ProductID`, `ProductName` |
# MAGIC | silver.calendar | `CalendarKey`, `CalendarDate`, `CalendarYear`, `Quarter`, `MonthName`, `MonthNumberOfYear`, `YearMonth` |
# MAGIC | silver.sales_territory | `TerritoryID`, `Country`, `Region`, `TerritoryGroup` |

# COMMAND ----------

# Clear any stale Spark in-memory cache from previous notebook runs.
# This prevents INTERNAL_ERROR "Couldn't find <column>" caused by a cached
# query plan referencing columns that no longer exist in the current table version.
#spark.catalog.clearCache()
logger.info("Spark catalog cache cleared")

silver_customer        = load_table(f"{SRC_SCHEMA}.customer")
silver_product         = load_table(f"{SRC_SCHEMA}.product")
silver_sales           = load_table(f"{SRC_SCHEMA}.sales")
silver_calendar        = load_table(f"{SRC_SCHEMA}.calendar")
silver_sales_territory = load_table(f"{SRC_SCHEMA}.sales_territory")

# Print actual schemas so column names are visible in notebook output
print("\n── silver.customer columns:")
print(silver_customer.columns)
print("\n── silver.sales columns:")
print(silver_sales.columns)
print("\n── silver.calendar columns:")
print(silver_calendar.columns)
print("\n── silver.sales_territory columns:")
print(silver_sales_territory.columns)

logger.info("All Silver tables loaded successfully")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Gold Build Functions

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.1 · `build_sales_dashboard()`
# MAGIC
# MAGIC Fully denormalised fact table joining Sales → Customer → Product → Calendar → Territory.
# MAGIC
# MAGIC **Join keys (actual Silver column names)**
# MAGIC - `silver.sales.CustomerKey` → `silver.customer.CustomerID`
# MAGIC - `silver.sales.ProductKey` → `silver.product.ProductID`
# MAGIC - `silver.sales.OrderDateKey` → `silver.calendar.CalendarKey`
# MAGIC - `silver.sales.SalesTerritoryKey` → `silver.sales_territory.TerritoryID`

# COMMAND ----------

def build_sales_dashboard() -> DataFrame:
    """
    Build gold.sales_dashboard — fully denormalised Power BI fact table.

    Join keys used:
      sales.CustomerKey      → customer.CustomerID
      sales.ProductKey       → product.ProductID
      sales.OrderDateKey     → calendar.CalendarKey
      sales.SalesTerritoryKey → sales_territory.TerritoryID
    """
    cust = silver_customer.select(
        F.col("CustomerID"),
        F.col("FullName").alias("CustomerName"),
        F.col("AgeGroup"),
    )

    prod = silver_product.select(
        F.col("ProductID"),
        F.col("ProductName"),
    )

    # Alias CalendarDate → "SalesDate" (not "OrderDate") to avoid AMBIGUOUS_REFERENCE
    # because silver.sales already contains a column named "OrderDate".
    cal = silver_calendar.select(
        F.col("CalendarKey"),
        F.col("CalendarDate").alias("SalesDate"),
        F.col("CalendarYear").alias("Year"),
        F.col("Quarter"),
        F.col("MonthName").alias("Month"),
        F.col("YearMonth"),
    )

    terr = silver_sales_territory.select(
        F.col("TerritoryID"),
        F.col("Country"),
        F.col("Region"),
        F.col("TerritoryGroup"),
    )

    return (
        silver_sales
        # Join customer
        .join(cust,
              silver_sales["CustomerKey"] == cust["CustomerID"],
              how="left")
        # Join product
        .join(prod,
              silver_sales["ProductKey"] == prod["ProductID"],
              how="left")
        # Join calendar
        .join(cal,
              silver_sales["OrderDateKey"] == cal["CalendarKey"],
              how="left")
        # Join territory
        .join(terr,
              silver_sales["SalesTerritoryKey"] == terr["TerritoryID"],
              how="left")
        .select(
            F.col("OrderNumber"),
            F.col("CustomerID"),
            F.col("CustomerName"),
            F.col("ProductID"),
            F.col("ProductName"),
            cal["SalesDate"].alias("OrderDate"),   # use table-qualified ref to avoid ambiguity
            F.col("Year"),
            F.col("Quarter"),
            F.col("Month"),
            F.col("YearMonth"),
            F.col("Country"),
            F.col("Region"),
            F.col("Revenue"),
            F.col("ProductCost"),
            F.col("Profit"),
            F.col("ProfitMargin"),
            F.col("FreightCost"),
            F.col("Tax"),
            F.col("OrderQuantity"),
        )
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.2 · `build_customer_360()`
# MAGIC
# MAGIC Unified customer profile with lifetime metrics.
# MAGIC Join key: `sales.CustomerKey` → `customer.CustomerID`

# COMMAND ----------

def build_customer_360() -> DataFrame:
    """
    Build gold.customer_360 — single customer profile with lifetime metrics.

    Aggregates silver.sales by CustomerKey, joins to silver.customer on
    CustomerID = CustomerKey, then joins territory for geo attributes.
    Derives HighValueCustomer flag and RevenueCategory segment.
    """
    customer_metrics = (
        silver_sales
        .groupBy("CustomerKey")
        .agg(
            F.round(F.sum("Revenue"),          2).alias("LifetimeRevenue"),
            F.countDistinct("OrderNumber")      .alias("TotalOrders"),
            F.round(F.avg("Revenue"),          2).alias("AvgOrderValue"),
            F.max("OrderDateKey")               .alias("LastPurchaseDateKey"),
        )
    )

    revenue_category_expr = (
        F.when(F.col("LifetimeRevenue") >= 10000, F.lit("Platinum"))
         .when(F.col("LifetimeRevenue") >= 5000,  F.lit("Gold"))
         .when(F.col("LifetimeRevenue") >= 1000,  F.lit("Silver"))
         .otherwise(F.lit("Bronze"))
    )

    # silver.customer has CustomerID; silver.sales has CustomerKey
    return (
        silver_customer
        .join(
            customer_metrics,
            silver_customer["CustomerID"] == customer_metrics["CustomerKey"],
            how="left",
        )
        .join(
            silver_sales_territory.select(
                "TerritoryID", "Country", "Region", "TerritoryGroup",
            ),
            silver_customer["GeographyKey"] == silver_sales_territory["TerritoryID"],
            how="left",
        )
        .withColumn(
            "HighValueCustomer",
            F.when(F.col("LifetimeRevenue") >= 5000, F.lit(True)).otherwise(F.lit(False)),
        )
        .withColumn("RevenueCategory", revenue_category_expr)
        .select(
            F.col("CustomerID"),
            F.col("CustomerCode"),
            F.col("FullName"),
            F.col("Age"),
            F.col("AgeGroup"),
            F.col("Country"),
            F.col("Region"),
            F.col("TerritoryGroup"),
            F.col("LifetimeRevenue"),
            F.col("TotalOrders"),
            F.col("AvgOrderValue"),
            F.col("LastPurchaseDateKey"),
            F.col("HighValueCustomer"),
            F.col("RevenueCategory"),
        )
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.3 · `build_product_performance()`
# MAGIC
# MAGIC Product analytics with revenue/profit rankings.
# MAGIC Join key: `sales.ProductKey` → `product.ProductID`

# COMMAND ----------

def build_product_performance() -> DataFrame:
    """
    Build gold.product_performance — product revenue, profit and ranking.

    Aggregates silver.sales by ProductKey, joins silver.product on
    ProductID = ProductKey. Derives revenue rank, profit rank, and
    top-performer flag (top 10% by revenue).
    """
    product_metrics = (
        silver_sales
        .groupBy("ProductKey")
        .agg(
            F.round(F.sum("Revenue"),   2).alias("TotalRevenue"),
            F.round(F.sum("Profit"),    2).alias("TotalProfit"),
            F.sum("OrderQuantity")       .alias("UnitsSold"),
            F.round(F.avg("Revenue"),   2).alias("AvgSellingPrice"),
        )
        .withColumn(
            "ProductProfitMargin",
            F.round((F.col("TotalProfit") / F.col("TotalRevenue")) * 100, 2),
        )
    )

    w_rev    = Window.orderBy(F.col("TotalRevenue").desc())
    w_profit = Window.orderBy(F.col("TotalProfit").desc())

    product_count = product_metrics.count()
    top_n = max(1, int(product_count * 0.10))

    return (
        silver_product
        .select("ProductID", "ProductName")
        .join(
            product_metrics,
            silver_product["ProductID"] == product_metrics["ProductKey"],
            how="left",
        )
        .withColumn("RevenueRank",  F.rank().over(w_rev))
        .withColumn("ProfitRank",   F.rank().over(w_profit))
        .withColumn(
            "TopPerformer",
            F.when(F.rank().over(w_rev) <= F.lit(top_n), F.lit(True))
             .otherwise(F.lit(False)),
        )
        .select(
            "ProductID", "ProductName",
            "TotalRevenue", "TotalProfit", "ProductProfitMargin",
            "UnitsSold", "AvgSellingPrice",
            "RevenueRank", "ProfitRank", "TopPerformer",
        )
        .orderBy("RevenueRank")
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.4 · `build_territory_performance()`
# MAGIC
# MAGIC Geographical revenue and profit analysis.
# MAGIC Join key: `sales.SalesTerritoryKey` → `sales_territory.TerritoryID`

# COMMAND ----------

def build_territory_performance() -> DataFrame:
    """
    Build gold.territory_performance — geographical revenue and profit analysis.

    Aggregates silver.sales by SalesTerritoryKey, joins silver.sales_territory
    on TerritoryID. Derives avg revenue per customer and top-territory flag.
    """
    territory_metrics = (
        silver_sales
        .groupBy("SalesTerritoryKey")
        .agg(
            F.round(F.sum("Revenue"),      2).alias("TotalRevenue"),
            F.round(F.sum("Profit"),       2).alias("TotalProfit"),
            F.countDistinct("CustomerKey") .alias("CustomerCount"),
        )
    )

    w_rev = Window.orderBy(F.col("TotalRevenue").desc())

    return (
        silver_sales_territory
        .join(
            territory_metrics,
            silver_sales_territory["TerritoryID"] == territory_metrics["SalesTerritoryKey"],
            how="left",
        )
        .withColumn(
            "AvgRevenuePerCustomer",
            F.round(F.col("TotalRevenue") / F.col("CustomerCount"), 2),
        )
        .withColumn(
            "TopTerritory",
            F.when(F.rank().over(w_rev) == 1, F.lit(True)).otherwise(F.lit(False)),
        )
        .select(
            "Country", "Region", "TerritoryGroup",
            "TotalRevenue", "TotalProfit",
            "CustomerCount", "AvgRevenuePerCustomer",
            "TopTerritory",
        )
        .orderBy(F.col("TotalRevenue").desc())
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.5 · `build_monthly_sales()`
# MAGIC
# MAGIC Monthly trend series grouped by Year / Quarter / Month.
# MAGIC Join key: `sales.OrderDateKey` → `calendar.CalendarKey`
# MAGIC Note: calendar column is `Quarter` (not `CalendarQuarter`)

# COMMAND ----------

def build_monthly_sales() -> DataFrame:
    """
    Build gold.monthly_sales — monthly trend table for time-series dashboards.

    Joins silver.sales with silver.calendar on OrderDateKey = CalendarKey,
    then groups by Year / Quarter / Month.
    Note: silver.calendar uses `Quarter` (renamed from CalendarQuarter in Silver).
    """
    cal = silver_calendar.select(
        F.col("CalendarKey"),
        F.col("CalendarYear").alias("Year"),
        F.col("Quarter"),
        F.col("MonthNumberOfYear"),
        F.col("MonthName"),
        F.col("YearMonth"),
    )

    return (
        silver_sales
        .join(
            cal,
            silver_sales["OrderDateKey"] == cal["CalendarKey"],
            how="left",
        )
        .groupBy("Year", "Quarter", "MonthNumberOfYear", "MonthName", "YearMonth")
        .agg(
            F.round(F.sum("Revenue"),        2).alias("Revenue"),
            F.round(F.sum("Profit"),         2).alias("Profit"),
            F.round(
                (F.sum("Profit") / F.sum("Revenue")) * 100, 2,
            ).alias("ProfitMargin"),
            F.countDistinct("OrderNumber")    .alias("OrderCount"),
            F.countDistinct("CustomerKey")    .alias("CustomerCount"),
            F.countDistinct("ProductKey")     .alias("ProductCount"),
        )
        .orderBy("Year", "MonthNumberOfYear")
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.6 · `build_executive_kpi()`
# MAGIC
# MAGIC Single-row KPI snapshot for executive dashboards.

# COMMAND ----------

def build_executive_kpi() -> DataFrame:
    """
    Build gold.executive_kpi — single-row aggregate KPI table.

    Aggregates silver.sales globally. Resolves top territory and top product
    by total revenue. Returns a single-row DataFrame with a fixed schema.
    """
    row = silver_sales.agg(
        F.round(F.sum("Revenue"),          2).alias("TotalRevenue"),
        F.round(F.sum("Profit"),           2).alias("TotalProfit"),
        F.countDistinct("OrderNumber")      .alias("TotalOrders"),
        F.countDistinct("CustomerKey")      .alias("TotalCustomers"),
        F.countDistinct("ProductKey")       .alias("TotalProducts"),
        F.round(F.avg("Revenue"),          2).alias("AvgOrderValue"),
    ).first()

    total_revenue  = float(row["TotalRevenue"] or 1)
    total_profit   = float(row["TotalProfit"]  or 0)
    total_orders   = int(row["TotalOrders"]    or 0)
    total_customers = int(row["TotalCustomers"] or 1)

    profit_margin       = round((total_profit / total_revenue) * 100, 2)
    avg_customer_revenue = round(total_revenue / total_customers, 2)

    # Top territory by revenue (join on SalesTerritoryKey → TerritoryID)
    top_territory = (
        silver_sales
        .join(
            silver_sales_territory.select("TerritoryID", "Country"),
            silver_sales["SalesTerritoryKey"] == silver_sales_territory["TerritoryID"],
            how="left",
        )
        .groupBy("Country")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .first()["Country"]
    )

    # Top product by revenue (join on ProductKey → ProductID)
    top_product = (
        silver_sales
        .join(
            silver_product.select("ProductID", "ProductName"),
            silver_sales["ProductKey"] == silver_product["ProductID"],
            how="left",
        )
        .groupBy("ProductName")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .first()["ProductName"]
    )

    schema = StructType([
        StructField("TotalRevenue",            DoubleType(), True),
        StructField("TotalProfit",             DoubleType(), True),
        StructField("ProfitMargin",            DoubleType(), True),
        StructField("TotalCustomers",          LongType(),   True),
        StructField("TotalProducts",           LongType(),   True),
        StructField("TotalOrders",             LongType(),   True),
        StructField("AvgOrderValue",           DoubleType(), True),
        StructField("AvgCustomerRevenue",      DoubleType(), True),
        StructField("HighestRevenueTerritory", StringType(), True),
        StructField("HighestRevenueProduct",   StringType(), True),
    ])

    kpi_row = [(
        total_revenue,
        total_profit,
        profit_margin,
        int(row["TotalCustomers"]),
        int(row["TotalProducts"]),
        total_orders,
        float(row["AvgOrderValue"]),
        avg_customer_revenue,
        str(top_territory),
        str(top_product),
    )]

    return spark.createDataFrame(kpi_row, schema=schema)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.7 · `build_customer_segmentation()`
# MAGIC
# MAGIC Aggregated Platinum / Gold / Silver / Bronze segment summary.

# COMMAND ----------

def build_customer_segmentation() -> DataFrame:
    """
    Build gold.customer_segmentation — aggregated segment analytics.

    Groups silver.sales by CustomerKey to get lifetime revenue per customer,
    assigns a Platinum/Gold/Silver/Bronze segment, then summarises per segment.
    """
    customer_revenue = (
        silver_sales
        .groupBy("CustomerKey")
        .agg(
            F.round(F.sum("Revenue"), 2).alias("LifetimeRevenue"),
            F.round(F.avg("Revenue"), 2).alias("AvgOrderValue"),
            F.round(F.avg("Profit"),  2).alias("AvgProfit"),
        )
        .withColumn(
            "Segment",
            F.when(F.col("LifetimeRevenue") >= 10000, F.lit("Platinum"))
             .when(F.col("LifetimeRevenue") >= 5000,  F.lit("Gold"))
             .when(F.col("LifetimeRevenue") >= 1000,  F.lit("Silver"))
             .otherwise(F.lit("Bronze")),
        )
    )

    segment_order = F.create_map(
        F.lit("Platinum"), F.lit(1),
        F.lit("Gold"),     F.lit(2),
        F.lit("Silver"),   F.lit(3),
        F.lit("Bronze"),   F.lit(4),
    )

    return (
        customer_revenue
        .groupBy("Segment")
        .agg(
            F.count("CustomerKey")              .alias("CustomerCount"),
            F.round(F.sum("LifetimeRevenue"), 2).alias("TotalRevenue"),
            F.round(F.avg("AvgOrderValue"),   2).alias("AvgOrderValue"),
            F.round(F.avg("AvgProfit"),       2).alias("AvgProfit"),
        )
        .withColumn("SortOrder", segment_order[F.col("Segment")])
        .orderBy("SortOrder")
        .drop("SortOrder")
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.8 · `build_sales_forecasting_base()`
# MAGIC
# MAGIC Historical monthly series for ML / forecasting models.
# MAGIC Join key: `sales.OrderDateKey` → `calendar.CalendarKey`

# COMMAND ----------

def build_sales_forecasting_base() -> DataFrame:
    """
    Build gold.sales_forecasting_base — historical monthly data for forecasting.

    Joins silver.sales with silver.calendar on OrderDateKey = CalendarKey,
    groups by calendar month. Ordered by Year / Month for easy time-series use.
    """
    cal = silver_calendar.select(
        F.col("CalendarKey"),
        F.col("CalendarDate").alias("Date"),
        F.col("CalendarYear").alias("Year"),
        F.col("MonthNumberOfYear").alias("Month"),
        F.col("YearMonth"),
    )

    return (
        silver_sales
        .join(
            cal,
            silver_sales["OrderDateKey"] == cal["CalendarKey"],
            how="left",
        )
        .groupBy("Date", "Year", "Month", "YearMonth")
        .agg(
            F.round(F.sum("Revenue"), 2).alias("Revenue"),
            F.round(F.sum("Profit"),  2).alias("Profit"),
            F.countDistinct("OrderNumber").alias("OrderCount"),
        )
        .orderBy("Year", "Month")
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.9 · `build_executive_summary()`
# MAGIC
# MAGIC Single combined narrative summary row for executive reporting.

# COMMAND ----------

def build_executive_summary() -> DataFrame:
    """
    Build gold.executive_summary — combined executive narrative summary.

    Derives top territory, top product, highest revenue month, and highest
    revenue quarter — all in a single summary row.
    Note: silver.calendar uses `Quarter` (not `CalendarQuarter`).
    """
    # Calendar join — use `Quarter` which is the actual silver.calendar column name
    cal = silver_calendar.select(
        F.col("CalendarKey"),
        F.col("CalendarYear"),
        F.col("Quarter"),          # renamed in Silver from CalendarQuarter
        F.col("MonthNumberOfYear"),
        F.col("MonthName"),
        F.col("YearMonth"),
    )

    sales_cal = (
        silver_sales
        .join(
            cal,
            silver_sales["OrderDateKey"] == cal["CalendarKey"],
            how="left",
        )
    )

    global_agg = silver_sales.agg(
        F.round(F.sum("Revenue"), 2).alias("TotalRevenue"),
        F.round(F.sum("Profit"),  2).alias("TotalProfit"),
        F.countDistinct("CustomerKey").alias("CustomerCount"),
        F.countDistinct("ProductKey") .alias("ProductCount"),
    ).first()

    top_territory = (
        silver_sales
        .join(
            silver_sales_territory.select("TerritoryID", "Country"),
            silver_sales["SalesTerritoryKey"] == silver_sales_territory["TerritoryID"],
            how="left",
        )
        .groupBy("Country")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .first()["Country"]
    )

    top_product = (
        silver_sales
        .join(
            silver_product.select("ProductID", "ProductName"),
            silver_sales["ProductKey"] == silver_product["ProductID"],
            how="left",
        )
        .groupBy("ProductName")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .first()["ProductName"]
    )

    top_month = (
        sales_cal
        .groupBy("YearMonth")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .first()["YearMonth"]
    )

    top_quarter = (
        sales_cal
        .groupBy("CalendarYear", "Quarter")
        .agg(F.sum("Revenue").alias("Rev"))
        .orderBy(F.col("Rev").desc())
        .limit(1)
        .select(
            F.concat(
                F.col("CalendarYear").cast("string"),
                F.lit("-Q"),
                F.col("Quarter").cast("string"),
            ).alias("QuarterLabel")
        )
        .first()["QuarterLabel"]
    )

    schema = StructType([
        StructField("TotalRevenue",          DoubleType(), True),
        StructField("TotalProfit",           DoubleType(), True),
        StructField("CustomerCount",         LongType(),   True),
        StructField("ProductCount",          LongType(),   True),
        StructField("TopTerritory",          StringType(), True),
        StructField("TopProduct",            StringType(), True),
        StructField("HighestRevenueMonth",   StringType(), True),
        StructField("HighestRevenueQuarter", StringType(), True),
    ])

    summary_row = [(
        float(global_agg["TotalRevenue"]),
        float(global_agg["TotalProfit"]),
        int(global_agg["CustomerCount"]),
        int(global_agg["ProductCount"]),
        str(top_territory),
        str(top_product),
        str(top_month),
        str(top_quarter),
    )]

    return spark.createDataFrame(summary_row, schema=schema)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4.10 · `build_metadata_catalog()`
# MAGIC
# MAGIC AI-readable metadata table for the Enterprise Change Orchestrator.
# MAGIC Runs last so it captures live row counts from the metrics registry.

# COMMAND ----------

def build_metadata_catalog() -> DataFrame:
    """
    Build gold.metadata_catalog — structured metadata for every Gold table.

    Designed to be consumed by the Enterprise Change Orchestrator so the AI
    application can understand the business data landscape, lineage, ownership,
    and classification of every Gold asset.

    Must run last in the pipeline so _metrics_registry contains all row counts.
    """
    refresh_ts_str = REFRESH_TS.strftime("%Y-%m-%d %H:%M:%S")
    metrics_map    = {m.target_table: m.row_count for m in _metrics_registry}

    def rc(table: str) -> int:
        return metrics_map.get(f"{TGT_SCHEMA}.{table}", 0)

    rows = [
        ("gold.sales_dashboard",         "Sales",    "OrderNumber",  rc("sales_dashboard"),         refresh_ts_str, "silver.sales,silver.customer,silver.product,silver.calendar,silver.sales_territory", "Fully denormalised fact table for Power BI reporting",                    "Data Engineering", "Internal"),
        ("gold.customer_360",            "Customer", "CustomerID",   rc("customer_360"),             refresh_ts_str, "silver.customer,silver.sales,silver.sales_territory",                               "Unified customer profile with lifetime metrics and segmentation",          "Data Engineering", "Internal"),
        ("gold.product_performance",     "Product",  "ProductID",    rc("product_performance"),      refresh_ts_str, "silver.product,silver.sales",                                                       "Product analytics with revenue, profit and performance rankings",          "Data Engineering", "Internal"),
        ("gold.territory_performance",   "Sales",    "TerritoryID",  rc("territory_performance"),    refresh_ts_str, "silver.sales_territory,silver.sales",                                               "Geographical revenue and profit analysis by territory",                    "Data Engineering", "Internal"),
        ("gold.monthly_sales",           "Sales",    "YearMonth",    rc("monthly_sales"),            refresh_ts_str, "silver.sales,silver.calendar",                                                      "Monthly aggregated sales trends for time-series dashboards",               "Data Engineering", "Internal"),
        ("gold.executive_kpi",           "Executive","N/A",          rc("executive_kpi"),            refresh_ts_str, "silver.sales,silver.sales_territory,silver.product",                                "Single-row global KPI snapshot for executive dashboards",                 "Data Engineering", "Confidential"),
        ("gold.customer_segmentation",   "Customer", "Segment",      rc("customer_segmentation"),    refresh_ts_str, "silver.sales",                                                                      "Customer revenue segmentation: Platinum/Gold/Silver/Bronze",               "Data Engineering", "Internal"),
        ("gold.sales_forecasting_base",  "Sales",    "YearMonth",    rc("sales_forecasting_base"),   refresh_ts_str, "silver.sales,silver.calendar",                                                      "Historical monthly series for ML forecasting and trend analysis",          "Data Engineering", "Internal"),
        ("gold.executive_summary",       "Executive","N/A",          rc("executive_summary"),        refresh_ts_str, "silver.sales,silver.sales_territory,silver.product,silver.calendar",                "Combined executive narrative summary with top-line business metrics",      "Data Engineering", "Confidential"),
        ("gold.metadata_catalog",        "Metadata", "TableName",    10,                             refresh_ts_str, "gold.*",                                                                            "AI-readable metadata catalog for Enterprise Change Orchestrator",          "Data Engineering", "Internal"),
    ]

    schema = StructType([
        StructField("TableName",           StringType(),  True),
        StructField("BusinessDomain",      StringType(),  True),
        StructField("PrimaryKey",          StringType(),  True),
        StructField("RecordCount",         IntegerType(), True),
        StructField("RefreshTimestamp",    StringType(),  True),
        StructField("SourceTables",        StringType(),  True),
        StructField("BusinessDescription", StringType(),  True),
        StructField("Owner",               StringType(),  True),
        StructField("DataClassification",  StringType(),  True),
    ])

    return spark.createDataFrame(rows, schema=schema)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Publishing Pipeline
# MAGIC
# MAGIC Executes every Gold build function in dependency order.
# MAGIC `metadata_catalog` runs last so it captures live row counts.

# COMMAND ----------

PIPELINE: list[tuple[str, Callable]] = [
    (f"{TGT_SCHEMA}.sales_dashboard",        build_sales_dashboard),
    (f"{TGT_SCHEMA}.customer_360",           build_customer_360),
    (f"{TGT_SCHEMA}.product_performance",    build_product_performance),
    (f"{TGT_SCHEMA}.territory_performance",  build_territory_performance),
    (f"{TGT_SCHEMA}.monthly_sales",          build_monthly_sales),
    (f"{TGT_SCHEMA}.executive_kpi",          build_executive_kpi),
    (f"{TGT_SCHEMA}.customer_segmentation",  build_customer_segmentation),
    (f"{TGT_SCHEMA}.sales_forecasting_base", build_sales_forecasting_base),
    (f"{TGT_SCHEMA}.executive_summary",      build_executive_summary),
    (f"{TGT_SCHEMA}.metadata_catalog",       build_metadata_catalog),  # must be last
]

pipeline_start = time.perf_counter()

results = [publish_table(tgt, fn) for tgt, fn in PIPELINE]

pipeline_elapsed = time.perf_counter() - pipeline_start

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Publishing Summary

# COMMAND ----------

successes = [m for m in results if m.status == "SUCCESS"]
failures  = [m for m in results if m.status == "FAILED"]

print("\n" + "═" * 70)
print("  GOLD PUBLISHING SUMMARY")
print("═" * 70)
print(f"  {'Table':<40} {'Rows':>8}  {'Time':>7}  Status")
print(f"  {'─' * 40} {'─' * 8}  {'─' * 7}  ──────")
for m in results:
    status_icon = "✅" if m.status == "SUCCESS" else "❌"
    print(f"  {m.target_table:<40} {m.row_count:>8,}  {m.elapsed_sec:>6.2f}s  {status_icon}")

print(f"  {'─' * 40} {'─' * 8}  {'─' * 7}  ──────")
total_rows = sum(m.row_count for m in successes)
print(f"  {'TOTAL':<40} {total_rows:>8,}  {pipeline_elapsed:>6.2f}s")
print("═" * 70)

if failures:
    print(f"\n  ⚠️  {len(failures)} table(s) FAILED:")
    for m in failures:
        print(f"     • {m.target_table}: {m.error_msg}")
    raise RuntimeError(
        f"Gold publishing completed with {len(failures)} failure(s). "
        "See the summary above for details."
    )

print("\n  ✅  All Gold Tables Successfully Published")
print("═" * 70 + "\n")

# COMMAND ----------

