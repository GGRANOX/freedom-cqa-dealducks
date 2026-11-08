"""HTTP API + раздача фронтенда.  Запуск: python -m backend.app  (или uvicorn backend.app:app)"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config, engine, features, llm, storage
from backend.scoring import ScoringError, compute_score

STATIC = config.ROOT / "static"


@asynccontextmanager
async def lifespan(_app):
    storage.db()
    if not storage.list_conversations() and config.CQA_API_URL:
        try:
            records = await features.fetch_dataset(config.DEFAULT_GROUP_ID)
            storage.upsert_conversations(config.DEFAULT_GROUP_ID, records)
        except Exception as e:  # noqa: BLE001
            print("Не удалось загрузить набор при старте:", e)
    yield


app = FastAPI(title="CQA · Freedom Broker", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


_hits: dict[str, list[float]] = {}
_PAID = ("/api/evaluate", "/api/batch", "/api/checklists/lint", "/api/checklists/trial", "/second")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Лимит платных запросов с одного IP (без входа в систему)."""
    lim = config.RATE_LIMIT_PER_MIN
    if lim and request.method == "POST" and any(p in request.url.path for p in _PAID):
        ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
        nowt = time.time()
        hits = [t for t in _hits.get(ip, []) if nowt - t < 60]
        if len(hits) >= lim:
            return JSONResponse({"error": "Слишком много запросов на оценку. Подождите минуту."}, status_code=429,
                                headers={"Retry-After": "60"})
        hits.append(nowt)
        _hits[ip] = hits
        if len(_hits) > 5000:
            _hits.clear()
    return await call_next(request)


from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, e: StarletteHTTPException):
    return JSONResponse({"error": e.detail}, status_code=e.status_code, headers=getattr(e, "headers", None))


@app.exception_handler(ValueError)
async def _value_error(_: Request, e: ValueError):
    return JSONResponse({"error": str(e)}, status_code=400)


@app.exception_handler(llm.LLMError)
async def _llm_error(_: Request, e: llm.LLMError):
    return JSONResponse({"error": f"Ошибка модели: {e}"}, status_code=502)


def _public(ev: dict) -> dict:
    return {k: v for k, v in ev.items() if not k.startswith("_")} | {
        "original_total": ev.get("_original_total"), "usage": ev.get("_usage")}


def _sse(gen):
    async def stream():
        async for ev in gen:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- служебное ----------

@app.get("/api/health")
async def health():
    return {"ok": True, "criteria_model": llm.model_for(config.CRITERIA_PROVIDER),
            "annotation_model": llm.model_for(config.ANNOTATION_PROVIDER),
            "providers": {"openai": bool(config.OPENAI_API_KEY), "gemini": bool(config.GEMINI_API_KEY),
                          "groq": bool(config.GROQ_API_KEY)},
            "data_api": bool(config.CQA_API_URL), "default_group": config.DEFAULT_GROUP_ID,
            "database": storage.backend_name(), "votes": config.VOTES, "vote_threshold": config.VOTE_THRESHOLD,
            "spent_today_usd": round(storage.spent_today(), 4), "daily_budget_usd": config.DAILY_BUDGET_USD}


# ---------- наборы и диалоги ----------

@app.get("/api/groups")
async def groups():
    return storage.groups()


@app.post("/api/groups/{group_id}/sync")
async def sync_group(group_id: str):
    records = await features.fetch_dataset(group_id)
    n = storage.upsert_conversations(group_id, records)
    return {"group_id": group_id, "loaded": n}


class ImportBody(BaseModel):
    group_id: str = "LOCAL"
    payload: dict


@app.post("/api/groups/import")
async def import_group(body: ImportBody):
    """Загрузка ответа API из файла (если сеть до API недоступна)."""
    p = body.payload
    records = p.get("data", {}).get("records") if "data" in p else p.get("records")
    if not records:
        raise ValueError("нет data.records")
    gid = p.get("data", {}).get("dataset_group_id") or body.group_id
    return {"group_id": gid, "loaded": storage.upsert_conversations(gid, records)}


@app.get("/api/conversations")
async def conversations(group_id: Optional[str] = None):
    return storage.list_conversations(group_id)


@app.get("/api/conversations/{cid}")
async def conversation(cid: str):
    c = storage.get_conversation(cid)
    if not c:
        raise HTTPException(404, "диалог не найден")
    from backend import transcript as tr
    return {**c, "metrics": tr.metrics(c["transcript"]),
            "rel_times": tr.rel_times(c["transcript"]),
            "history": storage.evaluation_history(cid)}


