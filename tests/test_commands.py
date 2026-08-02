import json

import pytest
import requests
import responses

from src import commands, notify, store

URL = "https://api.telegram.org/botTOKEN/getUpdates"


@pytest.fixture()
def session():
    return requests.Session()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    return conn


def _update(update_id, chat_id, text=None, has_message=True):
    if not has_message:
        return {"update_id": update_id, "edited_channel_post": {}}
    message = {"message_id": update_id, "chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    else:
        message["sticker"] = {"file_id": "abc"}
    return {"update_id": update_id, "message": message}


# ---------------------------------------------------------------------------
# fetch_updates
# ---------------------------------------------------------------------------


@responses.activate
def test_fetch_updates_uses_offset_from_meta_and_advances_it(session, db):
    store.set_meta(db, "tg_offset", "5")
    responses.add(
        responses.POST,
        URL,
        json={"ok": True, "result": [_update(5, 999, "/status")]},
        status=200,
    )
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == ["/status"]
    body = json.loads(responses.calls[0].request.body)
    assert body["offset"] == 5
    assert store.get_meta(db, "tg_offset") == "6"


@responses.activate
def test_fetch_updates_without_prior_offset_omits_offset_param(session, db):
    responses.add(responses.POST, URL, json={"ok": True, "result": []}, status=200)
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == []
    body = json.loads(responses.calls[0].request.body)
    assert "offset" not in body


@responses.activate
def test_foreign_chat_is_ignored_but_offset_advances(session, db):
    responses.add(
        responses.POST,
        URL,
        json={"ok": True, "result": [_update(10, 111, "/status")]},
        status=200,
    )
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == []
    assert store.get_meta(db, "tg_offset") == "11"


@responses.activate
def test_non_text_update_is_skipped_but_offset_advances(session, db):
    responses.add(
        responses.POST,
        URL,
        json={"ok": True, "result": [_update(20, 999, text=None)]},
        status=200,
    )
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == []
    assert store.get_meta(db, "tg_offset") == "21"


@responses.activate
def test_update_without_message_is_skipped_but_offset_advances(session, db):
    responses.add(
        responses.POST,
        URL,
        json={"ok": True, "result": [_update(30, 999, has_message=False)]},
        status=200,
    )
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == []
    assert store.get_meta(db, "tg_offset") == "31"


@responses.activate
def test_mixed_batch_keeps_only_allowed_chat_texts_and_advances_past_all(session, db):
    responses.add(
        responses.POST,
        URL,
        json={
            "ok": True,
            "result": [
                _update(1, 111, "/status"),
                _update(2, 999, "/now"),
                _update(3, 999, text=None),
                _update(4, 999, "/help"),
            ],
        },
        status=200,
    )
    texts = commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)
    assert texts == ["/now", "/help"]
    assert store.get_meta(db, "tg_offset") == "5"


@responses.activate
def test_transport_error_raises_telegram_error(session, db):
    responses.add(responses.POST, URL, body=requests.ConnectionError("boom"))
    with pytest.raises(notify.TelegramError):
        commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)


@responses.activate
def test_api_error_raises_telegram_error(session, db):
    responses.add(
        responses.POST, URL, json={"ok": False, "description": "unauthorized"}, status=401
    )
    with pytest.raises(notify.TelegramError, match="unauthorized"):
        commands.fetch_updates(session, "TOKEN", db, allowed_chat_id=999)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def test_parse_command_with_args():
    assert commands.parse("/threshold 220") == ("/threshold", ["220"])


def test_parse_strips_bot_suffix_and_lowercases():
    assert commands.parse("/Status@my_bot") == ("/status", [])


def test_parse_route_add_with_multiple_args():
    assert commands.parse("/route add TAS HKT") == ("/route", ["add", "TAS", "HKT"])


def test_parse_non_command_returns_none():
    assert commands.parse("привет, как дела?") == (None, [])


def test_parse_empty_string_returns_none():
    assert commands.parse("") == (None, [])


def test_parse_bare_slash_returns_none():
    assert commands.parse("/") == (None, [])


# ---------------------------------------------------------------------------
# handle
# ---------------------------------------------------------------------------


def test_help_lists_all_commands_and_mentions_delay(db, config_stub):
    text = commands.handle(db, config_stub, "/help", [], now="2026-08-01T06:00:00Z")
    for cmd in (
        "/status",
        "/now",
        "/threshold",
        "/pause",
        "/resume",
        "/route add",
        "/bought",
        "/mismatch",
        "/help",
    ):
        assert cmd in text
    assert "15" in text


def test_start_returns_same_as_help(db, config_stub):
    help_text = commands.handle(db, config_stub, "/help", [], now="x")
    start_text = commands.handle(db, config_stub, "/start", [], now="x")
    assert help_text == start_text


def test_status_reports_never_scanned_on_empty_db(db, config_stub):
    text = commands.handle(db, config_stub, "/status", [], now="2026-08-01T06:00:00Z")
    assert "ни разу" in text.lower()
    assert "0" in text


