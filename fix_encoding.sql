-- ============================================================
-- GeoDash - Fix encodage CP850 + CP1252 double-encode
-- IDEMPOTENT : pose un marqueur, ne se rejoue plus
-- v3 : ajout des motifs CP1252 (tirets cadratin, guillemets typo, ellipsis)
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS _migration_marker (
    key VARCHAR(100) PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM _migration_marker WHERE key = 'fix_encoding_cp850_v3') THEN
    RAISE NOTICE '[fix_encoding] Deja applique - skip.';
    RETURN;
  END IF;

  RAISE NOTICE '[fix_encoding] Application du fix encodage...';

  UPDATE dashboard_zone SET name = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE name LIKE '%├%' OR name LIKE '%ÔÇ%';

  UPDATE dashboard_zone SET description = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(description,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE description LIKE '%├%' OR description LIKE '%ÔÇ%';

  UPDATE dashboard_roadsegment SET name = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE name LIKE '%├%' OR name LIKE '%ÔÇ%';

  UPDATE dashboard_roadsegment SET notes = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(notes,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE notes LIKE '%├%' OR notes LIKE '%ÔÇ%';

  UPDATE dashboard_floodrisk SET name = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE name LIKE '%├%' OR name LIKE '%ÔÇ%';

  UPDATE dashboard_vegetationdensity SET name = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE name LIKE '%├%' OR name LIKE '%ÔÇ%';

  UPDATE dashboard_alert SET title = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE title LIKE '%├%' OR title LIKE '%ÔÇ%';

  UPDATE dashboard_alert SET message = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(message,
    '├®','é'),'├¿','è'),'├½','ë'),'├»','ï'),'├┤','ô'),'├¬','ê'),'├º','ç'),'├ó','â'),'├á','à'),'├ë','É'),'├ê','Ê'),'├Â','Ö'),'├Ä','Î'),
    'ÔÇö','—'),'ÔÇô','–'),'ÔÇÿ',''''),'ÔÇÖ',''''),'ÔÇ£','"'),'ÔÇ¥','"'),'ÔÇª','…') WHERE message LIKE '%├%' OR message LIKE '%ÔÇ%';

  INSERT INTO _migration_marker (key) VALUES ('fix_encoding_cp850_v3');

  RAISE NOTICE '[fix_encoding] Termine.';
END
$$;

COMMIT;