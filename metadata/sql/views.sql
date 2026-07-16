-- =============================================================================
-- Enterprise Metadata Engine — SQL Layer
-- views.sql: Reporting views for customer_360 and sales analytics
-- =============================================================================

-- --------------------------------------------------------------------------
-- Customer 360 view — full customer profile with territory
-- --------------------------------------------------------------------------
CREATE VIEW dbo.vw_customer_360 AS
SELECT
    c.customer_key,
    c.customer_id,
    c.first_name,
    c.last_name,
    c.email,
    c.country_region_code,
    c.yearly_income,
    c.total_children,
    t.territory_name,
    t.country,
    t.region
FROM dbo.dim_customer AS c
LEFT JOIN dbo.dim_sales_territory AS t
    ON c.territory_key = t.territory_key;

-- --------------------------------------------------------------------------
-- Monthly sales summary — aggregated sales by month and territory
-- --------------------------------------------------------------------------
CREATE VIEW dbo.vw_monthly_sales AS
SELECT
    d.year,
    d.month,
    d.month_name,
    t.territory_name,
    t.country,
    COUNT(DISTINCT s.sales_order_key)   AS order_count,
    SUM(s.order_quantity)               AS total_units_sold,
    SUM(s.sales_amount)                 AS total_sales_amount,
    SUM(s.tax_amount)                   AS total_tax,
    AVG(s.sales_amount)                 AS avg_order_value
FROM dbo.fact_internet_sales AS s
INNER JOIN dbo.dim_date AS d
    ON s.order_date_key = d.date_key
INNER JOIN dbo.dim_sales_territory AS t
    ON s.territory_key = t.territory_key
GROUP BY
    d.year, d.month, d.month_name,
    t.territory_name, t.country;

-- --------------------------------------------------------------------------
-- Territory performance view — revenue and order KPIs by territory
-- --------------------------------------------------------------------------
CREATE VIEW dbo.vw_territory_performance AS
SELECT
    t.territory_key,
    t.territory_name,
    t.country,
    t.region,
    t.group_name,
    COUNT(DISTINCT s.sales_order_key)   AS total_orders,
    COUNT(DISTINCT s.customer_key)      AS unique_customers,
    SUM(s.sales_amount)                 AS total_revenue,
    AVG(s.sales_amount)                 AS avg_order_value
FROM dbo.fact_internet_sales AS s
INNER JOIN dbo.dim_sales_territory AS t
    ON s.territory_key = t.territory_key
GROUP BY
    t.territory_key, t.territory_name,
    t.country, t.region, t.group_name;

-- --------------------------------------------------------------------------
-- Product sales view — per-product sales performance
-- --------------------------------------------------------------------------
CREATE VIEW dbo.vw_product_sales AS
SELECT
    p.product_key,
    p.product_name,
    p.category,
    p.subcategory,
    SUM(s.order_quantity)   AS total_units_sold,
    SUM(s.sales_amount)     AS total_revenue,
    AVG(s.unit_price)       AS avg_unit_price
FROM dbo.fact_internet_sales AS s
INNER JOIN dbo.dim_product AS p
    ON s.product_key = p.product_key
GROUP BY
    p.product_key, p.product_name,
    p.category, p.subcategory;
