CREATE TABLE IF NOT EXISTS `team2-robotdb-history` (
  event_id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  endpoint VARCHAR(255) NOT NULL,
  node_id VARCHAR(512) NOT NULL,
  received_at DATETIME(6) NOT NULL,
  source_at DATETIME(6) NULL,
  server_at DATETIME(6) NULL,
  status_code BIGINT UNSIGNED NOT NULL,
  variant_type VARCHAR(32) NOT NULL,
  value_json LONGTEXT NOT NULL,
  collection_mode VARCHAR(16) NOT NULL DEFAULT 'LEGACY',
  INDEX ix_node_time (node_id(191), received_at),
  INDEX ix_received (received_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `team2-vision-history` LIKE `team2-robotdb-history`;

CREATE TABLE IF NOT EXISTS `team2-robotdb` (
  node_id VARCHAR(512) NOT NULL PRIMARY KEY,
  tag_name VARCHAR(255) NOT NULL,
  value_text LONGTEXT NOT NULL,
  value_number DOUBLE NULL,
  variant_type VARCHAR(32) NOT NULL,
  status_code BIGINT UNSIGNED NOT NULL,
  source_at DATETIME(6) NULL,
  server_at DATETIME(6) NULL,
  received_at DATETIME(6) NOT NULL,
  event_id CHAR(36) CHARACTER SET ascii NOT NULL,
  collection_mode VARCHAR(16) NOT NULL DEFAULT 'LEGACY',
  INDEX ix_tag_name (tag_name(191))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `team2-vision` LIKE `team2-robotdb`;

CREATE TABLE IF NOT EXISTS `team2-order` LIKE `team2-robotdb`;

CREATE TABLE IF NOT EXISTS `team2-order-history` LIKE `team2-robotdb-history`;

CREATE TABLE IF NOT EXISTS `team2-plcstatus` LIKE `team2-robotdb`;

CREATE TABLE IF NOT EXISTS `team2-plcstatus-history` LIKE `team2-robotdb-history`;
