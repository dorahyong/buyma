# AI를 이용한 바이마 상품관리 크롤러 - okmall

## 바이마 API ACCESS_TOKEN 테이블
CREATE TABLE `buyma_tokens` (
	`id` INT(11) NOT NULL DEFAULT '1',
	`access_token` TEXT NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`refresh_token` TEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`updated_at` TIMESTAMP NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
	PRIMARY KEY (`id`) USING BTREE,
	CONSTRAINT `single_row` CHECK (`id` = 1)
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
;
