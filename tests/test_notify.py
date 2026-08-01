import json

import pytest
import requests
import responses

from src import notify

URL = "https://api.telegram.org/botTOKEN/sendMessage"


@pytest.fixture()
def session():
    return requests.Session()


@responses.activate
def test_send_message_posts_expected_payload(session):
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    notify.send_message(session, "TOKEN", "12345", "привет")
    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == "12345"
    assert body["text"] == "привет"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True


@responses.activate
def test_send_message_raises_on_api_error(session):
    responses.add(
        responses.POST, URL, json={"ok": False, "description": "chat not found"}, status=400
    )
    with pytest.raises(notify.TelegramError, match="chat not found"):
        notify.send_message(session, "TOKEN", "12345", "привет")


@responses.activate
def test_send_message_raises_on_transport_error(session):
    responses.add(responses.POST, URL, body=requests.ConnectionError("boom"))
    with pytest.raises(notify.TelegramError):
        notify.send_message(session, "TOKEN", "12345", "привет")


@responses.activate
def test_long_text_is_split_into_several_calls(session):
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    text = "\n".join(f"строка {i}" for i in range(1200))
    notify.send_message(session, "TOKEN", "12345", text)
    assert len(responses.calls) > 1


def test_split_message_keeps_chunks_under_limit():
    text = "\n".join("x" * 100 for _ in range(200))
    chunks = notify.split_message(text, limit=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_message_prefers_newline_boundaries():
    text = "первая\nвторая\nтретья"
    chunks = notify.split_message(text, limit=14)
    assert chunks[0] == "первая"


def test_split_message_handles_single_oversized_line():
    text = "y" * 50
    chunks = notify.split_message(text, limit=20)
    assert len(chunks) == 3
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_split_message_short_text_is_one_chunk():
    assert notify.split_message("коротко", limit=100) == ["коротко"]
