import pytest
import responses

from src import runner, store

UPDATES_URL = "https://api.telegram.org/botTG/getUpdates"
SEND_URL = "https://api.telegram.org/botTG/sendMessage"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")


@pytest.fixture()
def db(env, tmp_path):
    path = tmp_path / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    conn.close()
    return path


def _update(update_id, text, chat_id=999):
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


@responses.activate
def test_two_commands_in_one_batch_get_two_replies(db, config_stub):
    responses.add(
        responses.POST,
        UPDATES_URL,
        json={"ok": True, "result": [_update(1, "/status"), _update(2, "/pause")]},
        status=200,
    )
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)

    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["handled"] == 2
    sent = [c for c in responses.calls if c.request.url.startswith(SEND_URL)]
    assert len(sent) == 2


@responses.activate
def test_no_updates_sends_nothing(db, config_stub):
    responses.add(responses.POST, UPDATES_URL, json={"ok": True, "result": []}, status=200)

    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["handled"] == 0
    assert not [c for c in responses.calls if c.request.url.startswith(SEND_URL)]


@responses.activate
def test_plain_text_is_not_handled(db, config_stub):
    responses.add(
        responses.POST,
        UPDATES_URL,
        json={"ok": True, "result": [_update(1, "спасибо, что подсказал!")]},
        status=200,
    )

    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["handled"] == 0
    assert not [c for c in responses.calls if c.request.url.startswith(SEND_URL)]


@responses.activate
def test_foreign_chat_message_is_ignored(db, config_stub):
    responses.add(
        responses.POST,
        UPDATES_URL,
        json={"ok": True, "result": [_update(1, "/status", chat_id=111)]},
        status=200,
    )

    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["handled"] == 0
    assert not [c for c in responses.calls if c.request.url.startswith(SEND_URL)]


@responses.activate
def test_threshold_command_persists_to_meta(db, config_stub):
    responses.add(
        responses.POST,
        UPDATES_URL,
        json={"ok": True, "result": [_update(1, "/threshold 199")]},
        status=200,
    )
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)

    runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    conn = store.connect(db)
    assert store.get_meta(conn, "abs_threshold_usd") == "199.0"


@responses.activate
def test_send_error_on_one_reply_does_not_stop_the_rest(db, config_stub):
    responses.add(
        responses.POST,
        UPDATES_URL,
        json={"ok": True, "result": [_update(1, "/status"), _update(2, "/help")]},
        status=200,
    )
    responses.add(
        responses.POST, SEND_URL, json={"ok": False, "description": "blocked"}, status=200
    )
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)

    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["handled"] == 1
    sent = [c for c in responses.calls if c.request.url.startswith(SEND_URL)]
    assert len(sent) == 2


def test_run_commands_requires_tg_env(monkeypatch, tmp_path, config_stub):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    with pytest.raises(runner.ConfigurationError):
        runner.run_commands(config_stub, db_path=tmp_path / "history.sqlite")
