# SMC Hint Plain Contract

**Статус:** зафіксований 2025-12-06. Використовується у Stage1→UI пайплайні як єдиний формат `smc_hint`.

## 1. Мета

- Надати стабільний JSON-контракт для `SmcHint`, який можна передавати в UI, stage-телеметрію й аналітику без залежності від Python dataclass-ів.
- Забезпечити єдине джерело правди для A/B‑експериментів: Stage1 та CLI (snapshot runner) серіалізують hint через `smc_core.serializers.to_plain_smc_hint`.

## 2. Топ-рівень

```jsonc
SmcHintPlain = {
  "structure": SmcStructure | null,
  "liquidity": SmcLiquidity | null,
  "zones": SmcZones | null,
  "signals": SmcSignals,   // масив plain-сигналів; зараз завжди []
  "meta": SmcSnapshotMeta  // загальні атрибути snapshot
}
```

- `to_plain_smc_hint` конвертує будь-який `SmcHint` у цей словник (рекурсивно через dataclass → dict, Enum → name, timestamps → ISO).
- `app/screening_producer.process_asset_batch` завжди кладe в `normalized["smc_hint"]` саме plain-дикт, тому UI отримує стабільний JSON (без залежності від Python типів).

## 3. Meta (top-level)

```jsonc
SmcSnapshotMeta = {
  "snapshot_tf": "5m",
  "last_price": 4201.23
}
```

- `snapshot_tf` відповідає `SmcInput.tf_primary`.
- `last_price` береться з останнього бару `tf_primary` (або відсутній, якщо даних немає).

### 3.1. Session context (SMC-owned)

`SmcHint.meta` може містити стабільний контекст торгових сесій, який SMC рахує сам з OHLCV
(UTC-вікна: ASIA 22–07, LONDON 07–13, NY 13–22) та прокидає далі без залежності від FXCM.

```jsonc
SmcSnapshotMeta = {
  "snapshot_tf": "5m",
  "last_price": 4201.23,

  "smc_session_tag": "LONDON",
  "smc_session_start_ms": 1732182000000,
  "smc_session_end_ms": 1732203600000,
  "smc_session_high": 4210.5,
  "smc_session_low": 4195.2,
  "smc_session_tf": "1m",
  "smc_sessions": {
    "ASIA": {"start_ms": 0, "end_ms": 0, "high": 0.0, "low": 0.0, "range": 0.0, "mid": 0.0, "bars": 0, "is_active": false, "tf": "1m"},
    "LONDON": {"start_ms": 0, "end_ms": 0, "high": 0.0, "low": 0.0, "range": 0.0, "mid": 0.0, "bars": 0, "is_active": true, "tf": "1m"},
    "NY": {"start_ms": 0, "end_ms": 0, "high": 0.0, "low": 0.0, "range": 0.0, "mid": 0.0, "bars": 0, "is_active": false, "tf": "1m"}
  }
}
```

Примітки:

- `range`/`mid` заповнюються лише коли є і `high`, і `low`, інакше `null`.
- `session_tag` (без `smc_`) може існувати як legacy-ключ для сумісності.

## 4. Block `structure`

```jsonc
SmcStructure = {
  "primary_tf": "5m",
  "trend": "UP" | "DOWN" | "RANGE" | "UNKNOWN",
  "swings": [SmcSwing, ...],
  "legs": [SmcLeg, ...],
  "ranges": [SmcRange, ...],
  "active_range": SmcRange | null,
  "range_state": "INSIDE" | "DEV_UP" | "DEV_DOWN" | "NONE",
  "events": [SmcStructureEvent, ...],
  "ote_zones": [SmcOteZone, ...],
  "bias": "LONG" | "SHORT" | "NEUTRAL",
  "meta": SmcStructureMeta
}
```

### 4.1. Swings

```jsonc
SmcSwing = {
  "index": 118,
  "time": "2025-11-21T14:00:00Z",
  "price": 4202.55,
  "kind": "HIGH" | "LOW",
  "strength": 1..3
}
```

