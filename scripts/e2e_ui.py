"""Сквозная проверка интерфейса в настоящем браузере с настоящей моделью (≈ $0.2).

Сервер должен быть запущен (run.bat / python -m backend.app). Нужен playwright:
    pip install playwright && python -m playwright install chromium
    python -m scripts.e2e_ui [http://localhost:8000] [conversation_id]
Скриншоты — в data/e2e/. Код выхода 0 = все шаги пройдены.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
CID = sys.argv[2] if len(sys.argv) > 2 else "p1-kz-003"
OUT = Path("data/e2e")
OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool, str]] = []


def step(name):
    def deco(fn):
        def run(page):
            t0 = time.time()
            try:
                fn(page)
                results.append((name, True, f"{time.time() - t0:.1f}s"))
                print(f"  ✓ {name}  ({time.time() - t0:.1f}s)", flush=True)
            except Exception as e:  # noqa: BLE001
                page.screenshot(path=str(OUT / f"FAIL_{len(results):02d}.png"))
                results.append((name, False, str(e)[:300]))
                print(f"  ✗ {name}: {e}"[:400], flush=True)
        return run
    return deco


def open_all(page):
    loc = page.locator(".crit[data-cid]:not(.open) .crit-h")
    for _ in range(20):
        if not loc.count():
            break
        loc.first.click()
        page.wait_for_timeout(120)
    page.wait_for_timeout(400)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{len(results):02d}_{name}.png"))


@step("Список диалогов загружается")
def s_list(page):
    page.goto(f"{BASE}/#/")
    expect(page.locator(".list-row").first).to_be_visible(timeout=20000)
    n = page.locator(".list-row").count()
    assert n > 0, "пустой список"
    page.fill("#q", CID)
    page.wait_for_timeout(500)
    assert page.locator(".list-row").count() == 1, "поиск не отфильтровал"
    page.fill("#q", "")
    page.wait_for_timeout(400)
    shot(page, "list")


@step("Живая оценка диалога (SSE) и итог")
def s_eval(page):
    page.goto(f"{BASE}/#/c/{CID}")
    expect(page.locator("#run")).to_be_visible(timeout=15000)
    page.click("#run")
    expect(page.locator("#live")).to_be_visible(timeout=5000)
    expect(page.locator(".crit.fresh").first).to_be_visible(timeout=90000)
    shot(page, "live")
    expect(page.locator(".crit[data-cid]")).to_have_count(8, timeout=180000)
    expect(page.locator("#run")).to_be_enabled(timeout=10000)
    total = page.locator(".total .ring .num").inner_text()
    assert float(total) >= 0
    shot(page, "evaluated")


@step("Клик по доказательству подсвечивает реплику")
def s_evidence(page):
    open_all(page)
    ev = page.locator(".crit.open .ev[data-i]").first
    idx = ev.get_attribute("data-i")
    ev.click()
    page.wait_for_timeout(300)
    assert "flash" in page.locator(f"#m{idx}").get_attribute("class"), "реплика не подсвечена"
    assert page.locator(f"#m{idx} mark").count() >= 1, "цитата не выделена"
    shot(page, "evidence")


@step("Разбор партии: знаки ходов и eval bar")
def s_annotations(page):
    assert page.locator(".msg.operator .sym img").count() >= 1, "нет знаков у реплик оператора"
    assert page.locator("#evalbar").count() == 1, "нет eval bar"


@step("Подача апелляции")
def s_appeal(page):
    page.locator(".crit.open [data-appeal]").first.click()
    page.fill("#ap-comment", "E2E: проверка апелляции")
    page.click("#ap-send")
    expect(page.locator(".trail").first).to_be_visible(timeout=10000)
    shot(page, "appeal_sent")


@step("Проверяющий: ИИ-секундант и решение, итог пересчитан")
def s_decide(page):
    page.goto(f"{BASE}/#/appeals")
    expect(page.locator(".side-item").first).to_be_visible(timeout=10000)
    page.locator(".side-item").first.click()
    page.click("#ask")
    expect(page.locator(".second .ev").first).to_be_visible(timeout=120000)
    page.click('[data-act="change"]')
    opts = page.locator("#new option").all_inner_texts()
    page.select_option("#new", index=len(opts) - 1)
    page.fill("#reason", "E2E: решение проверяющего")
    page.click("#decide")
    expect(page.locator(".flow .big").nth(2)).not_to_have_text("—", timeout=10000)
    shot(page, "decided")


@step("Карточка: исходная оценка → решение → финальный результат")
def s_trail(page):
    page.goto(f"{BASE}/#/c/{CID}")
    expect(page.locator(".crit[data-cid]").first).to_be_visible(timeout=15000)
    open_all(page)
    txt = page.locator(".trail").last.inner_text()
    assert "→" in txt, "нет цепочки апелляции"
    shot(page, "trail")


@step("Строгий режим — только таблица критериев")
def s_strict(page):
    page.check("#strict")
    expect(page.locator(".strict-table")).to_be_visible(timeout=5000)
    assert page.locator(".strict-table tbody tr").count() == 8
    assert page.locator(".msg .sym img").count() == 0, "знаки ходов видны в строгом режиме"
    shot(page, "strict")
    page.uncheck("#strict")


@step("Редактор: изменение критерия, проверка формулировки, пробный запуск, новая версия")
def s_editor(page):
    page.goto(f"{BASE}/#/checklist")
    expect(page.locator("details.crit-edit").first).to_be_visible(timeout=10000)
    before = page.locator("#ver option").count()
    page.locator("details.crit-edit").nth(6).locator("summary").click()
    ta = page.locator("details.crit-edit").nth(6).locator('[data-k="description"]')
    ta.fill(f"Да — оператор спросил, остались ли у клиента вопросы, и попрощался. (E2E {int(time.time())})")
    ta.blur()
    page.wait_for_timeout(300)
    page.locator("details.crit-edit").nth(6).locator("[data-lint]").click()
    expect(page.locator(".lint b").first).to_be_visible(timeout=120000)
    page.click("#trial")
    expect(page.locator(".trial-row[data-cid]").first).to_be_visible(timeout=240000)
    expect(page.locator(".trial-row .spinner")).to_have_count(0, timeout=240000)
    shot(page, "trial")
    page.fill("#comment", "E2E")
    page.click("#save")
    page.wait_for_timeout(1500)
    assert page.locator("#ver option").count() == before + 1, "версия не создана"


@step("Языки EN/KZ и тёмная тема")
def s_lang(page):
    page.goto(f"{BASE}/#/")
    page.click('#lang button[data-l="en"]')
    expect(page.locator("h1")).to_have_text("Conversations", timeout=3000)
    page.click('#lang button[data-l="kz"]')
    expect(page.locator("h1")).to_have_text("Диалогтар", timeout=3000)
    page.click('#lang button[data-l="ru"]')
    page.check("#theme")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    shot(page, "dark")
    page.uncheck("#theme")


def main():
    print(f"E2E: {BASE}, диалог {CID}")
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for fn in (s_list, s_eval, s_evidence, s_annotations, s_appeal, s_decide, s_trail, s_strict, s_editor, s_lang):
            fn(page)
        b.close()
    ok = sum(1 for r in results if r[1])
    print(f"\nИтого: {ok}/{len(results)} шагов пройдено; JS-ошибок: {len(errors)}")
    for e in errors:
        print("  JS:", e[:200])
    report = ["# E2E-отчёт", "", f"База: {BASE}, диалог `{CID}`", ""] + \
             [f"- {'✅' if r[1] else '❌'} {r[0]} — {r[2]}" for r in results] + [f"", f"JS-ошибок: {len(errors)}"]
    (OUT / "REPORT.md").write_text("\n".join(report), "utf-8")
    sys.exit(0 if ok == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
