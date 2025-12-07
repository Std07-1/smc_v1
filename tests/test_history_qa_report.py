"""Тести агрегованого звіту History QA."""

from app.history_qa_runner import HistoryQaReport, HistoryQaSymbolReport


def test_history_qa_report_summary_includes_warmup_and_requests() -> None:
    symbol_report = HistoryQaSymbolReport(
        symbol="xauusd",
        status="success",
        bars_requested=300,
        bars_available=300,
        bars_processed=251,
        snapshots_written=251,
        warmup_bars=49,
    )
    report = HistoryQaReport(
        status="success",
        symbols=[symbol_report],
        started_at=0.0,
        finished_at=5.0,
        warmup_bars=49,
        bars_requested_per_symbol=300,
    )

    summary = report.to_summary()

    assert summary["warmup_bars"] == 49
    assert summary["bars_requested_per_symbol"] == 300
    assert summary["bars_requested_total"] == 300
    assert summary["bars_processed"] == 251
