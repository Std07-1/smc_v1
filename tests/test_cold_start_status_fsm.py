from app.cold_start import (
    ColdstartHistoryReport,
    build_status_payload,
    history_report_to_summary,
)


def make_report(status: str, ready: int, pending: list[str]) -> ColdstartHistoryReport:
    return ColdstartHistoryReport(
        symbols_total=ready + len(pending),
        symbols_ready=ready,
        symbols_pending=pending,
        required_bars=300,
        status=status,
        report_ts=1_700_000_000.0,
    )


def test_history_report_summary_contains_status_and_pending():
    report = make_report("success", ready=2, pending=[])
    summary = history_report_to_summary(report)
    assert summary is not None
    assert summary["status"] == "success"
    assert summary["symbols_ready"] == 2
    assert summary["symbols_pending"] == []


def test_build_status_payload_respects_state_and_summary():
    report = make_report("timeout", ready=0, pending=["xauusd"])
    summary = history_report_to_summary(report)
    qa = {"status": "pending", "symbols_total": 1}
    payload = build_status_payload(phase="error", history=summary, qa=qa)
    assert payload["state"] == "error"
    assert payload["phase"] == "error"
    assert payload["history"]["status"] == "timeout"  # type: ignore
    assert payload["history"]["symbols_pending"] == ["xauusd"]  # type: ignore
    assert payload["qa"] == qa
