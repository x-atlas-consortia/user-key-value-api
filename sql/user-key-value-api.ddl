-- MySQL Workbench Forward Engineering

SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0;
SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0;
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

-- -----------------------------------------------------
-- Schema hm_user_key_value
-- -----------------------------------------------------
-- Explicitly set to default collation and character set for typical MySQL behavior.

-- -----------------------------------------------------
-- Table `user_key_value`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `user_key_value` (
  `GLOBUS_IDENTITY_ID` VARCHAR(50) NOT NULL,
  `KEY_NAME` VARCHAR(50) NOT NULL,
  `KEY_VALUE` JSON NOT NULL,
  `UPSERT_UTC_TIME` TIMESTAMP NOT NULL DEFAULT now(),
  PRIMARY KEY (`GLOBUS_IDENTITY_ID`, `KEY_NAME`))
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8mb4
COLLATE = utf8mb4_0900_ai_ci;


SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
