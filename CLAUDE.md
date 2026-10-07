# CLAUDE.md - WiFiLab

## Overview

WiFiLab - Wi-Fi сканер, site survey и отчёты для macOS: CoreWLAN (pyobjc) + FastAPI + vanilla JS UI на
127.0.0.1 (случайный свободный порт), menubar `.app`. Форк Wi-Fi части FieldTab (анализ каналов, engineering heatmaps, формат плана
`fieldtab.plan/1`); экспорты планшета импортируются. **Публичный репо** github.com/igrbtn/WiFiLab (MIT).

## Quick Start

```bash
./scripts/build_app.sh && open /Applications/WiFiLab.app      # venv ~/.venvs/wifilab, лог ~/Library/Logs/WiFiLab.log
PYTHONPATH=src ~/.venvs/wifilab/bin/python -m wifilab.cli web   # из исходников (SSID/BSSID скрыты, см. ниже)
PYTHONPATH=src ~/.venvs/wifilab/bin/python -m wifilab.cli url   # URL запущенного экземпляра (server.json)
WIFILAB_SCANNER=fake WIFILAB_PORT=8197 PYTHONPATH=src ~/.venvs/wifilab/bin/python -m wifilab.cli web   # симуляция
~/.venvs/wifilab/bin/python -m pytest -q && ~/.venvs/wifilab/bin/ruff check .
```

## Architecture

- `scanner/`: `corewlan.py` (реальный, read-only, никогда не ассоциируется), `fake.py` (симулированный этаж
  30x18 м, RSSI по path loss от позиции - точки съёмки в fake-режиме зависят от координат). `get_scanner("auto")`
  = CoreWLAN на macOS, fake иначе.
- macOS 14+ отдаёт SSID/BSSID только приложению с Location Services. TCC привязан к бандлу: `scripts/launcher.c`
  встраивает libpython (как FieldTab Desk), Info.plist с `NSLocationWhenInUseUsageDescription`; `location.request()`
  только на main thread (menubar через `AppHelper.callAfter`). Python из терминала = анонимные сети (считаются в
  `anonymous`, баннер в UI). Бандл не трогает ~/Documents.
- macOS троттлит активные сканы ("Resource busy") - фолбэк на `cachedScanResults()`, `res["cached"]`.
- `scanloop.ScanService`: один скан за раз (lock), фоновый цикл, `enrich()` добавляет vendor/span/sec_class/snr.
- `analysis.py` - единственный источник истины по каналам: span в номерах каналов (5 МГц), load = max(1, rssi+100)
  на каждый перекрытый канал (как у планшета), рекомендации, issues. JS только рисует (`span_lo/hi` с сервера).
- `store.py` - SQLite: scans/obs (история), projects (весь проект одним JSON), settings.
- Проект = plan (`fieldtab.plan/1`) + points (каждая точка хранит свой скан) + aps (placed, `apmarks.validate_aps`)
  + survey_aps (импорт). `plans.py`/`apmarks.py`/`fieldtab.py` - порт из FieldTab Desk, держать совместимость.
- Heatmaps считаются в браузере (`static/js/heat.js`), отчёт рендерит canvas и шлёт PNG в `/api/report.docx`.
  `heatmap.py` - серверный порт той же логики (Pillow): без картинок от браузера docx и `/api/report.html`
  рендерят карты сами; live-отчёт берёт последний проект с точками. Список видов держать синхронным с
  `reportViews()` в report.js.
- `server.py`: порт 0 = случайный свободный (сокет биндится до uvicorn), `<data dir>/server.json` (600) с URL,
  удаляется при выходе; второй запуск открывает живой экземпляр. `--port`/`WIFILAB_PORT` - фиксированный.
- `oui.py`: полный реестр IEEE (MA-L/MA-M/MA-S) в `data/oui.tsv.gz`, longest-prefix, LAA = "Private (randomized)";
  обновление: `scripts/update_oui.py` (бандл) или Settings (в data dir). `<data dir>/oui.csv` перекрывает всё.
- Точки съёмки: `ScanService.scan_fresh()` пересканирует, пока результат CoreWLAN совпадает с прошлой точкой
  (кэш ОС ~10 с), иначе точка помечается `stale`.

## Configuration

`.env.example`: `WIFILAB_PORT` (пусто = случайный), `WIFILAB_DATA_DIR`, `WIFILAB_SCANNER` (auto|corewlan|fake),
`WIFILAB_SCAN_INTERVAL` (15), `WIFILAB_RETENTION_DAYS` (14), `WIFILAB_AUTOSCAN`. Секретов нет.
Интервал/пауза меняются в UI и хранятся в settings. Доп. OUI: `<data dir>/oui.csv`.
Скриншоты README - только с `WIFILAB_SCANNER=fake` (репо публичный).

## Testing

pytest (`tests/`, fake scanner + `:memory:` store), node-тесты чистых JS-функций (`tests/js/*_test.mjs` через
`tests/test_js.py`), `test_smoke.py` проверяет версии, ASCII в коде и отсутствие длинного тире.
Фикстуры только синтетические (локально-администрируемые MAC 02:..., выдуманные SSID): репо публичный -
никаких реальных сканов, планов, площадок, IP и кредов. Сканы и БД не коммитить.

## Versioning

`VERSION` = `pyproject.toml` = `wifilab.__version__` (сейчас 0.2.1), semver. `LAUNCHER_VERSION` в
`scripts/build_app.sh` менять только при изменении launcher.c/plist (иначе macOS заново спросит Location).
