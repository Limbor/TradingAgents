"""Tests for the local-safe A-share DeepSeek backtest helper."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backtest_cn_deepseek.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("backtest_cn_deepseek", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_sample(path: Path, rows: int = 100) -> None:
    dates = pd.bdate_range("2026-01-01", periods=rows)
    closes = [10 + i * 0.05 for i in range(rows)]
    df = pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Open": [c - 0.02 for c in closes],
            "High": [c + 0.05 for c in closes],
            "Low": [c - 0.05 for c in closes],
            "Close": closes,
            "Volume": [100000 + i for i in range(rows)],
            "Amount": [1000000 + i * 1000 for i in range(rows)],
            "Turnover": [2.0 + i * 0.01 for i in range(rows)],
        }
    )
    df.to_csv(path, index=False)


@pytest.mark.unit
def test_build_payload_contains_future_return_and_features(tmp_path):
    script = _load_script()
    sample = tmp_path / "sample.csv"
    _write_sample(sample)

    payload = script.build_payload("600000.SH", sample, horizon=10)

    assert payload["symbol"] == "600000.SH"
    assert payload["horizon_days"] == 10
    assert "future_return_pct" in payload
    assert {"sma5", "sma10", "sma20", "sma60", "rsi14", "macdh"} <= set(
        payload["feature_snapshot"]
    )
    assert len(payload["recent_rows"]) == 10


@pytest.mark.unit
def test_model_payload_excludes_forward_labels(tmp_path):
    script = _load_script()
    sample = tmp_path / "sample.csv"
    _write_sample(sample)
    payload = script.build_payload("600000.SH", sample, horizon=10)

    model_payload = script.build_model_payload(payload)

    assert "future_return_pct" not in model_payload
    assert "future_date" not in model_payload
    assert model_payload["signal_date"] == payload["signal_date"]
    assert model_payload["feature_snapshot"] == payload["feature_snapshot"]


@pytest.mark.unit
def test_local_decision_and_direction_scoring_are_deterministic(tmp_path):
    script = _load_script()
    sample = tmp_path / "sample.csv"
    _write_sample(sample)
    payload = script.build_payload("600000.SH", sample, horizon=10)

    decision = script.local_decision(payload)

    assert decision["action"] in {"BUY", "HOLD", "SELL"}
    assert script.is_correct("BUY", 1.0) is True
    assert script.is_correct("SELL", -1.0) is True
    assert script.is_correct("HOLD", 2.0) is True
    assert script.is_correct("HOLD", 5.0) is False


@pytest.mark.unit
def test_deepseek_mode_requires_explicit_upload_flag():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--provider", "deepseek"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing DeepSeek upload without --allow-upload" in result.stderr
