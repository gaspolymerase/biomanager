"""Who may fetch an uploaded file.

Every uploaded file (a notebook page's picture or attachment, a file kept
with a plasmid, a capture's photo or CSV) is saved under a random name in
the uploads folder (services.save_uploaded_file) and served at
/static/uploads/<name>. A random name is not a lock: a name copied out of a
page, a log or a lab copy would open the file for anyone signed in. So each
file is served only to someone who may see a record it belongs to, by the
rules the record's own page uses.

What a file belongs to is kept in `upload_owners` (models.UploadOwner):

  source "upload"        who uploaded it (the notebook editor's pictures and
                         files, before the page naming them is saved)
  source <table>         a record naming it (uploads/<name> anywhere in its
                         text): a notebook page, a version of one, a
                         template, a plasmid's file, a capture, ...

Rows are added by an `after_flush` hook whenever a record whose text names
an upload is saved, whichever page or API saved it, but only when whoever
saves it may open the file already (`_may_carry`: they uploaded it in this
request or before, or a record they see owns it), so pasting a file's
address into one's own page opens nothing; and by revision
0038_upload_owners for what was saved before. They are never taken away: a
record that went (or that someone may no longer see) simply stops letting
anyone fetch the file.

Who sees a record (`_record_visible`) follows the pages, as a member's copy
of the lab does (app/lab_copy.member_view): a notebook page, and what hangs
off it, as lab_notebook.role_for says (owner, shares, the lab, project
groups; a guest is not "the lab"); a capture its owner only; a template its
owner, and the lab or a project group if it is theirs; a record in a
personal or project group's database as lab.can_see says; someone's own
rows (notifications, batches, ...) them; the admin-only tables admins; the
lab's shared records everyone signed in. A file no record names (an orphan,
or a picture whose page went) is for whoever uploaded it and the lab's
admins, who look after the lab; it is refused to everyone else.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote

from sqlalchemy import Table, event, inspect as sa_inspect, select
from sqlalchemy import String, Text
from sqlalchemy.orm import Session

from .models import UploadOwner

# Off while the start-up steps before Alembic save records (services.init_database).
enabled = True

UPLOADER = "upload"
# What names an upload in a record's text (as app/lab_copy.UPLOAD_REF).
UPLOAD_REF = re.compile(r"uploads/([^\s\"'<>()\[\]\\?#]+)")
NAME_MAX = 300


def names_in(text: str | None) -> set[str]:
    """The uploaded files a piece of text names."""
    if not text or "uploads/" not in text:
        return set()
    return {name for name in (unquote(n).strip("/") for n in UPLOAD_REF.findall(text))
            if name and len(name) <= NAME_MAX and ".." not in name.split("/")}


def stored_name(path: str) -> str:
    """'uploads/<name>' (as save_uploaded_file returns it) as <name>."""
    return path.split("uploads/", 1)[1] if "uploads/" in path else path


def note_uploader(session, saved_path: str, username: str) -> None:
    """Whoever uploaded a file may fetch it, before any record names it."""
    if saved_path and username:
        session.add(UploadOwner(name=stored_name(saved_path), source=UPLOADER, username=username))


def note_record(session, saved_path: str, source: str, record_id: int) -> None:
    """A record owns a file before its text names it: a picture uploaded
    into a notebook page the uploader may edit (app.notebook_upload_image)."""
    session.add(UploadOwner(name=stored_name(saved_path), source=source, record_id=record_id, username=""))


# ---------------------------------------------------------------- noting what names a file

# Tables whose text never makes a file anyone's: the change history keeps
# what records said (the admins' to read), and the live editor's state is
# base64.
NOT_OWNERS = frozenset({"upload_owners", "audit_log", "notebook_sync_updates", "notebook_presence",
                        "page_presence"})
_TEXT_ATTRS: dict = {}


def _text_attrs(mapper) -> list[str]:
    """The attributes of a mapped class whose column can hold text naming an upload."""
    if mapper not in _TEXT_ATTRS:
        from .models import EncryptedText
        keys = []
        for column in mapper.local_table.columns:
            if isinstance(column.type, (String, Text)) and not isinstance(column.type, EncryptedText):
                try:
                    keys.append(mapper.get_property_by_column(column).key)
                except Exception:  # noqa: BLE001 — a column no attribute maps
                    continue
        _TEXT_ATTRS[mapper] = keys
    return _TEXT_ATTRS[mapper]


def note_fresh(saved_path: str) -> None:
    """A file this request uploaded (services.save_uploaded_file): the
    record it saves the file with (a plasmid's file, a capture) owns it."""
    from flask import g, has_request_context
    if saved_path and has_request_context():
        g.setdefault("fresh_uploads", set()).add(stored_name(saved_path))


def _may_carry(session, name: str) -> bool:
    """May the record being saved make `name` its own? Only if whoever saves
    it may open the file already (they uploaded it, now or before, or a
    record they see owns it): a name copied into a page of one's own must
    not open someone else's file. Outside a request (start-up, scripts,
    revisions, a copy or a move of the whole lab) the records are the lab's
    own, as they were."""
    from flask import g, has_request_context
    if not has_request_context():
        return True
    if name in g.get("fresh_uploads", ()):
        return True
    return may_fetch(session, g.get("user"), name)


@event.listens_for(Session, "after_flush")
def _note_names(session, flush_context):
    if not enabled:
        return
    found: dict[tuple[str, int], set[str]] = {}
    for obj, changed_only in [(o, False) for o in session.new] + [(o, True) for o in session.dirty]:
        table = getattr(obj, "__table__", None)
        if not isinstance(table, Table) or table.name in NOT_OWNERS or "id" not in table.columns:
            continue
        state = sa_inspect(obj)
        # Its id (a new record's identity key is set only after this hook).
        try:
            record_id = state.dict.get(state.mapper.get_property_by_column(table.c.id).key)
        except Exception:  # noqa: BLE001 — an id no attribute maps
            continue
        if not isinstance(record_id, int):
            continue
        names: set[str] = set()
        for key in _text_attrs(state.mapper):
            if changed_only and not state.attrs[key].history.has_changes():
                continue
            value = state.dict.get(key)          # as saved; never loads anything
            names |= names_in(value if isinstance(value, str) else None)
        if names:
            found.setdefault((table.name, record_id), set()).update(names)
    if not found:
        return
    conn = session.connection()
    owners = UploadOwner.__table__
    rows = []
    carries: dict[str, bool] = {}
    for (source, record_id), names in found.items():
        have = set(conn.execute(select(owners.c.name).where(owners.c.source == source,
                                                            owners.c.record_id == record_id,
                                                            owners.c.name.in_(sorted(names)))).scalars())
        for n in sorted(names - have):
            if n not in carries:
                carries[n] = _may_carry(session, n)
            if carries[n]:
                rows.append({"name": n, "source": source, "record_id": record_id, "username": ""})
    if rows:
        now = datetime.utcnow()
        conn.execute(owners.insert(), [{**r, "created_at": now} for r in rows])


# ---------------------------------------------------------------- who may fetch a file

def _tables():
    from .db import Base
    return Base.metadata.tables


def _model_for(table_name: str):
    from .db import Base
    for mapper in Base.registry.mappers:
        if getattr(mapper.class_, "__tablename__", None) == table_name:
            return mapper.class_
    return None


def _page_visible(session, user, page_id) -> bool | None:
    from . import lab_notebook
    from .models import NotebookPage
    page = session.get(NotebookPage, page_id) if page_id else None
    if page is None:
        return None
    return lab_notebook.role_for(session, page, user) is not None


def _record_visible(session, user, source: str, record_id: int) -> bool | None:
    """True: this person may see the record; False: it exists and they may
    not; None: there is no such record (any more)."""
    from . import groups, lab, lab_copy
    table = _tables().get(source)
    if table is None or "id" not in table.columns:
        return None
    row = session.execute(select(table).where(table.c.id == record_id)).mappings().first()
    if row is None:
        return None
    me, admin = user.username, user.role == "admin"
    if source == "notebook_pages":
        return _page_visible(session, user, record_id)
    if "page_id_fk" in row:                 # versions, comments, signatures… of a page
        return _page_visible(session, user, row["page_id_fk"])
    if source == "captures":
        return row["owner_username"] == me
    if source == "notebook_templates":
        if row["owner_username"] == me:
            return True
        if not row["lab"]:
            return False
        return row["share_group_id"] is None or groups.in_group(row["share_group_id"], user)
    if source == "plasmid_files":
        from .models import PlasmidRecord
        return True if session.get(PlasmidRecord, row["plasmid_row_id"]) is not None else None
    if source in lab_copy.ADMIN_ONLY:
        return admin
    if source in lab_copy.OWN_ROWS:
        return row[lab_copy.OWN_ROWS[source]] == me
    if source in lab_copy.OWN_OR_SHARED:
        return row["owner"] == me or bool(row["is_shared"]) and (
            row["share_group_id"] is None or groups.in_group(row["share_group_id"], user))
    if source in lab_copy.PERSONAL_DATABASES:
        model = _model_for(source)
        return lab.can_see(session.get(model, record_id), user) if model is not None else admin
    for fk in table.foreign_keys:
        if fk.parent.name == "module_id_fk" and fk.column.table.name in lab_copy.PERSONAL_DATABASES:
            model = _model_for(fk.column.table.name)
            module = session.get(model, row["module_id_fk"]) if model is not None and row["module_id_fk"] else None
            return lab.can_see(module, user) if module is not None else None
    return True                             # the lab's shared records: everyone signed in sees them


def may_fetch(session, user, name: str) -> bool:
    """May this person fetch uploads/<name>? One indexed look-up of what it
    belongs to, then the rule of each record until one lets them."""
    if user is None or not name:
        return False
    owners = list(session.execute(select(UploadOwner.source, UploadOwner.record_id, UploadOwner.username)
                                  .where(UploadOwner.name == name)).all())
    refused = False
    for source, record_id, username in owners:
        if source == UPLOADER:
            if username == user.username:
                return True
            continue
        seen = _record_visible(session, user, source, record_id)
        if seen:
            return True
        if seen is False:
            refused = True
    # A file no record names: its uploader's (above) and the admins'.
    return user.role == "admin" and not refused


def serve(filename: str):
    """/static/uploads/<filename>: the file, to someone who may see it (else
    404, as if it weren't there). security.guard_uploads has already sent
    anyone not signed in to the sign-in page."""
    from flask import abort, g, send_from_directory

    from . import services
    from .db import SessionLocal
    with SessionLocal() as session:
        allowed = may_fetch(session, g.get("user"), filename)
    if not allowed:
        abort(404)
    return send_from_directory(services.UPLOAD_DIR, filename)


# ---------------------------------------------------------------- a member's copy of the lab

def narrow_copy(run, tables) -> None:
    """In a member's copy (app/lab_copy.member_view, `run` executing SQL with
    :me bound): only what names a file of theirs or a record the copy kept."""
    run("DELETE FROM upload_owners WHERE source = :uploader AND username != :me", uploader=UPLOADER)
    for (source,) in list(run("SELECT DISTINCT source FROM upload_owners")):
        if source == UPLOADER:
            continue
        table = tables.get(source)
        if table is None or source == "upload_owners" or "id" not in table.columns:
            run("DELETE FROM upload_owners WHERE source = :source", source=source)
        else:   # a name from the app's own tables
            run(f'DELETE FROM upload_owners WHERE source = :source AND record_id NOT IN (SELECT id FROM "{source}")',
                source=source)
