"""Run the pages' own code on someone's behalf, inside one transaction that
is then rolled back (a preview) or kept (an approval), whole.

A proposal (app/proposals.py) is a list of things a person could do on the
pages: record a litter, wean a cage, mark a step done. Rather than a second
copy of each page's rules, each change is sent to the page itself, as the
form it would post, through Flask's full request handling (every
before_request check: signed in, the function switched on, a read-only
desktop, permissions in the view). Every session the page opens joins one
outer transaction (db.JOINED), so its commits become savepoints:

    with contained.transaction() as tx:
        result = contained.run(app, "alex", "POST", "/colony/cages/12/give-birth",
                               data={"date_give_birth": "2026-10-04"})
        ...                       # read what changed, through the same transaction
        tx.commit()               # or leave the block: everything is rolled back

`run` reports what the page said (its flashed messages, or its JSON) and
whether it refused. The audit listener (app/audit.py) writes its usual rows
inside the transaction, so a preview can read exactly what would change.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import SimpleNamespace

from flask import g, get_flashed_messages
from markupsafe import Markup
from werkzeug.datastructures import MultiDict

from .db import JOINED, contained_engine

_ACTING: ContextVar = ContextVar("biomanager_acting_user", default=None)
_LANG: ContextVar = ContextVar("biomanager_acting_lang", default=None)

# What /api/v1 sees as the token while a proposal calls the API's own writes
# (app.load_current_user): read-and-change, and no rate limit (id 0).
API_TOKEN = SimpleNamespace(id=0, label="proposal", scope="write")


def acting_user() -> str | None:
    """Whose request this is, when a proposal is running the pages."""
    return _ACTING.get()


def acting_lang() -> str | None:
    """The language the pages answer in then (the person's own)."""
    return _LANG.get()


@dataclass
class PageResult:
    ok: bool
    status: int
    messages: list[tuple[str, str]] = field(default_factory=list)   # (category, text)
    data: dict | None = None                                         # a JSON answer
    location: str = ""                                               # where it redirected

    @property
    def errors(self) -> list[str]:
        return [text for category, text in self.messages if category == "error"]

    @property
    def warnings(self) -> list[str]:
        return [text for category, text in self.messages if category == "warning"]


class Transaction:
    def __init__(self, outer):
        self._outer = outer
        self.committed = False

    def commit(self) -> None:
        self._outer.commit()
        self.committed = True

    def savepoint(self):
        """A part that can be undone alone (rollback()) or kept (commit())."""
        return self._outer.connection.begin_nested()


@contextmanager
def transaction():
    """One outer transaction every SessionLocal() inside joins. Rolled back
    on the way out unless commit() was called."""
    conn = contained_engine().connect()
    outer = conn.begin()
    token = JOINED.set(conn)
    try:
        yield Transaction(outer)
    finally:
        JOINED.reset(token)
        try:
            if outer.is_active:
                outer.rollback()
        finally:
            conn.close()


def run(app, username: str, method: str, path: str, data=None, json_body=None,
        batch_id: int | None = None, label: str = "", lang: str | None = None) -> PageResult:
    """Make one request to the app as `username`, inside the current
    transaction, and say how the page answered. With `batch_id`, what it
    changes belongs to that batch (pages' own batches join it, audit.batch)
    and is labelled `label` in the change history. The page answers in
    `lang` (English if not given)."""
    if isinstance(data, list):  # a form's (name, value) pairs, a name repeating
        data = MultiDict(data)
    token, lang_token = _ACTING.set(username), _LANG.set(lang)
    try:
        with app.test_request_context(path, method=method, data=data, json=json_body):
            if batch_id is not None:
                g.audit_batch_id = batch_id
                g.audit_batch = label
            try:
                response = app.full_dispatch_request()
            except Exception as exc:  # noqa: BLE001 — a page that fails refuses the change
                app.logger.exception("a proposal's page failed: %s %s", method, path)
                return PageResult(False, 500, [("error", f"The page failed: {exc}")])
            messages = [(c, _plain(t)) for c, t in get_flashed_messages(with_categories=True)]
    finally:
        _ACTING.reset(token)
        _LANG.reset(lang_token)
    body = None
    if response.mimetype == "application/json":
        try:
            body = json.loads(response.get_data(as_text=True) or "null")
        except ValueError:
            body = None
    refused = response.status_code >= 400 or any(c == "error" for c, _ in messages)
    if isinstance(body, dict):
        if isinstance(body.get("warnings"), list):  # the API's writes pass on the pages' warnings
            messages += [("warning", _plain(w)) for w in body["warnings"]]
        if body.get("ok") is False or (body.get("error") and not body.get("ok")):
            refused = True
            if body.get("error"):
                messages.append(("error", str(body["error"])))
    location = response.headers.get("Location", "") if 300 <= response.status_code < 400 else ""
    if location.startswith("/login"):
        refused = True
        messages.append(("error", "Not signed in"))
    return PageResult(not refused, response.status_code, messages, body if isinstance(body, dict) else None, location)


def _plain(text) -> str:
    """A flashed message as words (some carry a link, as Markup)."""
    return str(Markup(text).striptags()) if isinstance(text, Markup) else str(text)
