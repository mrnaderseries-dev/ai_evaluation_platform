from enum import StrEnum


class ComponentType(StrEnum):
    API = "api"
    INGESTION = "ingestion"
    KAFKA = "kafka"
    DATABASE = "database"
    EVALUATION = "evaluation"
    MONITORING = "monitoring"
    BATCH_PIPELINE = "batch_pipeline"
    EXTERNAL_PROVIDER = "external_provider"