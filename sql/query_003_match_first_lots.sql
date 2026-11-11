CREATE INDEX lot_history_lookup
    ON lot_history (facility, parent_entity, lot_timestamp_key);

WITH ranked_matches AS (
SELECT
       intervention.facility,
       intervention.parent_entity,
       intervention.ept_entity,
       intervention.statehist_id,
       intervention.ept_state,
       intervention.event_start,
       intervention.event_end,
       first_lot.lot,
       first_lot.operation,
         first_lot.first_lot_timestamp,
         ROW_NUMBER() OVER (
           PARTITION BY intervention.facility, intervention.parent_entity,
             CASE WHEN intervention.ept_entity LIKE 'DGX%'
               THEN intervention.ept_entity END,
             intervention.ept_state,
                first_lot.lot, first_lot.operation, first_lot.first_lot_timestamp
           ORDER BY intervention.event_end_key DESC,
              CAST(intervention.statehist_id AS INTEGER) DESC
         ) AS event_rank
FROM interventions intervention
JOIN lot_history first_lot
  ON first_lot.facility = intervention.facility
 AND first_lot.parent_entity = intervention.parent_entity
 AND first_lot.lot_timestamp_key = (
     SELECT MIN(candidate.lot_timestamp_key)
     FROM lot_history candidate
     WHERE candidate.facility = intervention.facility
       AND candidate.parent_entity = intervention.parent_entity
       AND candidate.lot_timestamp_key >= intervention.event_end_key
 )
WHERE intervention.facility <> ''
  AND intervention.parent_entity <> ''
  AND intervention.event_end_key <> ''
)
SELECT facility, parent_entity, ept_entity, statehist_id, ept_state,
       event_start, event_end, lot, operation, first_lot_timestamp
FROM ranked_matches
WHERE event_rank = 1
ORDER BY facility, parent_entity, event_start, CAST(statehist_id AS INTEGER),
         first_lot_timestamp, NULLIF(lot, '') NULLS LAST;