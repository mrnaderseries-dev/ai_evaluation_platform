CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id VARCHAR(64) PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    source_ref VARCHAR(255) NOT NULL,
    evidence_kind VARCHAR(100) NOT NULL,
    domain VARCHAR(100) NOT NULL,
    subject_type VARCHAR(100) NOT NULL,
    subject_id VARCHAR(255) NOT NULL,
    agent_id VARCHAR(64),
    system_id VARCHAR(255),
    system_version VARCHAR(100),
    run_id VARCHAR(64),
    evaluation_run_id VARCHAR(64),
    trace_id VARCHAR(128),
    span_id VARCHAR(128),
    parent_span_id VARCHAR(128),
    component_id VARCHAR(255),
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    attributes JSON NOT NULL DEFAULT '{}',
    limitations JSON NOT NULL DEFAULT '[]',
    source_snapshot JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_evidence_domain_observed
    ON evidence_records (domain, observed_at);
CREATE INDEX IF NOT EXISTS ix_evidence_subject_observed
    ON evidence_records (subject_type, subject_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_evidence_agent_observed
    ON evidence_records (agent_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_evidence_system_observed
    ON evidence_records (system_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_evidence_run ON evidence_records (run_id);
CREATE INDEX IF NOT EXISTS ix_evidence_evaluation_run
    ON evidence_records (evaluation_run_id);
CREATE TABLE IF NOT EXISTS persisted_relationships (
    relationship_id VARCHAR(64) PRIMARY KEY,
    analysis_id VARCHAR(64) NOT NULL
        REFERENCES analyses (analysis_id) ON DELETE RESTRICT,
    relationship_key VARCHAR(128) NOT NULL,
    relationship_type VARCHAR(100) NOT NULL,
    basis VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'validated', 'rejected')),
    algorithm_name VARCHAR(255) NOT NULL,
    algorithm_version VARCHAR(100) NOT NULL,
    scope JSON NOT NULL DEFAULT '{}',
    attributes JSON NOT NULL DEFAULT '{}',
    limitations JSON NOT NULL DEFAULT '[]',
    discovered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at TIMESTAMP WITH TIME ZONE,
    validated_by VARCHAR(255),
    validation_notes TEXT,
    CONSTRAINT uq_relationship_analysis_key
        UNIQUE (analysis_id, relationship_key)
);
CREATE INDEX IF NOT EXISTS ix_relationship_status_discovered
    ON persisted_relationships (status, discovered_at);
CREATE INDEX IF NOT EXISTS ix_relationship_type_discovered
    ON persisted_relationships (relationship_type, discovered_at);
CREATE TABLE IF NOT EXISTS relationship_evidence (
    relationship_evidence_id VARCHAR(64) PRIMARY KEY,
    relationship_id VARCHAR(64) NOT NULL
        REFERENCES persisted_relationships (relationship_id)
        ON DELETE CASCADE,
    evidence_id VARCHAR(64) NOT NULL
        REFERENCES evidence_records (evidence_id)
        ON DELETE RESTRICT,
    role VARCHAR(32) NOT NULL
        CHECK (role IN ('supporting', 'counter', 'context')),
    evidence_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_relationship_evidence_role
        UNIQUE (relationship_id, evidence_id, role)
);
CREATE INDEX IF NOT EXISTS ix_relationship_evidence_evidence
    ON relationship_evidence (evidence_id);
