@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo [CQA] Создаю виртуальное окружение...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo [CQA] Устанавливаю зависимости...
python -m pip install -q --disable-pip-version-check -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo [CQA] Создан файл .env — впишите ключи API и запустите снова.
  notepad .env
  exit /b 1
)
start "" http://localhost:8000
python -m backend.app
