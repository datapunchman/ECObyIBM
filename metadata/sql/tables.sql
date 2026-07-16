-- =============================================================================
-- Enterprise Metadata Engine — SQL Layer
-- tables.sql: Physical table definitions for the customer_360 reporting schema
-- =============================================================================

-- --------------------------------------------------------------------------
-- Customer dimension
-- --------------------------------------------------------------------------
CREATE TABLE dbo.dim_customer (
    customer_key        INT             NOT NULL,
    customer_id         VARCHAR(20)     NOT NULL,
    first_name          VARCHAR(50)     NOT NULL,
    last_name           VARCHAR(50)     NOT NULL,
    email               VARCHAR(100)    NULL,
    phone               VARCHAR(20)     NULL,
    country_region_code VARCHAR(10)     NULL,
    territory_key       INT             NULL,
    birth_date          DATE            NULL,
    gender              CHAR(1)         NULL,
    yearly_income       DECIMAL(18, 2)  NULL,
    total_children      TINYINT         NULL,
    created_at          DATETIME        NOT NULL DEFAULT GETDATE(),
    updated_at          DATETIME        NULL
);

-- --------------------------------------------------------------------------
-- Product dimension
-- --------------------------------------------------------------------------
CREATE TABLE dbo.dim_product (
    product_key         INT             NOT NULL,
    product_id          VARCHAR(20)     NOT NULL,
    product_name        VARCHAR(100)    NOT NULL,
    category            VARCHAR(50)     NULL,
    subcategory         VARCHAR(50)     NULL,
    list_price          DECIMAL(18, 2)  NULL,
    standard_cost       DECIMAL(18, 2)  NULL,
    color               VARCHAR(20)     NULL,
    size                VARCHAR(10)     NULL,
    weight              DECIMAL(8, 2)   NULL,
    model_name          VARCHAR(100)    NULL,
    created_at          DATETIME        NOT NULL DEFAULT GETDATE()
);

-- --------------------------------------------------------------------------
-- Sales territory dimension
-- --------------------------------------------------------------------------
CREATE TABLE dbo.dim_sales_territory (
    territory_key       INT             NOT NULL,
    territory_name      VARCHAR(100)    NOT NULL,
    country             VARCHAR(50)     NOT NULL,
    region              VARCHAR(50)     NULL,
    group_name          VARCHAR(50)     NULL
);

-- --------------------------------------------------------------------------
-- Date dimension
-- --------------------------------------------------------------------------
CREATE TABLE dbo.dim_date (
    date_key            INT             NOT NULL,
    full_date           DATE            NOT NULL,
    year                SMALLINT        NOT NULL,
    quarter             TINYINT         NOT NULL,
    month               TINYINT         NOT NULL,
    month_name          VARCHAR(20)     NOT NULL,
    day_of_week         TINYINT         NOT NULL,
    week_of_year        TINYINT         NOT NULL,
    is_weekend          BIT             NOT NULL DEFAULT 0,
    fiscal_year         SMALLINT        NULL,
    fiscal_quarter      TINYINT         NULL
);

-- --------------------------------------------------------------------------
-- Internet sales fact
-- --------------------------------------------------------------------------
CREATE TABLE dbo.fact_internet_sales (
    sales_order_key     INT             NOT NULL,
    order_date_key      INT             NOT NULL,
    due_date_key        INT             NOT NULL,
    ship_date_key       INT             NOT NULL,
    customer_key        INT             NOT NULL,
    product_key         INT             NOT NULL,
    territory_key       INT             NOT NULL,
    order_quantity      SMALLINT        NOT NULL,
    unit_price          DECIMAL(18, 2)  NOT NULL,
    unit_price_discount DECIMAL(18, 4)  NULL,
    extended_amount     DECIMAL(18, 2)  NOT NULL,
    sales_amount        DECIMAL(18, 2)  NOT NULL,
    tax_amount          DECIMAL(18, 2)  NULL,
    freight             DECIMAL(18, 2)  NULL,
    order_date          DATE            NULL
);
