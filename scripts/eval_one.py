"""python -m scripts.eval_one p1-ru-001  — оценить один диалог из data/dev.json"""
import asyncio, json, sys
from pathlib import Path
from backend import engine

async def main(cid):
    if not Path("data/dev.json").exists():
        from backend import config, features
        Path("data").mkdir(exist_ok=True)
        Path("data/dev.json").write_text(json.dumps({"data": {"records": await features.fetch_dataset(config.DEFAULT_GROUP_ID)}},
                                                    ensure_ascii=False), "utf-8")
    recs = {r["conversation_id"]: r for r in json.load(open("data/dev.json", encoding="utf-8"))["data"]["records"]}
    cl = json.load(open("backend/checklist_default.json", encoding="utf-8"))
    async def emit(ev): print("  ·", json.dumps(ev, ensure_ascii=False)[:160], flush=True)
    res = await engine.evaluate(recs[cid], cl, emit=emit)
    Path("data/out").mkdir(parents=True, exist_ok=True)
    Path(f"data/out/{cid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    print("ИТОГ", res["total_score"], "грубая:", res["gross_error"]["flag"], "$", res["cost_usd"], res["duration_ms"], "мс", res["_usage"])
    for c in res["criteria"]:
        print(f'{c["criterion_id"]} {c["answer"]:5} {str(c["value"]):4} {c["score"]:6} /{c["max_score"]:6} conf={c["confidence"]} low={c["low_confidence"]} | {c["explanation"][:120]}')
        for e in c["evidence"]: print(f'      [{e["message_index"]}] {e["verified"]} «{e["quote"][:70]}»')
    for a in res["annotations"]: print("   ", a["message_index"], a["symbol"], a["comment"], "|", a["best_move"][:60])
    print("   sentiment", res["sentiment_curve"])

asyncio.run(main(sys.argv[1]))