def test_status_reports_scan_data(db, config_stub):
    store.set_meta(db, "last_scan_at", "2026-08-01T05:00:00Z")
    store.set_meta(db, "last_scan_errors", "2")
    text = commands.handle(db, config_stub, "/status", [], now="2026-08-01T06:00:00Z")
    assert "2026-08-01T05:00:00Z" in text
    assert "2" in text
    assert f"${config_stub.abs_threshold_usd:.0f}" in text
    assert "FRU" in text and "HKT" in text


def test_status_notes_pause(db, config_stub):
    store.set_meta(db, "paused", "1")
    text = commands.handle(db, config_stub, "/status", [], now="2026-08-01T06:00:00Z")
    assert "пауз" in text.lower()


def test_now_returns_digest_text(db, config_stub):
    text = commands.handle(db, config_stub, "/now", [], now="2026-08-01T06:00:00Z")
    assert isinstance(text, str)
    assert len(text) > 0


def test_threshold_without_args_shows_current(db, config_stub):
    text = commands.handle(db, config_stub, "/threshold", [], now="x")
    assert f"${config_stub.abs_threshold_usd:.0f}" in text
    assert store.get_meta(db, "abs_threshold_usd") is None


def test_threshold_with_number_updates_meta(db, config_stub):
    text = commands.handle(db, config_stub, "/threshold", ["199"], now="x")
    assert store.get_meta(db, "abs_threshold_usd") == "199.0"
    assert "199" in text


def test_threshold_with_garbage_explains_format_and_does_not_touch_meta(db, config_stub):
    text = commands.handle(db, config_stub, "/threshold", ["abc"], now="x")
    assert store.get_meta(db, "abs_threshold_usd") is None
    assert "число" in text.lower() or "220" in text


def test_threshold_rejects_non_positive(db, config_stub):
    text = commands.handle(db, config_stub, "/threshold", ["-5"], now="x")
    assert store.get_meta(db, "abs_threshold_usd") is None
    assert "положит" in text.lower()

    text_zero = commands.handle(db, config_stub, "/threshold", ["0"], now="x")
    assert store.get_meta(db, "abs_threshold_usd") is None
    assert "положит" in text_zero.lower()


def test_pause_sets_meta(db, config_stub):
    commands.handle(db, config_stub, "/pause", [], now="x")
    assert store.get_meta(db, "paused") == "1"


def test_resume_sets_meta(db, config_stub):
    commands.handle(db, config_stub, "/resume", [], now="x")
    assert store.get_meta(db, "paused") == "0"


def test_route_add_writes_json(db, config_stub):
    commands.handle(db, config_stub, "/route", ["add", "tas", "hkt"], now="x")
    assert json.loads(store.get_meta(db, "extra_routes")) == [["TAS", "HKT"]]


def test_route_add_is_idempotent(db, config_stub):
    commands.handle(db, config_stub, "/route", ["add", "TAS", "HKT"], now="x")
    text = commands.handle(db, config_stub, "/route", ["add", "TAS", "HKT"], now="x")
    assert json.loads(store.get_meta(db, "extra_routes")) == [["TAS", "HKT"]]
    assert "уже" in text.lower()


def test_route_add_rejects_bad_codes(db, config_stub):
    text = commands.handle(db, config_stub, "/route", ["add", "TA", "HKT"], now="x")
    assert store.get_meta(db, "extra_routes") is None
    assert "код" in text.lower() or "формат" in text.lower()


def test_route_without_add_explains_format(db, config_stub):
    text = commands.handle(db, config_stub, "/route", [], now="x")
    assert "/route add" in text


def test_bought_without_alerts_explains(db, config_stub):
    text = commands.handle(db, config_stub, "/bought", [], now="x")
    assert "алерт" in text.lower()


def test_mismatch_without_alerts_explains(db, config_stub):
    text = commands.handle(db, config_stub, "/mismatch", [], now="x")
    assert "алерт" in text.lower()


def test_bought_sets_feedback(db, config_stub):
    store.record_alert(db, "ALA-HKT-2026-10-06", 200.0, "2026-08-01T00:00:00Z", "buy")
    commands.handle(db, config_stub, "/bought", [], now="x")
    row = db.execute("SELECT feedback FROM alerts_sent").fetchone()
    assert row["feedback"] == "bought"


def test_mismatch_sets_feedback(db, config_stub):
    store.record_alert(db, "ALA-HKT-2026-10-06", 200.0, "2026-08-01T00:00:00Z", "buy")
    commands.handle(db, config_stub, "/mismatch", [], now="x")
    row = db.execute("SELECT feedback FROM alerts_sent").fetchone()
    assert row["feedback"] == "mismatch"


def test_unknown_command_suggests_help(db, config_stub):
    text = commands.handle(db, config_stub, "/frobnicate", [], now="x")
    assert "/help" in text
