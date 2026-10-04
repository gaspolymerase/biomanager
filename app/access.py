"""Who may change what.

The lab model this encodes:

  * You manage your own colony. A record you own is yours to edit or delete.
  * Shared resources are everyone's. Breeder cages are the case that matters
    in practice — the whole lab picks mice out of them, so the whole lab has
    to be able to edit them; they start shared, and any other cage can be. Shared with a project group (app/groups.py)
    instead, they are its members' to edit.
  * Unowned records stay open. Records predating ownership have an empty
    owner, and locking the lab out of its own history helps nobody.
  * Admins can do anything, always.

Visibility is deliberately *not* restricted: everyone can read the whole
colony, because a census with holes in it is not a census. What the scope
switch changes is the default filter, not permission.

Keep this the single source of truth — a second copy of these rules in a
route is how they drift apart.
"""
from __future__ import annotations

from flask import g

# The cage purpose that starts out shared with the lab: a breeder cage
# (app/models.py), which stays so unless its owner or an admin makes it
# personal. Any other cage starts personal, and its owner or an admin may
# share it. Matched case-insensitively against a trimmed value.
STARTS_SHARED_PURPOSES = {"breeder"}


def current_user():
    return g.get("user") if g else None


# The roles an admin gives (Manage users), besides pending and guest:
ROLES = {
    "member": ("Member", "Changes their own records, and the lab's shared ones."),
    "care": ("Animal care", "Technicians and vets: may change any lab's animals, cages, tanks and vials "
                            "(weights, moves, flips, health). Other people's experiments and notebook pages, "
                            "and settings, stay theirs; they keep their own like a member."),
    "facility": ("Facility manager", "Animal care, plus the facility's racks, rooms, incubators, water systems "
                                     "and databases' settings. Not accounts."),
    "admin": ("Admin", "Everything, including accounts and Lab setup."),
}
# Records animal care staff look after, whoever owns them.
CARE_RECORDS = {"MouseRecord", "CageRecord", "TankRecord", "FishRecord", "StockUnit", "Organism", "OrgHousing",
                "OrgCohort", "ClutchRecord", "LitterRecord"}
# Records with a Lab common switch (is_shared) that any member may edit.
LAB_COMMON_RECORDS = {"PlasmidRecord"}


def is_admin(user=None) -> bool:
    user = user or current_user()
    return getattr(user, "role", None) == "admin"


def is_facility(user=None) -> bool:
    user = user or current_user()
    return getattr(user, "role", None) in ("facility", "admin")


def is_care(user=None) -> bool:
    """Animal care staff (technicians, vets), a facility manager or an admin."""
    user = user or current_user()
    return getattr(user, "role", None) in ("care", "facility", "admin")


def username(user=None) -> str:
    user = user or current_user()
    return getattr(user, "username", "") or ""


def owns(record, user=None) -> bool:
    """True when `record.owner` names this user."""
    owner = (getattr(record, "owner", "") or "").strip()
    return bool(owner) and owner == username(user)


def is_unowned(record) -> bool:
    return not (getattr(record, "owner", "") or "").strip()


def can_be_shared(cage) -> bool:
    """Any cage can be shared, with the lab or a project group."""
    return cage is not None


def is_shared_cage(cage) -> bool:
    """A cage shared with the lab or with a project group (a breeder cage
    starts so; any other once its owner shares it)."""
    return can_be_shared(cage) and bool(getattr(cage, "is_shared", False))


def cage_shared_with(cage, user=None) -> bool:
    """A shared cage this person may work in: the lab's, or one of their
    project groups'."""
    from . import groups
    return is_shared_cage(cage) and groups.record_shared_with(cage, user)


def can_set_sharing(cage, user=None) -> bool:
    """Who may make a cage shared or personal: its owner or an admin
    (anyone while it has no owner), not everyone who may edit it because
    it is shared."""
    return can_be_shared(cage) and can_manage(cage, user)


def can_edit(record, user=None, shared: bool = False) -> bool:
    """The general rule, used for anything carrying an `owner`."""
    if record is None:
        return False
    if is_admin(user):
        return True
    if shared:
        return True
    if type(record).__name__ in CARE_RECORDS and is_care(user):
        return True
    if type(record).__name__ in LAB_COMMON_RECORDS and getattr(record, "is_shared", False):
        from . import groups
        if groups.record_shared_with(record, user):
            return True
    return owns(record, user) or is_unowned(record)


def can_manage(record, user=None) -> bool:
    """Delete it, give it to someone else, make it personal or lab common:
    its owner or an admin (anyone, while it is unowned), even when lab
    common lets everyone edit it."""
    return record is not None and (is_admin(user) or owns(record, user) or is_unowned(record))


def can_edit_cage(cage, user=None) -> bool:
    return can_edit(cage, user, shared=cage_shared_with(cage, user))


def can_edit_mouse(mouse, user=None) -> bool:
    """A mouse is editable if it is yours, unowned, or sitting in a shared
    breeder cage that the whole lab (or the person's project group) works
    out of."""
    if mouse is None:
        return False
    return can_edit(mouse, user, shared=cage_shared_with(getattr(mouse, "cage", None), user))