@app.get("/api/conversations/{cid}/audio")
async def conversation_audio(cid: str):
    c = storage.get_conversation(cid)
    if not c:
        raise HTTPException(404)
    return await features.audio_src(c)


@app.get("/api/conversations/{cid}/evaluation")
async def conversation_evaluation(cid: str):
    ev = storage.latest_evaluation(cid)
    if not ev:
        raise HTTPException(404, "ещё не оценён")
    return _public(features.recompute(ev))


# ---------- чек-листы ----------

@app.get("/api/checklists")
async def checklists():
    return storage.list_checklists()


@app.get("/api/checklists/{cid}")
async def checklist(cid: str, version: Optional[int] = None):
    cl = storage.get_checklist(cid, version)
    if not cl:
        raise HTTPException(404)
    return {**cl, "versions": storage.checklist_versions(cid)}


class SaveChecklist(BaseModel):
    checklist: dict
    comment: str = ""


def _validate_checklist(cl: dict) -> None:
    ids = [c.get("id") for c in cl.get("criteria", [])]
    if not ids or len(ids) != len(set(ids)) or not all(ids):
        raise ValueError("у каждого критерия должен быть уникальный id")
    sec = {s["id"] for s in cl.get("sections", [])}
    for c in cl["criteria"]:
        if not c.get("name"):
            raise ValueError(f"{c['id']}: нет названия")
        if c.get("type") not in ("binary", "scale", "flag"):
            raise ValueError(f"{c['id']}: тип должен быть binary | scale | flag")
        if c["type"] == "scale" and len(c.get("scale") or []) < 2:
            raise ValueError(f"{c['id']}: у шкалы должно быть минимум 2 ступени")
        if float(c.get("weight", 0)) < 0:
            raise ValueError(f"{c['id']}: вес не может быть отрицательным")
        if sec and c.get("section_id") not in sec:
            raise ValueError(f"{c['id']}: неизвестная секция {c.get('section_id')}")
    if not any(c["type"] != "flag" and float(c.get("weight", 0)) > 0 for c in cl["criteria"]):
        raise ValueError("нужен хотя бы один критерий с весом")


@app.post("/api/checklists")
async def save_checklist(body: SaveChecklist):
    _validate_checklist(body.checklist)
    return storage.save_checklist(body.checklist, body.comment)


class LintBody(BaseModel):
    checklist: dict
    criterion_id: str


@app.post("/api/checklists/lint")
async def lint(body: LintBody):
    _guard_budget()
    c = next((x for x in body.checklist["criteria"] if x["id"] == body.criterion_id), None)
    if not c:
        raise ValueError("критерий не найден")
    return await features.lint_criterion(c, body.checklist)


class TrialBody(BaseModel):
    checklist: dict
    base_version: Optional[int] = None
    conversation_ids: list[str] = []
    group_id: Optional[str] = None
    n: int = 5


def _pick_trial(group_id: Optional[str], n: int) -> list[str]:
    convs = storage.list_conversations(group_id)
    evaluated = [c for c in convs if c["eval_id"]]
    pool = evaluated or convs
    picked, seen = [], set()
    for c in pool:  # разнообразие: язык × канал
        key = (c["language"], c["channel"])
        if key not in seen:
            picked.append(c["id"])
            seen.add(key)
    for c in pool:
        if len(picked) >= n:
            break
        if c["id"] not in picked:
            picked.append(c["id"])
    return picked[:n]


@app.post("/api/checklists/trial")
async def trial(body: TrialBody):
    _guard_budget()
    _validate_checklist(body.checklist)
    ids = body.conversation_ids or _pick_trial(body.group_id, body.n)
    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev):
        await q.put(ev)

    async def work():
        try:
            res = await features.trial_run(body.checklist, body.base_version, ids, emit)
            await q.put({"type": "done", **res})
        except Exception as e:  # noqa: BLE001
            await q.put({"type": "error", "error": str(e)})
        await q.put(None)

    async def gen():
        yield {"type": "start", "conversation_ids": ids}
        task = asyncio.create_task(work())
        while (ev := await q.get()) is not None:
            yield ev
        await task
    return _sse(gen())


# ---------- оценка ----------

class EvalBody(BaseModel):
    conversation_id: str
    checklist_id: Optional[str] = None
    checklist_version: Optional[int] = None
    annotations: bool = True


