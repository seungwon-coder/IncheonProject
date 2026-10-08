CREATE DATABASE IF NOT EXISTS smart_factory CHARACTER SET utf8mb4;
USE smart_factory;
CREATE TABLE `orders` (
  `order_id` int NOT NULL AUTO_INCREMENT,
  `product_code` int NOT NULL,
  `order_qty` int NOT NULL,
  `completed_qty` int NOT NULL DEFAULT '0',
  `defect_qty` int NOT NULL DEFAULT '0',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'WAITING',
  `order_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `start_time` datetime DEFAULT NULL,
  `complete_time` datetime DEFAULT NULL,
  PRIMARY KEY (`order_id`),
  KEY `product_code` (`product_code`),
  CONSTRAINT `orders_ibfk_1` FOREIGN KEY (`product_code`) REFERENCES `product_master` (`product_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `product_master` (
  `product_id` int NOT NULL AUTO_INCREMENT,
  `product_code` int NOT NULL,
  `product_name` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `body_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `seat_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `light_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`product_id`),
  UNIQUE KEY `product_code` (`product_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `production_counter_reset` (
  `reset_id` bigint NOT NULL AUTO_INCREMENT,
  `reset_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`reset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `production_counter_reset_detail` (
  `reset_id` bigint NOT NULL,
  `product_code` int NOT NULL,
  `base_order_qty` bigint NOT NULL DEFAULT '0',
  `base_ok_qty` bigint NOT NULL DEFAULT '0',
  `base_ng_qty` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`reset_id`,`product_code`),
  KEY `fk_reset_detail_product` (`product_code`),
  CONSTRAINT `fk_reset_detail_product` FOREIGN KEY (`product_code`) REFERENCES `product_master` (`product_code`),
  CONSTRAINT `fk_reset_detail_reset` FOREIGN KEY (`reset_id`) REFERENCES `production_counter_reset` (`reset_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `production_history` (
  `history_id` bigint NOT NULL AUTO_INCREMENT,
  `order_id` int NOT NULL,
  `product_code` int NOT NULL,
  `result` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `complete_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`history_id`),
  KEY `order_id` (`order_id`),
  KEY `product_code` (`product_code`),
  CONSTRAINT `production_history_ibfk_1` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`),
  CONSTRAINT `production_history_ibfk_2` FOREIGN KEY (`product_code`) REFERENCES `product_master` (`product_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `production_wip` (
  `wip_id` bigint NOT NULL AUTO_INCREMENT,
  `order_id` int NOT NULL,
  `product_code` int NOT NULL,
  `attempt_no` int NOT NULL,
  `current_stage` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'BODY_FEED',
  `is_replacement` tinyint(1) NOT NULL DEFAULT '0',
  `input_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`wip_id`),
  UNIQUE KEY `uq_wip_order_attempt` (`order_id`,`attempt_no`),
  KEY `fk_wip_product` (`product_code`),
  CONSTRAINT `fk_wip_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`),
  CONSTRAINT `fk_wip_product` FOREIGN KEY (`product_code`) REFERENCES `product_master` (`product_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `user_master` (
  `user_id` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_name` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `security_level` int NOT NULL,
  `last_login` datetime DEFAULT NULL,
  `expire_date` date DEFAULT NULL,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
