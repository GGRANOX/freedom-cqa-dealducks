"""Интеграционные тесты HTTP API с подменённой моделью (без сети и без затрат).

Подменная модель отвечает по схеме и цитирует реальные реплики, поэтому проверяется весь путь:
SSE-оценка → проверка цитат → подсчёт → сохранение → апелляция → решение → пересчёт → экспорт.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import config, llm, storage

CONV = {
    "conversation_id": "t-ru-001", "channel": "agent chat", "language": "RU", "audio_url": None,
    "transcript": [
        {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть мой тариф?"},
        {"timestamp": "00:05", "speaker": "bot", "text": "Тариф указан в отчёте брокера."},
        {"timestamp": "00:20", "speaker": "system", "text": "Эскалация на оператора."},
        {"timestamp": "01:00", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина. Откройте «Кабинет», тариф под ФИО."},
        {"timestamp": "01:20", "speaker": "client", "text": "Нашла, спасибо."},
        {"timestamp": "01:40", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
    ],
}

CALLS = {"n": 0, "bad_quote_once": set()}


def fake_answer(name: str, schema: dict) -> dict:
    if name == "annotations":
        return {"annotations": [{"message_index": 3, "symbol": "!", "comment": "Чёткий ответ", "best_move": "", "criterion_ids": ["c01"]},
                                {"message_index": 5, "symbol": "?", "comment": "Нет предложения помощи", "best_move": "Остались вопросы?", "criterion_ids": ["c07"]}],
                "sentiment": [{"message_index": 0, "value": -0.2}, {"message_index": 4, "value": 0.8}]}
    if name == "second":
        return {"verdict": "change", "suggested_answer": "yes", "suggested_value": None, "reasoning": "Есть прощание",
                "evidence": [{"message_index": 5, "quote": "Хорошего вечера!"}], "confidence": 0.7}
    if name == "lint":
        return {"clarity_score": 3, "ambiguities": ["неясно X"], "suggested_description": "Да — …", "suggested_guidance": "…",
                "na_recommended": True, "suggested_na_condition": "Клиент ушёл"}
    cid = name.replace("criterion_", "")
    answers = schema["properties"]["answer"]["enum"]
    if "scale" in answers:
        steps = [v for v in schema["properties"]["value"]["enum"] if v is not None]
        ans, val = "scale", max(steps)
    elif cid == "c02":
        ans, val = "na", None
    elif cid in ("c07", "c08"):
        ans, val = "no", None
    else:
        ans, val = "yes", None
    quote = "Меня зовут Алина"
    if cid == "c05" and cid not in CALLS["bad_quote_once"]:
        CALLS["bad_quote_once"].add(cid)
        quote = "Эту фразу оператор не писал"  # проверка повтора при выдуманной цитате
    return {"reasoning": "тест", "answer": ans, "value": val, "explanation": f"{cid}: тестовое обоснование",
            "evidence": [{"message_index": 3, "quote": quote}], "checked_range": None,
            "na_reason": "Данных хватало" if ans == "na" else "", "confidence": 0.9}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    db = tmp_path_factory.mktemp("db") / "test.db"
    storage.reset()
    config.DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")  # можно прогнать на PostgreSQL
    config.DB_PATH = str(db)
    if config.DATABASE_URL:  # чистая схема для повторяемости
        import psycopg
        with psycopg.connect(config.DATABASE_URL, autocommit=True) as c:
            for t in ("conversations", "checklists", "evaluations", "appeals", "jobs", "spend"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
    orig, orig_url = llm.call_json, config.CQA_API_URL
    config.CQA_API_URL = ""

    async def fake_call(provider, system, user, schema, name="result", retries=3):
        CALLS["n"] += 1
        return llm.LLMResponse(data=fake_answer(name, schema), model="fake-model", provider=provider,
                               tokens_in=1000, tokens_out=100, cost_usd=0.001)

    llm.call_json = fake_call
    from backend.app import app
    with TestClient(app) as c:
        yield c
    llm.call_json = orig
    config.CQA_API_URL = orig_url
    storage.reset()


def sse(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_health_and_default_checklist(client):
    h = client.get("/api/health").json()
    assert h["ok"]
    cls = client.get("/api/checklists").json()
    assert cls[0]["id"] == "freedom-chat-quality" and cls[0]["version"] == 1
    cl = client.get("/api/checklists/freedom-chat-quality").json()
    assert len(cl["criteria"]) == 8 and sum(c["weight"] for c in cl["criteria"]) == 100


def test_import_and_list(client):
    r = client.post("/api/groups/import", json={"payload": {"data": {"dataset_group_id": "TEST", "records": [CONV]}}})
    assert r.json() == {"group_id": "TEST", "loaded": 1}
    rows = client.get("/api/conversations?group_id=TEST").json()
    assert rows[0]["id"] == "t-ru-001" and rows[0]["total_score"] is None
    conv = client.get("/api/conversations/t-ru-001").json()
    assert conv["metrics"]["first_response_sec"] == 40


def test_evaluate_stream_and_result(client):
    events = sse(client.post("/api/evaluate", json={"conversation_id": "t-ru-001"}))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("criterion") == 8
    ev = client.get(f"/api/evaluations/{events[-1]['evaluation_id']}").json()
    # c02 N/A (15) → применимо 85; c07 Нет (5) → 80/85*100
    assert ev["total_score"] == 94.12
    assert ev["gross_error"]["flag"] is False
    c02 = next(c for c in ev["criteria"] if c["criterion_id"] == "c02")
    assert c02["answer"] == "na" and c02["na_reason"] and c02["max_score"] == 0
    c01 = next(c for c in ev["criteria"] if c["criterion_id"] == "c01")
    assert c01["effective_weight"] == 11.76 and c01["evidence"][0]["verified"] and c01["evidence"][0]["timestamp"] == "01:00"
    c05 = next(c for c in ev["criteria"] if c["criterion_id"] == "c05")
    assert c05["evidence"][0]["verified"] and not c05["low_confidence"]  # выдуманная цитата → повтор → ок
    assert ev["annotations"][1]["best_move"] and ev["sentiment_curve"][1]["value"] == 0.8
    assert ev["cost_usd"] > 0 and "fake-model" in ev["model"]
    assert client.get("/api/conversations?group_id=TEST").json()[0]["total_score"] == 94.12


def test_appeal_full_cycle(client):
    ev = client.get("/api/conversations/t-ru-001/evaluation").json()
    r = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07", "comment": "Оператор попрощался"})
    a = r.json()
    assert a["status"] == "open" and a["original"]["answer"] == "no" and "[5]" in a["original"]["fragment"]
    dup = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07", "comment": "ещё раз"})
    assert dup.status_code == 400
    assert client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07", "comment": " "}).status_code == 400
    second = client.post(f"/api/appeals/{a['id']}/second").json()
    assert second["verdict"] == "change" and second["evidence"][0]["verified"]
    assert client.post(f"/api/appeals/{a['id']}/decision", json={"action": "change", "new_answer": "yes", "reason": ""}).status_code == 400
    d = client.post(f"/api/appeals/{a['id']}/decision", json={"action": "change", "new_answer": "yes", "reason": "Прощание есть"}).json()
    assert d["status"] == "changed" and d["final"]["total_score"] == 100.0 and d["original"]["total_score"] == 94.12
    ev2 = client.get(f"/api/evaluations/{ev['evaluation_id']}").json()
    assert ev2["total_score"] == 100.0 and ev2["original_total"] == 94.12
    c07 = next(c for c in ev2["criteria"] if c["criterion_id"] == "c07")
    assert c07["overridden"] and c07["answer"] == "yes"
    assert ev2["appeals"][0]["status"] == "changed"
    assert client.post(f"/api/appeals/{a['id']}/decision", json={"action": "confirm", "reason": "x"}).status_code == 400


def test_decision_validation_and_confirm(client):
    ev = client.get("/api/conversations/t-ru-001/evaluation").json()
    a = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c01", "comment": "не согласен"}).json()
    assert client.post(f"/api/appeals/{a['id']}/decision", json={"action": "change", "new_answer": "na", "reason": "x"}).status_code == 400
    d = client.post(f"/api/appeals/{a['id']}/decision", json={"action": "confirm", "reason": "Оценка верна"}).json()
    assert d["status"] == "confirmed" and d["final"]["total_score"] == 100.0


def test_appeal_examples_reach_prompt(client):
    ex = storage.appeal_examples("c07")
    assert ex and ex[0]["decision"] == "Да" and "Прощание есть" in ex[0]["reason"]


def test_checklist_versions_and_validation(client):
    cl = client.get("/api/checklists/freedom-chat-quality").json()
    cl.pop("versions")
    cl["criteria"].append({"id": "c09", "section_id": "flow", "name": "Новый", "type": "scale", "weight": 10,
                           "na_allowed": False, "description": "Шкала", "scale": [{"value": 0}, {"value": 5}]})
    saved = client.post("/api/checklists", json={"checklist": cl, "comment": "добавлен c09"}).json()
    assert saved["version"] == 2
    vers = client.get("/api/checklists/freedom-chat-quality").json()["versions"]
    assert [v["version"] for v in vers] == [2, 1]
    assert len(client.get("/api/checklists/freedom-chat-quality?version=1").json()["criteria"]) == 8
    bad = json.loads(json.dumps(cl)); bad["criteria"][0]["type"] = "weird"
    assert client.post("/api/checklists", json={"checklist": bad}).status_code == 400
    bad = json.loads(json.dumps(cl)); bad["criteria"][1]["id"] = bad["criteria"][0]["id"]
    assert client.post("/api/checklists", json={"checklist": bad}).status_code == 400
    # новый критерий применяется через настройки: оценка версией 2 содержит c09
    events = sse(client.post("/api/evaluate", json={"conversation_id": "t-ru-001", "checklist_version": 2, "annotations": False}))
    ev = client.get(f"/api/evaluations/{events[-1]['evaluation_id']}").json()
    assert ev["checklist_version"] == 2 and any(c["criterion_id"] == "c09" for c in ev["criteria"])


def test_lint_and_trial(client):
    cl = client.get("/api/checklists/freedom-chat-quality?version=1").json()
    cl.pop("versions")
    lint = client.post("/api/checklists/lint", json={"checklist": cl, "criterion_id": "c07"}).json()
    assert lint["clarity_score"] == 3 and lint["na_recommended"]
    cl["criteria"][6]["description"] = "Да — оператор спросил, остались ли вопросы."
    events = sse(client.post("/api/checklists/trial", json={"checklist": cl, "base_version": 1, "conversation_ids": ["t-ru-001"]}))
    done = events[-1]
    assert done["type"] == "done" and done["changed"] == ["c07"] and done["rows"][0]["after"] is not None


def test_batch_and_export(client):
    import time
    job = client.post("/api/batch", json={"group_id": "TEST", "sync": False, "annotations": False}).json()
    for _ in range(50):
        st = client.get(f"/api/batch/{job['id']}").json()
        if st["status"] == "finished":
            break
        time.sleep(0.1)
    assert st["status"] == "finished" and st["done"] == 1 and st["failed"] == 0
    csv = client.get("/api/export.csv?group_id=TEST").text
    assert "t-ru-001" in csv and "total_score" in csv


def test_frontend_served(client):
    assert "<cqa-app>" in client.get("/").text
    assert client.get("/static/js/api.js").status_code == 200
    assert client.get("/assets/icons/brilliant.png").status_code == 200
    assert client.get("/assets/../backend/config.py").status_code == 404


def test_voting_majority(client, monkeypatch):
    """Низкая уверенность → 3 независимые оценки → большинство голосов."""
    import asyncio
    from backend import engine
    from backend.transcript import metrics, render
    seq = iter(["no", "yes", "yes"])

    async def fake(provider, system, user, schema, name="result", retries=3):
        a = next(seq)
        return llm.LLMResponse(data={"reasoning": "", "answer": a, "value": None, "explanation": a,
                                     "evidence": [{"message_index": 3, "quote": "Меня зовут Алина"}],
                                     "checked_range": None, "na_reason": "", "confidence": 0.6},
                               model="fake", provider=provider, cost_usd=0.001)
    monkeypatch.setattr(llm, "call_json", fake)
    cl = storage.get_checklist("freedom-chat-quality", 1)
    c = next(x for x in cl["criteria"] if x["id"] == "c05")
    r = asyncio.run(engine.judge_criterion(c, cl, CONV, render(CONV["transcript"]), metrics(CONV["transcript"]),
                                           llm.Usage(), "openai"))
    assert r["answer"] == "yes" and r["_votes"] == ["no", "yes", "yes"] and r["confidence"] < 0.6


def test_high_confidence_no_voting(client, monkeypatch):
    import asyncio
    from backend import engine
    from backend.transcript import metrics, render
    calls = []

    async def fake(provider, system, user, schema, name="result", retries=3):
        calls.append(1)
        return llm.LLMResponse(data={"reasoning": "", "answer": "yes", "value": None, "explanation": "",
                                     "evidence": [{"message_index": 3, "quote": "Меня зовут Алина"}],
                                     "checked_range": None, "na_reason": "", "confidence": 0.99},
                               model="fake", provider=provider, cost_usd=0.001)
    monkeypatch.setattr(llm, "call_json", fake)
    cl = storage.get_checklist("freedom-chat-quality", 1)
    c = next(x for x in cl["criteria"] if x["id"] == "c01")
    asyncio.run(engine.judge_criterion(c, cl, CONV, render(CONV["transcript"]), metrics(CONV["transcript"]),
                                       llm.Usage(), "openai"))
    assert len(calls) == 1


def test_budget_and_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.0001)
    storage.add_spend(1.0)
    r = client.post("/api/evaluate", json={"conversation_id": "t-ru-001"})
    assert r.status_code == 429 and "бюджет" in r.json()["error"]
    assert client.get("/api/conversations/t-ru-001/evaluation").status_code == 200  # просмотр доступен
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.0)
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 1)
    from backend import app as appmod
    appmod._hits.clear()
    client.post("/api/checklists/lint", json={"checklist": storage.get_checklist("freedom-chat-quality", 1), "criterion_id": "c01"})
    r = client.post("/api/checklists/lint", json={"checklist": storage.get_checklist("freedom-chat-quality", 1), "criterion_id": "c01"})
    assert r.status_code == 429
    appmod._hits.clear()


def test_group_id_validated(client):
    r = client.post("/api/groups/DEV&x=1/sync")
    assert r.status_code in (400, 404)
