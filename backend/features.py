"""Апелляции и пересчёт, ИИ-секундант, проверка формулировки критерия, пробный прогон, аудио звонков."""
from __future__ import annotations

import asyncio
import copy
import html
import re
from urllib.parse import quote

import httpx

from backend import config, engine, llm, storage, transcript as tr
from backend.scoring import apply_overrides, compute_score, validate_judgment, ScoringError


# ---------- апелляции ----------

def _fragment(conv: dict, crit: dict, radius: int = 2) -> tuple[str, list[int]]:
    t = conv["transcript"]
    idx = sorted({e["message_index"] for e in crit.get("evidence", []) if isinstance(e.get("message_index"), int)})
    if not idx and crit.get("checked_range"):
        idx = list(range(crit["checked_range"]["from_index"], crit["checked_range"]["to_index"] + 1))
    if not idx:
        idx = [i for i, m in enumerate(t) if m["speaker"] == "operator"][:3]
    lo, hi = max(0, min(idx) - radius), min(len(t) - 1, max(idx) + radius)
    lines = [f"[{i}] {tr.SPEAKER_LABEL.get(t[i]['speaker'], t[i]['speaker'])}: {t[i]['text']}"
             for i in range(lo, hi + 1)]
    return "\n".join(lines)[:1500], list(range(lo, hi + 1))


def create_appeal(evaluation_id: str, criterion_id: str, comment: str) -> dict:
    ev = storage.get_evaluation(evaluation_id)
    if not ev:
        raise ValueError("оценка не найдена")
    crit = next((c for c in ev["criteria"] if c["criterion_id"] == criterion_id), None)
    if not crit:
        raise ValueError("критерий не найден")
    if any(a["criterion_id"] == criterion_id and a["status"] == "open"
           for a in storage.list_appeals(evaluation_id=evaluation_id)):
        raise ValueError("по этому критерию уже есть открытая апелляция")
    conv = storage.get_conversation(ev["conversation_id"])
    fragment, rng = _fragment(conv, crit) if conv else ("", [])
    original = {**{k: crit.get(k) for k in ("answer", "value", "score", "max_score", "explanation",
                                            "evidence", "name")},
                "total_score": ev["total_score"], "fragment": fragment, "fragment_range": rng}
    return storage.create_appeal(evaluation_id, ev["conversation_id"], criterion_id, comment, original)


def recompute(ev: dict) -> dict:
    """Пересчитать оценку с учётом всех решений проверяющего (поверх исходных ответов модели)."""
    checklist = storage.get_checklist(ev["checklist_id"], ev["checklist_version"])
    overrides = {}
    appeals = storage.list_appeals(evaluation_id=ev["evaluation_id"])
    for a in sorted(appeals, key=lambda a: a.get("decided_at") or ""):
        if a["status"] == "changed" and a["decision"]:
            overrides[a["criterion_id"]] = {"answer": a["decision"]["new_answer"],
                                            "value": a["decision"].get("new_value"),
                                            "reason": a["decision"].get("reason", "")}
    judgments = apply_overrides(ev["_judgments"], overrides)
    scored = compute_score(checklist, judgments)
    by_id = {s["criterion_id"]: s for s in scored["criteria"]}
    for c in ev["criteria"]:
        s = by_id[c["criterion_id"]]
        c.update({k: s[k] for k in ("answer", "value", "score", "max_score", "effective_weight")})
        c["overridden"] = c["criterion_id"] in overrides
    ev["total_score"] = scored["total_score"]
    ev["sections"] = scored["sections"]
    if scored["gross_error"]["flag"] != ev["gross_error"]["flag"]:
        ev["gross_error"] = scored["gross_error"]
        if scored["gross_error"]["flag"]:
            ev["gross_error"]["reason"] = overrides.get(scored["gross_error"]["criterion_id"], {}).get("reason", "")
    ev["appeals"] = [{
        "appeal_id": a["id"], "criterion_id": a["criterion_id"], "comment": a["comment"],
        "status": a["status"], "created_at": a["created_at"], "original": a["original"],
        "decision": a["decision"], "final": a["final"] or {}} for a in appeals]
    return ev


