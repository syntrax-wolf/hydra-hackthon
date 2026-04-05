-- ============================================================
-- SCHEMA: finance
-- Tables: offices, sales_transactions
-- Views:  mv_daily_office_profit_loss, mv_daily_product_revenue
-- ============================================================


-- ──────────────────────────────────────────────────────────────
-- OFFICES
-- Single combined table: identity + one-time capital + monthly opex + working capital.
-- No granular cost breakdowns — three impactful financial numbers per office.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE finance.offices (
    office_id                           SERIAL PRIMARY KEY,

    -- Identity
    office_name                         VARCHAR(200) NOT NULL,
    city                                VARCHAR(100),
    state                               VARCHAR(100),
    office_type                         VARCHAR(50) CHECK (office_type IN (
                                            'headquarters', 'branch', 'factory',
                                            'warehouse', 'store'
                                        )),
    date_opened                         DATE,
    operational_status                  VARCHAR(20) DEFAULT 'active',

    -- One-time capital invested (cumulative total across all categories)
    one_time_capital_invested           DECIMAL(14,2) DEFAULT 0,

    -- Monthly operating expense (single rolled-up number)
    monthly_operating_expense           DECIMAL(14,2) DEFAULT 0,
    operating_expense_period_month      DATE,           -- first-of-month this expense is for

    -- Working capital snapshot (updated monthly)
    accounts_receivable_amount          DECIMAL(14,2) DEFAULT 0,    -- money owed TO us
    inventory_value_amount              DECIMAL(14,2) DEFAULT 0,    -- stock value at cost
    cash_on_hand_amount                 DECIMAL(14,2) DEFAULT 0,    -- liquid cash
    accounts_payable_amount             DECIMAL(14,2) DEFAULT 0,    -- money WE owe
    net_working_capital                 DECIMAL(14,2) GENERATED ALWAYS AS (
        accounts_receivable_amount + inventory_value_amount + cash_on_hand_amount
        - accounts_payable_amount
    ) STORED,
    working_capital_period_month        DATE,           -- first-of-month this snapshot is for

    last_updated_at                     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX index_offices_by_city ON finance.offices (city);
CREATE INDEX index_offices_by_status ON finance.offices (operational_status);


-- ──────────────────────────────────────────────────────────────
-- SALES TRANSACTIONS
-- Individual sale records. Every purchase event.
-- Profit is auto-computed as a generated column.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE finance.sales_transactions (
    transaction_id                      BIGSERIAL PRIMARY KEY,
    office_id                           INTEGER REFERENCES finance.offices(office_id),
    product_id                          INTEGER,        -- references inventory.products
    customer_name                       VARCHAR(200),   -- denormalised (no customer table)
    quantity_sold                       INTEGER,
    cost_price_per_unit                 DECIMAL(12,2),
    selling_price_per_unit              DECIMAL(12,2),
    total_selling_amount                DECIMAL(14,2),  -- quantity × selling_price_per_unit
    total_cost_amount                   DECIMAL(14,2),  -- quantity × cost_price_per_unit
    discount_amount                     DECIMAL(12,2) DEFAULT 0,
    profit_amount                       DECIMAL(14,2) GENERATED ALWAYS AS (
        total_selling_amount - discount_amount - total_cost_amount
    ) STORED,
    payment_method                      VARCHAR(30),
    is_sale_day                         BOOLEAN DEFAULT false,  -- was this during a promotion?
    transaction_date                    DATE NOT NULL,
    created_at                          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX index_transaction_by_office_date ON finance.sales_transactions
    (office_id, transaction_date DESC);
CREATE INDEX index_transaction_by_product ON finance.sales_transactions
    (product_id, transaction_date DESC);
CREATE INDEX index_transaction_by_date ON finance.sales_transactions
    (transaction_date DESC);


-- ──────────────────────────────────────────────────────────────
-- MATERIALIZED VIEW: Daily Office Profit & Loss
-- One row per office per day. Aggregates all transactions.
-- Replaces ClickHouse — works perfectly at hackathon scale.
-- ──────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW finance.mv_daily_office_profit_loss AS
SELECT
    st.transaction_date                                         AS date,
    o.office_id,
    o.office_name,
    o.city,

    -- Revenue
    SUM(st.total_selling_amount)                                AS gross_revenue,
    SUM(st.discount_amount)                                     AS total_discounts,
    SUM(st.total_selling_amount - st.discount_amount)           AS net_revenue,

    -- Cost
    SUM(st.total_cost_amount)                                   AS total_cost_of_goods_sold,

    -- Profit
    SUM(st.profit_amount)                                       AS gross_profit,
    ROUND(
        CASE WHEN SUM(st.total_selling_amount - st.discount_amount) > 0
             THEN SUM(st.profit_amount)
                  / SUM(st.total_selling_amount - st.discount_amount) * 100
             ELSE 0 END, 2
    )                                                           AS gross_margin_percentage,

    -- Volume
    COUNT(*)                                                    AS total_transaction_count,
    SUM(st.quantity_sold)                                       AS total_units_sold,

    -- Sale day vs normal day split
    SUM(CASE WHEN st.is_sale_day THEN st.quantity_sold ELSE 0 END)
                                                                AS units_sold_on_sale_days,
    SUM(CASE WHEN NOT st.is_sale_day THEN st.quantity_sold ELSE 0 END)
                                                                AS units_sold_on_normal_days,

    -- Daily operating expense estimate (monthly / 30)
    ROUND(o.monthly_operating_expense / 30.0, 2)                AS estimated_daily_operating_expense

FROM finance.sales_transactions st
JOIN finance.offices o ON o.office_id = st.office_id
GROUP BY
    st.transaction_date,
    o.office_id, o.office_name, o.city,
    o.monthly_operating_expense;

CREATE UNIQUE INDEX index_mv_daily_office_pnl
    ON finance.mv_daily_office_profit_loss (office_id, date);


-- ──────────────────────────────────────────────────────────────
-- MATERIALIZED VIEW: Daily Product Revenue Per Office (DETAILED)
-- Everything about a product in a particular office on a given day.
-- CP, SP, profit, units sold, inventory position, sale-day breakdown.
-- ──────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW finance.mv_daily_product_revenue AS
SELECT
    st.transaction_date                                         AS date,
    st.office_id,
    o.office_name,
    o.city                                                      AS office_city,
    st.product_id,
    p.stock_keeping_unit,
    p.product_name,
    p.category                                                  AS product_category,
    p.subcategory                                               AS product_subcategory,

    -- Pricing per unit
    MIN(st.cost_price_per_unit)                                 AS cost_price_per_unit,
    MIN(st.selling_price_per_unit)                              AS selling_price_per_unit,
    ROUND(AVG(st.selling_price_per_unit
              - st.cost_price_per_unit), 2)                     AS average_profit_per_unit,

    -- Sales volume
    SUM(st.quantity_sold)                                       AS total_units_sold,
    COUNT(*)                                                    AS number_of_transactions,

    -- Revenue & profit
    SUM(st.total_selling_amount)                                AS gross_sales_amount,
    SUM(st.discount_amount)                                     AS total_discount_amount,
    SUM(st.total_selling_amount - st.discount_amount)           AS net_sales_amount,
    SUM(st.total_cost_amount)                                   AS total_cost_amount,
    SUM(st.profit_amount)                                       AS total_profit_amount,
    ROUND(
        CASE WHEN SUM(st.total_selling_amount - st.discount_amount) > 0
             THEN SUM(st.profit_amount)
                  / SUM(st.total_selling_amount - st.discount_amount) * 100
             ELSE 0 END, 2
    )                                                           AS profit_margin_percentage,

    -- Sale day analysis
    BOOL_OR(st.is_sale_day)                                     AS had_sale_event,
    SUM(CASE WHEN st.is_sale_day
             THEN st.quantity_sold ELSE 0 END)                  AS units_sold_on_sale_days,
    SUM(CASE WHEN NOT st.is_sale_day
             THEN st.quantity_sold ELSE 0 END)                  AS units_sold_on_normal_days,
    SUM(CASE WHEN st.is_sale_day
             THEN st.profit_amount ELSE 0 END)                  AS profit_on_sale_days,
    SUM(CASE WHEN NOT st.is_sale_day
             THEN st.profit_amount ELSE 0 END)                  AS profit_on_normal_days,

    -- Inventory position (current snapshot from inventory_levels)
    il.current_quantity                                          AS units_currently_in_inventory,
    il.safety_stock_quantity,
    CASE WHEN il.current_quantity < il.safety_stock_quantity
         THEN true ELSE false END                               AS is_below_safety_stock

FROM finance.sales_transactions st
JOIN finance.offices o
    ON o.office_id = st.office_id
JOIN inventory.products p
    ON p.product_id = st.product_id
LEFT JOIN inventory.inventory_levels il
    ON il.product_id = st.product_id
    AND il.warehouse_id = st.office_id      -- assumes office_id maps to warehouse_id
GROUP BY
    st.transaction_date,
    st.office_id, o.office_name, o.city,
    st.product_id, p.stock_keeping_unit, p.product_name,
    p.category, p.subcategory,
    il.current_quantity, il.safety_stock_quantity;

CREATE INDEX index_mv_product_revenue_by_office_product
    ON finance.mv_daily_product_revenue (office_id, product_id, date);
CREATE INDEX index_mv_product_revenue_by_category
    ON finance.mv_daily_product_revenue (product_category, date);


-- ──────────────────────────────────────────────────────────────
-- REFRESH FUNCTION
-- Call daily or on-demand before analytical queries.
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION finance.refresh_all_materialized_views()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY finance.mv_daily_office_profit_loss;
    REFRESH MATERIALIZED VIEW CONCURRENTLY finance.mv_daily_product_revenue;
END;
$$ LANGUAGE plpgsql;
