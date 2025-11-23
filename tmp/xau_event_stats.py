import sys
from pathlib import Path

import pandas as pd

from smc_core.config import SMC_CORE_CONFIG
from smc_core.smc_types import SmcInput
from smc_structure import compute_structure_state, metrics

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def load_tf(tf: str) -> pd.DataFrame:
    path = Path(f"datastore/xauusd_bars_{tf}_snapshot.jsonl")
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_json(str(path), lines=True)
    df = df.sort_values("open_time").reset_index(drop=True)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def main() -> None:
    frames = {tf: load_tf(tf) for tf in ("5m", "15m", "1h")}
    frames = {tf: df for tf, df in frames.items() if not df.empty}
    if "5m" not in frames:
        raise SystemExit("немає 5m даних")
    snap = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf=frames)
    cfg = SMC_CORE_CONFIG
    state = compute_structure_state(snap, cfg)

    df = frames["5m"].tail(cfg.max_lookback_bars).copy()
    atr = metrics.compute_atr(df, 14)

    rows = []
    for event in state.events:
        leg = event.source_leg
        amp = abs(leg.to_swing.price - leg.from_swing.price)
        pct = amp / max(leg.from_swing.price, 1e-9)
        idx = leg.to_swing.index
        atr_val = float("nan")
        if atr is not None and idx < len(atr) and pd.notna(atr.iloc[idx]):
            atr_val = float(atr.iloc[idx])
        ratio = float("nan")
        if atr_val and atr_val > 0 and atr_val == atr_val:
            ratio = amp / atr_val
        rows.append(
            {
                "event": event.event_type,
                "direction": event.direction,
                "time": leg.to_swing.time,
                "amp": amp,
                "pct": pct,
                "atr": atr_val,
                "amp_atr": ratio,
            }
        )

    if not rows:
        print("no events")
        return

    df_rows = pd.DataFrame(rows)
    print(df_rows)
    print("\nsummary:")
    print(df_rows[["amp", "pct", "atr", "amp_atr"]].describe())


if __name__ == "__main__":
    main()
