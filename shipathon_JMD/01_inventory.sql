-- ============================================================
-- SCHEMA: inventory
-- Tables: products, warehouses, inventory_levels,
--         stock_movements, product_pricing, price_history
-- ============================================================


-- ──────────────────────────────────────────────────────────────
-- PRODUCTS
-- Master catalog of every product the company sells or stocks.
-- Embedding enables semantic search: "wireless accessories" → finds Bluetooth headphones.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.products (
    product_id                  SERIAL PRIMARY KEY,
    stock_keeping_unit          VARCHAR(50) UNIQUE NOT NULL,
    product_name                TEXT NOT NULL,
    product_description         TEXT,
    category                    VARCHAR(100),
    subcategory                 VARCHAR(100),
    unit_of_measure             VARCHAR(20) DEFAULT 'pcs',
    is_active                   BOOLEAN DEFAULT true,
    created_at                  TIMESTAMPTZ DEFAULT now(),

    -- BGE-M3 1024d embedding of (product_name + product_description + category)
    product_name_embedding      VECTOR(1024),

    -- Auto-generated tsvector for keyword search
    full_text_search_vector     tsvector
);

-- HNSW vector index for semantic product search
CREATE INDEX index_products_embedding ON inventory.products
    USING hnsw (product_name_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index for full-text keyword search
CREATE INDEX index_products_full_text ON inventory.products
    USING gin(full_text_search_vector);

-- B-tree indexes for filtered queries
CREATE INDEX index_products_category ON inventory.products (category, subcategory);

-- Trigger: auto-populate full_text_search_vector on insert/update
CREATE FUNCTION inventory.update_product_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.full_text_search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.product_name, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.product_description, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.category, '')), 'C') ||
        setweight(to_tsvector('english', COALESCE(NEW.subcategory, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_product_search_vector
    BEFORE INSERT OR UPDATE ON inventory.products
    FOR EACH ROW EXECUTE FUNCTION inventory.update_product_search_vector();


-- ──────────────────────────────────────────────────────────────
-- WAREHOUSES
-- Physical locations where inventory is stored.
-- Also covers stores and office stockrooms.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.warehouses (
    warehouse_id                SERIAL PRIMARY KEY,
    warehouse_name              VARCHAR(200) NOT NULL,
    city                        VARCHAR(100),
    state                       VARCHAR(100),
    warehouse_type              VARCHAR(50) CHECK (warehouse_type IN (
                                    'warehouse', 'store', 'office'
                                )),
    capacity_square_feet        INTEGER
);


-- ──────────────────────────────────────────────────────────────
-- INVENTORY LEVELS
-- The hot table: current stock position per product per warehouse.
-- Updated on every sale, receipt, transfer, or adjustment.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.inventory_levels (
    product_id                  INTEGER REFERENCES inventory.products(product_id),
    warehouse_id                INTEGER REFERENCES inventory.warehouses(warehouse_id),
    current_quantity            INTEGER NOT NULL DEFAULT 0,
    safety_stock_quantity       INTEGER NOT NULL DEFAULT 0,
    reorder_point_quantity      INTEGER NOT NULL DEFAULT 0,
    reorder_order_quantity      INTEGER NOT NULL DEFAULT 0,
    maximum_stock_quantity      INTEGER,
    last_updated_at             TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (product_id, warehouse_id)
);

-- Partial index: only rows that need attention (below safety stock)
CREATE INDEX index_inventory_below_safety_stock ON inventory.inventory_levels (warehouse_id)
    WHERE current_quantity < safety_stock_quantity;

-- Partial index: items needing reorder
CREATE INDEX index_inventory_below_reorder_point ON inventory.inventory_levels (warehouse_id)
    WHERE current_quantity < reorder_point_quantity;


-- ──────────────────────────────────────────────────────────────
-- STOCK MOVEMENTS
-- Immutable log of every stock event (inbound, outbound, transfer, adjustment, return).
-- Each row changes inventory_levels by +/- quantity.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.stock_movements (
    movement_id                 BIGSERIAL PRIMARY KEY,
    product_id                  INTEGER REFERENCES inventory.products(product_id),
    warehouse_id                INTEGER REFERENCES inventory.warehouses(warehouse_id),
    movement_type               VARCHAR(20) CHECK (movement_type IN (
                                    'inbound', 'outbound', 'transfer_in',
                                    'transfer_out', 'adjustment', 'return'
                                )),
    quantity                    INTEGER NOT NULL,
    reference_identifier        VARCHAR(100),       -- PO number, sales order, transfer ID
    unit_cost_at_movement       DECIMAL(12,2),      -- cost per unit at time of movement
    moved_at                    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX index_movements_by_date ON inventory.stock_movements (moved_at DESC);
CREATE INDEX index_movements_by_product ON inventory.stock_movements (product_id, moved_at DESC);


-- ──────────────────────────────────────────────────────────────
-- PRODUCT PRICING
-- Detailed pricing analysis per product: our prices, competitor prices,
-- demand signals, sale-day performance, and margin calculations.
-- Embedding enables semantic queries: "high margin low competition products"
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.product_pricing (
    pricing_id                          SERIAL PRIMARY KEY,
    product_id                          INTEGER REFERENCES inventory.products(product_id) UNIQUE,

    -- Our pricing
    cost_price_per_unit                 DECIMAL(12,2),
    base_selling_price_per_unit         DECIMAL(12,2),
    current_selling_price_per_unit      DECIMAL(12,2),
    floor_price_per_unit                DECIMAL(12,2),      -- minimum we will sell at
    ceiling_price_per_unit              DECIMAL(12,2),      -- maximum the market will bear
    margin_percentage                   DECIMAL(5,2) GENERATED ALWAYS AS (
        CASE WHEN current_selling_price_per_unit > 0
             THEN ((current_selling_price_per_unit - cost_price_per_unit)
                   / current_selling_price_per_unit) * 100
             ELSE 0 END
    ) STORED,

    -- Competitor intelligence
    competitor_minimum_price            DECIMAL(12,2),
    competitor_maximum_price            DECIMAL(12,2),
    competitor_average_price            DECIMAL(12,2),
    number_of_competitors_tracked       INTEGER DEFAULT 0,
    last_competitor_check_date          DATE,

    -- Demand signals
    demand_elasticity_coefficient       DECIMAL(5,3),       -- % demand change per 1% price change
    average_daily_units_normal_day      DECIMAL(10,2),
    average_daily_units_sale_day        DECIMAL(10,2),
    typical_sale_discount_price         DECIMAL(12,2),
    last_sale_event_date                DATE,

    -- BGE-M3 embedding of pricing context summary
    pricing_context_embedding           VECTOR(1024),

    updated_at                          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX index_pricing_embedding ON inventory.product_pricing
    USING hnsw (pricing_context_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX index_pricing_by_margin ON inventory.product_pricing (margin_percentage DESC);


-- ──────────────────────────────────────────────────────────────
-- PRICE HISTORY
-- Timestamped log of price changes for trend analysis.
-- Tracks our prices and competitor prices over time.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE inventory.price_history (
    history_id                  BIGSERIAL PRIMARY KEY,
    product_id                  INTEGER REFERENCES inventory.products(product_id),
    price_amount                DECIMAL(12,2),
    price_type                  VARCHAR(20) CHECK (price_type IN (
                                    'our_price', 'cost_price', 'competitor_average',
                                    'competitor_minimum', 'market_lowest'
                                )),
    recorded_at                 TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX index_price_history_by_product ON inventory.price_history
    (product_id, recorded_at DESC);
