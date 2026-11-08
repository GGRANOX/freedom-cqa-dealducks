#!/usr/bin/env bash
# Деплой CQA в Google Cloud: Cloud Run + Cloud SQL (PostgreSQL 16) + Secret Manager.
# Идемпотентно: повторный запуск обновляет сервис новой версией кода.
#
#   GCP_KEY=gcp-key.json REGION=europe-west1 ./scripts/deploy_gcp.sh
#
# Ключи моделей и адрес API организаторов берутся из .env и data/organizers_api.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

KEY="${GCP_KEY:-gcp-key.json}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-cqa}"
SQL_INSTANCE="${SQL_INSTANCE:-cqa-pg}"
DB_NAME=cqa
DB_USER=cqa
BUDGET="${DAILY_BUDGET_USD:-20}"
RATE="${RATE_LIMIT_PER_MIN:-30}"

log() { printf "\n\033[1;32m▶ %s\033[0m\n" "$*"; }
envval() { grep -E "^$1=" .env data/organizers_api.txt 2>/dev/null | head -1 | cut -d= -f2- | sed 's/\r$//'; }

[ -f "$KEY" ] || { echo "Нет ключа сервисного аккаунта: $KEY"; exit 1; }
PROJECT=$(python3 -c "import json;print(json.load(open('$KEY'))['project_id'])")
SA=$(python3 -c "import json;print(json.load(open('$KEY'))['client_email'])")
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

log "Авторизация: $SA, проект $PROJECT, регион $REGION"
gcloud auth activate-service-account "$SA" --key-file="$KEY" --quiet
gcloud config set project "$PROJECT" --quiet

log "Включаю API"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  sqladmin.googleapis.com secretmanager.googleapis.com --quiet || echo "  (часть API уже включена или нет прав — продолжаю)"

log "Artifact Registry"
gcloud artifacts repositories describe cqa --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create cqa --repository-format=docker --location "$REGION" --quiet

log "Cloud SQL PostgreSQL ($SQL_INSTANCE)"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=ENTERPRISE \
    --tier=db-f1-micro --region="$REGION" --storage-size=10 --storage-auto-increase --quiet
fi
gcloud sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create "$DB_NAME" --instance "$SQL_INSTANCE" --quiet
DB_PASS_FILE=data/.db_password
[ -f "$DB_PASS_FILE" ] || python3 -c "import secrets;print(secrets.token_urlsafe(24))" > "$DB_PASS_FILE"
DB_PASS=$(cat "$DB_PASS_FILE")
if gcloud sql users list --instance "$SQL_INSTANCE" --format="value(name)" | grep -qx "$DB_USER"; then
  gcloud sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASS" --quiet
else
  gcloud sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASS" --quiet
fi
CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format="value(connectionName)")
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONN}"

log "Секреты"
SECRETS_FLAG=""
put_secret() {  # имя значение
  [ -n "$2" ] || return 0
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    printf "%s" "$2" | gcloud secrets versions add "$1" --data-file=- --quiet >/dev/null
  else
    printf "%s" "$2" | gcloud secrets create "$1" --data-file=- --replication-policy=automatic --quiet >/dev/null
  fi
  SECRETS_FLAG="${SECRETS_FLAG:+$SECRETS_FLAG,}$3=$1:latest"
}
USE_SECRETS=1
gcloud secrets list --limit=1 >/dev/null 2>&1 || USE_SECRETS=0
ENV_EXTRA=""
if [ "$USE_SECRETS" = 1 ]; then
  put_secret cqa-openai-key "$(envval OPENAI_API_KEY)" OPENAI_API_KEY
  put_secret cqa-gemini-key "$(envval GEMINI_API_KEY)" GEMINI_API_KEY
  put_secret cqa-groq-key "$(envval GROQ_API_KEY)" GROQ_API_KEY
  put_secret cqa-database-url "$DATABASE_URL" DATABASE_URL
  put_secret cqa-data-api-url "$(envval CQA_API_URL)" CQA_API_URL
  # доступ сервисного аккаунта Cloud Run к секретам
  RUN_SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
  for s in cqa-openai-key cqa-gemini-key cqa-groq-key cqa-database-url cqa-data-api-url; do
    gcloud secrets describe "$s" >/dev/null 2>&1 && gcloud secrets add-iam-policy-binding "$s" \
      --member="serviceAccount:$RUN_SA" --role=roles/secretmanager.secretAccessor --quiet >/dev/null || true
  done
else
  echo "  Secret Manager недоступен — ключи передаются переменными окружения сервиса"
  ENV_EXTRA="OPENAI_API_KEY=$(envval OPENAI_API_KEY),GEMINI_API_KEY=$(envval GEMINI_API_KEY),DATABASE_URL=$DATABASE_URL,CQA_API_URL=$(envval CQA_API_URL)"
fi

TAG=$(date +%Y%m%d-%H%M%S)
IMAGE="$REGION-docker.pkg.dev/$PROJECT/cqa/app:$TAG"
log "Сборка образа в Cloud Build: $IMAGE"
gcloud builds submit --tag "$IMAGE" --quiet .

log "Деплой Cloud Run ($SERVICE)"
ENV_VARS="CRITERIA_PROVIDER=openai,ANNOTATION_PROVIDER=gemini,OPENAI_MODEL=$(envval OPENAI_MODEL || true),GEMINI_MODEL=$(envval GEMINI_MODEL || true),DEFAULT_GROUP_ID=DEV,DAILY_BUDGET_USD=$BUDGET,RATE_LIMIT_PER_MIN=$RATE,BATCH_CONCURRENCY=8"
ENV_VARS=$(echo "$ENV_VARS" | sed 's/OPENAI_MODEL=,//; s/GEMINI_MODEL=,//')
[ -n "$ENV_EXTRA" ] && ENV_VARS="$ENV_VARS,$ENV_EXTRA"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --platform managed --allow-unauthenticated \
  --add-cloudsql-instances "$CONN" --set-env-vars "^@^$(echo "$ENV_VARS" | tr ',' '@')" \
  ${SECRETS_FLAG:+--set-secrets "$SECRETS_FLAG"} \
  --cpu 1 --memory 1Gi --no-cpu-throttling --timeout 3600 --concurrency 80 --min-instances 0 --max-instances 3 --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format="value(status.url)")
log "Smoke-тест $URL"
python3 scripts/smoke.py "$URL"
echo "$URL" > data/.deployed_url
log "Готово: $URL"
echo "Откат: gcloud run services update-traffic $SERVICE --region $REGION --to-revisions=<предыдущая ревизия>=100"
