"""Подсчёт итога строго по чек-листу (стр. 2 PDF, «Порядок расчёта»).

Чистые функции: никаких вызовов модели, БД или сети.

1. Применимость: N/A только там, где разрешён (na_allowed).
2. «Да» = полный вес, «Нет» = 0. Шкала — только одна из заданных ступеней.
3. Шкала переводится в баллы пропорционально весу; верхняя ступень = полный вес.
4. Веса N/A-критериев пропорционально перераспределяются между применимыми,
   максимум остаётся 100.
5. Итог округляется до 2 знаков (ROUND_HALF_UP). Нет применимых критериев
   с весом — итог не рассчитывается (None). Грубая ошибка — отдельный флаг,
   на итог не влияет.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MAX_TOTAL = Decimal(100)
_Q = Decimal("0.01")


class ScoringError(ValueError):
    pass


def _r2(x: Decimal) -> float:
    return float(x.quantize(_Q, rounding=ROUND_HALF_UP))


def scale_steps(criterion: dict) -> list[int]:
    return sorted({int(s["value"]) for s in criterion.get("scale", [])})


def validate_judgment(criterion: dict, j: dict) -> None:
    cid = criterion["id"]
    ctype = criterion["type"]
    answer = j.get("answer")
    if answer == "na":
        if not criterion.get("na_allowed"):
            raise ScoringError(f"{cid}: N/A не разрешён для этого критерия")
        return
    if ctype in ("binary", "flag"):
        if answer not in ("yes", "no"):
            raise ScoringError(f"{cid}: ожидается yes/no, получено {answer!r}")
    elif ctype == "scale":
        if answer != "scale":
            raise ScoringError(f"{cid}: ожидается scale, получено {answer!r}")
        steps = scale_steps(criterion)
        if not steps:
            raise ScoringError(f"{cid}: у шкального критерия нет ступеней")
        if j.get("value") not in steps:
            raise ScoringError(f"{cid}: значение {j.get('value')!r} не из ступеней {steps}")
    else:
        raise ScoringError(f"{cid}: неизвестный тип {ctype!r}")


def _ratio(criterion: dict, j: dict) -> Decimal:
    """Доля полного веса, которую даёт ответ (0..1)."""
    if criterion["type"] == "scale":
        top = Decimal(max(scale_steps(criterion)))
        return Decimal(j["value"]) / top if top > 0 else Decimal(0)
    return Decimal(1) if j["answer"] == "yes" else Decimal(0)


def compute_score(checklist: dict, judgments: dict[str, dict]) -> dict[str, Any]:
    """judgments: {criterion_id: {"answer": "yes"|"no"|"scale"|"na", "value": int|None}}

    Возвращает {"total_score", "gross_error", "sections", "criteria"} — поля
    контракта результата, относящиеся к подсчёту.
    """
    criteria = checklist["criteria"]
    for c in criteria:
        if c["id"] not in judgments:
            raise ScoringError(f"{c['id']}: нет оценки")
        validate_judgment(c, judgments[c["id"]])

    scored = [c for c in criteria if c["type"] != "flag" and Decimal(str(c["weight"])) > 0]
    applicable = [c for c in scored if judgments[c["id"]]["answer"] != "na"]
    applicable_weight = sum((Decimal(str(c["weight"])) for c in applicable), Decimal(0))
    factor = (MAX_TOTAL / applicable_weight) if applicable_weight > 0 else Decimal(0)

    out_criteria: list[dict] = []
    raw_points: dict[str, Decimal] = {}
    raw_max: dict[str, Decimal] = {}
    gross = {"flag": False, "criterion_id": None, "reason": ""}

    for c in criteria:
        j = judgments[c["id"]]
        weight = Decimal(str(c["weight"]))
        is_scored = c in scored
        if j["answer"] == "na" or not is_scored:
            eff = Decimal(0)
            pts = Decimal(0)
        else:
            eff = weight * factor
            pts = _ratio(c, j) * eff
        raw_points[c["id"]] = pts
        raw_max[c["id"]] = eff
        if c["type"] == "flag" and j["answer"] == "yes" and not gross["flag"]:
            gross = {"flag": True, "criterion_id": c["id"], "reason": j.get("reason", "")}
        out_criteria.append({
            "criterion_id": c["id"],
            "answer": j["answer"],
            "value": j.get("value"),
            "score": _r2(pts),
            "max_score": _r2(eff),
            "weight": float(weight),
            "effective_weight": _r2(eff),
        })

    total = _r2(sum(raw_points.values(), Decimal(0))) if applicable else None

    sections = []
    for s in checklist.get("sections", []):
        ids = [c["id"] for c in criteria if c.get("section_id") == s["id"]]
        if not any(c in scored for c in criteria if c["id"] in ids):
            continue  # секция без весовых критериев (напр. грубые нарушения)
        smax = sum((raw_max[i] for i in ids), Decimal(0))
        sections.append({
            "id": s["id"], "name": s["name"],
            "score": _r2(sum((raw_points[i] for i in ids), Decimal(0))) if smax > 0 else None,
            "max_score": _r2(smax),
        })

    return {"total_score": total, "gross_error": gross, "sections": sections, "criteria": out_criteria}


def apply_overrides(judgments: dict[str, dict], overrides: dict[str, dict]) -> dict[str, dict]:
    """Решения проверяющего поверх исходных оценок (для пересчёта после апелляции)."""
    merged = {k: dict(v) for k, v in judgments.items()}
    for cid, o in overrides.items():
        merged[cid] = {**merged.get(cid, {}), **o}
    return merged
