# Чиста Stage1 система

Документ описує актуальний стан «чистої» Stage1-системи після видалення залежностей від `trend_breakout`. Усі модулі та інструкції наведені українською мовою відповідно до вимог репозиторію.

## Інфраструктура та залежності
- **Python**: 3.11 (venv у корені проєкту `./.venv`).
- **Обов'язкові пакети**: `requirements.txt`, синхронізований між середовищами; ключова залежність `numpy==2.3.5` вирівняна з dev-стабами.
- **Dev-інструменти**: `requirements-dev.txt` (black, ruff, mypy, pytest, stubs) встановлюються в те саме середовище, щоб уникнути конфліктів типів.

## Мінімальний запуск (Stage1-only)
1. **Активувати віртуальне середовище** (PowerShell приклад):
   ```powershell
   cd C:\Aione_projects\smc_v1
   .\.venv\Scripts\Activate.ps1
   ```
2. **Встановити залежності**:
   ```powershell
   C:/Aione_projects/smc_v1/.venv/Scripts/python.exe -m pip install -r requirements.txt
   C:/Aione_projects/smc_v1/.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
   ```
3. **Перевірити .env** (Redis + Binance ключі). Без дійсних ключів Stage1 може працювати в режимі лише читання, але WS поток не стартує.
4. **Запустити систему**:
   ```powershell
   C:/Aione_projects/smc_v1/.venv/Scripts/python.exe -m app.main
   ```
   - Очікувані логи: ініціалізація UnifiedDataStore, запуск WSWorker, Screening Producer, регулярні публікації станів у Redis.
   - Завершення: `Ctrl+C` (отримаємо `[Pipeline] Завершення за скасуванням`).

## Склад Stage1 після очищення
- **`app/main.py`**: лише Stage1-оркестрація (bootstrap → preload → WSWorker → Screening Producer). Жодних імпортів `trend_breakout`.
- **`app/screening_producer.py`**: асинхронний цикл збору сигналів з `AssetMonitorStage1` та публікації через `publish_full_state`. Параметри `enable_trend_breakout` / `trend_breakout_concurrency` видалені.
- **`app/asset_state_manager.py`**: структура стану активу містить тільки Stage1 поля (`signal`, `stats`, `tp/sl`, тригери). Ключ `trend_breakout` та повʼязані мерджі прибрано.
- **`config/config.py`**: залишено `PRELOAD_1M_LOOKBACK_INIT` як єдиний контроль глибини історії; константи `TB_*` відсутні.

## Моніторинг та діагностика
- **Логи**: вся телеметрія у stdout (RichHandler). Основні теги: `[Pipeline]`, `[Stage1 RECEIVE]`, `✅ Опубліковано стан …`.
- **Redis**: канал `ai_one:ui:asset_state`, ключ `ai_one:ui:snapshot`. Для швидкої перевірки можна прочитати `state_manager.state` через UI consumer (запускається автоматично).
- **Тести**: запускати таргетно (`pytest tests/stage1` та відповідні модулі). Обовʼязково виконувати після змін у Stage1/monitor/statemanager.

## Правила змін
- Зберігати «чистоту» Stage1: нові функції мають працювати в межах `stage1` та `app` без сторонніх залежностей, що можуть вплинути на latency.
- Будь-які нові стратегії/шари (наприклад, SMC) повинні мати окремий модуль і вмикатись фіче-флагом. Базовий шлях `app.main → Stage1` має залишатися мінімальним.
- При додаванні нових метрик у стани активів обовʼязково оновлювати UI payload та документацію.

## Типові проблеми та вирішення
| Симптом | Причина | Рішення |
| --- | --- | --- |
| `ModuleNotFoundError: trend_breakout` | Використання старих гілок / кешованих байткодів | Видалити `__pycache__`, переконатися, що немає імпортів у коді.
| `numpy-typing-compat` скаржиться на версію | Нерівні версії `numpy` | Перевстановити залежності (див. кроки вище).
| WSWorker не стартує | Відсутні Redis/ключі або Binance stream недоступний | Перевірити `.env`, мережу, статус Redis.

## Checklist перед продакшном
- [ ] Актуальний `.venv`, встановлені обидва списки залежностей.
- [ ] `python -m app.main` працює ≥15 хвилин без помилок.
- [ ] Redis канал `ai_one:ui:asset_state` оновлюється (можна перевірити через `redis-cli MONITOR`).
- [ ] UI consumer підхоплює schema версії `1.0` без додаткових полів.
- [ ] Усі зміни задокументовані та покриті таргетними тестами.
