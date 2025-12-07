import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redis.asyncio import Redis

from app.settings import load_datastore_cfg, settings
from data.unified_store import UnifiedDataStore


async def main() -> None:
    cfg = load_datastore_cfg()
    redis = Redis(host=settings.redis_host, port=settings.redis_port)
    store = UnifiedDataStore(redis=redis, cfg=cfg)  # type: ignore
    await store.start_maintenance()
    df = await store.get_df("xauusd", "1m", limit=5)
    print(df)
    await store.stop_maintenance()
    await redis.close()


asyncio.run(main())