def _resolve_checklist(cid: Optional[str], version: Optional[int]) -> dict:
    cid = cid or storage.list_checklists()[0]["id"]
    cl = storage.get_checklist(cid, version)
    if not cl:
        raise ValueError("чек-лист не найден")
    return cl


def _examples(cl: dict, conversation_id: str | None = None) -> dict:
    return {c["id"]: storage.appeal_examples(c["id"], exclude_conversation=conversation_id) for c in cl["criteria"]}


def _guard_budget() -> None:
    """Дневной потолок расходов на модели (защита публичной ссылки без входа)."""
    if config.DAILY_BUDGET_USD and storage.spent_today() >= config.DAILY_BUDGET_USD:
        raise HTTPException(429, f"Дневной бюджет на оценку (${config.DAILY_BUDGET_USD:.0f}) исчерпан. "
                                 "Просмотр результатов доступен, новые оценки — завтра.")


async def _evaluate_and_save(conv: dict, cl: dict, annotations: bool, emit=None) -> dict:
    res = await engine.evaluate(conv, cl, with_annotations=annotations,
                                examples_by_criterion=_examples(cl, conv["conversation_id"]), emit=emit)
    return storage.save_evaluation(res)


@app.post("/api/evaluate")
async def evaluate(body: EvalBody):
    """Живая оценка: критерии приходят по мере готовности (SSE)."""
    conv = storage.get_conversation(body.conversation_id)
    if not conv:
        raise HTTPException(404, "диалог не найден")
    _guard_budget()
    cl = _resolve_checklist(body.checklist_id, body.checklist_version)
    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev):
        if ev.get("type") != "done":
            await q.put(ev)

    async def work():
        try:
            res = await _evaluate_and_save(conv, cl, body.annotations, emit)
            await q.put({"type": "done", "evaluation_id": res["evaluation_id"],
                         "total_score": res["total_score"], "cost_usd": res["cost_usd"],
                         "duration_ms": res["duration_ms"]})
        except Exception as e:  # noqa: BLE001
            await q.put({"type": "error", "error": str(e)})
        await q.put(None)

    async def gen():
        yield {"type": "start", "criteria": [{"id": c["id"], "name": c["name"]} for c in cl["criteria"]],
               "model": llm.model_for(config.CRITERIA_PROVIDER), "checklist_version": cl["version"]}
        task = asyncio.create_task(work())
        while (ev := await q.get()) is not None:
            yield ev
        await task
    return _sse(gen())


@app.get("/api/evaluations/{eid}")
async def evaluation(eid: str):
    ev = storage.get_evaluation(eid)
    if not ev:
        raise HTTPException(404)
    return _public(features.recompute(ev))


# ---------- пакетная оценка ----------

_jobs: dict[str, dict] = {}
_job_tasks: set = set()


class BatchBody(BaseModel):
    group_id: str
    checklist_id: Optional[str] = None
    checklist_version: Optional[int] = None
    sync: bool = True
    only_new: bool = False
    annotations: bool = True
    concurrency: Optional[int] = None


@app.post("/api/batch")
async def batch(body: BatchBody):
    _guard_budget()
    cl = _resolve_checklist(body.checklist_id, body.checklist_version)
    if body.sync:
        try:
            storage.upsert_conversations(body.group_id, await features.fetch_dataset(body.group_id))
        except Exception as e:  # noqa: BLE001 — если API недоступен, работаем с уже загруженными
            if not storage.list_conversations(body.group_id):
                raise ValueError(f"не удалось загрузить набор {body.group_id}: {e}")
    convs = storage.list_conversations(body.group_id)
    if body.only_new:
        convs = [c for c in convs if not (c["eval_id"] and c["checklist_id"] == cl["id"]
                                          and c["checklist_version"] == cl["version"])]
    job = {"id": storage.new_id(), "kind": "batch", "status": "running", "group_id": body.group_id,
           "checklist_id": cl["id"], "checklist_version": cl["version"], "total": len(convs), "done": 0,
           "failed": 0, "cost_usd": 0.0, "started": time.time(), "items": {c["id"]: "queued" for c in convs},
           "errors": {}, "created_at": storage.now()}
    _jobs[job["id"]] = job
    storage.save_job(job)  # прогресс в БД: на Cloud Run опрос может прийти в другой экземпляр
    sem = asyncio.Semaphore(body.concurrency or config.BATCH_CONCURRENCY)

    async def one(cid: str):
        async with sem:
            job["items"][cid] = "running"
            for attempt in range(2):
                try:
                    res = await engine.evaluate(storage.get_conversation(cid), cl,
                                                with_annotations=body.annotations,
                                                examples_by_criterion=_examples(cl, cid))
                    storage.save_evaluation(res)
                    job["cost_usd"] = round(job["cost_usd"] + res["cost_usd"], 5)
                    job["done"] += 1
                    job["items"][cid] = "done"
                    job["errors"].pop(cid, None)
                    break
                except Exception as e:  # noqa: BLE001
                    job["errors"][cid] = str(e)[:300]
            else:
                job["failed"] += 1
                job["items"][cid] = "failed"
            job["elapsed"] = round(time.time() - job["started"], 1)
            storage.save_job(job)

    async def run():
        await asyncio.gather(*(one(c["id"]) for c in convs))
        job["status"] = "finished"
        job["elapsed"] = round(time.time() - job["started"], 1)
        storage.save_job(job)

    task = asyncio.create_task(run())
    _job_tasks.add(task)  # держим ссылку, чтобы задачу не собрал GC
    task.add_done_callback(_job_tasks.discard)
    return job