def can_edit_experiment(experiment, user=None) -> bool:
    """An experiment belongs to whoever created it (owner_username); only
    they, or an admin, may rename it, change its members or delete it."""
    if experiment is None:
        return False
    if is_admin(user):
        return True
    owner = (getattr(experiment, "owner_username", "") or "").strip()
    return not owner or owner == username(user)


def can_edit_litter(litter, user=None) -> bool:
    """A litter has no owner of its own: its date of birth is the age of
    every mouse in it. So it is editable by someone who may edit all of
    its mice (an empty litter by anyone)."""
    if litter is None:
        return False
    if is_admin(user) or is_care(user):
        return True
    return all(can_edit_mouse(mouse, user) for mouse in (getattr(litter, "mice", None) or []))


def can_edit_rack(rack, user=None) -> bool:
    """Resizing, renaming or deleting a rack moves every cage in it, so it
    is for whoever created the rack, or an admin. Racks that predate the
    creator column are admin-only (an admin can still change them all).
    Used for mouse racks, inventory boxes and fly/worm racks alike."""
    if rack is None:
        return False
    if is_facility(user):
        return True
    creator = (getattr(rack, "created_by", "") or "").strip()
    return bool(creator) and creator == username(user)


def can_edit_strain(strain, user=None) -> bool:
    """Renaming or removing a strain changes the list everyone picks from,
    so it is for whoever added it, or an admin. Strains that predate the
    creator column are admin-only. Anyone may add a strain (and so becomes
    its creator); picking an existing one is open to all."""
    if strain is None:
        return False
    if is_admin(user):
        return True
    creator = (getattr(strain, "created_by", "") or "").strip()
    return bool(creator) and creator == username(user)


def can_edit_presets(user=None) -> bool:
    """The colony's saved dropdown values are the lab's vocabulary: only an
    admin adds, renames or removes them. Everyone picks from them."""
    return is_admin(user)


def denied_message(what: str, owner: str = "") -> str:
    """The refusal for records that are not owned through `.owner`."""
    who = f"{owner}’s" if owner else "someone else’s"
    return f"That {what} is {who}. Ask them, or an admin, to make the change."


def can_configure(module, user=None) -> bool:
    """Changing a database's definition — its fields, schedule rules,
    racks and locations, vocabulary, or deleting it — is for an admin or
    whoever created it. Records inside it follow can_edit."""
    if module is None:
        return False
    if is_facility(user):
        return True
    creator = (getattr(module, "created_by", "") or "").strip()
    return bool(creator) and creator == username(user)


def reason_denied(record, user=None, noun: str | None = None) -> str:
    """A message worth showing someone, rather than a bare 403. `noun`
    names the kind of record ("reagent", "cage"…); "record" otherwise."""
    from .i18n import gettext, translate_value
    owner = (getattr(record, "owner", "") or "").strip() or gettext("someone else")
    return gettext("That %(thing)s belongs to %(owner)s. Ask them, or an admin, to make the change.",
                   thing=translate_value(noun or "record"), owner=owner)


# ---------------------------------------------------------------------------
# Scope: which slice of the colony a list view shows by default
# ---------------------------------------------------------------------------

SCOPES = (
    ("mine", "My colony"),
    ("groups", "My groups"),
    ("shared", "Shared"),
    ("all", "Everyone"),
)
SCOPE_HINTS = {
    "mine": "Your own mice, and the shared cages you work in",
    "groups": "The mice and cages of everyone in your project groups, and the cages shared with them",
    "shared": "The shared cages you work in: the lab's and your groups'",
    "all": "Every mouse in the lab (you still edit only your own)",
}
VALID_SCOPES = {key for key, _ in SCOPES}
DEFAULT_SCOPE = "mine"


def scopes_for(user=None):
    """The scope switch: My groups only for someone in a group."""
    from . import groups
    has_groups = bool(groups.ids_of(user))
    return tuple((key, label) for key, label in SCOPES if key != "groups" or has_groups)


def resolve_scope(raw: str | None) -> str:
    scope = (raw or "").strip().lower()
    return scope if scope in VALID_SCOPES else DEFAULT_SCOPE


def cage_group(cage):
    """The project group a shared cage is shared with, if any."""
    return getattr(cage, "share_group_id", None) if is_shared_cage(cage) else None


def in_scope(record, scope: str, shared: bool = False, user=None, group_id=None) -> bool:
    """Filter predicate for list views. Applied in Python rather than SQL
    because 'shared' depends on the cage a mouse happens to sit in.
    `group_id` is the project group the record (or its cage) is shared with."""
    if scope == "all":
        return True
    if scope == "shared":
        return shared
    if scope == "groups":
        from . import groups
        owner = (getattr(record, "owner", "") or "").strip()
        return owner in groups.colleagues(user) or groups.in_group(group_id, user)
    # "mine" still shows shared resources — they are part of your working set.
    return owns(record, user) or shared or is_unowned(record)
