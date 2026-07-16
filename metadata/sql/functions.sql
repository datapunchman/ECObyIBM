-- =============================================================================
-- Enterprise Metadata Engine — SQL Layer
-- functions.sql: Scalar and table-valued functions for customer_360 schema
-- =============================================================================

-- --------------------------------------------------------------------------
-- Calculate customer lifetime value (scalar)
-- --------------------------------------------------------------------------
CREATE FUNCTION dbo.fn_customer_lifetime_value
(
    @customer_key INT
)
RETURNS DECIMAL(18, 2)
AS
BEGIN
    DECLARE @clv DECIMAL(18, 2);
    SELECT @clv = SUM(s.sales_amount)
    FROM dbo.fact_internet_sales AS s
    WHERE s.customer_key = @customer_key;
    RETURN ISNULL(@clv, 0.00);
END;

-- --------------------------------------------------------------------------
-- Get sales for date range (table-valued)
-- --------------------------------------------------------------------------
CREATE FUNCTION dbo.fn_sales_by_date_range
(
    @start_date DATE,
    @end_date   DATE
)
RETURNS TABLE
AS
RETURN
(
    SELECT
        s.sales_order_key,
        s.order_date,
        s.customer_key,
        s.product_key,
        s.territory_key,
        s.order_quantity,
        s.sales_amount,
        d.year,
        d.month,
        d.quarter
    FROM dbo.fact_internet_sales AS s
    INNER JOIN dbo.dim_date AS d
        ON s.order_date_key = d.date_key
    WHERE d.full_date BETWEEN @start_date AND @end_date
);

-- --------------------------------------------------------------------------
-- Calculate discount percentage (scalar)
-- --------------------------------------------------------------------------
CREATE FUNCTION dbo.fn_discount_percentage
(
    @list_price    DECIMAL(18, 2),
    @actual_price  DECIMAL(18, 2)
)
RETURNS DECIMAL(5, 2)
AS
BEGIN
    IF @list_price IS NULL OR @list_price = 0
        RETURN 0.00;
    RETURN ROUND((@list_price - @actual_price) / @list_price * 100, 2);
END;