### 4.2. Legs

```jsonc
SmcLeg = {
  "from_swing": SmcSwing,
  "to_swing": SmcSwing,
  "label": "HH" | "HL" | "LH" | "LL"
}
```

### 4.3. Range

```jsonc
SmcRange = {
  "high": 4210.0,
  "low": 4192.0,
  "eq_level": 4201.0,
  "start_time": "2025-11-21T12:00:00Z",
  "end_time": "2025-11-21T19:00:00Z",
  "state": "INSIDE" | "DEV_UP" | "DEV_DOWN"
}
```

### 4.4. Events

- Масив `SmcStructureEvent` (BOS/CHOCH). У поточних XAU зрізах пустий, але контракт включає всі поля з `smc_core.smc_types.SmcStructureEvent` (event_type, direction, price, time, source_leg, meta).

### 4.5. OTE-зони

```jsonc
SmcOteZone = {
  "leg": SmcLeg,
  "ote_min": 4198.94,
  "ote_max": 4200.55,
  "direction": "SHORT" | "LONG",
  "role": "PRIMARY" | "COUNTERTREND"
}
```

### 4.6. Meta

```jsonc
SmcStructureMeta = {
  "bar_count": 200,
  "cfg_min_swing": 3,
  "cfg_min_range_bars": 12,
  "bos_min_move_atr_m1": 0.6,
  "bos_min_move_pct_m1": 0.002,
  "leg_min_amplitude_atr_m1": 0.8,
  "ote_trend_only_m1": true,
  "ote_max_active_per_side_m1": 1,
  "atr_period": 14,
  "atr_available": true,
  "atr_last": 1.75,
  "atr_median": 1.93,
  "bias": "SHORT",
  "last_choch_ts": "2025-11-21T17:00:00Z",
  "symbol": "xauusd",
  "tf_input": "5m",
  "snapshot_start_ts": "2025-11-20T20:00:00Z",
  "snapshot_end_ts": "2025-11-21T21:55:00Z",
  "swing_times": ["2025-11-21T12:00:00Z", ...]
}
```

## 5. Block `liquidity`

```jsonc
SmcLiquidity = {
  "pools": [SmcPool, ...],
  "magnets": [SmcMagnet, ...],
  "amd_phase": "ACCUMULATION" | "MANIPULATION" | "DISTRIBUTION" | "UNKNOWN",
  "meta": SmcLiquidityMeta
}
```

### 5.1. Pools

```jsonc
SmcPool = {
  "level": 4205.21,
  "liq_type": "EQH" | "EQL" | "TLQ" | "SLQ" | "RANGE_EXTREME" | "WICK_CLUSTER" | ...,
  "strength": 6.0,
  "n_touches": 4,
  "first_time": "2025-11-21T09:00:00Z",
  "last_time": "2025-11-21T19:30:00Z",
  "role": "PRIMARY" | "COUNTERTREND" | "NEUTRAL",
  "source_swings": [118, 120],
  "meta": {
    "source": "wick_cluster",
    "cluster_size": 3
  }
}
```

### 5.2. Magnets

```jsonc
SmcMagnet = {
  "price_min": 4193.0,
  "price_max": 4257.8,
  "center": 4211.4,
  "liq_type": "POOL_CLUSTER" | "RANGE_EXTREME",
  "role": "PRIMARY" | "COUNTERTREND" | "NEUTRAL",
  "pools": [SmcPool, ...],
  "meta": {
    "pool_count": 6,
    "source_types": ["EQL", "WICK_CLUSTER"],
    "symbol": "xauusd",
    "bias": "SHORT"
  }
}
```

### 5.3. Meta

```jsonc
SmcLiquidityMeta = {
  "bar_count": 200,
  "symbol": "xauusd",
  "primary_tf": "5m",
  "pool_count": 25,
  "magnet_count": 2,
  "bias": "SHORT",
  "sfp_events": [],
  "wick_clusters": [
    {
      "level": 4200.8,
      "side": "HIGH",
      "count": 3,
      "max_wick": 0.7,
      "source": "swing",
      "first_ts": "2025-11-21T10:45:00Z",
      "last_ts": "2025-11-21T14:15:00Z"
    }
  ],
  "amd_reason": "range_inside + multiple magnets"
}
```

