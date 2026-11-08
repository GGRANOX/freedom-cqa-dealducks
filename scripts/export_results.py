"""Экспорт результатов прогона в results/<env>/results.json.

python -m scripts.export_results data/runs/fbroker results/prod/results.json [--group FBroker]
Берёт сохранённые результаты оценки (каждый — реальные вызовы модели) и пишет их одним файлом.
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

ANS = {"yes": "Да", "no": "Нет", "na": "N/A"}


def criterion_out(c: dict) -> dict:
    return {
        "criterion_id": c["criterion_id"],
        "name": c.get("name", ""),
        "answer": c["answer"],
        "answer_label": "N/A" if c["answer"] == "na" else (str(c.get("value")) if c["answer"] == "scale" else ANS.get(c["answer"], c["answer"])),
        "value": c.get("value"),
        "score": c["score"],
        "max_score": c["max_score"],
        "weight": c["weight"],
        "effective_weight": c["effective_weight"],
        "explanation": c.get("explanation", ""),
        "evidence": [{"message_index": e["message_index"], "timestamp": e.get("timestamp", ""), "quote": e["quote"]}
                     for e in c.get("evidence", [])],
        "checked_range": c.get("checked_range"),
        "na_reason": c.get("na_reason", ""),
        "confidence": c.get("confidence"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("out")
    ap.add_argument("--group", default="FBroker")
    a = ap.parse_args()
    items = []
    for f in sorted(glob.glob(f"{a.run_dir}/p*.json")):
        r = json.loads(Path(f).read_text("utf-8"))
        items.append({
            "conversation_id": r["conversation_id"],
            "checklist_id": r["checklist_id"],
            "checklist_version": r["checklist_version"],
            "total_score": r["total_score"],
            "gross_error": r["gross_error"],
            "sections": r["sections"],
            "criteria": [criterion_out(c) for c in r["criteria"]],
            "model": r["model"],
            "cost_usd": r["cost_usd"],
            "evaluated_at": r.get("created_at"),
        })
    out = {"team": "DealDucks", "dataset_group_id": a.group, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "total_records": len(items), "results": items}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"{len(items)} диалогов → {a.out}")


if __name__ == "__main__":
    main()
