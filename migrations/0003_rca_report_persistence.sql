ALTER TABLE root_cause_analyses ADD COLUMN report_json JSON NOT NULL DEFAULT '{}';
ALTER TABLE root_cause_analyses ADD COLUMN scope_json JSON NOT NULL DEFAULT '{}';
ALTER TABLE root_cause_analyses ADD COLUMN conclusion_level VARCHAR(64);
ALTER TABLE root_cause_analyses ADD COLUMN conclusion_type VARCHAR(64);
ALTER TABLE root_cause_analyses ADD COLUMN algorithm_name VARCHAR(255);
ALTER TABLE root_cause_analyses ADD COLUMN algorithm_version VARCHAR(100);
CREATE INDEX IF NOT EXISTS ix_root_cause_analyses_fingerprint ON root_cause_analyses (evidence_fingerprint);
CREATE UNIQUE INDEX IF NOT EXISTS uq_root_cause_analyses_investigation ON root_cause_analyses (investigation_id);