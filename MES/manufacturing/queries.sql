USE `opcua-manufacturing`;
SELECT tag_name, value_text, value_number, status_code,
       DATE_ADD(received_at, INTERVAL 9 HOUR) AS received_kst
FROM `team2-robotdb` ORDER BY tag_name;

SELECT node_id, value_json, status_code, received_at
FROM `team2-robotdb-history` ORDER BY received_at DESC LIMIT 100;

SELECT tag_name,value_text,value_number,status_code,
       DATE_ADD(received_at,INTERVAL 9 HOUR) AS received_kst
FROM `team2-vision` ORDER BY tag_name;

SELECT node_id,value_json,status_code,received_at
FROM `team2-vision-history` ORDER BY received_at DESC LIMIT 100;

SELECT tag_name,value_text,value_number,status_code,
       DATE_ADD(received_at,INTERVAL 9 HOUR) AS received_kst
FROM `team2-order` ORDER BY tag_name;

SELECT node_id,value_json,status_code,received_at
FROM `team2-order-history` ORDER BY received_at DESC LIMIT 100;

SELECT tag_name,value_text,value_number,status_code,
       DATE_ADD(received_at,INTERVAL 9 HOUR) AS received_kst
FROM `team2-plcstatus` ORDER BY tag_name;

SELECT node_id,value_json,status_code,received_at
FROM `team2-plcstatus-history` ORDER BY received_at DESC LIMIT 100;
