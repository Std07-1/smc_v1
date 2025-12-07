import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
tests_path = ROOT / "tests"
if str(tests_path) not in sys.path:
    sys.path.append(str(tests_path))

from test_smc_orderblock_basic import _short_setup, _structure

from smc_core.config import SmcCoreConfig
from smc_zones.orderblock_detector import detect_order_blocks

snapshot, leg, bos_event = _short_setup()
structure = _structure([leg], [bos_event], bias="SHORT")
zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())
print("zones", len(zones))
