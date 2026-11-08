"""Работа с транскриптом: индексы реплик, разбор времени, метрики, проверка цитат.

Всё детерминировано, без модели.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

SPEAKER_LABEL = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}


def parse_ts(ts: str) -> Optional[float]:
    """Секунды: MM:SS / HH:MM:SS (чаты) или ISO 8601 (звонки, epoch-секунды)."""
    if not ts:
        return None
    ts = ts.strip()
    if re.fullmatch(r"\d{1,3}:\d{2}(:\d{2})?", ts):
        parts = [int(p) for p in ts.split(":")]
        sec = 0
        for p in parts:
            sec = sec * 60 + p
        return float(sec)
    try:
        s = re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00"))
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def rel_times(transcript: list[dict]) -> list[Optional[float]]:
    abs_t = [parse_ts(m.get("timestamp", "")) for m in transcript]
    base = next((t for t in abs_t if t is not None), None)
    return [None if t is None or base is None else round(t - base, 3) for t in abs_t]


def fmt_rel(sec: Optional[float]) -> str:
    if sec is None:
        return "--:--"
    sec = int(round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render(transcript: list[dict], channel: str = "") -> str:
    """Транскрипт с индексами для промпта: [i] (+MM:SS) РОЛЬ: текст"""
    rel = rel_times(transcript)
    lines = []
    for i, m in enumerate(transcript):
        who = SPEAKER_LABEL.get(m.get("speaker", ""), m.get("speaker", "").upper())
        lines.append(f"[{i}] ({fmt_rel(rel[i])}) {who}: {m.get('text', '')}")
    return "\n".join(lines)


# ---------- проверка цитат ----------

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'",
                         "—": "-", "–": "-", "ё": "е", "Ё": "Е", " ": " "})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").translate(_QUOTES).lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,!?;:\"'…")


def quote_in(quote: str, text: str) -> bool:
    q = norm(quote.replace("...", " ").replace("…", " "))
    if len(q) < 2:
        return False
    t = norm(text)
    if q in t:
        return True
    # допускаем пропуск через многоточие: все фрагменты по порядку
    frags = [norm(f) for f in re.split(r"\.\.\.|…", quote) if norm(f)]
    if len(frags) > 1:
        pos = 0
        for f in frags:
            k = t.find(f, pos)
            if k < 0:
                return False
            pos = k + len(f)
        return True
    return False


def verify_evidence(evidence: list[dict], transcript: list[dict]) -> tuple[list[dict], list[str]]:
    """Проверяет каждую цитату. Если цитата найдена в другой реплике — исправляет индекс.
    Возвращает (evidence с verified/timestamp, список ошибок)."""
    out, errors = [], []
    for ev in evidence:
        idx = ev.get("message_index")
        quote = ev.get("quote", "") or ""
        e = {"message_index": idx, "quote": quote, "timestamp": "", "verified": False}
        if isinstance(idx, int) and 0 <= idx < len(transcript) and quote_in(quote, transcript[idx]["text"]):
            e["verified"] = True
        else:
            hits = [i for i, m in enumerate(transcript) if quote_in(quote, m.get("text", ""))]
            if hits:
                best = min(hits, key=lambda i: abs(i - idx) if isinstance(idx, int) else i)
                e["message_index"], e["verified"] = best, True
            else:
                errors.append(f"[{idx}] цитата не найдена дословно: «{quote[:80]}»")
        if isinstance(e["message_index"], int) and 0 <= e["message_index"] < len(transcript):
            e["timestamp"] = transcript[e["message_index"]].get("timestamp", "")
        out.append(e)
    return out, errors


# ---------- детерминированные метрики ----------

def metrics(transcript: list[dict]) -> dict:
    rel = rel_times(transcript)
    op_idx = [i for i, m in enumerate(transcript) if m.get("speaker") == "operator"]
    res = {"first_response_sec": None, "avg_response_sec": None, "max_pause_sec": None,
           "operator_talk_share": None,
           "operator_messages": len(op_idx),
           "client_messages": sum(1 for m in transcript if m.get("speaker") == "client")}
    if op_idx:
        first = op_idx[0]
        # от последней реплики не-оператора (эскалация/клиент) до первого ответа оператора
        prev = next((j for j in range(first - 1, -1, -1) if rel[j] is not None), None)
        # для ожидания важен момент перевода на оператора/последний вопрос клиента
        esc_kw = ("эскалац", "перевед", "transferred", "жіберілді", "операторға", "оператор")
        esc = next((j for j in range(first - 1, -1, -1)
                    if transcript[j].get("speaker") == "system" and rel[j] is not None
                    and any(k in transcript[j].get("text", "").lower() for k in esc_kw)),
                   next((j for j in range(first - 1, -1, -1)
                         if transcript[j].get("speaker") == "system" and rel[j] is not None), prev))
        start = esc if esc is not None else prev
        if start is not None and rel[first] is not None:
            res["first_response_sec"] = round(rel[first] - rel[start], 1)
        # время ответа: клиент -> следующий оператор
        deltas = []
        last_client = None
        for i, m in enumerate(transcript):
            if m.get("speaker") == "client" and rel[i] is not None:
                if last_client is None:
                    last_client = rel[i]
            elif m.get("speaker") == "operator" and rel[i] is not None:
                if last_client is not None and i > first:
                    deltas.append(rel[i] - last_client)
                last_client = None
        if deltas:
            res["avg_response_sec"] = round(sum(deltas) / len(deltas), 1)
        span = [rel[i] for i in range(op_idx[0], len(transcript)) if rel[i] is not None]
        gaps = [b - a for a, b in zip(span, span[1:])]
        if gaps:
            res["max_pause_sec"] = round(max(gaps), 1)
    op_chars = sum(len(m["text"]) for m in transcript if m.get("speaker") == "operator")
    cl_chars = sum(len(m["text"]) for m in transcript if m.get("speaker") == "client")
    if op_chars + cl_chars:
        res["operator_talk_share"] = round(op_chars / (op_chars + cl_chars), 3)
    return res


# ---------- смещения в аудиозаписи звонка ----------

_PAUSE = ("приостановлен", "paused", "тоқтатыл", "кідіртіл")
_RESUME = ("возобновлен", "resumed", "жалғастырыл", "қайта басталды")
_START = ("запущен", "начат", "started", "басталды")


def audio_offsets(transcript: list[dict]) -> list[float | None]:
    """Секунды от начала аудиозаписи для каждой реплики звонка.
    Учитывает паузы записи («Запись и транскрибирование приостановлены/возобновлены»)."""
    abs_t = [parse_ts(m.get("timestamp", "")) for m in transcript]
    start = None
    for m, t in zip(transcript, abs_t):
        if t is None:
            continue
        if start is None:
            start = t
        txt = m.get("text", "").lower()
        if m.get("speaker") == "system" and any(k in txt for k in _START) and "запись" in txt or "жазу" in txt:
            start = min(start, t)
            break
    if start is None:
        return [None] * len(transcript)
    paused_total, pause_at = 0.0, None
    out = []
    for m, t in zip(transcript, abs_t):
        txt = m.get("text", "").lower()
        if m.get("speaker") == "system" and t is not None:
            if any(k in txt for k in _PAUSE) and pause_at is None:
                pause_at = t
            elif any(k in txt for k in _RESUME) and pause_at is not None:
                paused_total += max(0.0, t - pause_at)
                pause_at = None
        if t is None:
            out.append(None)
            continue
        eff = (pause_at if pause_at is not None and t > pause_at else t) - start - paused_total
        out.append(round(max(0.0, eff), 1))
    return out
