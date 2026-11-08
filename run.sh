#!/usr/bin/env bash
# Запуск CQA одной командой: ./run.sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "Создан .env — впишите ключи API и запустите снова."; exit 1; fi
( sleep 2; (xdg-open http://localhost:8000 || open http://localhost:8000) >/dev/null 2>&1 || true ) &
python -m backend.app
