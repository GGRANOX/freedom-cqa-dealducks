"""Промпты: один вызов на критерий + разбор партии. Строятся из чек-листа,
поэтому новый/изменённый критерий применяется без правки кода."""
from __future__ import annotations

import json

SYSTEM_CRITERION = """Ты — строгий аудитор контроля качества Freedom Broker (брокерская компания). \
Ты оцениваешь ОДИН критерий чек-листа по работе ОПЕРАТОРА в клиентском диалоге.

Правила:
- Оцениваешь только реплики с ролью ОПЕРАТОР. Реплики БОТА и СИСТЕМЫ — контекст (например, системное \
сообщение о подтверждении личности, о переводе на оператора, о завершении чата). Ошибки бота не вина оператора.
- Диалог может быть на русском, казахском, английском или смешанным. Транскрипты звонков получены \
распознаванием речи и содержат ошибки распознавания — суди по смыслу, не наказывай за них.
- Следуй формулировке критерия буквально, не добавляй требований от себя и не смягчай имеющиеся.
- «Нет» — действие было нужно, но оператор его пропустил или выполнил неправильно. \
«N/A» — только если N/A разрешён и выполнено условие неприменимости. Если N/A не разрешён, выбирай Да/Нет.
- Доказательства: индекс реплики [i] и ДОСЛОВНАЯ цитата из ЭТОЙ реплики (точное копирование подстроки, \
без перевода и пересказа; можно короткий фрагмент). Можно цитировать реплики любой роли.
- Если действие пропущено — укажи checked_range: диапазон реплик, где действие ожидалось и проверено.
- explanation — 1–2 предложения на русском: вывод и на чём он основан.
- confidence — уверенность от 0 до 1.
"""


def criterion_schema(c: dict) -> dict:
    if c["type"] == "scale":
        answers = ["scale"]
        steps = sorted({int(s["value"]) for s in c.get("scale", [])}, reverse=True)
        value = {"type": ["integer", "null"], "enum": steps + [None],
                 "description": "Ступень шкалы при answer=scale, иначе null"}
    else:
        answers = ["yes", "no"]
        value = {"type": "null", "description": "Всегда null для да/нет"}
    if c.get("na_allowed"):
        answers.append("na")
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "Краткий разбор по шагам перед ответом"},
            "answer": {"type": "string", "enum": answers},
            "value": value,
            "explanation": {"type": "string"},
            "evidence": {"type": "array", "items": {
                "type": "object",
                "properties": {"message_index": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["message_index", "quote"], "additionalProperties": False}},
            "checked_range": {"type": ["object", "null"], "properties": {
                "from_index": {"type": "integer"}, "to_index": {"type": "integer"}},
                "required": ["from_index", "to_index"], "additionalProperties": False},
            "na_reason": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["reasoning", "answer", "value", "explanation", "evidence", "checked_range",
                     "na_reason", "confidence"],
        "additionalProperties": False,
    }


def _criterion_block(c: dict, checklist: dict) -> str:
    lines = [f"КРИТЕРИЙ {c['id']}: {c['name']}"]
    if c["type"] == "scale":
        lines.append("Тип: шкала. Выбери ровно одну ступень (answer=scale, value=ступень):")
        for s in sorted(c.get("scale", []), key=lambda s: -int(s["value"])):
            lines.append(f"  {s['value']} — {s.get('label', '')}")
    elif c["type"] == "flag":
        lines.append("Тип: флаг грубой ошибки. yes = нарушение выявлено, no = не выявлено.")
    else:
        lines.append("Тип: да/нет (answer=yes|no).")
    lines.append(f"Формулировка: {c.get('description', '')}")
    if c.get("na_allowed"):
        lines.append(f"N/A разрешён. Условие N/A: {c.get('na_condition', '')}")
    else:
        lines.append("N/A НЕ разрешён.")
    if c.get("guidance"):
        lines.append(f"Правила применения: {c['guidance']}")
    return "\n".join(lines)


def _examples_block(examples: list[dict]) -> str:
    if not examples:
        return ""
    out = ["\nРЕШЕНИЯ ПРОВЕРЯЮЩЕГО ПО ЭТОМУ КРИТЕРИЮ (прецеденты из апелляций, учитывай их логику):"]
    for e in examples:
        out.append(f"- Фрагмент: {e.get('fragment', '')[:600]}\n  Исходная оценка: {e.get('original')}; "
                   f"решение: {e.get('decision')}; причина: {e.get('reason', '')}")
    return "\n".join(out)


def criterion_user_prompt(c: dict, checklist: dict, conv: dict, rendered: str, metrics: dict,
                          examples: list[dict] | None = None, feedback: str = "") -> str:
    meta = (f"Канал: {conv.get('channel')}; язык набора: {conv.get('language')}. "
            f"Метрики (посчитаны кодом): первая реакция оператора {metrics.get('first_response_sec')} с "
            f"(порог {checklist.get('first_response_threshold_sec', 180)} с), "
            f"макс. пауза {metrics.get('max_pause_sec')} с.")
    parts = [_criterion_block(c, checklist), _examples_block(examples or []), "", meta,
             "\nТРАНСКРИПТ ([индекс] (время от начала) РОЛЬ: текст):", rendered]
    if feedback:
        parts.append(f"\nИСПРАВЬ ПРЕДЫДУЩИЙ ОТВЕТ: {feedback}")
    return "\n".join(p for p in parts if p is not None)


# ---------- разбор партии ----------

SYSTEM_ANNOTATE = """Ты — тренер операторов поддержки Freedom Broker. Разбираешь диалог как шахматную партию.
Для КАЖДОЙ реплики оператора поставь знак:
"!!" — блестяще (сверх ожиданий), "!" — хорошо, "?!" — неточность, "?" — ошибка, "??" — зевок (грубая ошибка: \
оскорбление, предложение услуг в обход компании, раскрытие конфиденциальных данных).
Дай короткий комментарий (до 15 слов, на русском) и id связанных критериев чек-листа.
Для "?" и "??" — best_move: что стоило сказать (на языке диалога, 1–2 предложения). Для остальных best_move = "".
Для КАЖДОЙ реплики клиента — настроение value от -1 (раздражён) до +1 (доволен).
Оценивай согласованно с результатами по критериям, если они даны."""


def annotate_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "annotations": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "message_index": {"type": "integer"},
                    "symbol": {"type": "string", "enum": ["!!", "!", "?!", "?", "??"]},
                    "comment": {"type": "string"},
                    "best_move": {"type": "string"},
                    "criterion_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["message_index", "symbol", "comment", "best_move", "criterion_ids"],
                "additionalProperties": False}},
            "sentiment": {"type": "array", "items": {
                "type": "object",
                "properties": {"message_index": {"type": "integer"}, "value": {"type": "number"}},
                "required": ["message_index", "value"], "additionalProperties": False}},
        },
        "required": ["annotations", "sentiment"],
        "additionalProperties": False,
    }


def annotate_user_prompt(checklist: dict, rendered: str, criteria_results: list[dict]) -> str:
    crit = "\n".join(f"{c['id']} {c['name']}" for c in checklist["criteria"])
    res = json.dumps([{k: r.get(k) for k in ("criterion_id", "answer", "value", "explanation")}
                      for r in criteria_results], ensure_ascii=False)
    return f"Критерии:\n{crit}\n\nРезультаты оценки:\n{res}\n\nТРАНСКРИПТ:\n{rendered}"
