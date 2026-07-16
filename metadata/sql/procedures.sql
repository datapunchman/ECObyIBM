-- =============================================================================
-- Enterprise Metadata Engine — SQL Layer
-- procedures.sql: Stored procedures for customer_360 reporting schema
-- =============================================================================

-- --------------------------------------------------------------------------
-- Get customer orders — retrieve all sales for a given customer
-- --------------------------------------------------------------------------
CREATE PROCEDURE dbo.usp_get_customer_orders
    @customer_id VARCHAR(20),
    @start_date  DATE = NULL,
    @end_date    DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        s.sales_order_key,
        s.order_date,
        s.order_quantity,
        s.sales_amount,
        p.product_name,
        p.category
    FROM dbo.fact_internet_sales AS s
    INNER JOIN dbo.dim_customer AS c
        ON s.customer_key = c.customer_key
    INNER JOIN dbo.dim_product AS p
        ON s.product_key = p.product_key
    WHERE c.customer_id = @customer_id
      AND (@start_date IS NULL OR s.order_date >= @start_date)
      AND (@end_date   IS NULL OR s.order_date <= @end_date)
    ORDER BY s.order_date DESC;
END;

-- --------------------------------------------------------------------------
-- Refresh territory summary — upsert territory KPIs into a summary table
-- --------------------------------------------------------------------------
CREATE PROCEDURE dbo.usp_refresh_territory_summary
    @report_year SMALLINT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        t.territory_key,
        t.territory_name,
        t.country,
        d.year,
        COUNT(DISTINCT s.sales_order_key)   AS total_orders,
        SUM(s.sales_amount)                 AS total_revenue
    FROM dbo.fact_internet_sales AS s
    INNER JOIN dbo.dim_sales_territory AS t
        ON s.territory_key = t.territory_key
    INNER JOIN dbo.dim_date AS d
        ON s.order_date_key = d.date_key
    WHERE d.year = @report_year
    GROUP BY
        t.territory_key, t.territory_name,
        t.country, d.year;
END;

-- --------------------------------------------------------------------------
-- Get top products by revenue — ranked product list for a given period
-- --------------------------------------------------------------------------
CREATE PROCEDURE dbo.usp_get_top_products
    @top_n       INT  = 10,
    @report_year SMALLINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (@top_n)
        p.product_key,
        p.product_name,
        p.category,
        SUM(s.sales_amount) AS total_revenue
    FROM dbo.fact_internet_sales AS s
    INNER JOIN dbo.dim_product AS p
        ON s.product_key = p.product_key
    INNER JOIN dbo.dim_date AS d
        ON s.order_date_key = d.date_key
    WHERE @report_year IS NULL OR d.year = @report_year
    GROUP BY p.product_key, p.product_name, p.category
    ORDER BY total_revenue DESC;
END;
