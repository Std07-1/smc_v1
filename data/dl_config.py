"""
Централізована конфігурація для всіх модулів data_layer.

Без бізнес-логіки — лише дані, моделі та легкі структури.
Вплив нових полів на PnL/точність описаний у докстрінгах.

Особливості:
  • Типи Tf, TfAdapter для таймфреймів.
  • Константи TF_MS, CORE_COLS для стандартизації.
  • Моделі NewsWindow, SessionProfile, HttpConfig.
  • CoreConfig з усіма параметрами системи.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import requests

# ── Типи ─────────────────────────────────────────────────────────────────────
Tf = Literal["M1", "M5", "H1"]
TfAdapter = Literal["TICK", "M1", "M5", "H1"]  # для адаптерів, включає TICK

# ── Константи ────────────────────────────────────────────────────────────────
# Мапа таймфреймів до мілісекунд
TF_MS: dict[Tf, int] = {"M1": 60_000, "M5": 300_000, "H1": 3_600_000}

# Мапа для Yahoo: M5/H1 -> 15m/1h
TfYahoo = Literal["15m", "1h", "1d"]
TF_TO_YF: dict[Literal["M5", "H1"], TfYahoo] = {"M5": "15m", "H1": "1h"}

# Колонки для барів
CORE_COLS = ["open_time", "open", "high", "low", "close"]
OPTIONAL_COLS = ["volume", "close_time"]

# Мінімальний крок ціни для FX (pip)
FX_PIP: float = 0.00001


# ── Моделі даних ─────────────────────────────────────────────────────────────
@dataclass
class NewsWindow:
    """Вікно blackout відносно UTC-часу події."""

    title: str
    start: datetime  # inclusive, UTC
    end: datetime  # exclusive, UTC
    impact: Literal["high", "med", "low"] = "high"


@dataclass
class SessionProfile:
    """Сесійний профіль торгових годин у UTC."""

    name: Literal["london", "ny"]
    start_hhmm: tuple[int, int]
    end_hhmm: tuple[int, int]

    def contains(self, t) -> bool:  # type: ignore
        # Для імпорту без циклу, реалізація тут
        from datetime import time

        s = time(self.start_hhmm[0], self.start_hhmm[1], tzinfo=None)  # UTC assumed
        e = time(self.end_hhmm[0], self.end_hhmm[1], tzinfo=None)
        if s <= e:
            return s <= t < e
        # перетин опівночі
        return t >= s or t < e


# Лондонська сесія: 07:00–16:30 UTC
LONDON = SessionProfile("london", (7, 0), (16, 30))
# Нью-Йоркська сесія: 12:00–21:00 UTC
NEWYORK = SessionProfile("ny", (12, 0), (21, 0))


@dataclass
class HttpConfig:
    """Конфігурація HTTP-клієнта з retry/backoff."""

    timeout: float = 15.0
    retries: int = 3
    backoff_base: float = 0.5
    backoff_factor: float = 2.0
    headers: dict[str, str] | None = None


class HttpClient:
    """Синхронний HTTP-клієнт із простим retry/backoff."""

    def __init__(self, config: HttpConfig | None = None) -> None:
        self.config = config or HttpConfig()
        self._session = requests.Session()
        if self.config.headers:
            self._session.headers.update(self.config.headers)

    def get(self, url: str, *, timeout: float | None = None) -> bytes:
        attempts = max(1, int(self.config.retries))
        wait = max(0.0, float(self.config.backoff_base))
        last_error: requests.RequestException | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._session.get(
                    url,
                    timeout=timeout or self.config.timeout,
                )
                response.raise_for_status()
                return response.content
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                sleep_for = wait or 0.0
                if sleep_for > 0:
                    time.sleep(min(30.0, sleep_for))
                wait = (wait or 0.5) * self.config.backoff_factor
        assert last_error is not None  # для mypy, сюди не дійдемо без помилки
        raise last_error

    def close(self) -> None:
        self._session.close()


# ── Основна конфігурація ─────────────────────────────────────────────────────
@dataclass
class CoreConfig:
    """Централізована конфігурація Data Layer. Без бізнес-логіки — лише дані та параметри.

    Нові поля: докстрінги обов'язкові, значення за замовчуванням помірні.
    Вплив на PnL/точність: описаний для критичних параметрів.
    """

    # Шлях до кореневої директорії даних
    data_root: str = "data"

    # Сесійний профіль: "london" (07:00-16:30 UTC) або "ny" (12:00-21:00 UTC)
    session: Literal["london", "ny"] = "london"

    # Режим роботи: "online" (тільки сесія), "blackout" (завжди), "background" (поза сесією)
    mode: Literal["online", "blackout", "background"] = "online"

    # Інтервал мікробатчу у sink (мс). Вплив: нижче — частіше записи, вище — latency.
    batch_ms: int = 2_000

    # Максимум рядків у батчі sink. Вплив: вище — ефективніше, але ризик OOM при піку.
    batch_max_rows: int = 50_000

    # Розмір черги sink. Вплив: вище — буфер для сплесків, але пам'ять.
    queue_maxsize: int = 100_000

    # Символи для моніторингу (e.g. ["EURUSD"])
    symbols: list[str] = field(default_factory=list)

    # Таймфрейми для обробки (["M1", "M5", "H1"])
    tfs: list[Tf] = field(default_factory=lambda: ["M1", "M5", "H1"])

    # Мапа TF до мілісекунд. Вплив: використовується для розрахунків часових інтервалів.
    tf_ms_map: dict[Tf, int] = field(
        default_factory=lambda: {"M1": 60_000, "M5": 300_000, "H1": 3_600_000}
    )

    # Новини для blackout: список NewsWindow. Вплив: запобігає trades під впливом новин, підвищує winrate.
    news_windows: list[NewsWindow] = field(default_factory=list)

    # Таймфрейм для Yahoo pull у background режимі
    yahoo_pull_tf: Tf = "H1"

    # Днів назад для Yahoo pull. Вплив: вище — більше даних, але повільніше.
    yahoo_window_days: int = 30

    # Шаблон URL для Dukascopy (якщо надано). Вплив: дозволяє custom джерело для backfill.
    duka_url_template: str | None = (
        None  # e.g. "https://.../{symbol}/{kind}/{yyyy}/{mm}/{dd}/{hh}.csv.gz"
    )

    # Ємність RAM буферів per TF. Вплив: вище — більше історії в RAM, швидше доступ, але пам'ять.
    ram_capacity_by_tf: dict[Tf, int] = field(
        default_factory=lambda: {"M1": 6000, "M5": 3000, "H1": 2000}
    )

    # Дозволити додаткові колонки у RAM. Вплив: True — дозволяє enrichment, але ризик несумісності.
    allow_extras_in_ram: bool = True

    # HTTP таймаут (сек). Вплив: вище — надійніше, але повільніше.
    http_timeout: float = 15.0

    # HTTP retries. Вплив: вище — надійніше, але latency.
    http_retries: int = 3

    # HTTP backoff base (сек). Вплив: вище — менше навантаження на джерело.
    http_backoff_base: float = 0.5

    # HTTP backoff factor. Вплив: вище — експоненціальний backoff.
    http_backoff_factor: float = 2.0

    # Компресія для Parquet файлів. Вплив: "zstd" — баланс швидкості/розміру, "snappy" — швидше, але більше файли.
    parquet_compression: str = "zstd"

    # Днів для backfill вікна. Вплив: вище — більше історії, але повільніше завантаження; покращує PnL через більше даних для навчання.
    backfill_window_days: int = 30

    # Чи використовувати Yahoo для FX-пар. Вплив: False — вимикає Yahoo для FX, зменшує шум і пропуски; покращує winrate.
    prefer_yahoo_fx: bool = False

    # Чи увімкнути FXCM як джерело. Вплив: True — додає резерв для M1/H1, підвищує повноту даних; покращує PnL.
    enable_fxcm: bool = True

    # Чи виконувати повну валідацію барів перед записом/додаванням у RAM.
    # Вплив: True — гарантує чистоту часової сітки, але може відкидати дані; False — приймаємо все як є, підвищує прозорість Stage1.
    enable_bar_validation: bool = False

    # Календар ринку FX: відкриті UTC-вікна для торгів (виключає вихідні).
    # Формат: (weekday, (start_h, start_m), (end_h, end_m)). Валідація missing_steps виконується всередині цих вікон.
    fx_market_windows: list[tuple[int, tuple[int, int], tuple[int, int]]] = field(
        default_factory=lambda: [
            (0, (21, 0), (24, 0)),  # понеділок 21:00–24:00
            (1, (0, 0), (24, 0)),  # вівторок 00:00–24:00
            (2, (0, 0), (24, 0)),  # середа 00:00–24:00
            (3, (0, 0), (24, 0)),  # четвер 00:00–24:00
            (4, (0, 0), (22, 0)),  # п'ятниця 00:00–22:00
        ]
    )

    # Свята (UTC) для додаткового blackout ринку. Вказуємо інтервали [start, end).
    fx_holidays_utc: list[tuple[datetime, datetime]] = field(
        default_factory=lambda: [
            (
                datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
            ),
        ]
    )

    # Базова адреса CDN FXCM (глобальна, доступна і з Європи).
    fxcm_base_url: str = "https://candledata.fxcorporate.com"

    # Шаблон URL для FXCM CandleData (історія M1/H1). Підставляємо base для гнучкості.
    fxcm_url_template: str = "{base}/{periodicity}/{instrument}/{year}/{week}.csv.gz"

    # Секрет для перевірки HMAC-підпису FXCM payload (None → перевірка вимкнена).
    fxcm_hmac_secret: str | None = None

    # Алгоритм HMAC для FXCM payload (має збігатися зі стороною конектора).
    fxcm_hmac_algo: str = "sha256"

    # Чи обов'язкова наявність підпису; False дозволяє поступовий rollout.
    fxcm_hmac_required: bool = False

    # Максимальна кількість хвилин для автозаповнення прогалин M1 з FXCM за один backfill.
    # Вплив: обмежує втручання альтернативного джерела, зберігає структуру барів.
    missing_fill_max_minutes: int = 5

    # TTL кешу для FXCM 404-відповідей (секунди). Вплив: зменшує шум у логах, поважає CDN.
    fxcm_404_ttl_sec: int = 21_600

    # Чи записувати ресемпловані M5/H1 на диск одразу. Вплив: False — тримаємо в RAM, знижує IO.
    write_resampled_htf_to_disk: bool = False

    # Чи увімкнути OANDA streaming. Вплив: True — забезпечує онлайн-стрім, покращує свіжість даних; підвищує winrate.
    enable_oanda_stream: bool = True

    # Токен для OANDA API. Вплив: необхідний для доступу до OANDA; без нього — обмежена функціональність.
    oanda_token: str | None = None

    # Account ID для OANDA. Вплив: необхідний для streaming; без нього — неможливо підключитися.
    oanda_account_id: str | None = None

    # Чи увімкнути Twelve Data як резерв. Вплив: False — використовується лише ad-hoc; обмежені ліміти, не для основного потоку.
    enable_twelve_data: bool = False

    # API ключ для Twelve Data. Вплив: необхідний для запитів; без нього — недоступно.
    twelve_data_api_key: str | None = None

    # Чи увімкнути Alpha Vantage як резерв. Вплив: False — використовується лише ad-hoc; обмежені ліміти, не для основного потоку.
    enable_alpha_vantage: bool = False

    # API ключ для Alpha Vantage. Вплив: необхідний для запитів; без нього — недоступно.
    alpha_vantage_api_key: str | None = None
