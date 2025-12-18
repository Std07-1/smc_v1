# FXCM tick-agg: оновлення контракту (2025-12-13)

<!-- markdownlint-disable MD013 -->

Цей документ фіксує зміни в контракті між зовнішнім FXCM-конектором і `smc_v1`, коли конектор перейшов на агрегацію з тік-стріму (tick-agg) для OHLCV.

## 1) Що змінилось

- Потік `fxcm:ohlcv` може містити **live-бар** з `complete=false` (бар ще формується всередині поточного bucket).
- Поля `complete` та `synthetic` є **опціональними**:
  - відсутність `complete` трактуємо як `complete=true` (закритий/фінальний бар);
  - `synthetic=true` означає, що бар синтетичний (наприклад, заповнення/плейсхолдер) і не має використовуватись як джерело істини.
- Додаткові поля (microstructure/діагностика) можуть зʼявлятись у bar-обʼєктах і мають ігноруватись консюмерами, якщо вони не потрібні.

## 2) Контракт `fxcm:ohlcv` (мінімально необхідні поля)

Мінімальна форма (показані лише ключові поля):

```json
{
  "symbol": "XAUUSD",
  "tf": "1m",
  "bars": [
    {
      "open_time": 1764002100000,
      "close_time": 1764002159999,
      "open": 4209.10,
      "high": 4210.00,
      "low": 4208.80,
      "close": 4209.70,
      "volume": 149.0,
      "complete": false,
      "synthetic": false
    }
  ]
}
```

## 3) Політики на стороні `smc_v1`

- **UDS (UnifiedDataStore) приймає лише `complete=true` бари.** Live-бар (`complete=false`) не записується в UDS.
- **`synthetic=true` не має впливати на історію в UDS.** Якщо синтетичні бари присутні в потоці, вони можуть враховуватись лише як діагностика/метрики, але не як дані для зберігання.
- **Soft validation:** якщо в payload є некоректні бари (нечислові значення, відсутні ключові поля), `smc_v1` має пропускати лише такі бари, не валячи процес і не відкидаючи весь пакет.

## 4) Політики Web UI

- UI може показувати live-бар (`complete=false`) як "поточну" свічку (окрема серія/стиль), але **не підмішувати** його в історичні (complete) дані.
- Для повної свічки (`complete=true`) UI робить upsert у history і, якщо був live-бар з тим самим `open_time`, закриває/прибирає його.
- Volume-серія підтримується (histogram), але live-volume можливий лише якщо UI передає `volume` у live-бар. Dev стенд `UI_v2/web_client/chart_demo.js` це робить; основний шлях `UI_v2/web_client/app.js` для FXCM WS зараз будує live-бар лише з OHLC, тому live-volume може бути нульовим.

## 5) Де в коді це реалізовано

- Інгест та фільтрація `complete=false`: `data/fxcm_ingestor.py`.
- Soft-валидація повідомлень: `core/contracts/fxcm_validate.py`.
- Dev стенд для графіка: `UI_v2/web_client/dev_chart_playground.html`, `UI_v2/web_client/chart_demo.js`, `UI_v2/web_client/chart_adapter.js`.
- Тести контракту/політик: `tests/test_fxcm_schema_and_ingestor_contract.py`.
