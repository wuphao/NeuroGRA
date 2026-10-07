-- 配套：MySQL中高级面试深入解读.md
-- MySQL 8.0 / 8.4，使用全新的学习数据库；不删除或覆盖已有数据库。
-- CREATE DATABASE 若报“已存在”，请停止执行并换一个学习库名。
-- 使用遇错停止的批处理模式；讲解稿提供 Windows mysql --execute 命令。
-- 该文件含 10 万行订单造数，只执行一次；各并发实验按讲解稿操作。

CREATE DATABASE mysql_interview_lab_20261003
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE mysql_interview_lab_20261003;

CREATE TABLE orders (
  id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  status TINYINT NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  remark VARCHAR(500) NOT NULL,
  PRIMARY KEY (id),
  KEY idx_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE lab_digits (d INT PRIMARY KEY);
INSERT INTO lab_digits VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9);

INSERT INTO orders(id,user_id,status,amount,created_at,remark)
SELECT n+1, MOD(n,100)+1, MOD(FLOOR(n/1000),4),
       MOD(n*37,100000)/100,
       TIMESTAMPADD(SECOND,FLOOR(n/10),'2026-01-01 00:00:00'),
       RPAD('lab',200,'x')
FROM (
  SELECT a.d+10*b.d+100*c.d+1000*d.d+10000*e.d AS n
  FROM lab_digits a CROSS JOIN lab_digits b CROSS JOIN lab_digits c
  CROSS JOIN lab_digits d CROSS JOIN lab_digits e
) AS generated_rows;
DROP TABLE lab_digits;

CREATE TABLE accounts (
  id INT PRIMARY KEY,
  balance INT NOT NULL,
  version INT NOT NULL DEFAULT 0
) ENGINE=InnoDB;
INSERT INTO accounts VALUES (1,100,0),(2,200,0);

CREATE TABLE lock_demo (
  id INT PRIMARY KEY,
  k INT NOT NULL,
  payload INT NOT NULL,
  KEY idx_k(k)
) ENGINE=InnoDB;
INSERT INTO lock_demo VALUES (10,1,100),(20,2,200),(30,2,300),(40,3,400);

CREATE TABLE on_call (
  id INT PRIMARY KEY,
  is_on_call TINYINT NOT NULL
) ENGINE=InnoDB;
INSERT INTO on_call VALUES (1,1),(2,1);

CREATE TABLE job_queue (
  id INT PRIMARY KEY,
  state TINYINT NOT NULL DEFAULT 0,
  payload VARCHAR(100) NOT NULL,
  KEY idx_state_id(state,id)
) ENGINE=InnoDB;
INSERT INTO job_queue VALUES (1,0,'job-a'),(2,0,'job-b'),(3,0,'job-c');

CREATE TABLE biz_request (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  request_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  result_text VARCHAR(200) NOT NULL,
  UNIQUE KEY uk_request(request_id)
) ENGINE=InnoDB;

ANALYZE TABLE orders;
SELECT COUNT(*) AS order_rows FROM orders;
SELECT VERSION() AS mysql_version, @@transaction_isolation AS isolation_level;
