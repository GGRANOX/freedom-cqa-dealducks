"""Хранилище: PostgreSQL (прод, DATABASE_URL=postgresql://…) или SQLite (локально, по умолчанию).

Один SQL для обеих СУБД: плейсхолдеры `?` переводятся в `%s` для PostgreSQL, upsert — через
`INSERT … ON CONFLICT … DO UPDATE` (поддерживают обе).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY, group_id TEXT, channel TEXT, language TEXT, data TEXT NOT NULL, fetched_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS conv_group ON conversations(group_id)",
    """CREATE TABLE IF NOT EXISTS checklists (
      id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT, data TEXT NOT NULL, comment TEXT, created_at TEXT,
      PRIMARY KEY (id, version))""",
    """CREATE TABLE IF NOT EXISTS evaluations (
      id TEXT PRIMARY KEY, conversation_id TEXT, checklist_id TEXT, checklist_version INTEGER,
      total_score REAL, original_total REAL, gross INTEGER, data TEXT NOT NULL, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ev_conv ON evaluations(conversation_id, created_at)",
    """CREATE TABLE IF NOT EXISTS appeals (
      id TEXT PRIMARY KEY, evaluation_id TEXT, conversation_id TEXT, criterion_id TEXT, comment TEXT, status TEXT,
      original TEXT, decision TEXT, final TEXT, second TEXT, created_at TEXT, decided_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ap_eval ON appeals(evaluation_id)",
    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, data TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, cost_usd REAL NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0)",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class _DB:
    def __init__(self, url: str):
        self.pg = url.startswith(("postgres://", "postgresql://"))
        self.lock = threading.RLock()
        if self.pg:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            self.pool = ConnectionPool(url.replace("postgres://", "postgresql://", 1), min_size=1, max_size=10,
                                       kwargs={"row_factory": dict_row, "autocommit": True}, open=True)
        else:
            path = url.replace("sqlite:///", "") if url.startswith("sqlite") else url
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        for stmt in SCHEMA:
            self.execute(stmt)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql: str, args: tuple = ()) -> None:
        if self.pg:
            with self.pool.connection() as c:
                c.execute(self._sql(sql), args)
        else:
            with self.lock:
                self.conn.execute(sql, args)
                self.conn.commit()

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        if self.pg:
            with self.pool.connection() as c:
                return list(c.execute(self._sql(sql), args).fetchall())
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def close(self) -> None:
        if self.pg:
            self.pool.close()
        else:
            self.conn.close()


_db: _DB | None = None
_init_lock = threading.Lock()


def db() -> _DB:
    global _db
    with _init_lock:
        if _db is None:
            _db = _DB(config.DATABASE_URL or config.DB_PATH)
            _seed_checklist()
    return _db


def reset() -> None:
    """Закрыть соединение (тесты / смена DATABASE_URL)."""
    global _db
    if _db is not None:
        _db.close()
    _db = None


def backend_name() -> str:
    return "postgresql" if db().pg else "sqlite"


def _exec(sql: str, args: tuple = ()) -> None:
    db().execute(sql, args)


def _q(sql: str, args: tuple = ()) -> list[dict]:
    return db().query(sql, args)


# ---------- чек-листы ----------

def _seed_checklist() -> None:
    if _db.query("SELECT 1 AS x FROM checklists LIMIT 1"):
        return
    default = json.loads((Path(__file__).parent / "checklist_default.json").read_text("utf-8"))
    _db.execute("INSERT INTO checklists (id, version, name, data, comment, created_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (id, version) DO NOTHING",
                (default["id"], 1, default["name"], json.dumps(default, ensure_ascii=False),
                 "Исходный чек-лист из PDF организаторов", now()))


def list_checklists() -> list[dict]:
    rows = _q("SELECT c.id, c.version, c.name FROM checklists c "
              "WHERE c.version = (SELECT MAX(version) FROM checklists x WHERE x.id = c.id) ORDER BY c.id")
    return rows


def checklist_versions(cid: str) -> list[dict]:
    return _q("SELECT id, version, name, comment, created_at FROM checklists WHERE id=? ORDER BY version DESC", (cid,))


def get_checklist(cid: str, version: Optional[int] = None) -> Optional[dict]:
    if version:
        r = _q("SELECT data FROM checklists WHERE id=? AND version=?", (cid, version))
    else:
        r = _q("SELECT data FROM checklists WHERE id=? ORDER BY version DESC LIMIT 1", (cid,))
    return json.loads(r[0]["data"]) if r else None


def save_checklist(cl: dict, comment: str = "") -> dict:
    """Каждое сохранение = новая версия (повтор при гонке за номер версии)."""
    for _ in range(5):
        r = _q("SELECT MAX(version) AS v FROM checklists WHERE id=?", (cl["id"],))
        v = (r[0]["v"] or 0) + 1
        new = {**cl, "version": v}
        try:
            _exec("INSERT INTO checklists (id, version, name, data, comment, created_at) VALUES (?,?,?,?,?,?)",
                  (new["id"], v, new.get("name", ""), json.dumps(new, ensure_ascii=False), comment, now()))
            return new
        except Exception as e:  # noqa: BLE001 — конфликт первичного ключа: повторяем со следующей версией
            if "unique" not in str(e).lower() and "primary" not in str(e).lower() and "duplicate" not in str(e).lower():
                raise
    raise RuntimeError("не удалось сохранить версию чек-листа")


# ---------- диалоги ----------

def upsert_conversations(group_id: str, records: list[dict]) -> int:
    ts = now()
    for r in records:
        _exec("INSERT INTO conversations (id, group_id, channel, language, data, fetched_at) VALUES (?,?,?,?,?,?) "
              "ON CONFLICT (id) DO UPDATE SET group_id=excluded.group_id, channel=excluded.channel, "
              "language=excluded.language, data=excluded.data, fetched_at=excluded.fetched_at",
              (r["conversation_id"], group_id, r.get("channel"), r.get("language"),
               json.dumps(r, ensure_ascii=False), ts))
    return len(records)


def groups() -> list[dict]:
    return _q("SELECT group_id, COUNT(*) AS n, MAX(fetched_at) AS fetched_at FROM conversations GROUP BY group_id "
              "ORDER BY group_id")


def get_conversation(cid: str) -> Optional[dict]:
    r = _q("SELECT data FROM conversations WHERE id=?", (cid,))
    return json.loads(r[0]["data"]) if r else None


def list_conversations(group_id: Optional[str] = None) -> list[dict]:
    sql = """SELECT c.id, c.group_id, c.channel, c.language, c.data,
                    e.id AS eval_id, e.total_score, e.original_total, e.gross, e.checklist_id,
                    e.checklist_version, e.created_at AS evaluated_at,
                    (SELECT COUNT(*) FROM appeals a WHERE a.evaluation_id=e.id AND a.status='open') AS open_appeals
             FROM conversations c
             LEFT JOIN evaluations e ON e.id = (SELECT id FROM evaluations x WHERE x.conversation_id=c.id
                                                ORDER BY created_at DESC LIMIT 1)"""
    args: tuple = ()
    if group_id:
        sql += " WHERE c.group_id=?"
        args = (group_id,)
    out = []
    for d in _q(sql + " ORDER BY c.id", args):
        data = json.loads(d.pop("data"))
        t = data.get("transcript", [])
        first_client = next((m["text"] for m in t if m.get("speaker") == "client"
                             and len(m["text"]) > 3 and not m["text"].strip().isdigit()), "")
        d.update(messages=len(t), preview=first_client[:140],
                 operator_messages=sum(1 for m in t if m.get("speaker") == "operator"),
                 has_audio=bool(data.get("audio_url")))
        out.append(d)
    return out


# ---------- оценки ----------

def save_evaluation(res: dict) -> dict:
    eid = res.get("evaluation_id") or new_id()
    res["evaluation_id"] = eid
    res.setdefault("_original_total", res.get("total_score"))
    _exec("INSERT INTO evaluations (id, conversation_id, checklist_id, checklist_version, total_score, "
          "original_total, gross, data, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
          "ON CONFLICT (id) DO UPDATE SET total_score=excluded.total_score, gross=excluded.gross, data=excluded.data",
          (eid, res["conversation_id"], res["checklist_id"], res["checklist_version"], res.get("total_score"),
           res.get("_original_total"), int(res["gross_error"]["flag"]), json.dumps(res, ensure_ascii=False),
           res.get("created_at") or now()))
    return res


def get_evaluation(eid: str) -> Optional[dict]:
    r = _q("SELECT data FROM evaluations WHERE id=?", (eid,))
    return json.loads(r[0]["data"]) if r else None


def latest_evaluation(conversation_id: str) -> Optional[dict]:
    r = _q("SELECT data FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (conversation_id,))
    return json.loads(r[0]["data"]) if r else None


def evaluation_history(conversation_id: str) -> list[dict]:
    return _q("SELECT id, checklist_id, checklist_version, total_score, original_total, gross, created_at "
              "FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC", (conversation_id,))


# ---------- апелляции ----------

def _appeal_row(d: dict) -> dict:
    d = dict(d)
    for k in ("original", "decision", "final", "second"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def create_appeal(evaluation_id: str, conversation_id: str, criterion_id: str, comment: str,
                  original: dict) -> dict:
    aid = new_id()
    _exec("INSERT INTO appeals (id, evaluation_id, conversation_id, criterion_id, comment, status, original, "
          "decision, final, second, created_at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (aid, evaluation_id, conversation_id, criterion_id, comment, "open",
           json.dumps(original, ensure_ascii=False), None, None, None, now(), None))
    return get_appeal(aid)


def get_appeal(aid: str) -> Optional[dict]:
    r = _q("SELECT * FROM appeals WHERE id=?", (aid,))
    return _appeal_row(r[0]) if r else None


def list_appeals(status: Optional[str] = None, evaluation_id: Optional[str] = None) -> list[dict]:
    sql, args = "SELECT * FROM appeals WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if evaluation_id:
        sql += " AND evaluation_id=?"
        args.append(evaluation_id)
    return [_appeal_row(r) for r in _q(sql + " ORDER BY created_at DESC, id DESC", tuple(args))]


_APPEAL_COLS = {"status", "decision", "final", "second", "decided_at"}


def update_appeal(aid: str, **fields: Any) -> dict:
    bad = set(fields) - _APPEAL_COLS
    if bad:
        raise ValueError(f"недопустимые поля: {bad}")
    sets, args = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        args.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    _exec(f"UPDATE appeals SET {', '.join(sets)} WHERE id=?", (*args, aid))
    return get_appeal(aid)


def appeal_examples(criterion_id: str, limit: int = 3, exclude_conversation: str | None = None) -> list[dict]:
    """Последние решения проверяющего по критерию — для обучения на апелляциях.
    Тестовые апелляции (комментарий начинается с «E2E») и апелляции по самому оцениваемому диалогу не используются."""
    out = []
    for a in _q("SELECT * FROM appeals WHERE criterion_id=? AND status<>'open' ORDER BY decided_at DESC, id DESC",
                (criterion_id,)):
        a = _appeal_row(a)
        if not a["decision"] or (a.get("comment") or "").startswith("E2E"):
            continue
        if exclude_conversation and a["conversation_id"] == exclude_conversation:
            continue
        o = a["original"] or {}
        out.append({"fragment": o.get("fragment", ""),
                    "original": _fmt_ans(o.get("answer"), o.get("value")),
                    "decision": "подтверждено" if a["status"] == "confirmed"
                    else _fmt_ans(a["decision"].get("new_answer"), a["decision"].get("new_value")),
                    "reason": a["decision"].get("reason", "") + (f" (комментарий апелляции: {a['comment']})"
                                                                 if a.get("comment") else "")})
        if len(out) >= limit:
            break
    return out


def _fmt_ans(answer, value) -> str:
    return {"yes": "Да", "no": "Нет", "na": "N/A"}.get(answer, str(value))


# ---------- задания ----------

def save_job(job: dict) -> None:
    _exec("INSERT INTO jobs (id, kind, status, data, created_at) VALUES (?,?,?,?,?) "
          "ON CONFLICT (id) DO UPDATE SET status=excluded.status, data=excluded.data",
          (job["id"], job.get("kind", ""), job.get("status", ""), json.dumps(job, ensure_ascii=False),
           job.get("created_at") or now()))


def get_job(jid: str) -> Optional[dict]:
    r = _q("SELECT data FROM jobs WHERE id=?", (jid,))
    return json.loads(r[0]["data"]) if r else None


# ---------- расходы (дневной бюджет) ----------

def add_spend(cost_usd: float, calls: int = 1) -> None:
    _exec("INSERT INTO spend (day, cost_usd, calls) VALUES (?,?,?) ON CONFLICT (day) DO UPDATE SET "
          "cost_usd = spend.cost_usd + excluded.cost_usd, calls = spend.calls + excluded.calls",
          (today(), float(cost_usd), int(calls)))


def spent_today() -> float:
    r = _q("SELECT cost_usd FROM spend WHERE day=?", (today(),))
    return float(r[0]["cost_usd"]) if r else 0.0
