SELECT DISTINCT
       history.facility,
       history.entity AS parent_entity,
       history.lot,
       history.operation,
       TO_CHAR(history.txn_date, 'yyyy-mm-dd hh24:mi:ss') AS first_lot_timestamp,
       TO_CHAR(CAST(history.txn_date AS TIMESTAMP(9)),
               'YYYYMMDDHH24MISSFF9') AS lot_timestamp_key
FROM @[]@.F_ENTITYLOTHIST history
WHERE (history.facility IN
    SQL_Get_CSV_List('ept_interventions.csv', 'facility', 'history.facility IN'))
  AND (history.entity IN
    SQL_Get_CSV_List('ept_interventions.csv', 'parent_entity', 'history.entity IN'))
  AND history.unique_flag = 'Y'
  AND NVL(history.history_deleted_flag, 'N') = 'N'
  AND history.txn_date >= TO_DATE('<<<LOT_HISTORY_START>>>', 'YYYYMMDDHH24MISS')
  AND EXISTS (
      SELECT 1
      FROM @[]@.F_LOTHIST lot_history
      JOIN @[]@.F_PRODUCT product
        ON product.facility = lot_history.facility
       AND product.product = lot_history.product
       AND NVL(product.latest_version, 'Y') = 'Y'
      WHERE lot_history.facility = history.facility
        AND lot_history.lot = history.lot
        AND lot_history.operation = history.operation
        AND lot_history.prevout_date = history.prevout_date
        AND NVL(lot_history.history_deleted_flag, 'N') = 'N'
        AND (UPPER(TRIM(product.prodgroup3)) IN
            SQL_Get_CSV_List('target_products.csv', 'prodgroup3', 'UPPER(TRIM(product.prodgroup3)) IN'))
  )