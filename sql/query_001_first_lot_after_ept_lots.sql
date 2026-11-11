WITH ept_states AS (
    SELECT DISTINCT
           history.statehist_id,
           history.facility,
           equipment.parent_entity,
           history.entity AS ept_entity,
           history.txn_timestamp AS event_start,
           history.txn_timestamp
               + NUMTODSINTERVAL(history.elapse_seconds, 'SECOND') AS event_end,
           CASE
               WHEN history.ept_type = 'E116' THEN
                   CASE
                       WHEN history.automated_state_code = 1
                            AND history.task_code IN (0, 1, 2, 3, 4, 5, 6)
                           THEN NULL
                       WHEN history.automated_state_code = 2
                           AND history.blocked_reason_code = 6
                           THEN NULL
                         WHEN history.automated_state_code = 2
                           AND history.blocked_reason_code IN (0, 1, 2, 3, 4, 5, 7, 8, 9)
                          THEN 'UNSCDL DT'
                       WHEN history.automated_state_code IN (1, 2)
                           THEN detail.task_name
                   END
               WHEN history.ept_type = 'MANUAL'
                    AND history.manual_state_code IN ('PM CLOSED', 'PM OVERDUE')
                   THEN 'PM'
               ELSE history.manual_state_code
           END AS ept_state
    FROM @[]@.F_EPT_Entity equipment
    JOIN @[]@.F_EPT_Entity_StateHist history
      ON history.entity = equipment.entity
    LEFT JOIN @[]@.F_EPT_StateHist_Detail detail
      ON detail.statehist_id = history.statehist_id
    WHERE equipment.relation_expire_date IS NULL
      AND equipment.parent_entity LIKE 'JDC%'
      AND history.txn_timestamp >= SYSDATE - 1
), interventions AS (
        SELECT statehist_id, facility, parent_entity, ept_entity,
           event_start, event_end, ept_state
    FROM ept_states
        WHERE (
          ept_state IN ('PM', 'CONVERSION', 'SETUP SDT')
          OR (ept_state = 'UNSCDL DT' AND ept_entity LIKE 'DGX%')
         )
      AND event_end >= event_start
)
SELECT DISTINCT
       intervention.facility,
       intervention.parent_entity,
      intervention.ept_entity,
       intervention.statehist_id,
       intervention.ept_state,
       TO_CHAR(intervention.event_start, 'yyyy-mm-dd hh24:mi:ss') AS event_start,
       TO_CHAR(intervention.event_end, 'yyyy-mm-dd hh24:mi:ss') AS event_end,
       TO_CHAR(CAST(intervention.event_end AS TIMESTAMP(9)),
               'YYYYMMDDHH24MISSFF9') AS event_end_key
FROM interventions intervention
ORDER BY intervention.facility, intervention.parent_entity,
         event_start, intervention.statehist_id