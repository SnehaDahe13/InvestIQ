-- Client-Advisor Investment Product Recommendation Engine (MySQL 8.0+)
-- Run: mysql -u root -p < schema.sql
CREATE DATABASE IF NOT EXISTS ib_reco CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ib_reco;

CREATE TABLE advisors (
  advisor_id     INT AUTO_INCREMENT PRIMARY KEY,
  full_name      VARCHAR(100) NOT NULL,
  email          VARCHAR(120) NOT NULL,
  specialization VARCHAR(60),
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_advisor_email (email)
) ENGINE=InnoDB;

CREATE TABLE clients (
  client_id              INT AUTO_INCREMENT PRIMARY KEY,
  advisor_id             INT NULL,
  full_name              VARCHAR(100) NOT NULL,
  email                  VARCHAR(120) NOT NULL,
  password_hash          VARCHAR(255) NOT NULL,
  date_of_birth          DATE NOT NULL,
  annual_income          DECIMAL(14,2) NOT NULL,
  investment_horizon_yrs TINYINT NOT NULL,
  risk_score             TINYINT NOT NULL,              -- 1 (very safe) .. 10 (very aggressive)
  risk_category          ENUM('conservative','moderate','aggressive') NOT NULL,
  created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_client_email (email),
  KEY idx_clients_advisor (advisor_id),
  KEY idx_clients_risk (risk_category, risk_score),
  CONSTRAINT chk_risk CHECK (risk_score BETWEEN 1 AND 10),
  CONSTRAINT fk_client_advisor FOREIGN KEY (advisor_id) REFERENCES advisors(advisor_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE asset_classes (
  asset_class_id INT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(50) NOT NULL,
  UNIQUE KEY uq_asset_class (name)
) ENGINE=InnoDB;

CREATE TABLE products (
  product_id          INT AUTO_INCREMENT PRIMARY KEY,
  asset_class_id      INT NOT NULL,
  symbol              VARCHAR(20) NOT NULL,
  name                VARCHAR(150) NOT NULL,
  description         TEXT,
  risk_level          TINYINT NOT NULL,                 -- 1..10
  expected_return_pct DECIMAL(5,2) NOT NULL,
  volatility_pct      DECIMAL(5,2) NOT NULL,
  expense_ratio_pct   DECIMAL(4,2) NOT NULL DEFAULT 0,
  min_investment      DECIMAL(12,2) NOT NULL DEFAULT 0,
  current_price       DECIMAL(14,4) NOT NULL,
  is_active           TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_product_symbol (symbol),
  KEY idx_prod_active_risk (is_active, risk_level),     -- candidate filtering for the recommender
  KEY idx_prod_class_risk (asset_class_id, risk_level),
  FULLTEXT KEY ft_prod_search (name, description),      -- product search box
  CONSTRAINT chk_prod_risk CHECK (risk_level BETWEEN 1 AND 10),
  CONSTRAINT fk_prod_class FOREIGN KEY (asset_class_id) REFERENCES asset_classes(asset_class_id)
) ENGINE=InnoDB;

CREATE TABLE transactions (
  txn_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
  client_id  INT NOT NULL,
  product_id INT NOT NULL,
  txn_type   ENUM('BUY','SELL') NOT NULL,
  quantity   DECIMAL(18,4) NOT NULL,
  price      DECIMAL(14,4) NOT NULL,
  txn_ts     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_txn_client_ts (client_id, txn_ts),            -- client history, newest first
  KEY idx_txn_product_type (product_id, txn_type),      -- popularity / CF matrix build
  CONSTRAINT chk_qty CHECK (quantity > 0),
  CONSTRAINT fk_txn_client FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE,
  CONSTRAINT fk_txn_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB;

-- Current positions (maintained by trigger). Composite PK doubles as the client->holdings index.
CREATE TABLE holdings (
  client_id  INT NOT NULL,
  product_id INT NOT NULL,
  quantity   DECIMAL(18,4) NOT NULL,
  avg_cost   DECIMAL(14,4) NOT NULL,
  PRIMARY KEY (client_id, product_id),
  KEY idx_hold_product (product_id),
  CONSTRAINT fk_hold_client FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE,
  CONSTRAINT fk_hold_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB;

-- Every recommendation shown + the client's reaction = training signal and audit trail
CREATE TABLE recommendations (
  reco_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
  client_id     INT NOT NULL,
  product_id    INT NOT NULL,
  model_version VARCHAR(30) NOT NULL,
  score         DECIMAL(6,4) NOT NULL,
  reason        VARCHAR(255),
  status        ENUM('SHOWN','CLICKED','ACCEPTED','DISMISSED') NOT NULL DEFAULT 'SHOWN',
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  responded_at  TIMESTAMP NULL,
  KEY idx_reco_client_time (client_id, created_at),
  KEY idx_reco_product_status (product_id, status),
  CONSTRAINT fk_reco_client FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE,
  CONSTRAINT fk_reco_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB;

CREATE TABLE model_runs (
  run_id        INT AUTO_INCREMENT PRIMARY KEY,
  model_version VARCHAR(30) NOT NULL,
  n_clients     INT,
  n_products    INT,
  trained_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Keep holdings in sync with transactions
DELIMITER //
CREATE TRIGGER trg_txn_holdings AFTER INSERT ON transactions
FOR EACH ROW
BEGIN
  IF NEW.txn_type = 'BUY' THEN
    INSERT INTO holdings (client_id, product_id, quantity, avg_cost)
    VALUES (NEW.client_id, NEW.product_id, NEW.quantity, NEW.price)
    ON DUPLICATE KEY UPDATE
      avg_cost = (avg_cost * quantity + NEW.price * NEW.quantity) / (quantity + NEW.quantity),
      quantity = quantity + NEW.quantity;
  ELSE
    UPDATE holdings SET quantity = quantity - NEW.quantity
     WHERE client_id = NEW.client_id AND product_id = NEW.product_id;
    DELETE FROM holdings
     WHERE client_id = NEW.client_id AND product_id = NEW.product_id AND quantity <= 0;
  END IF;
END//
DELIMITER ;

CREATE VIEW v_client_portfolio AS
SELECT h.client_id, p.product_id, p.symbol, p.name, ac.name AS asset_class,
       h.quantity, h.avg_cost,
       h.quantity * h.avg_cost      AS invested,
       h.quantity * p.current_price AS market_value,
       h.quantity * (p.current_price - h.avg_cost) AS pnl
FROM holdings h
JOIN products p       ON p.product_id = h.product_id
JOIN asset_classes ac ON ac.asset_class_id = p.asset_class_id;
