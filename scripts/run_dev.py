"""Прогон всех диалогов набора с отчётом: точность против эталона, ошибки, стоимость.

python -m scripts.run_dev                      # все DEV, без разбора партии
python -m scripts.run_dev --only p1-ru-001,p1-kz-003 --annotations
python -m scripts.run_dev --reuse              # только пересчитать отчёт по сохранённым результатам

Эталон (data/reference.json) используется ТОЛЬКО здесь, для измерения точности.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

from backend import config, engine

OUT = Path("data/runs")


def label(j: dict) -> str:
    return "na" if j["answer"] == "na" else (str(j["value"]) if j["answer"] == "scale" else j["answer"])


async def run(records, checklist, annotations, concurrency, tag):
    sem = asyncio.Semaphore(concurrency)
    results, errors = {}, {}

    async def one(r):
        async with sem:
            try:
                res = await engine.evaluate(r, checklist, with_annotations=annotations)
                results[r["conversation_id"]] = res
                print(f"  ✓ {r['conversation_id']:10} {res['total_score']!s:>6}  ${res['cost_usd']:.4f}  "
                      f"{res['duration_ms'] / 1000:.1f}s", flush=True)
            except Exception as e:  # noqa: BLE001 — отчёт должен собраться при любых сбоях
                errors[r["conversation_id"]] = repr(e)[:300]
                print(f"  ✗ {r['conversation_id']}: {e!r}"[:200], flush=True)

    await asyncio.gather(*(one(r) for r in records))
    (OUT / tag).mkdir(parents=True, exist_ok=True)
    for cid, res in results.items():
        (OUT / tag / f"{cid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return results, errors


def report(results: dict, errors: dict, reference: dict, checklist: dict, wall: float) -> str:
    crit_ids = [c["id"] for c in checklist["criteria"]]
    agree = defaultdict(int)
    n = defaultdict(int)
    confusion = defaultdict(lambda: defaultdict(int))
    abs_err, mism = [], []
    low_conf = 0
    for cid, res in sorted(results.items()):
        low_conf += sum(1 for c in res["criteria"] if c.get("low_confidence"))
        ref = reference.get(cid)
        if not ref:
            continue
        got = res["_judgments"]
        for k in crit_ids:
            if k not in ref["judgments"]:
                continue
            a, b = label(ref["judgments"][k]), label(got[k])
            n[k] += 1
            confusion[k][(a, b)] += 1
            if a == b:
                agree[k] += 1
            else:
                expl = next(c["explanation"] for c in res["criteria"] if c["criterion_id"] == k)
                mism.append((cid, k, a, b, expl, ref.get("note", "")))
        if ref.get("total_score") is not None and res["total_score"] is not None:
            abs_err.append(abs(ref["total_score"] - res["total_score"]))
        gross_ref = ref["judgments"].get("c08", {}).get("answer") == "yes"
        if gross_ref != res["gross_error"]["flag"]:
            pass  # учтено в c08

    cost = sum(r["cost_usd"] for r in results.values())
    calls = sum(r["_usage"]["calls"] for r in results.values())
    lines = ["# Отчёт прогона DEV", "",
             f"Диалогов: {len(results)} оценено, {len(errors)} ошибок. Модель: "
             f"{', '.join(sorted({r['model'] for r in results.values()}))}",
             f"Стоимость: ${cost:.3f} всего, ${cost / max(1, len(results)):.4f} на диалог; вызовов {calls}; "
             f"время {wall:.0f} с; цитат с низкой уверенностью: {low_conf}", ""]
    if abs_err:
        exact = sum(1 for e in abs_err if e < 0.01)
        lines += [f"**Итоговый балл: MAE {sum(abs_err) / len(abs_err):.2f}**, точное совпадение {exact}/{len(abs_err)}, "
                  f"в пределах 5 баллов {sum(1 for e in abs_err if e <= 5)}/{len(abs_err)}", ""]
    lines += ["| Критерий | Совпадение |", "|---|---|"]
    tot_a = tot_n = 0
    for k in crit_ids:
        if n[k]:
            lines.append(f"| {k} | {agree[k]}/{n[k]} ({100 * agree[k] / n[k]:.0f}%) |")
            tot_a += agree[k]
            tot_n += n[k]
    if tot_n:
        lines.append(f"| **всего** | **{tot_a}/{tot_n} ({100 * tot_a / tot_n:.1f}%)** |")
    lines += ["", "## Расхождения (эталон → модель)", ""]
    for cid, k, a, b, expl, note in mism:
        lines.append(f"- `{cid}` {k}: **{a} → {b}** — {expl[:220]}" + (f"  _(эталон: {note})_" if note else ""))
    if errors:
        lines += ["", "## Ошибки выполнения", ""] + [f"- `{c}`: {e}" for c, e in errors.items()]
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev.json")
    ap.add_argument("--checklist", default="backend/checklist_default.json")
    ap.add_argument("--reference", default="data/reference.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--annotations", action="store_true")
    ap.add_argument("--concurrency", type=int, default=config.BATCH_CONCURRENCY)
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args()
    if not Path(a.data).exists():  # набор ещё не скачан — берём из API организаторов
        from backend import features
        recs = await features.fetch_dataset(config.DEFAULT_GROUP_ID)
        Path(a.data).parent.mkdir(parents=True, exist_ok=True)
        Path(a.data).write_text(json.dumps({"data": {"records": recs}}, ensure_ascii=False), "utf-8")
    records = json.load(open(a.data, encoding="utf-8"))["data"]["records"]
    if a.only:
        keep = set(a.only.split(","))
        records = [r for r in records if r["conversation_id"] in keep]
    checklist = json.load(open(a.checklist, encoding="utf-8"))
    reference = json.load(open(a.reference, encoding="utf-8")) if Path(a.reference).exists() else {}
    t0 = time.perf_counter()
    if a.reuse:
        results = {p.stem: json.loads(p.read_text("utf-8")) for p in (OUT / a.tag).glob("*.json")}
        errors = {}
    else:
        results, errors = await run(records, checklist, a.annotations, a.concurrency, a.tag)
    rep = report(results, errors, reference, checklist, time.perf_counter() - t0)
    (OUT / a.tag).mkdir(parents=True, exist_ok=True)
    (OUT / a.tag / "REPORT.md").write_text(rep, "utf-8")
    print("\n" + rep)


if __name__ == "__main__":
    asyncio.run(main())
