CREATE UNIQUE INDEX index_ingestion_log
ON raw.ingestion_log (source_file)
WHERE status = 'success';