def decide_appeal(aid: str, action: str, reason: str, new_answer: str | None = None,
                  new_value: int | None = None) -> dict:
    a = storage.get_appeal(aid)
    if not a:
        raise ValueError("апелляция не найдена")
    if a["status"] != "open":
        raise ValueError("апелляция уже рассмотрена")
    if not reason.strip():
        raise ValueError("укажите причину решения")
    ev = storage.get_evaluation(a["evaluation_id"])
    checklist = storage.get_checklist(ev["checklist_id"], ev["checklist_version"])
    crit_def = next(c for c in checklist["criteria"] if c["id"] == a["criterion_id"])
    if action == "confirm":
        decision = {"new_answer": a["original"]["answer"], "new_value": a["original"].get("value"),
                    "reason": reason}
        status = "confirmed"
    elif action == "change":
        if crit_def["type"] == "scale" and new_answer != "na":
            new_answer = "scale"
        j = {"answer": new_answer, "value": new_value if new_answer == "scale" else None}
        validate_judgment(crit_def, j)
        decision = {"new_answer": j["answer"], "new_value": j["value"], "reason": reason}
        status = "changed"
    else:
        raise ValueError("action: confirm | change")
    storage.update_appeal(aid, status=status, decision=decision, decided_at=storage.now())
    ev = recompute(ev)
    crit = next(c for c in ev["criteria"] if c["criterion_id"] == a["criterion_id"])
    final = {"answer": crit["answer"], "value": crit["value"], "score": crit["score"],
             "max_score": crit["max_score"], "total_score": ev["total_score"]}
    decision["new_score"] = crit["score"]
    storage.update_appeal(aid, final=final, decision=decision)
    ev = recompute(ev)
    storage.save_evaluation(ev)
    return storage.get_appeal(aid)


# ---------- ИИ-секундант ----------

SECOND_SYSTEM = """Ты — ИИ-секундант проверяющего в системе контроля качества Freedom Broker.
Оператор оспорил оценку критерия. Беспристрастно проверь, права ли исходная оценка, строго по формулировке
критерия. Сошлись на конкретные реплики [индекс] с дословной цитатой. Ответ на русском, кратко."""


def _second_schema(c: dict) -> dict:
    s = engine.prompts.criterion_schema(c)
    return {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["confirm", "change"]},
        "suggested_answer": s["properties"]["answer"],
        "suggested_value": s["properties"]["value"],
        "reasoning": {"type": "string"},
        "evidence": s["properties"]["evidence"],
        "confidence": {"type": "number"}},
        "required": ["verdict", "suggested_answer", "suggested_value", "reasoning", "evidence", "confidence"],
        "additionalProperties": False}


async def ai_second(aid: str) -> dict:
    a = storage.get_appeal(aid)
    if a.get("second"):
        return a["second"]
    ev = storage.get_evaluation(a["evaluation_id"])
    conv = storage.get_conversation(a["conversation_id"])
    checklist = storage.get_checklist(ev["checklist_id"], ev["checklist_version"])
    c = next(x for x in checklist["criteria"] if x["id"] == a["criterion_id"])
    o = a["original"]
    user = (engine.prompts._criterion_block(c, checklist) +
            f"\n\nИСХОДНАЯ ОЦЕНКА: {o.get('answer')} {o.get('value') or ''}; обоснование: {o.get('explanation')}"
            f"\nКОММЕНТАРИЙ АПЕЛЛЯЦИИ: {a['comment']}\n\nТРАНСКРИПТ:\n" + tr.render(conv["transcript"]))
    r = await llm.call_json(config.CRITERIA_PROVIDER, SECOND_SYSTEM, user, _second_schema(c), "second")
    d = r.data
    d["evidence"], _ = tr.verify_evidence(d.get("evidence") or [], conv["transcript"])
    d["cost_usd"] = round(r.cost_usd, 5)
    storage.update_appeal(aid, second=d)
    return d


# ---------- проверка формулировки критерия ----------

LINT_SYSTEM = """Ты — методолог контроля качества контакт-центра брокерской компании. Проверь формулировку
критерия чек-листа: где она двусмысленна (разные проверяющие поставят разные оценки), каких условий не хватает,
нужно ли правило N/A и каким оно должно быть. Предложи улучшенную формулировку и правила применения
(guidance) для ИИ-оценщика. Пиши по-русски, конкретно."""

LINT_SCHEMA = {"type": "object", "properties": {
    "clarity_score": {"type": "integer", "description": "1..5, 5 — однозначно"},
    "ambiguities": {"type": "array", "items": {"type": "string"}},
    "suggested_description": {"type": "string"},
    "suggested_guidance": {"type": "string"},
    "na_recommended": {"type": "boolean"},
    "suggested_na_condition": {"type": "string"}},
    "required": ["clarity_score", "ambiguities", "suggested_description", "suggested_guidance",
                 "na_recommended", "suggested_na_condition"], "additionalProperties": False}


async def lint_criterion(criterion: dict, checklist: dict) -> dict:
    others = "\n".join(f"- {c['id']} {c['name']}: {c.get('description', '')}"
                       for c in checklist.get("criteria", []) if c.get("id") != criterion.get("id"))
    user = (f"Критерий:\n{engine.prompts._criterion_block(criterion, checklist)}\n\n"
            f"Остальные критерии (не дублируй их):\n{others}")
    r = await llm.call_json(config.CRITERIA_PROVIDER, LINT_SYSTEM, user, LINT_SCHEMA, "lint")
    return {**r.data, "cost_usd": round(r.cost_usd, 5)}


# ---------- пробный прогон изменённого чек-листа ----------

def _changed_ids(old: dict | None, new: dict) -> list[str]:
    if not old:
        return [c["id"] for c in new["criteria"]]
    keys = ("name", "type", "weight", "na_allowed", "description", "na_condition", "guidance", "scale")
    o = {c["id"]: c for c in old["criteria"]}
    return [c["id"] for c in new["criteria"]
            if c["id"] not in o or any(c.get(k) != o[c["id"]].get(k) for k in keys)]


