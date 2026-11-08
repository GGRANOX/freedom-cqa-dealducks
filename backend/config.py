import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
# необязательный второй файл с адресом API организаторов (не коммитится), если его нет в .env
load_dotenv(ROOT / "data" / "organizers_api.txt", override=False, encoding="utf-8-sig")


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-6.1-sol")
OPENAI_REASONING = env("OPENAI_REASONING_EFFORT", "medium")
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.8-flash")
GROQ_API_KEY = env("GROQ_API_KEY")
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")

CRITERIA_PROVIDER = env("CRITERIA_PROVIDER", "openai")
ANNOTATION_PROVIDER = env("ANNOTATION_PROVIDER", "gemini")
TEMPERATURE = float(env("TEMPERATURE", "0"))

CQA_API_URL = env("CQA_API_URL")
DEFAULT_GROUP_ID = env("DEFAULT_GROUP_ID", "DEV")

PORT = int(env("PORT", "8000"))
DB_PATH = env("DB_PATH", str(ROOT / "data" / "cqa.db"))
DATABASE_URL = env("DATABASE_URL")  # postgresql://user:pass@host/db — если пусто, SQLite по DB_PATH

# Защита публичного развёртывания (без входа): дневной бюджет на модели и лимит запросов с одного IP
DAILY_BUDGET_USD = float(env("DAILY_BUDGET_USD", "0"))      # 0 = без ограничения
RATE_LIMIT_PER_MIN = int(env("RATE_LIMIT_PER_MIN", "0"))    # платных запросов в минуту с IP, 0 = без ограничения
BATCH_CONCURRENCY = int(env("BATCH_CONCURRENCY", "8"))

# Самосогласованность: при уверенности ниже порога — VOTES независимых оценок и голосование
VOTES = int(env("VOTES", "3"))
VOTE_THRESHOLD = float(env("VOTE_THRESHOLD", "0.95"))

# Цены $ за 1M токенов (вход, выход). Переопределяются через .env: PRICE_<MODEL>=in,out
PRICES = {
    "gpt-6.1-sol": (2.0, 10.0),
    "gemini-3.8-flash": (0.375, 1.875),
    "gemini-3.5-flash": (0.0, 0.0),
    "openai/gpt-oss-120b": (0.15, 0.60),
}


def price(model: str) -> tuple[float, float]:
    key = "PRICE_" + "".join(ch if ch.isalnum() else "_" for ch in model).upper()
    raw = env(key)
    if raw:
        a, b = raw.split(",")
        return float(a), float(b)
    return PRICES.get(model, (0.0, 0.0))
