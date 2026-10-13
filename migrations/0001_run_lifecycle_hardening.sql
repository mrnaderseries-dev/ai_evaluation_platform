ALTER TABLE runs ADD COLUMN monitoring_completed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE runs ADD COLUMN monitoring_claim_id VARCHAR(64);
ALTER TABLE runs ADD COLUMN monitoring_claim_expires_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE persisted_signals ADD COLUMN dedupe_key VARCHAR(64);
CREATE UNIQUE INDEX uq_persisted_signals_dedupe_key ON persisted_signals (dedupe_key);