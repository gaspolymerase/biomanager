"""Giving a record to someone else, and sharing it.

Every database's records carry an `owner`, and most can be shared with the
lab or a project group (app/groups.py). Who may change either is one rule
in every database, so a dialog, a sheet row, a batch action and the API all
ask here instead of checking for themselves:

  * Give it to someone else: its owner or an admin (anyone while it has no
    owner), and animal care for the animals, cages and tanks they look
    after (access.CARE_RECORDS). Not everyone who may edit it because it
    is shared: else anyone could take a shared cage and make it theirs.
    Only to a lab member, or to nobody.
  * Share it or make it personal: its owner or an admin, and only with a
    project group the person is in (groups.apply).

Each change answers with the refusal in words, or None when it was made
(or there was nothing to change). Mice are the exception that stays in the
colony (colony.populate_mouse_from_form): anyone who may edit a mouse in a
shared cage may take it, which is what Pick on the Breeders tab does.
"""
from __future__ import annotations

from sqlalchemy import select

from . import access, groups
from .i18n import gettext, translate_value


def lab_members(session) -> set[str]:
    """Every username in the lab: who a record may be given to."""
    from .models import UserAccount
    return set(session.scalars(select(UserAccount.username)))


def may_give_away(record, user=None) -> bool:
    """May this person give the record to someone else (or to nobody)?"""
    return access.can_manage(record, user) or (
        type(record).__name__ in access.CARE_RECORDS and access.is_care(user))


def _what(noun: str, name) -> str:
    """"tank T12", in the page's language."""
    return f"{translate_value(noun)} {name or ''}".strip()


def _owner_of(record) -> str:
    return (getattr(record, "owner", "") or "").strip()


def not_a_member(owner: str) -> str:
    return gettext("“%(owner)s” is not a lab member. Pick a username from the list.", owner=owner)


def owner_refused(session, record, new_owner, noun: str, name="", members=None, user=None) -> str | None:
    """Why `record` may not go to `new_owner`, or None. `members`: the lab's
    usernames, when the caller already has them (a batch)."""
    new_owner = (new_owner or "").strip()
    if new_owner == _owner_of(record):
        return None
    if not may_give_away(record, user):
        return gettext("Only %(owner)s or an admin can give %(what)s to someone else.",
                       owner=_owner_of(record) or gettext("its owner"), what=_what(noun, name))
    if new_owner and new_owner not in (members if members is not None else lab_members(session)):
        return not_a_member(new_owner)
    return None


def change_owner(session, record, new_owner, noun: str, name="", members=None, user=None) -> str | None:
    """Give `record` to `new_owner` (blank: nobody), or say why not."""
    new_owner = (new_owner or "").strip()
    refused = owner_refused(session, record, new_owner, noun, name, members, user)
    if refused is None and new_owner != _owner_of(record):
        record.owner = new_owner
    return refused


def batch_owner_refused(members, new_owner) -> str | None:
    """A batch Set owner names one person for every ticked record: refused
    as a whole when they are not in the lab (a blank too: a slip that would
    leave every ticked record open to everyone)."""
    new_owner = (new_owner or "").strip()
    if new_owner not in members:
        return gettext("“%(value)s” is not a lab member, so no owner was changed. Pick a username from the list.",
                       value=new_owner or gettext("(blank)"))
    return None


def change_sharing(record, raw, noun: str, name="", shared: bool | None = None, set_shared: bool = True,
                   user=None) -> str | None:
    """Share `record` as `raw` says ("0" personal, "1" the lab, "g<id>" a
    project group), or say why not. A tank or vial shared by its purpose
    passes `shared` (whether it is) and set_shared=False: only its group
    changes."""
    if not groups.differs(record, raw, shared=shared):
        return None
    if not access.can_manage(record, user):
        return gettext("Only %(owner)s or an admin can change whom %(what)s is shared with.",
                       owner=_owner_of(record) or gettext("its owner"), what=_what(noun, name))
    return groups.apply(record, raw, user, set_shared=set_shared)
