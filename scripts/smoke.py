"""Smoke-тест развёрнутого сервиса (без затрат на модель).   python scripts/smoke.py https://…run.app"""
import sys
import time

import httpx

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
checks = []


def check(name, fn):
    t0 = time.time()
    try:
        fn()
        checks.append((name, True, f"{(time.time() - t0) * 1000:.0f} мс"))
    except Exception as e:  # noqa: BLE001
        checks.append((name, False, str(e)[:200]))


c = httpx.Client(timeout=60, follow_redirects=True)


def health():
    h = c.get(f"{base}/api/health").json()
    assert h["ok"] and h["providers"]["openai"], h
    print("  health:", {k: h[k] for k in ("criteria_model", "annotation_model", "database", "votes", "daily_budget_usd")})


def page():
    r = c.get(f"{base}/")
    assert r.status_code == 200 and "<cqa-app>" in r.text
    assert c.get(f"{base}/static/js/app.js").status_code == 200
    assert c.get(f"{base}/assets/icons/brilliant.png").status_code == 200


def data():
    rows = c.get(f"{base}/api/conversations").json()
    if not rows:
        c.post(f"{base}/api/groups/DEV/sync").raise_for_status()
        rows = c.get(f"{base}/api/conversations").json()
    assert len(rows) >= 1, "нет диалогов"
    conv = c.get(f"{base}/api/conversations/{rows[0]['id']}").json()
    assert conv["transcript"], "пустой транскрипт"


def checklist():
    cl = c.get(f"{base}/api/checklists").json()
    assert cl and cl[0]["id"] == "freedom-chat-quality"


for n, f in [("health + БД", health), ("фронтенд", page), ("данные DEV", data), ("чек-лист", checklist)]:
    check(n, f)
for n, ok, info in checks:
    print(f"  {'✓' if ok else '✗'} {n} — {info}")
sys.exit(0 if all(ok for _, ok, _ in checks) else 1)