@app.get("/api/batch/{jid}")
async def batch_status(jid: str):
    job = _jobs.get(jid) or storage.get_job(jid)
    if not job:
        raise HTTPException(404)
    elapsed = job.get("elapsed") if job["status"] == "finished" else round(time.time() - job["started"], 1)
    return {**job, "elapsed": elapsed}


# ---------- апелляции ----------

class AppealBody(BaseModel):
    evaluation_id: str
    criterion_id: str
    comment: str


@app.post("/api/appeals")
async def create_appeal(body: AppealBody):
    if not body.comment.strip():
        raise ValueError("добавьте комментарий")
    return features.create_appeal(body.evaluation_id, body.criterion_id, body.comment)


@app.get("/api/appeals")
async def appeals(status: Optional[str] = None, evaluation_id: Optional[str] = None):
    return storage.list_appeals(status, evaluation_id)


@app.get("/api/appeals/{aid}")
async def appeal(aid: str):
    a = storage.get_appeal(aid)
    if not a:
        raise HTTPException(404)
    ev = storage.get_evaluation(a["evaluation_id"])
    cl = storage.get_checklist(ev["checklist_id"], ev["checklist_version"])
    return {**a, "criterion": next(c for c in cl["criteria"] if c["id"] == a["criterion_id"]),
            "evaluation_total": ev["total_score"]}


class DecisionBody(BaseModel):
    action: str
    reason: str
    new_answer: Optional[str] = None
    new_value: Optional[int] = None


@app.post("/api/appeals/{aid}/decision")
async def decide(aid: str, body: DecisionBody):
    try:
        return features.decide_appeal(aid, body.action, body.reason, body.new_answer, body.new_value)
    except ScoringError as e:
        raise ValueError(str(e))


@app.post("/api/appeals/{aid}/second")
async def second(aid: str):
    _guard_budget()
    return await features.ai_second(aid)


# ---------- экспорт (строгий режим / Dynamics 365) ----------

@app.get("/api/export.csv")
async def export_csv(group_id: Optional[str] = None):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    header_done = False
    for c in storage.list_conversations(group_id):
        ev = storage.latest_evaluation(c["id"])
        if not ev:
            continue
        ev = features.recompute(ev)
        if not header_done:
            w.writerow(["conversation_id", "channel", "language", "checklist", "version", "total_score",
                        "gross_error"] + [f"{x['criterion_id']} {x['name']}" for x in ev["criteria"]])
            header_done = True
        w.writerow([c["id"], c["channel"], c["language"], ev["checklist_id"], ev["checklist_version"],
                    ev["total_score"], "Да" if ev["gross_error"]["flag"] else "Нет"] +
                   [("N/A" if x["answer"] == "na" else
                     f"{x['score']}/{x['max_score']}" if x["max_score"] else
                     ("Да" if x["answer"] == "yes" else "Нет")) for x in ev["criteria"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=cqa_export.csv"})


# ---------- фронтенд ----------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/assets/{path:path}")
async def assets(path: str):
    p = (config.ROOT / "assets" / path).resolve()
    if not str(p).startswith(str((config.ROOT / "assets").resolve())) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn
    print(f"\n  CQA запущен: http://localhost:{config.PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
