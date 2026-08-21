"""Дашборд графики must ship ECharts/Plotly same-origin, not a CDN."""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DIST = _REPO / "plugins" / "moysklad" / "dashboard" / "dist"
_INDEX = _DIST / "index.js"
_ECHARTS = _DIST / "vendor" / "echarts.min.js"
_PLOTLY = _DIST / "vendor" / "plotly.min.js"


def test_chart_bundle_does_not_use_cdn() -> None:
    text = _INDEX.read_text(encoding="utf-8")
    assert "jsdelivr" not in text
    assert "cdn.jsdelivr.net" not in text
    assert "unpkg.com" not in text
    assert 'pluginVendorUrl("echarts.min.js")' in text
    assert 'pluginVendorUrl("plotly.min.js")' in text


def test_chart_vendor_files_exist() -> None:
    assert _ECHARTS.is_file() and _ECHARTS.stat().st_size > 100_000
    assert _PLOTLY.is_file() and _PLOTLY.stat().st_size > 100_000
