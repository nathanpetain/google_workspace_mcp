"""list_gmail_drafts, send_gmail_draft and delete_gmail_draft."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_tools import delete_gmail_draft, list_gmail_drafts, send_gmail_draft


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _http_error(status):
    return HttpError(SimpleNamespace(status=status, reason="x"), b"{}")


def _draft(draft_id, to="anna@example.com", subject="Offer", cc=None):
    headers = [{"name": "Subject", "value": subject}]
    if to:
        headers.append({"name": "To", "value": to})
    if cc:
        headers.append({"name": "Cc", "value": cc})
    return {
        "id": draft_id,
        "message": {
            "id": "m-" + draft_id,
            "threadId": "t1",
            "payload": {"headers": headers},
        },
    }


def _service(drafts=None, get=None, send=None, delete=None):
    svc = Mock()
    d = svc.users.return_value.drafts.return_value
    d.list.return_value.execute.return_value = {"drafts": drafts or []}
    d.get.return_value.execute.side_effect = get or (lambda *a, **k: None)
    d.send.return_value.execute.side_effect = send or (lambda *a, **k: {"id": "sent-1"})
    d.delete.return_value.execute.side_effect = delete or (lambda *a, **k: "")
    return svc, d


@pytest.mark.asyncio
async def test_list_returns_draft_ids_and_headers():
    svc, d = _service(drafts=[{"id": "r1"}, {"id": "r2"}])
    d.get.return_value.execute.side_effect = [
        _draft("r1"),
        _draft("r2", subject="Follow-up"),
    ]
    out = await _unwrap(list_gmail_drafts)(
        svc, "me@x.si", query="to:anna", page_size=99
    )
    assert "Draft ID: r1" in out and "Draft ID: r2" in out and "Follow-up" in out
    assert d.list.call_args.kwargs == {"userId": "me", "maxResults": 25, "q": "to:anna"}


@pytest.mark.asyncio
async def test_list_empty():
    svc, _ = _service(drafts=[])
    assert "No drafts found" in await _unwrap(list_gmail_drafts)(svc, "me@x.si")


@pytest.mark.asyncio
async def test_send_sends_once_without_retries():
    svc, d = _service()
    d.get.return_value.execute.side_effect = lambda *a, **k: _draft(
        "r1", cc="bob@example.com"
    )
    out = await _unwrap(send_gmail_draft)(svc, "me@x.si", " r1 ")
    assert (
        "Draft sent! Message ID: sent-1" in out
        and "anna@example.com, cc bob@example.com" in out
    )
    d.send.assert_called_once_with(userId="me", body={"id": "r1"})
    assert d.send.return_value.execute.call_args.kwargs == {"num_retries": 0}


@pytest.mark.asyncio
async def test_send_refuses_draft_without_recipient():
    svc, d = _service()
    d.get.return_value.execute.side_effect = lambda *a, **k: _draft("r1", to=None)
    with pytest.raises(UserInputError, match="no recipient"):
        await _unwrap(send_gmail_draft)(svc, "me@x.si", "r1")
    d.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_unknown_draft_is_user_error():
    def missing(*a, **k):
        raise _http_error(404)

    svc, d = _service(get=missing)
    with pytest.raises(UserInputError, match="No draft with ID"):
        await _unwrap(send_gmail_draft)(svc, "me@x.si", "nope")
    d.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_server_error_is_reported_as_ambiguous():
    def boom(*a, **k):
        raise _http_error(503)

    svc, d = _service(send=boom)
    d.get.return_value.execute.side_effect = lambda *a, **k: _draft("r1")
    with pytest.raises(ToolError, match="may already have been sent"):
        await _unwrap(send_gmail_draft)(svc, "me@x.si", "r1")
    assert d.send.return_value.execute.call_count == 1


@pytest.mark.asyncio
async def test_delete_deletes_the_draft():
    svc, d = _service()
    d.get.return_value.execute.side_effect = lambda *a, **k: _draft("r1", subject="Old")
    out = await _unwrap(delete_gmail_draft)(svc, "me@x.si", "r1")
    assert out == "Draft r1 deleted (subject: Old)."
    d.delete.assert_called_once_with(userId="me", id="r1")


@pytest.mark.asyncio
async def test_empty_draft_id_rejected():
    svc, d = _service()
    for tool in (send_gmail_draft, delete_gmail_draft):
        with pytest.raises(UserInputError):
            await _unwrap(tool)(svc, "me@x.si", "  ")
    d.get.assert_not_called()
