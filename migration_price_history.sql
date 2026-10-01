-- Run ONCE on the existing ib_reco database (after schema.sql):
--   mysql -u root -p < migration_price_history.sql
-- Then load demo prices with:  python seed_prices.py
USE ib_reco;

CREATE TABLE IF NOT EXISTS product_prices (
  price_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id   INT NOT NULL,
  price_date   DATE NOT NULL,
  open_price   DECIMAL(14,4) NOT NULL,
  high_price   DECIMAL(14,4) NOT NULL,
  low_price    DECIMAL(14,4) NOT NULL,
  close_price  DECIMAL(14,4) NOT NULL,
  volume       BIGINT NULL,                              -- NULL for NAV-priced mutual funds
  data_source  VARCHAR(30) NOT NULL DEFAULT 'DEMO_SYNTHETIC',  -- 'DEMO_SYNTHETIC' = sample data, NOT real market data
  UNIQUE KEY uq_price_product_date (product_id, price_date),   -- also serves "latest N days for a product" lookups
  CONSTRAINT chk_price_range CHECK (high_price >= low_price),
  CONSTRAINT fk_price_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
) ENGINE=InnoDB;
