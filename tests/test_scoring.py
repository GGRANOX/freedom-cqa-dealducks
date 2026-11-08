import copy
import json
from pathlib import Path

import pytest

from backend.scoring import ScoringError, apply_overrides, compute_score

CL = json.loads((Path(__file__).parent.parent / "backend" / "checklist_default.json").read_text("utf-8"))


def perfect():
    return {
        "c01": {"answer": "yes"}, "c02": {"answer": "yes"}, "c03": {"answer": "yes"},
        "c04": {"answer": "scale", "value": 20}, "c05": {"answer": "yes"},
        "c06": {"answer": "scale", "value": 10}, "c07": {"answer": "yes"},
        "c08": {"answer": "no"},
    }


def crit(res, cid):
    return next(c for c in res["criteria"] if c["criterion_id"] == cid)


def test_weights_sum_to_100():
    assert sum(c["weight"] for c in CL["criteria"]) == 100


def test_perfect_is_100():
    r = compute_score(CL, perfect())
    assert r["total_score"] == 100.0
    assert r["gross_error"]["flag"] is False


def test_all_no_is_zero():
    j = {k: {"answer": "no"} for k in ["c01", "c02", "c03", "c05", "c07", "c08"]}
    j["c04"] = {"answer": "scale", "value": 0}
    j["c06"] = {"answer": "scale", "value": 0}
    assert compute_score(CL, j)["total_score"] == 0.0


def test_scale_proportional_to_weight():
    j = perfect()
    j["c04"] = {"answer": "scale", "value": 15}   # 15/20 * 20 = 15
    j["c06"] = {"answer": "scale", "value": 7}    # 7/10 * 15 = 10.5
    r = compute_score(CL, j)
    assert crit(r, "c04")["score"] == 15.0
    assert crit(r, "c06")["score"] == 10.5
    assert r["total_score"] == 100 - 5 - 4.5


def test_na_redistributes_weight():
    # Идентификация N/A (20) -> применимые 80, коэффициент 1.25
    j = perfect()
    j["c03"] = {"answer": "na"}
    j["c01"] = {"answer": "no"}
    r = compute_score(CL, j)
    assert crit(r, "c03")["effective_weight"] == 0
    assert crit(r, "c01")["effective_weight"] == 12.5
    assert crit(r, "c02")["effective_weight"] == 18.75
    assert r["total_score"] == 87.5
    assert sum(c["max_score"] for c in r["criteria"]) == pytest.approx(100)


def test_rounding_two_decimals_half_up():
    # N/A у c02 (15) и c07 (5): применимые 80, ... делаем некратный набор
    j = perfect()
    j["c02"] = {"answer": "na"}          # applicable = 85, factor = 100/85
    j["c06"] = {"answer": "scale", "value": 7}
    r = compute_score(CL, j)
    # потеря: 0.3 * 15 * 100/85 = 5.294117... -> итог 94.705882... -> 94.71
    assert r["total_score"] == 94.71


def test_gross_error_flag_separate_from_total():
    j = perfect()
    j["c08"] = {"answer": "yes", "reason": "предложил перевести деньги лично"}
    r = compute_score(CL, j)
    assert r["total_score"] == 100.0
    assert r["gross_error"] == {"flag": True, "criterion_id": "c08", "reason": "предложил перевести деньги лично"}
    assert crit(r, "c08")["max_score"] == 0


def test_na_not_allowed_raises():
    j = perfect()
    j["c01"] = {"answer": "na"}
    with pytest.raises(ScoringError):
        compute_score(CL, j)


def test_scale_value_must_be_step():
    j = perfect()
    j["c04"] = {"answer": "scale", "value": 12}
    with pytest.raises(ScoringError):
        compute_score(CL, j)


def test_missing_criterion_raises():
    j = perfect()
    del j["c05"]
    with pytest.raises(ScoringError):
        compute_score(CL, j)


def test_no_applicable_criteria_total_none():
    cl = copy.deepcopy(CL)
    cl["criteria"] = [c for c in cl["criteria"] if c["na_allowed"] or c["type"] == "flag"]
    j = {"c02": {"answer": "na"}, "c03": {"answer": "na"}, "c04": {"answer": "na"},
         "c07": {"answer": "na"}, "c08": {"answer": "no"}}
    assert compute_score(cl, j)["total_score"] is None


def test_sections():
    j = perfect()
    j["c05"] = {"answer": "no"}
    r = compute_score(CL, j)
    s = {x["id"]: x for x in r["sections"]}
    assert set(s) == {"start", "security", "flow"}      # грубые нарушения без баллов
    assert s["start"] == {"id": "start", "name": "Начало и понимание запроса", "score": 25.0, "max_score": 25.0}
    assert s["flow"]["score"] == 20.0 and s["flow"]["max_score"] == 35.0


def test_custom_weights_not_summing_to_100_normalized():
    cl = copy.deepcopy(CL)
    for c in cl["criteria"]:
        if c["id"] == "c01":
            c["weight"] = 30  # сумма 120 — максимум всё равно 100
    r = compute_score(cl, perfect())
    assert r["total_score"] == 100.0
    assert crit(r, "c01")["effective_weight"] == 25.0


def test_new_criterion_via_settings():
    cl = copy.deepcopy(CL)
    cl["criteria"].append({"id": "c09", "section_id": "flow", "name": "Новый", "type": "scale",
                           "weight": 20, "na_allowed": True, "scale": [{"value": 0}, {"value": 3}, {"value": 5}]})
    j = perfect()
    j["c09"] = {"answer": "scale", "value": 3}
    r = compute_score(cl, j)
    # всего 120, c09: 20*100/120 = 16.667, 3/5 -> 10.0; итог 100 - 6.667
    assert r["total_score"] == 93.33


def test_appeal_override_recomputes():
    j = perfect()
    j["c07"] = {"answer": "no"}
    before = compute_score(CL, j)["total_score"]
    after = compute_score(CL, apply_overrides(j, {"c07": {"answer": "yes"}}))["total_score"]
    assert (before, after) == (95.0, 100.0)
    assert j["c07"]["answer"] == "no"  # исходные оценки не мутируются


def test_scoring_output_fits_contract():
    from backend.contract import EvaluationResult
    r = compute_score(CL, perfect())
    res = EvaluationResult(conversation_id="p1-ru-001", checklist_id=CL["id"], checklist_version=1,
                           total_score=r["total_score"], gross_error=r["gross_error"],
                           sections=r["sections"], criteria=r["criteria"])
    assert res.total_score == 100.0 and len(res.criteria) == 8
