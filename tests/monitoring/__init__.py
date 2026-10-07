# Monitoring contract tests
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.monitoring.detectors.domain import (
    ConfidenceDetectionInput,
    ConfidenceDetectorPolicy,
    ConfidenceDropDetector,
    RouteChangeDetector,
    RouteDetectionInput,
    RouteDetectorPolicy,
    ToolUsageDetectionInput,
    ToolUsageDetectorPolicy,
    ToolUsageDriftDetector,
)
