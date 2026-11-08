from backend.transcript import audio_offsets, metrics, parse_ts, quote_in, render, verify_evidence

CHAT = [
    {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть «тариф»?"},
    {"timestamp": "00:38", "speaker": "system", "text": "Эскалация на оператора."},
    {"timestamp": "01:15", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина. Откройте «Кабинет»."},
    {"timestamp": "01:28", "speaker": "client", "text": "Нашла, спасибо."},
    {"timestamp": "02:04", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
]


def test_parse_ts():
    assert parse_ts("01:15") == 75
    assert parse_ts("1:02:03") == 3723
    assert parse_ts("2026-11-03T13:10:00.6388245+00:00") is not None


def test_render_has_indices():
    r = render(CHAT)
    assert r.splitlines()[2].startswith("[2] (01:15) ОПЕРАТОР:")


def test_quote_in_tolerant_to_typography_and_ellipsis():
    assert quote_in('Откройте "Кабинет"', CHAT[2]["text"])
    assert quote_in("Здравствуйте! … Откройте", CHAT[2]["text"])
    assert not quote_in("Меня зовут Олег", CHAT[2]["text"])


def test_verify_evidence_fixes_wrong_index():
    ev, errors = verify_evidence([{"message_index": 4, "quote": "Меня зовут Алина"}], CHAT)
    assert ev[0]["verified"] and ev[0]["message_index"] == 2 and ev[0]["timestamp"] == "01:15"
    assert not errors


def test_verify_evidence_reports_fabricated_quote():
    ev, errors = verify_evidence([{"message_index": 2, "quote": "Я проверил ваш счёт"}], CHAT)
    assert not ev[0]["verified"] and errors


def test_metrics_first_response_from_escalation():
    m = metrics(CHAT)
    assert m["first_response_sec"] == 37
    assert m["operator_messages"] == 2 and m["client_messages"] == 2


def test_audio_offsets_skip_recording_pause():
    call = [
        {"timestamp": "2026-11-03T13:10:00+00:00", "speaker": "system", "text": "Запись и транскрибирование запущены."},
        {"timestamp": "2026-11-03T13:10:50+00:00", "speaker": "system", "text": "Запись и транскрибирование приостановлены."},
        {"timestamp": "2026-11-03T13:11:10+00:00", "speaker": "system", "text": "Запись и транскрибирование возобновлены."},
        {"timestamp": "2026-11-03T13:11:20+00:00", "speaker": "operator", "text": "Здравствуйте"},
    ]
    assert audio_offsets(call)[-1] == 60.0  # 80 c от начала минус 20 c паузы записи
