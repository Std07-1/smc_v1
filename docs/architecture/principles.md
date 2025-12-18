# Принципи архітектури

Цей репозиторій розвиваємо за принципами **DRY**, **SSOT**, **SoC**, **Contract-first** та **Canonical representation**.
Мета — менше дублювання, менше неявних конверсій, передбачувані payload між шарами та контрольована еволюція схем.

## Коротко про принципи

- **DRY (Don't Repeat Yourself)**: не копіюємо однакові шматки логіки (особливо серіалізацію/форматування).
- **SSOT (Single Source of Truth)**: одна центральна реалізація для JSON/часу/форматування — `core/*`.
- **SoC (Separation of Concerns)**: бізнес-логіка не форматує строки та не робить `json.dumps` напряму.
- **Contract-first**: payload між модулями описаний TypedDict/dataclass із `schema_version`.
- **Canonical representation**: всередині системи — канонічний формат; адаптери лише на межах I/O.

## Заборонено (щоб не скотитись у хаос)

- Нові місця з `json.dumps(..., default=str)` у бізнес-модулях — тільки через `core/serialization.py`.
- Нові "вічні" `utils.py` файли. Якщо модуль потрібен — називаємо його доменно (наприклад `serialization`, `contracts`, `ohlcv_models`).
- Форматування для UI/логів у бізнес-ядрі: форматтери — в `core/formatters.py`.
