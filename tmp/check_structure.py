import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
tests_path = ROOT / "tests"
if str(tests_path) not in sys.path:
    sys.path.append(str(tests_path))

from test_smc_orderblock_basic import _make_snapshot, _structure, _swing

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcStructureEvent, SmcStructureLeg
from smc_zones.orderblock_detector import detect_order_blocks

leg = (_swing(3, 98.6, "LOW"), _swing(6, 103.5, "HIGH"), "HL")
bos_event = SmcStructureEvent(
    event_type="BOS",
    direction="LONG",
    price_level=103.0,
    time=leg[1].time,
    source_leg=SmcStructureLeg(from_swing=leg[0], to_swing=leg[1], label="HL"),
)
structure = _structure([leg], [bos_event], bias="LONG")
snapshot = _make_snapshot(
    [
        (101.0, 101.2, 100.3, 100.6),
        (100.6, 100.7, 99.5, 99.8),
        (99.8, 100.0, 98.9, 99.1),
        (99.1, 99.2, 98.6, 98.8),
        (98.8, 100.8, 98.7, 100.4),
        (100.4, 102.2, 100.3, 101.9),
        (101.9, 103.5, 101.7, 103.0),
    ]
)
cfg = SmcCoreConfig()
zones = detect_order_blocks(snapshot, structure, cfg)

print("legs", len(structure.legs))
print("events", len(structure.events))
print("zones", len(zones))
print([z.zone_id for z in zones])
