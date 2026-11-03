"""Performance budget regression: track absolute RSS and growth separately."""

from benchmarks.check_budget import evaluate


def report(rss: float, growth: float | None = None) -> dict:
    def pair(d: float, a: float) -> dict:
        return {"direct": {"median": d}, "a2a": {"median": a}}
    footprint = {"rss_mb": rss, "cpu_percent_idle": 0}
    if growth is not None:
        footprint["rss_growth_mb"] = growth
    return {
        "ttft_ms": pair(100, 108),
        "total_ms": pair(1000, 1020),
        "tokens_per_s": pair(50, 50),
        "cancellation": {"result": "pass"},
        "adapter_footprint": footprint,
    }


def checks(rss: float, growth: float | None = None) -> dict:
    return {name: ok for name, ok, _ in evaluate(report(rss, growth))}


def test_observed_108mb_is_below_calibrated_limit():
    result = checks(108.3, 5.0)
    assert result["Adapter RSS"]
    assert result["Adapter RSS growth under load"]


def test_absolute_rss_still_gates_excessive_memory():
    assert not checks(140)["Adapter RSS"]


def test_sustained_growth_is_a_separate_failure_even_when_total_below_cap():
    result = checks(112, 24)
    assert result["Adapter RSS"]
    assert not result["Adapter RSS growth under load"]


def test_older_reports_without_growth_keep_absolute_gate():
    result = checks(110)
    assert result["Adapter RSS"]
    assert "Adapter RSS growth under load" not in result
