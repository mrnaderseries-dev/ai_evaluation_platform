"""Behavioral contract tests for SignalNormalizer.

Each test pins an explicit rule of normalize / normalize_many / _copy_payload /
_extract_attributes against constructed PersistedSignal instances. No
repository involvement; the normalizer is a pure function over the model.
"""
from datetime import datetime, timezone

from ai_eval_platform.analysis.signal_normalizer import SignalNormalizer
from ai_eval_platform.storage.models import PersistedSignal


def _signal(**kw):
    base = dict(
        signal_id="s1",
        agent_id="a1",
        signal_type="raw",
        scope="run",
        run_id="r1",
        evaluation_run_id=None,
        detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"user": "u", "value": 1},
        payload_type="raw",
    )
    base.update(kw)
    return PersistedSignal(**base)


class TestSignalNormalizer:
    def test_normalize_preserves_identity_and_payload(self):
        normalized = SignalNormalizer().normalize(_signal())
        assert normalized.signal_id == "s1"
        assert normalized.agent_id == "a1"
        assert normalized.signal_type == "raw"
        assert normalized.scope == "run"
        assert normalized.run_id == "r1"
        assert normalized.evaluation_run_id is None
        assert normalized.detected_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert normalized.recorded_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert normalized.raw_payload == {"user": "u", "value": 1}
        assert normalized.payload_type == "raw"
        assert normalized.detector_name is None
        assert normalized.dedupe_key is None

    def test_normalize_attributes_exclude_reserved_and_keep_unknown(self):
        payload = {
            "signal_id": "x",
            "agent_id": "y",
            "signal_type": "z",
            "scope": "s",
            "run_id": "r",
            "evaluation_run_id": "er",
            "detected_at": "t",
            "recorded_at": "t2",
            "user": "u",
            "payload_value": "v",
        }
        normalized = SignalNormalizer().normalize(_signal(payload=payload))
        assert normalized.attributes == {"user": "u", "payload_value": "v"}

    def test_normalize_non_dict_payload_wrapped(self):
        normalized = SignalNormalizer().normalize(_signal(payload="raw string"))
        assert normalized.raw_payload == {"value": "raw string"}
        assert normalized.attributes == {"value": "raw string"}

    def test_normalize_many_preserves_order_and_count(self):
        signals = (_signal(signal_id="s1"), _signal(signal_id="s2"))
        normalized = SignalNormalizer().normalize_many(signals)
        assert len(normalized) == 2
        assert normalized[0].signal_id == "s1"
        assert normalized[1].signal_id == "s2"

    def test_copy_payload_returns_distinct_dict(self):
        payload = {"k": 1}
        copied = SignalNormalizer._copy_payload(payload)
        copied["k"] = 2
        assert payload == {"k": 1}

    def test_copy_payload_non_dict_returns_value_key(self):
        assert SignalNormalizer._copy_payload(42) == {"value": 42}
        assert SignalNormalizer._copy_payload(None) == {"value": None}
