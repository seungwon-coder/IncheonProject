CREATE TABLE IF NOT EXISTS mes_orders (
 order_id VARCHAR(64) PRIMARY KEY, model VARCHAR(16) NOT NULL, lamp VARCHAR(16) NOT NULL,
 seat VARCHAR(16) NOT NULL, quantity INT NOT NULL, program_no INT NULL,
 created_at DATETIME(6) NOT NULL, operator_name VARCHAR(100) NOT NULL, run_mode VARCHAR(16) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_units (
 unit_id VARCHAR(80) PRIMARY KEY, order_id VARCHAR(64) NOT NULL,
 stage INT NOT NULL DEFAULT 0, state VARCHAR(32) NOT NULL DEFAULT 'planned',
 return_location VARCHAR(100) NULL, updated_at DATETIME(6) NOT NULL, run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY (order_id) REFERENCES mes_orders(order_id), INDEX ix_order(order_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_events (
 event_id CHAR(36) PRIMARY KEY, unit_id VARCHAR(80) NOT NULL,
 stage_code VARCHAR(32) NOT NULL, result VARCHAR(24) NOT NULL,
 operator_name VARCHAR(100) NOT NULL, note VARCHAR(1000) NOT NULL,
 occurred_at DATETIME(6) NOT NULL, source VARCHAR(24) NOT NULL DEFAULT 'MANUAL',
 run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY (unit_id) REFERENCES mes_units(unit_id), INDEX ix_unit_time(unit_id,occurred_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_inventory (
 item_id VARCHAR(32) NOT NULL, label VARCHAR(100) NOT NULL, quantity INT NOT NULL,
 capacity INT NOT NULL, updated_at DATETIME(6) NOT NULL, source VARCHAR(24) NOT NULL DEFAULT 'USER_INITIAL',
 run_mode VARCHAR(16) NOT NULL, PRIMARY KEY(item_id,run_mode)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_approvals (
 approval_id CHAR(36) PRIMARY KEY, unit_id VARCHAR(80) NOT NULL,
 state VARCHAR(16) NOT NULL, requested_at DATETIME(6) NOT NULL,
 decided_at DATETIME(6) NULL, approver VARCHAR(100) NULL, reason VARCHAR(1000) NOT NULL DEFAULT '',
 run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY (unit_id) REFERENCES mes_units(unit_id), INDEX ix_state(state)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_reports (
 report_id CHAR(36) PRIMARY KEY, approval_id CHAR(36) NOT NULL UNIQUE,
 filename VARCHAR(160) NOT NULL, created_at DATETIME(6) NOT NULL, snapshot_json LONGTEXT NOT NULL, run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY (approval_id) REFERENCES mes_approvals(approval_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_erp_outbox (
 report_id CHAR(36) PRIMARY KEY, state VARCHAR(32) NOT NULL DEFAULT 'WAITING_CONFIG',
 created_at DATETIME(6) NOT NULL, run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY (report_id) REFERENCES mes_reports(report_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_feedback (
 feedback_id CHAR(36) PRIMARY KEY, operator_name VARCHAR(100) NOT NULL,
 message VARCHAR(2000) NOT NULL, created_at DATETIME(6) NOT NULL, run_mode VARCHAR(16) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_dispatch_queue (
 unit_id VARCHAR(80) PRIMARY KEY, position BIGINT NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'waiting',
 kind VARCHAR(16) NOT NULL DEFAULT 'NORMAL', parent_unit_id VARCHAR(80) NULL,
 created_at DATETIME(6) NOT NULL, run_mode VARCHAR(16) NOT NULL,
 FOREIGN KEY(unit_id) REFERENCES mes_units(unit_id), INDEX ix_dispatch(run_mode,state,position)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_scada_outbox (
 event_id CHAR(36) PRIMARY KEY, event_type VARCHAR(32) NOT NULL, payload_json LONGTEXT NOT NULL,
 state VARCHAR(24) NOT NULL DEFAULT 'WAITING_CONFIG', created_at DATETIME(6) NOT NULL, run_mode VARCHAR(16) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_external_orders (
 source_name VARCHAR(64) NOT NULL, source_order_id VARCHAR(128) NOT NULL,
 run_mode VARCHAR(16) NOT NULL, mes_order_id VARCHAR(64) NOT NULL,
 source_created_at DATETIME(6) NULL, source_produced_at DATETIME(6) NULL,
 source_timezone VARCHAR(32) NOT NULL DEFAULT 'Asia/Seoul',
 source_payload LONGTEXT NOT NULL, imported_at DATETIME(6) NOT NULL,
 PRIMARY KEY(source_name,source_order_id),
 UNIQUE KEY ix_mes_external(mes_order_id),
 FOREIGN KEY(mes_order_id) REFERENCES mes_orders(order_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mes_external_production (
 source_name VARCHAR(64) NOT NULL, history_id VARCHAR(128) NOT NULL,
 source_order_id VARCHAR(128) NOT NULL, mes_order_id VARCHAR(64) NOT NULL,
 result VARCHAR(16) NOT NULL, completed_at DATETIME(6) NOT NULL,
 source_payload LONGTEXT NOT NULL, run_mode VARCHAR(16) NOT NULL,
 PRIMARY KEY(source_name,history_id), FOREIGN KEY(mes_order_id) REFERENCES mes_orders(order_id)
) ENGINE=InnoDB;