#### 5.3.1. Liquidity targets (Stage3)

`SmcLiquidityMeta` може містити `liquidity_targets` — список найближчих “цілей ліквідності”
з роллю `internal/external`. Це **non-breaking extension**, бо лежить у `meta`.

```jsonc
SmcLiquidityMeta = {
  "liquidity_targets": [
    {
      "role": "internal",
      "tf": "5m",
      "side": "above",
      "price": 4205.0,
      "type": "EQH",
      "strength": 80.0,
      "reason": ["source:magnet", "touches:4"]
    },
    {
      "role": "external",
      "tf": "4h",
      "side": "below",
      "price": 4180.0,
      "type": "HTF_SWING_LOW",
      "strength": 60.0,
      "reason": ["source:htf_pivot"]
    }
  ]
}
```

## 6. Block `zones`

```jsonc
SmcZones = {
  "zones": [SmcZone, ...],
  "active_zones": [SmcZone, ...],
  "poi_zones": [SmcZone, ...],
  "meta": SmcZonesMeta
}
```

### 6.1. Zone / Order Block

```jsonc
SmcZone = {
  "zone_type": "ORDER_BLOCK" | ...,
  "price_min": 4199.1,
  "price_max": 4200.6,
  "timeframe": "5m",
  "origin_time": "2025-11-21T21:20:00Z",
  "direction": "SHORT" | "LONG",
  "role": "PRIMARY" | "COUNTERTREND" | "NEUTRAL",
  "strength": 0.85,
  "confidence": 0.7,
  "components": ["orderblock", "leg_159_163"],
  "zone_id": "ob_xauusd_5m_482_163",
  "entry_mode": "BODY_05" | "WICK_TOUCH" | ...,
  "quality": "STRONG" | "WEAK",
  "reference_leg_id": "leg_159_163",
  "reference_event_id": "bos_1732233600",
  "bias_at_creation": "SHORT",
  "notes": "",
  "meta": {
    "body_pct": 0.62,
    "wick_top_pct": 0.18,
    "wick_bottom_pct": 0.2,
    "entry_mode": "BODY_05",
    "role": "PRIMARY",
    "has_bos": true,
    "bar_count": 6,
    "amplitude": 9.5,
    "quality": "STRONG"
  }
}
```

### 6.2. Meta

```jsonc
SmcZonesMeta = {
  "zone_count": 3,
  "orderblocks_total": 3,
  "orderblocks_primary": 1,
  "orderblocks_countertrend": 2,
  "orderblocks_long": 1,
  "orderblocks_short": 2
}
```

## 7. Block `signals`

- Містить список plain-сигналів SMC-core (планується на Етапі 5). Наразі Stage1 та snapshot runner повертають порожній масив `[]`, але контракт зарезервовано.

## 8. Відповідність пайплайну

- **Core:** `smc_core.serializers.to_plain_smc_hint(hint)` — єдиний спосіб сформувати plain JSON. Використовується CLI (`tools/smc_snapshot_runner.py`) і Stage1 (`screening_producer`).
- **Stage1:** у `process_asset_batch` plain hint записується в `normalized["smc_hint"]`, тому UI, Redis snapshot та історичні дампи бачать однакову схему.
- **Документація:** цей файл, `smc_core_stage1.md`, `smc_structure_stage2.md`, `smc_liquidity_stage3.md` і майбутній `smc_zones_stage4.md` посилаються на ті самі поля.

## 9. Приклади

- Повноцінні JSON-приклади: `reports/smc_xau_5m_snapshot_ob_v2*.json`, `datastore/xauusd_smc_1m_history.jsonl`, golden-набори `smc_xau_5m_2000bars_{A..D}.json`.
- Усі вони створені через `to_plain_smc_hint`, тож ця схема підтверджена реальними snapshot-ами.
