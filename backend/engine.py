"""Движок оценки: отдельный вызов на критерий, проверка цитат, подсчёт итога, разбор партии."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from backend import config, llm, prompts, transcript as tr
from backend.contract import EvaluationResult
from backend.scoring import ScoringError, compute_score, validate_judgment

Emit = Optional[Callable[[dict], Awaitable[None]]]
MAX_FIX_RETRIES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _label(d: dict) -> str:
    return "na" if d.get("answer") == "na" else (f"s{d.get('value')}" if d.get("answer") == "scale" else str(d.get("answer")))


async def judge_criterion(c: dict, checklist: dict, conv: dict, rendered: str, metrics: dict,
                          usage: llm.Usage, provider: str, examples: list | None = None) -> dict:
    """Оценка критерия; при уверенности ниже порога — ещё VOTES-1 независимых оценок и голосование."""
    first = await _judge_once(c, checklist, conv, rendered, metrics, usage, provider, examples)
    votes = max(1, config.VOTES)
    if votes == 1 or first.get("_problems") or float(first.get("confidence") or 0) >= config.VOTE_THRESHOLD:
        return first
    extra = await asyncio.gather(*(_judge_once(c, checklist, conv, rendered, metrics, usage, provider, examples)
                                   for _ in range(votes - 1)), return_exceptions=True)
    pool = [first] + [r for r in extra if isinstance(r, dict) and not r.get("_problems")]
    counts: dict[str, list] = {}
    for r in pool:
        counts.setdefault(_label(r), []).append(r)
    best = max(counts.values(), key=lambda rs: (len(rs), sum(float(r.get("confidence") or 0) for r in rs)))
    if len(best) == 1 and len(pool) >= 3 and c["type"] == "scale" and all(r.get("answer") == "scale" for r in pool):
        med = sorted(int(r["value"]) for r in pool)[len(pool) // 2]  # нет большинства — медиана шкалы
        best = [r for r in pool if r.get("value") == med]
    winner = max(best, key=lambda r: float(r.get("confidence") or 0))
    agree = len(best) / len(pool)
    return {**winner, "confidence": round(agree * float(winner.get("confidence") or 0) + (1 - agree) * 0.3, 3),
            "_votes": [_label(r) for r in pool]}


async def _judge_once(c: dict, checklist: dict, conv: dict, rendered: str, metrics: dict,
                      usage: llm.Usage, provider: str, examples: list | None = None) -> dict:
    t = conv["transcript"]
    schema = prompts.criterion_schema(c)
    feedback = ""
    result = None
    for attempt in range(MAX_FIX_RETRIES + 1):
        resp = await llm.call_json(provider, prompts.SYSTEM_CRITERION,
                                   prompts.criterion_user_prompt(c, checklist, conv, rendered, metrics,
                                                                 examples, feedback),
                                   schema, name=f"criterion_{c['id']}")
        usage.add(resp)
        d = resp.data
        problems = []
        j = {"answer": d.get("answer"), "value": d.get("value")}
        try:
            validate_judgment(c, j)
        except ScoringError as e:
            problems.append(str(e))
        evidence, ev_errors = tr.verify_evidence(d.get("evidence") or [], t)
        problems += ev_errors
        if d.get("answer") in ("yes", "scale") and c["type"] != "flag" and not evidence:
            problems.append("нет ни одной цитаты-доказательства")
        result = {**d, "evidence": evidence, "_problems": problems, "_attempts": attempt + 1}
        if not problems:
            break
        feedback = ("; ".join(problems) + ". Цитаты копируй дословно из реплики с указанным индексом.")
    return result


def _assemble_criterion(c: dict, raw: dict, scored: dict) -> dict:
    low = bool(raw.get("_problems"))
    conf = float(raw.get("confidence") or 0)
    if low:
        conf = min(conf, 0.3)
    cr = raw.get("checked_range")
    return {
        **scored,
        "name": c["name"],
        "explanation": raw.get("explanation", ""),
        "evidence": [e for e in raw.get("evidence", []) if e.get("verified")] or raw.get("evidence", []),
        "checked_range": cr if isinstance(cr, dict) else None,
        "na_reason": raw.get("na_reason", "") if raw.get("answer") == "na" else "",
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "low_confidence": low,
        "votes": raw.get("_votes", []),
    }


def _safe_judgment(c: dict, raw: dict) -> dict:
    """Если модель так и не дала валидный ответ — консервативный фолбэк (и флаг низкой уверенности)."""
    j = {"answer": raw.get("answer"), "value": raw.get("value"), "reason": raw.get("explanation", "")}
    try:
        validate_judgment(c, j)
        return j
    except ScoringError:
        if c["type"] == "scale":
            steps = sorted(int(s["value"]) for s in c["scale"])
            v = raw.get("value")
            near = min(steps, key=lambda s: abs(s - v)) if isinstance(v, (int, float)) else steps[0]
            return {"answer": "scale", "value": near}
        if raw.get("answer") == "na" or c["type"] == "flag":
            return {"answer": "no"}
        return {"answer": "no"}


async def annotate(checklist: dict, conv: dict, rendered: str, criteria: list[dict], usage: llm.Usage,
                   provider: str) -> tuple[list, list]:
    resp = await llm.call_json(provider, prompts.SYSTEM_ANNOTATE,
                               prompts.annotate_user_prompt(checklist, rendered, criteria),
                               prompts.annotate_schema(), name="annotations")
    usage.add(resp)
    t = conv["transcript"]
    ann = [a for a in resp.data.get("annotations", [])
           if 0 <= a.get("message_index", -1) < len(t) and t[a["message_index"]]["speaker"] == "operator"]
    sent = [{"message_index": s["message_index"], "value": max(-1.0, min(1.0, float(s["value"])))}
            for s in resp.data.get("sentiment", [])
            if 0 <= s.get("message_index", -1) < len(t) and t[s["message_index"]]["speaker"] == "client"]
    return ann, sent


async def evaluate(conv: dict, checklist: dict, *, provider: str | None = None,
                   annotation_provider: str | None = None, with_annotations: bool = True,
                   examples_by_criterion: dict | None = None, emit: Emit = None) -> dict:
    provider = provider or config.CRITERIA_PROVIDER
    annotation_provider = annotation_provider or config.ANNOTATION_PROVIDER
    t0 = time.perf_counter()
    usage = llm.Usage()
    rendered = tr.render(conv["transcript"], conv.get("channel", ""))
    mets = tr.metrics(conv["transcript"])
    crits = checklist["criteria"]

    async def run(c):
        raw = await judge_criterion(c, checklist, conv, rendered, mets, usage, provider,
                                    (examples_by_criterion or {}).get(c["id"]))
        if emit:
            await emit({"type": "criterion", "criterion_id": c["id"], "name": c["name"],
                        "answer": raw.get("answer"), "value": raw.get("value"),
                        "explanation": raw.get("explanation"), "cost_usd": round(usage.cost_usd, 5),
                        "elapsed_ms": int((time.perf_counter() - t0) * 1000)})
        return c["id"], raw

    raws = dict(await asyncio.gather(*(run(c) for c in crits)))
    judgments = {c["id"]: _safe_judgment(c, raws[c["id"]]) for c in crits}
    scored = compute_score(checklist, judgments)
    by_id = {s["criterion_id"]: s for s in scored["criteria"]}
    criteria = [_assemble_criterion(c, raws[c["id"]], by_id[c["id"]]) for c in crits]
    if scored["gross_error"]["flag"]:
        gid = scored["gross_error"]["criterion_id"]
        scored["gross_error"]["reason"] = raws[gid].get("explanation", "")

    annotations, sentiment = [], []
    if with_annotations:
        try:
            annotations, sentiment = await annotate(checklist, conv, rendered, criteria, usage,
                                                    annotation_provider)
        except llm.LLMError as e:
            if emit:
                await emit({"type": "warning", "message": f"разбор партии не удался: {e}"})

    res = EvaluationResult(
        evaluation_id=uuid.uuid4().hex[:12],
        conversation_id=conv["conversation_id"], checklist_id=checklist["id"],
        checklist_version=checklist.get("version", 1),
        model=", ".join(sorted(usage.by_model)), cost_usd=round(usage.cost_usd, 5),
        duration_ms=int((time.perf_counter() - t0) * 1000), created_at=_now(),
        total_score=scored["total_score"], gross_error=scored["gross_error"],
        sections=scored["sections"], criteria=criteria,
        annotations=annotations, sentiment_curve=sentiment, metrics=mets,
    ).model_dump()
    res["_judgments"] = judgments  # исходные ответы для пересчёта после апелляций
    res["_usage"] = {"calls": usage.calls, "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out}
    if emit:
        await emit({"type": "done", "total_score": res["total_score"], "cost_usd": res["cost_usd"],
                    "duration_ms": res["duration_ms"]})
    return res
