# Деплой: Google Cloud Run + Cloud SQL (PostgreSQL)

## Архитектура

```
Браузер ─HTTPS→ Cloud Run (контейнер FastAPI + статика, 0–3 экземпляра, CPU всегда выделен)
                    ├─ Cloud SQL PostgreSQL 16 (unix-socket /cloudsql/…)   — диалоги, версии чек-листа, оценки, апелляции, расходы
                    ├─ Secret Manager — OPENAI_API_KEY, GEMINI_API_KEY, DATABASE_URL, CQA_API_URL
                    ├─ OpenAI (gpt-6.1-sol) — критерии;  Vertex AI (gemini-3.8-flash) — разбор партии
                    └─ API организаторов (Power Automate) — наборы по group_id
```

## Одна команда

```bash
GCP_KEY=gcp-key.json REGION=europe-west1 ./scripts/deploy_gcp.sh
```

Скрипт идемпотентен: включает API, создаёт Artifact Registry, Cloud SQL (db-f1-micro), базу и пользователя, секреты,
собирает образ в Cloud Build, деплоит Cloud Run и запускает `scripts/smoke.py`.

Роли сервисного аккаунта: Cloud Run Admin, Cloud Build Editor, Cloud SQL Admin, Artifact Registry Administrator,
Service Account User, Storage Admin, Service Usage Admin (+ Secret Manager Admin — иначе ключи пойдут переменными окружения).

## Локально как в проде

```bash
docker compose up --build      # приложение + PostgreSQL → http://localhost:8080
```

## Чеклист

**До деплоя**
- [x] `python -m pytest -q` — 37 тестов (SQLite); `TEST_DATABASE_URL=postgresql://… pytest tests/test_api.py` — PostgreSQL
- [x] `python -m scripts.e2e_ui` — 10/10 шагов сценария демо
- [x] `python -m scripts.run_dev` — точность DEV ≥ 95%
- [x] Секреты не в образе (`.dockerignore`: `.env`, `gcp-key.json`, `organizers_api.txt`)
- [x] Бюджет `DAILY_BUDGET_USD=20`, лимит `RATE_LIMIT_PER_MIN=30`

**Деплой**
- [ ] `scripts/deploy_gcp.sh` завершился smoke-тестом ✓
- [ ] Открыть URL, оценить один диалог, проверить переход к реплике и апелляцию
- [ ] `/api/health` → `database: postgresql`

**После**
- [ ] Логи: `gcloud run services logs read cqa --region europe-west1 --limit 50`
- [ ] За час до стоп-кода: загрузить контрольный `group_id`, «Оценить весь набор»

**Откат** — если smoke-тест не прошёл, растут 5xx или не работает оценка:
```bash
gcloud run revisions list --service cqa --region europe-west1
gcloud run services update-traffic cqa --region europe-west1 --to-revisions=<предыдущая>=100
```

## Стоимость

Cloud SQL db-f1-micro ≈ $9/мес, Cloud Run при простое ≈ $0 (min-instances 0), модели ≈ $0.05–0.09 за диалог
(с голосованием и разбором партии). Дневной потолок расходов на модели — `DAILY_BUDGET_USD`.