async def trial_run(new_cl: dict, base_version: int | None, conversation_ids: list[str],
                    emit=None) -> dict:
    """«Было → стало» на нескольких диалогах. Неизменённые критерии берутся из прошлой оценки
    (той же версии), изменённые/новые — оцениваются заново."""
    old_cl = storage.get_checklist(new_cl["id"], base_version) if base_version else None
    changed = _changed_ids(old_cl, new_cl)
    usage_cost = 0.0
    rows = []

    async def one(cid: str):
        nonlocal usage_cost
        conv = storage.get_conversation(cid)
        prev = None
        for h in storage.evaluation_history(cid):
            if h["checklist_id"] == new_cl["id"] and old_cl and h["checklist_version"] == old_cl["version"]:
                prev = storage.get_evaluation(h["id"])
                break
        if prev is None and old_cl:
            prev = await engine.evaluate(conv, old_cl, with_annotations=False)
            prev = storage.save_evaluation(prev)
            usage_cost += prev["cost_usd"]
        base_j = dict(prev["_judgments"]) if prev else {}
        base_raw = {c["criterion_id"]: c for c in prev["criteria"]} if prev else {}
        need = [c for c in new_cl["criteria"] if c["id"] in changed or c["id"] not in base_j]
        usage = llm.Usage()
        rendered = tr.render(conv["transcript"])
        mets = tr.metrics(conv["transcript"])
        raws = await asyncio.gather(*(engine.judge_criterion(c, new_cl, conv, rendered, mets, usage,
                                                             config.CRITERIA_PROVIDER) for c in need))
        usage_cost += usage.cost_usd
        j = {c["id"]: base_j[c["id"]] for c in new_cl["criteria"] if c["id"] in base_j}
        new_expl = {}
        for c, raw in zip(need, raws):
            j[c["id"]] = engine._safe_judgment(c, raw)
            new_expl[c["id"]] = raw.get("explanation", "")
        j = {c["id"]: j[c["id"]] for c in new_cl["criteria"]}
        after = compute_score(new_cl, j)
        diffs = []
        for c in new_cl["criteria"]:
            if c["id"] in changed:
                b = base_raw.get(c["id"])
                diffs.append({"criterion_id": c["id"], "name": c["name"],
                              "before": {"answer": b["answer"], "value": b.get("value"), "score": b["score"]} if b else None,
                              "after": {"answer": j[c["id"]]["answer"], "value": j[c["id"]].get("value"),
                                        "score": next(s["score"] for s in after["criteria"] if s["criterion_id"] == c["id"])},
                              "explanation": new_expl.get(c["id"], "")})
        row = {"conversation_id": cid, "language": conv.get("language"), "channel": conv.get("channel"),
               "before": prev["total_score"] if prev else None, "after": after["total_score"],
               "gross_before": prev["gross_error"]["flag"] if prev else None,
               "gross_after": after["gross_error"]["flag"], "criteria": diffs}
        rows.append(row)
        if emit:
            await emit({"type": "row", **row})

    await asyncio.gather(*(one(c) for c in conversation_ids))
    rows.sort(key=lambda r: conversation_ids.index(r["conversation_id"]))
    return {"changed": changed, "rows": rows, "cost_usd": round(usage_cost, 4)}


# ---------- аудио звонков ----------

_audio_cache: dict[str, tuple[float, str]] = {}


async def audio_src(conv: dict) -> dict:
    """audio_url организаторов отдаёт HTML-плеер; достаём прямую ссылку на mp3 (живёт ~1 час)."""
    import time
    url = conv.get("audio_url")
    if not url:
        return {"src": None}
    hit = _audio_cache.get(url)
    if hit and time.time() - hit[0] < 45 * 60:
        src = hit[1]
    else:
        r = await llm.client().get(url)
        m = re.search(r'<source[^>]+src="([^"]+)"', r.text) or re.search(r'<audio[^>]+src="([^"]+)"', r.text)
        src = html.unescape(m.group(1)) if m else None
        if src:
            _audio_cache[url] = (time.time(), src)
    return {"src": src, "page": url, "offsets": tr.audio_offsets(conv["transcript"])}


async def fetch_dataset(group_id: str) -> list[dict]:
    if not config.CQA_API_URL:
        raise ValueError("CQA_API_URL не задан в .env")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", group_id or ""):
        raise ValueError("group_id: только латиница, цифры, _ и -")
    base = re.sub(r"[&?]group_id=[^&]*", "", config.CQA_API_URL)
    sep = "&" if "?" in base else "?"
    r = await llm.client().get(f"{base}{sep}group_id={quote(group_id)}", timeout=httpx.Timeout(120.0))
    r.raise_for_status()
    d = r.json()
    if not d.get("isSuccess", True):
        raise ValueError(d.get("error") or "API вернул ошибку")
    return d["data"]["records"]
