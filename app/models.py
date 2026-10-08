from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy import event
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


ORDER_STATUS_OPTIONS = ["requested", "ordered", "received", "cancelled"]
TASK_STATUS_OPTIONS = ["todo", "in_progress", "done"]
SAMPLE_TYPE_OPTIONS = ["blood", "tissue", "organoid", "dna", "rna", "protein", "custom"]
COLONY_VIEWS = [
    ("mice", "M"),
    ("cages", "C"),
    ("litters", "L"),
    ("breeders", "B"),
    ("experiments", "E"),
    ("strains", "T"),
    ("settings", "S"),
]
MOUSE_PRESET_FIELDS = [
    "gender",
    "genotype",
    "owner",
    "purpose",
    "status",
]
MOUSE_STATUS_OPTIONS = ["breeder", "experiment", "geno", "transfer", "sac"]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_name: Mapped[str] = mapped_column(String(120))
    vendor_name: Mapped[str] = mapped_column(String(120))
    item_name: Mapped[str] = mapped_column(String(200))
    catalog_number: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[str] = mapped_column(String(80), default="1")
    status: Mapped[str] = mapped_column(String(40), default="requested")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")


class AnimalRecord(Base):
    __tablename__ = "animals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    animal_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    species: Mapped[str] = mapped_column(String(40))
    strain_or_line: Mapped[str] = mapped_column(String(120), default="")
    sex: Mapped[str] = mapped_column(String(20), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    location: Mapped[str] = mapped_column(String(120), default="")
    protocol: Mapped[str] = mapped_column(String(120), default="")
    genotype: Mapped[str] = mapped_column(String(200), default="")
    custom_attributes: Mapped[str] = mapped_column(Text, default="")
    species_details: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    samples: Mapped[list["SampleRecord"]] = relationship(back_populates="animal", cascade="all, delete-orphan")
    events: Mapped[list["CalendarEvent"]] = relationship(back_populates="animal")
    notebook_entries: Mapped[list["NotebookEntry"]] = relationship(back_populates="animal")


class SampleRecord(Base):
    """A harvested sample and where it came from.

    The source used to be a required link to `animals`, a generic table the
    app never fills, which made samples impossible to create. It is now a
    kind (mouse, fish, organism:<module key>, other) plus the identifier
    used in that colony, so a sample can come from any database.
    """

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    animal_id_fk: Mapped[int | None] = mapped_column(ForeignKey("animals.id"), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(60), default="", index=True)
    source_ref: Mapped[str] = mapped_column(String(120), default="")
    sample_type: Mapped[str] = mapped_column(String(80))
    collection_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    storage_location: Mapped[str] = mapped_column(String(120), default="")
    amount: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    animal: Mapped["AnimalRecord | None"] = relationship(back_populates="samples")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[date] = mapped_column(Date)
    # Optional timed start/end. When null, falls back to event_date (all-day).
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_all_day: Mapped[bool] = mapped_column(Boolean, default=True)
    color: Mapped[str] = mapped_column(String(20), default="")  # hex, e.g. #7c3aed
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    animal_id_fk: Mapped[int | None] = mapped_column(ForeignKey("animals.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), default="experiment")
    description: Mapped[str] = mapped_column(Text, default="")
    # Shared: everyone sees it (with a group, its members); only its owner and
    # admins change it. Not shared: its owner's alone (app/app.py).
    is_shared: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true(), index=True)
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    animal: Mapped[AnimalRecord | None] = relationship(back_populates="events")
    notebook_entries: Mapped[list["NotebookEntry"]] = relationship(back_populates="event")


class TaskItem(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Optional timed due window. When null, falls back to due_date.
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="todo")
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(40), default="medium")
    color: Mapped[str] = mapped_column(String(20), default="")
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    # A lab to-do (everyone's to see and tick off), or with a group that
    # group's; otherwise its owner's own.
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), index=True)
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CalendarSubscription(Base):
    """External ICS / iCal subscription that gets fetched + parsed server-side
    and merged into the unified calendar feed."""
    __tablename__ = "calendar_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="#10b981")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Cache so we don't re-fetch the same URL on every page load.
    cached_payload: Mapped[str] = mapped_column(Text, default="")
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EncryptedText(TypeDecorator):
    """Text stored encrypted (app/security.py), read back as plain text.
    Values written before encryption existed read back unchanged and are
    encrypted the next time they are saved."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from .security import encrypt_text
        return encrypt_text(value)

    def process_result_value(self, value, dialect):
        from .security import decrypt_text
        return decrypt_text(value)


class GoogleCalendarLink(Base):
    """Per-user OAuth credentials for Google Calendar. We persist the refresh
    token; access tokens are short-lived and re-derived as needed."""
    __tablename__ = "google_calendar_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    google_email: Mapped[str] = mapped_column(String(200), default="")
    refresh_token: Mapped[str] = mapped_column(EncryptedText)
    access_token: Mapped[str] = mapped_column(EncryptedText, default="")
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    calendar_id: Mapped[str] = mapped_column(String(200), default="primary")
    color: Mapped[str] = mapped_column(String(20), default="#ef4444")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookEntry(Base):
    __tablename__ = "notebook_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    entry_date: Mapped[date] = mapped_column(Date, default=date.today)
    animal_id_fk: Mapped[int | None] = mapped_column(ForeignKey("animals.id"), nullable=True)
    event_id_fk: Mapped[int | None] = mapped_column(ForeignKey("calendar_events.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    attachment_path: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    animal: Mapped[AnimalRecord | None] = relationship(back_populates="notebook_entries")
    event: Mapped[CalendarEvent | None] = relationship(back_populates="notebook_entries")


class ChemicalReference(Base):
    __tablename__ = "chemical_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    molecular_weight: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")


class LitterRecord(Base):
    __tablename__ = "litters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    litter_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    cohort_name: Mapped[str] = mapped_column(String(120), default="")
    father_info: Mapped[str] = mapped_column(String(120), default="")
    mother_info: Mapped[str] = mapped_column(String(120), default="")
    total_pups: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Set when its cage is weaned: from then on it is not "due to wean"
    # anywhere (Home, the calendar, the cage sheet; services.weaning_due).
    weaned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mice: Mapped[list["MouseRecord"]] = relationship(back_populates="litter")


class MouseRack(Base):
    """A cage rack. A cage sits in it at (rack_row, rack_col); the name of
    that position ("D7", "4-7", "37"…) follows the rack's naming scheme."""

    __tablename__ = "mouse_racks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    rows: Mapped[int] = mapped_column(Integer, default=8)
    cols: Mapped[int] = mapped_column(Integer, default=10)
    room: Mapped[str] = mapped_column(String(120), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # How positions are named (app/positions.py): letters/numbers, order…
    naming: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Who added the rack: they (or an admin) may resize, rename or delete
    # it. Racks from before this column have no creator and are admin-only.
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CageRecord(Base):
    __tablename__ = "mouse_cages"
    # One cage per place in a rack, even when two people drop cages on the
    # same place at once (migrations/versions/0006_one_cage_per_place.py).
    __table_args__ = (Index("uq_mouse_cages_place", "rack_id_fk", "rack_row", "rack_col", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cage_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    cage_location: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(String(80), default="")
    # Who manages this cage. Empty means unowned, which stays editable by
    # everyone so existing cages are not locked away — see app/access.py.
    owner: Mapped[str] = mapped_column(String(120), default="", index=True)
    # Whether the cage is shared with the lab (or, with share_group_id, a
    # project group). A breeder cage starts so (_breeder_cages_start_shared
    # below), any other cage personal; its owner or an admin decides.
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Shared with one project group (app/groups.py) rather than the lab;
    # empty: the lab. No foreign key: deleting a group clears it here.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active_override: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    date_give_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    card_id: Mapped[str] = mapped_column(String(120), default="")
    genotype_summary: Mapped[str] = mapped_column(String(200), default="")
    location_detail: Mapped[str] = mapped_column(String(120), default="")
    room: Mapped[str] = mapped_column(String(120), default="")
    # Rack placement, 1-based. cage_location stays a free-text note.
    rack_id_fk: Mapped[int | None] = mapped_column(ForeignKey("mouse_racks.id"), nullable=True, index=True)
    rack_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rack_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mice: Mapped[list["MouseRecord"]] = relationship(back_populates="cage")
    rack: Mapped["MouseRack | None"] = relationship()


@event.listens_for(CageRecord.purpose, "set")
def _breeder_cages_start_shared(cage, value, oldvalue, _initiator):
    """A cage that becomes a breeder cage starts out shared with the lab, and
    one that stops being one starts personal again; its owner or an admin
    can then share it or not."""
    def breeder(v):
        return isinstance(v, str) and v.strip().lower() == "breeder"
    if breeder(value) and not breeder(oldvalue):
        cage.is_shared = True
    elif breeder(oldvalue) and not breeder(value):
        cage.is_shared, cage.share_group_id = False, None


class MouseRecord(Base):
    __tablename__ = "mice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mouse_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # What is written on the animal: an ear tag's number, a notch (RF, LB),
    # a tattoo. Free text, because every lab marks them differently, and not
    # unique — mouse_id is the identity everything else links by (0024).
    ear_tag: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # Columns the lab added to this built-in database (app/custom_fields.py):
    # the same JSON an inventory item has carried from the start (0025).
    attrs: Mapped[str] = mapped_column(Text, default="", server_default="")
    date_of_death: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str] = mapped_column(String(20), default="")
    transgene_1: Mapped[str] = mapped_column(String(200), default="")
    transgene_2: Mapped[str] = mapped_column(String(200), default="")
    transgene_3: Mapped[str] = mapped_column(String(200), default="")
    transgene_4: Mapped[str] = mapped_column(String(200), default="")
    genotype: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    cage_id_fk: Mapped[int | None] = mapped_column(ForeignKey("mouse_cages.id"), nullable=True)
    litter_id_fk: Mapped[int | None] = mapped_column(ForeignKey("litters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    cage: Mapped[CageRecord | None] = relationship(back_populates="mice")
    litter: Mapped[LitterRecord | None] = relationship(back_populates="mice")


class DropdownOption(Base):
    __tablename__ = "dropdown_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_name: Mapped[str] = mapped_column(String(80), index=True)
    option_value: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserAccount(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    short_name: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    role_title: Mapped[str] = mapped_column(String(80), default="")
    default_landing: Mapped[str] = mapped_column(String(40), default="")
    role: Mapped[str] = mapped_column(String(40), default="member")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_transfer: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_picked: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_breeder_aging: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_genotyping: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_lab: Mapped[bool] = mapped_column(Boolean, default=True)
    # Notebook pages shared with them, comments and @mentions, meeting notes and action items.
    notify_notebook: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_experiments: Mapped[bool] = mapped_column(Boolean, default=True)
    # When they finished (or skipped) the welcome tour; None shows it once.
    welcomed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # A temporary account (a guest pass, app/guests.py) stops working then.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The newest version whose What's new note they have seen (app/whats_new.py).
    whats_new_seen: Mapped[str] = mapped_column(String(40), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GuestPass(Base):
    """A code that signs someone outside the lab in as a temporary member
    (app/guests.py). Only a hash of the code is kept; the admin sees the
    code once, when it is made."""
    __tablename__ = "guest_passes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(80))            # who it is for
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # ended early by an admin
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LabCopyKey(Base):
    """A key a computer uses to keep a copy of the lab's database
    (app/lab_copy.py). Only its hash is kept; the person sees it once."""
    __tablename__ = "lab_copy_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))                  # which computer, e.g. "Lab iMac"
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_bytes: Mapped[int] = mapped_column(Integer, default=0)       # size of the last copy it took
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # What the computer last said about itself (app/devices.py): how it uses
    # the lab ("copy", "window", "master"), its version, and where it shares
    # the lab when it is the master; when it was last heard from.
    device_role: Mapped[str] = mapped_column(String(20), default="", server_default="")
    device_version: Mapped[str] = mapped_column(String(40), default="", server_default="")
    device_url: Mapped[str] = mapped_column(String(300), default="", server_default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserIdentity(Base):
    """A Google or Microsoft account that signs in as a BioManager user
    (app/oidc.py). Matched on the provider's issuer and subject, the one
    identifier that never changes and is never reassigned; never on email,
    which would let whoever controls an address take over the account."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StrainRecord(Base):
    __tablename__ = "strains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strain_number: Mapped[str] = mapped_column(String(80), default="")
    strain_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    strain_background: Mapped[str] = mapped_column(String(200), default="")
    supplier: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Who added the strain: they (or an admin) may rename or remove it.
    # Strains from before this column have no creator and are admin-only.
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlasmidBox(Base):
    """A freezer box of plasmid tubes: rows × columns, its positions named
    by a scheme (app/positions.py), kept in a freezer or shelf (`location`,
    which groups boxes in the grid's picker). Whoever made it, or an admin,
    may resize, rename or delete it (access.can_edit_rack); boxes made by
    the one-off migration from the old free-text box names have no creator
    and are admin-only."""
    __tablename__ = "plasmid_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    rows: Mapped[int] = mapped_column(Integer, default=9)
    cols: Mapped[int] = mapped_column(Integer, default=9)
    naming: Mapped[str] = mapped_column(Text, default="{}")
    location: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlasmidRecord(Base):
    __tablename__ = "plasmids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plasmid_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # Columns the lab added to this built-in database (app/custom_fields.py),
    # as an inventory item has always carried its own (0025).
    attrs: Mapped[str] = mapped_column(Text, default="", server_default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    backbone: Mapped[str] = mapped_column(String(200), default="")
    insert_seq: Mapped[str] = mapped_column(String(200), default="")
    resistance: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    # The tube's DNA after a miniprep: ng/µL and A260/280, as typed numbers.
    # A server default like their revisions' (0008), so an insert that
    # doesn't name them still works.
    concentration: Mapped[str] = mapped_column(String(40), default="", server_default="")
    a260_280: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # Lab common: anyone may edit it; deleting it or changing its owner is
    # still the owner's (or an admin's), as for lab common stock.
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), index=True)
    # Shared with one project group (app/groups.py) rather than the lab;
    # empty: the lab. No foreign key: deleting a group clears it here.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # ---- Sequence design (the "working" side of the plasmid record) ------
    # full_sequence: raw nucleotide string (uppercase ACGT/N), no newlines
    # is_circular: True for plasmids (rings), False for linear sequences
    # features_json: JSON list of {name, start, end, type, direction, color, notes}
    #   (start/end are 0-indexed inclusive; direction is 1, -1, or 0)
    # sequence_format: original upload format ("genbank", "fasta", "raw")
    full_sequence: Mapped[str] = mapped_column(Text, default="")
    is_circular: Mapped[bool] = mapped_column(Boolean, default=True)
    features_json: Mapped[str] = mapped_column(Text, default="")
    sequence_format: Mapped[str] = mapped_column(String(20), default="")
    sequence_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Storage-box positioning (used by the grid view).
    # `storage_box` is a free-form box name (e.g. "-80 box A").
    # `box_row` / `box_col` are 0-indexed grid positions inside that box.
    storage_box: Mapped[str] = mapped_column(String(80), default="", index=True)
    box_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    box_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The box it is in (plasmid_boxes). storage_box keeps the box's name as a
    # readable mirror, so older code (and a rollback) still sees the box.
    box_id_fk: Mapped[int | None] = mapped_column(ForeignKey("plasmid_boxes.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")


class PlasmidSequenceVersion(Base):
    """One state a plasmid's sequence has had (app/plasmid_versions.py):
    what it was, who made it and how, so an earlier one can be restored.
    Tied to the plasmid by its row id with no foreign key, so a deleted
    plasmid that Batch history brings back gets its versions back too."""
    __tablename__ = "plasmid_sequence_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plasmid_row_id: Mapped[int] = mapped_column(Integer, index=True)
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    saved_by: Mapped[str] = mapped_column(String(80), default="")
    # How it came to be: upload, editor, hand, clear, restore, baseline.
    how: Mapped[str] = mapped_column(String(20), default="")
    detail: Mapped[str] = mapped_column(String(200), default="")
    full_sequence: Mapped[str] = mapped_column(Text, default="")
    is_circular: Mapped[bool] = mapped_column(Boolean, default=True)
    features_json: Mapped[str] = mapped_column(Text, default="")
    sequence_format: Mapped[str] = mapped_column(String(20), default="")


class PlasmidParent(Base):
    """What a plasmid was made from (app/plasmid_lineage.py): a parent in the
    lab (parent_row_id), or one from outside it (parent_label only, e.g.
    "Addgene #11150"), in a role, by a method, with the details an assembly
    records (enzymes, coordinates, primers). Row ids without foreign keys,
    as for sequence versions, so undoing a delete brings links back."""
    __tablename__ = "plasmid_parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    child_row_id: Mapped[int] = mapped_column(Integer, index=True)
    parent_row_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_label: Mapped[str] = mapped_column(String(200), default="")
    # backbone, insert, template, donor, other
    role: Mapped[str] = mapped_column(String(20), default="other")
    # digest_ligate, gibson, golden_gate, pcr, mutagenesis, gateway, synthesis, other, or ""
    method: Mapped[str] = mapped_column(String(30), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(80), default="")


class PlasmidFile(Base):
    """A file kept with a plasmid: a sequencing trace (.ab1), a gel photo, a
    map from the vendor. The file itself is in the uploads folder
    (app/services.save_uploaded_file); this row is what the page lists. The
    plasmid's row id with no foreign key, as for versions, so undoing a
    plasmid's delete brings its files back with it."""
    __tablename__ = "plasmid_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plasmid_row_id: Mapped[int] = mapped_column(Integer, index=True)
    # trace (a sequencing read), image (a gel or a colony plate), document
    kind: Mapped[str] = mapped_column(String(20), default="document")
    name: Mapped[str] = mapped_column(String(200), default="")
    path: Mapped[str] = mapped_column(String(300), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(String(300), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    uploaded_by: Mapped[str] = mapped_column(String(80), default="")


class FeatureLibraryEntry(Base):
    """A named element the lab reuses, by its sequence (app/feature_library.py):
    Detect features on any plasmid's map finds it there. Collected from the
    lab's annotated plasmids or added from one; `seq_hash` keeps one entry
    per sequence."""
    __tablename__ = "feature_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    type: Mapped[str] = mapped_column(String(40), default="misc_feature")
    # viral, promoter, coding, selection, origin, terminator, other
    category: Mapped[str] = mapped_column(String(20), default="other", index=True)
    sequence: Mapped[str] = mapped_column(Text, default="")   # uppercase, as the feature reads 5′→3′
    seq_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    color: Mapped[str] = mapped_column(String(20), default="")
    notes_json: Mapped[str] = mapped_column(Text, default="{}")
    # The plasmid it was taken from; no foreign key, as for versions.
    source_row_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Empty when the lab's own maps gave it; else where it came from, e.g.
    # the common-features pack (app/feature_pack.py).
    source_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(80), default="")


class NotebookTab(Base):
    __tablename__ = "notebook_tabs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160), default="Untitled topic")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pages: Mapped[list["NotebookPage"]] = relationship(
        back_populates="tab",
        cascade="all, delete-orphan",
        order_by="NotebookPage.position",
    )


class NotebookPage(Base):
    __tablename__ = "notebook_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tab_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_tabs.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled page")
    body: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # User-editable date stamp shown below the title. Defaults to "today"
    # at creation time; the user can change it via the inline date picker
    # in the notebook UI.
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=date.today)
    # JSON-encoded list of structured property fields:
    #   [{"name": "Experiment type", "type": "select", "value": "ChIP-seq",
    #     "options": ["ChIP-seq", "RNA-seq", ...]},
    #    {"name": "Cell line", "type": "text", "value": "HEK293"}, ...]
    # Stored as text so it round-trips through SQLite/Postgres unchanged.
    properties: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tab: Mapped[NotebookTab] = relationship(back_populates="pages")


class AuditEntry(Base):
    """Captures destructive or notable changes to tracked records.

    Today only deletes are logged (the most painful "who removed X?"
    question); updates use the updated_by/updated_at columns on each
    record instead of a row per change.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(40), index=True)
    record_id: Mapped[int] = mapped_column(Integer)
    record_label: Mapped[str] = mapped_column(String(200), default="")
    action: Mapped[str] = mapped_column(String(20), default="delete")
    changed_by: Mapped[str] = mapped_column(String(80), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    details: Mapped[str] = mapped_column(Text, default="")
    # The batch this change belonged to, if any.
    batch_id_fk: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    # Structured form of `details`, which is prose: {"changes": {field:
    # [before, after]}} for an update, {"snapshot": {...}} for a delete.
    # This is what undo reads — parsing the prose would be guesswork.
    changes_json: Mapped[str] = mapped_column(Text, default="")


class NotebookTemplate(Base):
    __tablename__ = "notebook_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160), default="Untitled template")
    body: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="")
    # The page type a page made from it gets (note, experiment, protocol…);
    # empty: a note. Lab: everyone in the lab can start pages from it.
    kind: Mapped[str] = mapped_column(String(20), default="", server_default="")
    lab: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    # With lab on: the template is this project group's rather than the lab's.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotificationRecord(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_username: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    # transfer | picked | genotyping | orders | lab | account | general (app/notify.py)
    category: Mapped[str] = mapped_column(String(40), default="general")
    link: Mapped[str] = mapped_column(String(300), default="")    # a path in this app
    actor: Mapped[str] = mapped_column(String(80), default="")    # who caused it
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Experiment / cohort tracking. An Experiment groups a set of mice together
# under a shared treatment plan and timeline. The ExperimentMouse join table
# adds per-mouse treatment-group labels (e.g. "vehicle", "drug 10 mg/kg").
# Body weight readings live in MouseWeight, keyed only by mouse so the same
# longitudinal series is visible whether or not the mouse is in an
# experiment.
# ---------------------------------------------------------------------------


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    treatment_plan: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="active")
    owner_username: Mapped[str] = mapped_column(String(80), index=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Which database's animals it is on (app/experiments.py): "colony" (mice,
    # in ExperimentMouse), "zebrafish", "stocks:<key>" or "organisms:<key>"
    # (in ExperimentSubject); and what is measured over its days, as JSON
    # {"key", "label", "unit", "kind": "value" | "fraction"}. Blank: the
    # database's usual one (body weight for mice, survival for flies…).
    db: Mapped[str] = mapped_column(String(120), default="colony", index=True)
    readout: Mapped[str] = mapped_column(Text, default="")

    memberships: Mapped[list["ExperimentMouse"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    steps: Mapped[list["ExperimentStep"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan",
        order_by="(ExperimentStep.position, ExperimentStep.id)",
    )


class ExperimentSubject(Base):
    """An animal, or a group of them, in an experiment that isn't the mouse
    colony's: a zebrafish row, a fly vial or worm plate, an organism."""
    __tablename__ = "experiment_subjects"
    __table_args__ = (UniqueConstraint("experiment_id_fk", "subject_kind", "subject_id", name="uq_experiment_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id_fk: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20))            # fish | unit | organism
    subject_id: Mapped[int] = mapped_column(Integer)
    treatment_group: Mapped[str] = mapped_column(String(80), default="")
    start_count: Mapped[int | None] = mapped_column(Integer, nullable=True)   # animals at the start, for a survival
    note: Mapped[str] = mapped_column(String(200), default="")
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped[Experiment] = relationship()


class ExperimentReading(Base):
    """One day's readout for one animal or group of an experiment: a
    length, a score, how many are alive. (A mouse's body weight is a
    MouseWeight instead, so it is on the mouse too.)"""
    __tablename__ = "experiment_readings"
    __table_args__ = (UniqueConstraint("experiment_id_fk", "subject", "readout_key", "read_on",
                                       name="uq_experiment_reading"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id_fk: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    subject: Mapped[str] = mapped_column(String(40), index=True)        # "fish:12", "unit:3", "mouse:9"
    readout_key: Mapped[str] = mapped_column(String(40), default="")
    read_on: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(200), default="")
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExperimentStep(Base):
    """One manipulation in an experiment's plan: what is done to its mice,
    on which days (day 1 is the start date), and to which group. What
    was actually done, and when, is in ExperimentStepRecord
    (app/experiment_steps.py)."""
    __tablename__ = "experiment_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id_fk: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    days: Mapped[str] = mapped_column(String(120), default="1")          # "1", "2-5", "1, 8, 15"
    kind: Mapped[str] = mapped_column(String(30), default="injection")
    agent: Mapped[str] = mapped_column(String(200), default="")          # Tamoxifen, HDM
    dose: Mapped[str] = mapped_column(String(80), default="")            # 20 mg/kg, 25 µg
    route: Mapped[str] = mapped_column(String(60), default="")           # i.p., intranasal
    concentration: Mapped[str] = mapped_column(String(60), default="")   # 10 mg/mL: gives the volume
    treatment_group: Mapped[str] = mapped_column(String(80), default="")  # blank: every mouse
    notes: Mapped[str] = mapped_column(Text, default="")
    # The inventory item used (a reagent, its lot): offered when it is recorded.
    reagent_item_id_fk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped[Experiment] = relationship(back_populates="steps")
    records: Mapped[list["ExperimentStepRecord"]] = relationship(
        back_populates="step", cascade="all, delete-orphan")

    @property
    def audit_label(self) -> str:
        return f"{self.agent or self.kind} (day {self.days})"


class ExperimentStepRecord(Base):
    """A manipulation done: which day of the plan, the date it was done,
    by whom, to which mice, and the amount each got (from its weight)."""
    __tablename__ = "experiment_step_records"
    __table_args__ = (UniqueConstraint("step_id_fk", "day", name="uq_experiment_step_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id_fk: Mapped[int] = mapped_column(ForeignKey("experiment_steps.id"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    done_on: Mapped[date] = mapped_column(Date, default=date.today)
    done_by: Mapped[str] = mapped_column(String(80), default="")
    # [{"mouse": row id, "mouse_id": 1042, "grams": 24.1, "amount": "0.48 mg", "volume": "48 µL"}]
    mice: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str] = mapped_column(Text, default="")
    # What was used, as it was then: {"id", "name", "lot", "expires", "inventory"}.
    reagent: Mapped[str] = mapped_column(Text, default="")
    # Sample records made with it, if the person chose to: [{"id", "number", "name", "inventory"}].
    samples: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    step: Mapped[ExperimentStep] = relationship(back_populates="records")

    @property
    def audit_label(self) -> str:
        return f"{self.step.agent or self.step.kind}, day {self.day}" if self.step else f"day {self.day}"


class ExperimentRegimen(Base):
    """A saved regimen: an experiment's manipulations and their days, kept
    to start the next cohort from. Starting from one plans the days; each
    is still recorded as it is done."""
    __tablename__ = "experiment_regimens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    family: Mapped[str] = mapped_column(String(20), default="mouse", index=True)   # mouse | fish | fly | worm | organism
    steps: Mapped[str] = mapped_column(Text, default="[]")    # [{"kind", "agent", "dose", "route", "concentration", "days", "group", "notes"}]
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExperimentMouse(Base):
    __tablename__ = "experiment_mice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id_fk: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    mouse_id_fk: Mapped[int] = mapped_column(ForeignKey("mice.id"), index=True)
    treatment_group: Mapped[str] = mapped_column(String(80), default="")
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")

    experiment: Mapped[Experiment] = relationship(back_populates="memberships")
    mouse: Mapped["MouseRecord"] = relationship()


class MouseWeight(Base):
    __tablename__ = "mouse_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mouse_id_fk: Mapped[int] = mapped_column(ForeignKey("mice.id"), index=True)
    weigh_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    grams: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(String(200), default="")
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mouse: Mapped["MouseRecord"] = relationship()


# ---------------------------------------------------------------------------
# ZEBRAFISH — parallel to the mouse colony, but:
#   * Fish are usually counted as groups, not individuals (FishRecord.count)
#   * Tanks live on Racks, which belong to a WaterSystem
#   * Crosses produce Clutches (embryo cohorts), not litters
#   * Age units shift (dpf → wpf → mpf) — derived in the route layer
# ---------------------------------------------------------------------------

TANK_PURPOSE_OPTIONS = ["stock", "breeding", "mating", "experiment", "quarantine"]
FISH_SEX_OPTIONS = ["M", "F", "mixed", "unknown"]
FISH_STATUS_OPTIONS = ["alive", "transfer", "geno", "sac"]


class WaterSystem(Base):
    """A recirculation system that feeds one or more racks. Water quality
    logs hang off this since the water is shared across all tanks on a
    system."""
    __tablename__ = "water_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    room: Mapped[str] = mapped_column(String(120), default="")
    target_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_conductivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Who added the system: they (or an admin) may delete it.
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    racks: Mapped[list["FishRack"]] = relationship(back_populates="system", cascade="all, delete-orphan")
    water_logs: Mapped[list["WaterLog"]] = relationship(back_populates="system", cascade="all, delete-orphan")


class FishRack(Base):
    """A physical rack on a WaterSystem. Tanks sit at (row, col) positions."""
    __tablename__ = "fish_racks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    system_id_fk: Mapped[int | None] = mapped_column(ForeignKey("water_systems.id"), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=8)
    cols: Mapped[int] = mapped_column(Integer, default=10)
    naming: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Who added it: they (or an admin) may rename, resize or delete it
    # (access.can_edit_rack). Racks from before this are admin-only.
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    system: Mapped[WaterSystem | None] = relationship(back_populates="racks")
    tanks: Mapped[list["TankRecord"]] = relationship(back_populates="rack")


class FishLine(Base):
    """A genetic line / strain. ZFIN-style name + optional transgene fields.
    parent_line_id_fk lets us draw a founder lineage tree."""
    __tablename__ = "fish_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    zfin_name: Mapped[str] = mapped_column(String(200), default="")
    background: Mapped[str] = mapped_column(String(120), default="")
    transgene_summary: Mapped[str] = mapped_column(String(400), default="")
    allele: Mapped[str] = mapped_column(String(200), default="")
    iacuc_protocol: Mapped[str] = mapped_column(String(120), default="")
    founder_info: Mapped[str] = mapped_column(Text, default="")
    parent_line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_lines.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Whoever made the line, unless handed on; blank on lines from before.
    owner: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parent_line: Mapped["FishLine | None"] = relationship(remote_side="FishLine.id", foreign_keys=[parent_line_id_fk])
    tanks: Mapped[list["TankRecord"]] = relationship(back_populates="line")


class TankRecord(Base):
    """A single tank. Lives on a Rack at (row, col). Has a purpose (stock /
    breeding / mating / experiment / quarantine) and a single primary
    FishLine. Holds zero-or-more FishRecord rows (group-count + opt-in
    individual)."""
    __tablename__ = "tanks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tank_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # Columns the lab added to this built-in database (app/custom_fields.py),
    # as an inventory item has always carried its own (0025).
    attrs: Mapped[str] = mapped_column(Text, default="", server_default="")
    rack_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_racks.id"), nullable=True)
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(String(40), default="stock", index=True)
    # A shared purpose's tank or vial is the lab's; with a group, that group's.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_lines.id"), nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    card_id: Mapped[str] = mapped_column(String(120), default="")
    # When this tank was set up as a mating tank, when it should return.
    mating_return_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    mating_father_tank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mating_mother_tank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_genotyping: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    rack: Mapped[FishRack | None] = relationship(back_populates="tanks")
    line: Mapped[FishLine | None] = relationship(back_populates="tanks")
    fish: Mapped[list["FishRecord"]] = relationship(back_populates="tank", cascade="all, delete-orphan")


class FishRecord(Base):
    """One row per (tank, sex, line) group. `count` is the population; for
    rare individuals (transgenic founders), `count=1` and individual_id
    becomes the unique label."""
    __tablename__ = "fish"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tank_id_fk: Mapped[int] = mapped_column(ForeignKey("tanks.id"), index=True)
    # Columns the lab added to this built-in database (app/custom_fields.py),
    # as an inventory item has always carried its own (0025).
    attrs: Mapped[str] = mapped_column(Text, default="", server_default="")
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_lines.id"), nullable=True)
    individual_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    sex: Mapped[str] = mapped_column(String(20), default="mixed")
    status: Mapped[str] = mapped_column(String(40), default="alive")
    date_of_fertilization: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Set when the status turns sac (today, editable); cleared on revival.
    sac_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    clutch_id_fk: Mapped[int | None] = mapped_column(ForeignKey("clutches.id"), nullable=True)
    genotype: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tank: Mapped[TankRecord] = relationship(back_populates="fish")
    line: Mapped[FishLine | None] = relationship()


class ClutchRecord(Base):
    """A clutch = embryos from one mating event. DOF (date of fertilization)
    drives all derived dates (tank-up, fin-clip, adult)."""
    __tablename__ = "clutches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clutch_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    date_of_fertilization: Mapped[date] = mapped_column(Date, index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_lines.id"), nullable=True)
    father_tank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mother_tank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embryo_count: Mapped[int] = mapped_column(Integer, default=0)
    larvae_count: Mapped[int] = mapped_column(Integer, default=0)
    adults_count: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line: Mapped[FishLine | None] = relationship()


class WaterLog(Base):
    """One row per (system, day) reading."""
    __tablename__ = "water_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Blank once its system is deleted: the readings are kept as history.
    system_id_fk: Mapped[int | None] = mapped_column(ForeignKey("water_systems.id"), nullable=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    conductivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    salinity: Mapped[float | None] = mapped_column(Float, nullable=True)
    alarm: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    system: Mapped[WaterSystem | None] = relationship(back_populates="water_logs")


class FishSacLog(Base):
    """One row per fish-culling event. Quantity-based to mirror FishRecord.count."""
    __tablename__ = "fish_sac_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tank_id_fk: Mapped[int | None] = mapped_column(ForeignKey("tanks.id"), nullable=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("fish_lines.id"), nullable=True)
    # The fish row whose status change wrote this entry; reviving it removes it.
    fish_id_fk: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(String(200), default="")
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ===========================================================================
# CONFIGURABLE ORGANISM MODULES
#
# The mouse and zebrafish modules above are hand-written. Everything below is
# the generic engine that lets a lab define a new organism database — flies,
# worms, anything — by describing it instead of writing tables.
#
# Every row carries `module_id_fk`, so modules are fully isolated from one
# another, and a per-module `attrs` JSON column holds whatever extra fields
# that module declared (see app/organisms.py for the vocabulary).
# ===========================================================================


class _JsonAttrs:
    """Decoded access to the `attrs` JSON column.

    Templates read `row.attrs_dict`; nothing in Jinja should be calling
    json.loads. Malformed JSON degrades to an empty dict rather than raising
    mid-render.
    """

    @property
    def attrs_dict(self) -> dict:
        import json
        try:
            value = json.loads(self.attrs or "{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}


class OrganismModule(Base):
    """One configurable species database.

    The nouns are stored rather than hard-coded because they are what the UI
    calls things: a Drosophila module says "vial" and "stock" where the mouse
    module says "cage" and "strain".
    """
    __tablename__ = "organism_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    label_plural: Mapped[str] = mapped_column(String(120), default="")
    icon: Mapped[str] = mapped_column(String(60), default="circle-dashed")
    blurb: Mapped[str] = mapped_column(Text, default="")

    # --- Vocabulary -------------------------------------------------------
    organism_noun: Mapped[str] = mapped_column(String(60), default="animal")
    organism_noun_plural: Mapped[str] = mapped_column(String(60), default="animals")
    housing_noun: Mapped[str] = mapped_column(String(60), default="enclosure")
    housing_noun_plural: Mapped[str] = mapped_column(String(60), default="enclosures")
    container_noun: Mapped[str] = mapped_column(String(60), default="rack")
    line_noun: Mapped[str] = mapped_column(String(60), default="line")
    line_noun_plural: Mapped[str] = mapped_column(String(60), default="lines")
    cohort_noun: Mapped[str] = mapped_column(String(60), default="cohort")
    cohort_noun_plural: Mapped[str] = mapped_column(String(60), default="cohorts")
    cross_noun: Mapped[str] = mapped_column(String(60), default="cross")

    # --- Behaviour --------------------------------------------------------
    # "individual" | "group" | "hybrid" — see organisms.IDENTITY_MODES.
    identity_mode: Mapped[str] = mapped_column(String(20), default="hybrid")
    age_unit: Mapped[str] = mapped_column(String(20), default="days")

    # JSON-encoded lists/dicts. Kept as Text so the same schema works on
    # SQLite and PostgreSQL without a dialect-specific JSON type.
    capabilities: Mapped[str] = mapped_column(Text, default="[]")
    schedule_rules: Mapped[str] = mapped_column(Text, default="[]")
    housing_purposes: Mapped[str] = mapped_column(Text, default="[]")
    statuses: Mapped[str] = mapped_column(Text, default="[]")
    sexes: Mapped[str] = mapped_column(Text, default="[]")
    settings: Mapped[str] = mapped_column(Text, default="{}")

    preset_key: Mapped[str] = mapped_column(String(60), default="")
    position: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # A personal database: only this user (and admins) see it. Empty: the lab's.
    private_to: Mapped[str] = mapped_column(String(80), default="")
    # A project group's database: only its members (and admins) see it.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fields: Mapped[list["ModuleField"]] = relationship(
        back_populates="module", cascade="all, delete-orphan",
        order_by="ModuleField.position",
    )


class ModuleField(Base):
    """A field a lab added to one of its module's entities.

    Values live in the entity's `attrs` JSON column; this row is what makes
    the form, the table column and the validation appear.
    """
    __tablename__ = "organism_module_fields"
    __table_args__ = (UniqueConstraint("module_id_fk", "entity", "key", name="uq_module_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    entity: Mapped[str] = mapped_column(String(20), default="organism")
    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    field_type: Mapped[str] = mapped_column(String(20), default="text")
    options: Mapped[str] = mapped_column(Text, default="[]")
    default_value: Mapped[str] = mapped_column(String(200), default="")
    help_text: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    show_in_table: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=100)

    module: Mapped[OrganismModule] = relationship(back_populates="fields")

    @property
    def options_list(self) -> list[str]:
        import json
        try:
            value = json.loads(self.options or "[]")
            return [str(v) for v in value] if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []


class OrgLocation(Base):
    """A node in the location tree: facility, room, system, rack, incubator.

    `parent_id_fk` makes it a tree so one module can say
    system -> rack -> shelf while another says just incubator.
    `rows`/`cols` turn a node into a grid that housing units sit in.
    """
    __tablename__ = "organism_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    parent_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_locations.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="rack")
    name: Mapped[str] = mapped_column(String(120))
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Who added it: they, whoever may configure the database, or an admin
    # may change or delete it (Racks & boxes can hand it to someone else).
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parent: Mapped["OrgLocation | None"] = relationship(
        remote_side="OrgLocation.id", foreign_keys=[parent_id_fk])


class OrgLine(Base, _JsonAttrs):
    """A strain / line / stock: the named genetic entity, independent of the
    animals currently carrying it."""
    __tablename__ = "organism_lines"
    __table_args__ = (UniqueConstraint("module_id_fk", "code", name="uq_line_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    parent_line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    genotype: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    protocol: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(120), default="")
    external_ref: Mapped[str] = mapped_column(String(200), default="")
    # Anchors for the "turnover" and "re-freeze" schedule rules.
    last_refreshed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_frozen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parent_line: Mapped["OrgLine | None"] = relationship(
        remote_side="OrgLine.id", foreign_keys=[parent_line_id_fk])


class OrgHousing(Base, _JsonAttrs):
    """A cage / tank / vial / plate — whatever carries the physical label."""
    __tablename__ = "organism_housing"
    __table_args__ = (UniqueConstraint("module_id_fk", "code", name="uq_housing_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    location_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_locations.id"), nullable=True)
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(String(60), default="", index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    protocol: Mapped[str] = mapped_column(String(120), default="")
    card_id: Mapped[str] = mapped_column(String(120), default="")
    established_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Anchor for recurring maintenance (flip, chunk, water change).
    last_serviced_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    needs_attention: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    location: Mapped[OrgLocation | None] = relationship()
    line: Mapped[OrgLine | None] = relationship()
    residents: Mapped[list["Organism"]] = relationship(
        # No delete cascade: deleting a unit unassigns its residents (their
        # housing_id_fk goes to NULL) rather than deleting other people's
        # animals along with it.
        back_populates="housing")


class OrgCross(Base, _JsonAttrs):
    """A mating / cross event. Parents are recorded as free labels plus
    optional line references, because who counts as a parent differs by
    organism: a pair of mice, two tanks of fish, a bottle of virgins."""
    __tablename__ = "organism_crosses"
    __table_args__ = (UniqueConstraint("module_id_fk", "code", name="uq_cross_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    housing_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_housing.id"), nullable=True)
    # pair | trio | bulk | self | group
    cross_type: Mapped[str] = mapped_column(String(40), default="pair")
    sire_line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    dam_line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    sire_label: Mapped[str] = mapped_column(String(200), default="")
    dam_label: Mapped[str] = mapped_column(String(200), default="")
    set_up_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    collected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    housing: Mapped[OrgHousing | None] = relationship()
    sire_line: Mapped[OrgLine | None] = relationship(foreign_keys=[sire_line_id_fk])
    dam_line: Mapped[OrgLine | None] = relationship(foreign_keys=[dam_line_id_fk])


class OrgCohort(Base, _JsonAttrs):
    """A litter / clutch / progeny batch: animals born together, whose birth
    date drives every derived date downstream."""
    __tablename__ = "organism_cohorts"
    __table_args__ = (UniqueConstraint("module_id_fk", "code", name="uq_cohort_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    cross_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_crosses.id"), nullable=True)
    birth_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # Where the cohort is in a nursery-style pipeline.
    stage: Mapped[str] = mapped_column(String(40), default="", index=True)
    location_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_locations.id"), nullable=True)
    count_initial: Mapped[int] = mapped_column(Integer, default=0)
    count_current: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line: Mapped[OrgLine | None] = relationship()
    cross: Mapped[OrgCross | None] = relationship()
    members: Mapped[list["Organism"]] = relationship(back_populates="cohort")


class Organism(Base, _JsonAttrs):
    """The tracked living unit — one animal, or one group with a headcount.

    `count` is what makes both work from a single table: an individually
    tracked mouse is a row with count=1 and a code; a vial of flies is a row
    with count=40 and no individual identity. A group that later needs one of
    its animals named just gets a second row with count=1.
    """
    __tablename__ = "organisms"
    __table_args__ = (UniqueConstraint("module_id_fk", "code", name="uq_organism_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    # Blank for anonymous groups; unique within the module when set.
    code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    housing_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_housing.id"), nullable=True, index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True)
    cohort_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_cohorts.id"), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    sex: Mapped[str] = mapped_column(String(30), default="", index=True)
    status: Mapped[str] = mapped_column(String(40), default="", index=True)
    birth_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    death_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_procedure_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    genotype: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    protocol: Mapped[str] = mapped_column(String(120), default="")
    # Single-parent lineage: selfing worms and clonal lines have one parent,
    # so pedigree cannot assume two.
    parent_a_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organisms.id"), nullable=True)
    parent_b_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organisms.id"), nullable=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    housing: Mapped[OrgHousing | None] = relationship(back_populates="residents")
    line: Mapped[OrgLine | None] = relationship()
    cohort: Mapped[OrgCohort | None] = relationship(back_populates="members")
    parent_a: Mapped["Organism | None"] = relationship(
        remote_side="Organism.id", foreign_keys=[parent_a_id_fk])
    parent_b: Mapped["Organism | None"] = relationship(
        remote_side="Organism.id", foreign_keys=[parent_b_id_fk])


class OrgEvent(Base):
    """Append-only lifecycle log. Never updated, never deleted — this is what
    makes a retrospective census answerable and satisfies the requirement to
    retain records past protocol expiry.

    `subject_kind` + `subject_id` is a deliberate soft reference so one log
    covers animals, housing units, cohorts, crosses and lines.
    """
    __tablename__ = "organism_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")


class OrgDue(Base):
    """One outstanding item from a schedule rule. Materialised rather than
    computed on the fly so it can be listed, assigned, snoozed and ticked
    off, and so completing a recurring rule can roll the next one forward."""
    __tablename__ = "organism_due"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_key: Mapped[str] = mapped_column(String(40), index=True)
    due_on: Mapped[date] = mapped_column(Date, index=True)
    snoozed_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_to: Mapped[str] = mapped_column(String(80), default="", index=True)
    done_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    done_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrgMeasurement(Base):
    """A timestamped reading against any subject: body weight on an animal,
    pH on a water system, temperature on an incubator."""
    __tablename__ = "organism_measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    metric: Mapped[str] = mapped_column(String(60), index=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    recorded_by: Mapped[str] = mapped_column(String(80), default="")
    alarm: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class OrgGenotype(Base):
    """A genotyping call, with the assay that produced it."""
    __tablename__ = "organism_genotypes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20), default="organism", index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    assay: Mapped[str] = mapped_column(String(120), default="")
    result: Mapped[str] = mapped_column(String(200), default="")
    zygosity: Mapped[str] = mapped_column(String(40), default="")
    called_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    called_by: Mapped[str] = mapped_column(String(80), default="")
    image_path: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrgPreservationLot(Base):
    """A frozen lot of a line. `vials_remaining` decrements on thaw, and
    `recovery_ok` records whether a test thaw actually came back — which for
    worms is the difference between having a strain and thinking you do."""
    __tablename__ = "organism_preservation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("organism_modules.id"), index=True)
    line_id_fk: Mapped[int | None] = mapped_column(ForeignKey("organism_lines.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(120), default="")
    method: Mapped[str] = mapped_column(String(80), default="")
    frozen_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    frozen_by: Mapped[str] = mapped_column(String(80), default="")
    vial_count: Mapped[int] = mapped_column(Integer, default=0)
    vials_remaining: Mapped[int] = mapped_column(Integer, default=0)
    storage_text: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[str] = mapped_column(String(80), default="")
    recovery_tested_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    recovery_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line: Mapped[OrgLine | None] = relationship()


class BatchRecord(Base):
    """One batch operation, so it can be listed, explained and undone.

    Bulk actions previously left only a scatter of audit rows tagged with a
    text label. That reads well enough but cannot be reversed: turning
    "genotype: ∅ → C57BL/6" back into a value means parsing prose. A batch
    row gives the operation an identity, and the audit entries beneath it
    carry structured before/after values that undo can actually apply.
    """
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # create | update | delete | mixed — what undoing it would have to do.
    action: Mapped[str] = mapped_column(String(30), default="update", index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    target_table: Mapped[str] = mapped_column(String(40), default="", index=True)
    actor: Mapped[str] = mapped_column(String(80), default="", index=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    undone_by: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None


class DatabaseAlias(Base):
    """An address a database had before it was renamed (app/database_keys.py):
    /inventory/<old_key> and the rest still find it, so printed QR labels,
    bookmarks, @old_key mentions in notebook pages and scripts keep working.
    `kind` is inventory, stocks or organisms; `module_id` that kind's row."""

    __tablename__ = "database_aliases"
    __table_args__ = (UniqueConstraint("kind", "old_key", name="uq_database_aliases_kind_old_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    old_key: Mapped[str] = mapped_column(String(80))
    module_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Lab inventories: samples, orders, reagents, antibodies and custom lists.
#
# Like the organism engine, an inventory is a configuration row, not a new
# table: a lab can keep several ("Samples — tissue bank", "Samples — blood")
# and rename any of them. Preset-specific fields live in `attrs`, described
# by the module's settings (app/inventory.py).
# ---------------------------------------------------------------------------


class InventoryModule(Base):
    __tablename__ = "inventory_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), default="custom")   # the preset it came from
    icon: Mapped[str] = mapped_column(String(40), default="box")
    blurb: Mapped[str] = mapped_column(Text, default="")
    item_noun: Mapped[str] = mapped_column(String(60), default="item")
    item_noun_plural: Mapped[str] = mapped_column(String(60), default="items")
    settings: Mapped[str] = mapped_column(Text, default="{}")
    position: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # A personal database: only this user (and admins) see it. Empty: the lab's.
    private_to: Mapped[str] = mapped_column(String(80), default="")
    # A project group's database: only its members (and admins) see it.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventoryRack(Base):
    """A freezer box, shelf or drawer: rows × columns, named by its scheme."""

    __tablename__ = "inventory_racks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("inventory_modules.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), default="box")
    rows: Mapped[int] = mapped_column(Integer, default=9)
    cols: Mapped[int] = mapped_column(Integer, default=9)
    naming: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Where the box is kept ("−80 °C", "LN₂"): what goes in takes it as its
    # "Stored at" (inventory_service.follow_box). Empty: not said.
    stored_at: Mapped[str] = mapped_column(String(40), default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")   # may resize or delete it (and admins)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventoryItem(Base, _JsonAttrs):
    """One sample, order line, reagent, antibody… `is_shared` marks lab
    common stock, editable by everyone; otherwise the owner (or an admin)."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("inventory_modules.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=0, index=True)   # 1, 2, 3… within its inventory
    name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(80), default="", index=True)
    status: Mapped[str] = mapped_column(String(40), default="", index=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Shared with one project group (app/groups.py) rather than the lab;
    # empty: the lab. No foreign key: deleting a group clears it here.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    quantity: Mapped[str] = mapped_column(String(60), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    vendor: Mapped[str] = mapped_column(String(120), default="")
    catalog_number: Mapped[str] = mapped_column(String(120), default="")
    lot: Mapped[str] = mapped_column(String(120), default="")
    rack_id_fk: Mapped[int | None] = mapped_column(ForeignKey("inventory_racks.id"), nullable=True, index=True)
    rack_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rack_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_note: Mapped[str] = mapped_column(String(200), default="")
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    rack: Mapped["InventoryRack | None"] = relationship()


class AppSetting(Base):
    """Small lab-wide settings, e.g. the display name of a built-in database."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# Fly and worm stocks.
#
# Flies and worms are kept in vials and plates, and the vial or plate is
# the record: its genotype, what it is for (stock, cross, experiment…),
# and where it sits (incubator → rack → position). Genotypes are a plain
# list that grows as people type new ones; there are no line codes.
# Settings (nouns, purposes, temperatures and intervals) live on the
# module; presets are in app/stocks.py.
# ---------------------------------------------------------------------------


class StockModule(Base):
    __tablename__ = "stock_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="fly")  # fly | worm
    icon: Mapped[str] = mapped_column(String(60), default="fly")
    blurb: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[str] = mapped_column(Text, default="{}")
    position: Mapped[int] = mapped_column(Integer, default=200)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # A personal database: only this user (and admins) see it. Empty: the lab's.
    private_to: Mapped[str] = mapped_column(String(80), default="")
    # A project group's database: only its members (and admins) see it.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockIncubator(Base):
    """An incubator (or room): what the racks sit in, and whose temperature
    sets how fast everything inside develops."""
    __tablename__ = "stock_incubators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("stock_modules.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    temperature: Mapped[str] = mapped_column(String(10), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockRack(Base):
    """A rack of vials or a box of plates. Flipping is done a rack at a
    time, so the flip date lives here."""
    __tablename__ = "stock_racks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("stock_modules.id"), index=True)
    incubator_id_fk: Mapped[int | None] = mapped_column(ForeignKey("stock_incubators.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    rows: Mapped[int] = mapped_column(Integer, default=10)
    cols: Mapped[int] = mapped_column(Integer, default=10)
    naming: Mapped[str] = mapped_column(Text, default="{}")
    last_flipped_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Days between flips; empty = from the incubator's temperature.
    flip_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    incubator: Mapped[StockIncubator | None] = relationship()


class StockGenotype(Base):
    """A genotype the lab has written down. Grows by itself as vials are
    labelled; kept for suggestions, stock-centre numbers and notes."""
    __tablename__ = "stock_genotypes"
    __table_args__ = (UniqueConstraint("module_id_fk", "genotype", name="uq_stock_genotype"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("stock_modules.id"), index=True)
    genotype: Mapped[str] = mapped_column(String(400))
    alias: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(120), default="")
    stock_number: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockUnit(Base, _JsonAttrs):
    """One vial or plate."""
    __tablename__ = "stock_units"
    __table_args__ = (UniqueConstraint("module_id_fk", "number", name="uq_stock_unit_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("stock_modules.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, index=True)
    genotype: Mapped[str] = mapped_column(String(400), default="", index=True)
    purpose: Mapped[str] = mapped_column(String(40), default="stock", index=True)
    # A shared purpose's tank or vial is the lab's; with a group, that group's.
    share_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Crosses: the two parents (♀ virgins × ♂; hermaphrodites × males).
    female_genotype: Mapped[str] = mapped_column(String(400), default="")
    male_genotype: Mapped[str] = mapped_column(String(400), default="")
    rack_id_fk: Mapped[int | None] = mapped_column(ForeignKey("stock_racks.id"), nullable=True, index=True)
    rack_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rack_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    set_up_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    # Egg collection: the cross a progeny vial came from, when the cross
    # was last collected, and when the progeny are due to emerge.
    parent_id_fk: Mapped[int | None] = mapped_column(ForeignKey("stock_units.id"), nullable=True)
    last_collected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ready_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Temperature-shift experiments.
    shift_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    shift_to: Mapped[str] = mapped_column(String(10), default="")
    shifted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    score_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    discarded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")

    rack: Mapped[StockRack | None] = relationship()
    parent: Mapped["StockUnit | None"] = relationship(remote_side="StockUnit.id")

    @property
    def audit_label(self) -> str:
        """How the audit log and batches name a vial: its number and genotype."""
        return f"No. {self.number}" + (f" · {self.genotype[:60]}" if self.genotype else "")


class StockFrozen(Base):
    """A frozen lot (worms: -80 °C or liquid nitrogen), with a thaw check,
    because a strain you cannot recover is a strain you do not have."""
    __tablename__ = "stock_frozen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id_fk: Mapped[int] = mapped_column(ForeignKey("stock_modules.id"), index=True)
    genotype: Mapped[str] = mapped_column(String(400), default="")
    frozen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    vials: Mapped[int] = mapped_column(Integer, default=0)
    vials_left: Mapped[int] = mapped_column(Integer, default=0)
    location: Mapped[str] = mapped_column(String(200), default="")
    thaw_tested_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    thaw_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Calendar: repeats, protocol timelines, equipment booking, away days and
# the per-person feed a phone subscribes to (app/lab_calendar.py). Each is
# its own table, so existing calendar rows need no new columns.
# ---------------------------------------------------------------------------


class CalendarRepeat(Base):
    """How one calendar event repeats: every `interval` days, weeks or
    months from its date, until `until` (or for good), minus skipped dates."""
    __tablename__ = "calendar_repeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id_fk: Mapped[int] = mapped_column(ForeignKey("calendar_events.id", ondelete="CASCADE"),
                                             unique=True, index=True)
    freq: Mapped[str] = mapped_column(String(10), default="weekly")  # daily | weekly | monthly
    interval: Mapped[int] = mapped_column(Integer, default=1)
    until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ISO dates taken out of the series, comma separated.
    skip: Mapped[str] = mapped_column(Text, default="")


class ProtocolTemplate(Base):
    """A reusable timeline: steps counted in days from a start (day 0).
    `steps` is a JSON list of {"from": 0, "to": 4, "title": "Tamoxifen"}."""
    __tablename__ = "protocol_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[str] = mapped_column(Text, default="[]")
    color: Mapped[str] = mapped_column(String(20), default="")
    owner: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProtocolRun(Base):
    """A template applied from one start date, to an experiment or a named
    group. The steps are copied when it starts, so editing the template
    later leaves runs already under way as they were; moving `start_date`
    moves every step."""
    __tablename__ = "protocol_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id_fk: Mapped[int | None] = mapped_column(ForeignKey("protocol_templates.id", ondelete="SET NULL"),
                                                       nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    label: Mapped[str] = mapped_column(String(160), default="")
    start_date: Mapped[date] = mapped_column(Date)
    steps: Mapped[str] = mapped_column(Text, default="[]")
    experiment_id_fk: Mapped[int | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"),
                                                         nullable=True, index=True)
    color: Mapped[str] = mapped_column(String(20), default="")
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Equipment(Base):
    """A shared instrument people book time on: a confocal, a rig."""
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    location: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(20), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EquipmentBooking(Base):
    """A slot on an instrument. Two bookings of one instrument never overlap."""
    __tablename__ = "equipment_bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id_fk: Mapped[int] = mapped_column(ForeignKey("equipment.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    purpose: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    equipment: Mapped[Equipment] = relationship()


class Absence(Base):
    """Someone away (leave, a conference), and who covers their work."""
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20), default="leave")  # leave | conference | other
    note: Mapped[str] = mapped_column(String(200), default="")
    cover: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CalendarFeed(Base):
    """A person's private calendar link for Apple, Google or Outlook
    Calendar. Looked up by a hash of the token; the token itself is kept
    encrypted so the link can be shown again."""
    __tablename__ = "calendar_feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token: Mapped[str] = mapped_column(EncryptedText, default="")
    scope: Mapped[str] = mapped_column(String(10), default="mine")  # mine | lab
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)



# ---------------------------------------------------------------------------
# The lab notebook's own features (app/lab_notebook.py), beside the tabs,
# pages and templates above. Each lives in its own table, so an existing
# database needs no column changes. A page's rows here go when the page does
# (lab_notebook.delete_page_rows).
# ---------------------------------------------------------------------------


class ApiToken(Base):
    """A personal access token for the API (app/api.py): a script, an
    instrument or another tool acting as this person, with their
    permissions. Only its hash is kept; the person sees it once."""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))                   # what uses it, e.g. "Balance in B12"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hint: Mapped[str] = mapped_column(String(16), default="")         # its first characters, to tell tokens apart
    scope: Mapped[str] = mapped_column(String(10), default="read")    # read | write | propose
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Proposal(Base):
    """Changes an assistant proposed (app/proposals.py), waiting for its
    person to approve them, all at once, on Proposed changes. Approved, they
    go in as one batch (batch_id_fk)."""
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(80), index=True)
    token_id_fk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="")        # "Claude", or the token's name
    summary: Mapped[str] = mapped_column(Text, default="")            # the assistant's own words
    # pending | approved | discarded | superseded | expired | invalid
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replaces_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batch_id_fk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The change history's newest row when it was checked: a record changed
    # after it is changed since the preview.
    audit_mark: Mapped[int] = mapped_column(Integer, default=0)
    lang: Mapped[str] = mapped_column(String(10), default="")


class ProposalChange(Base):
    """One change of a proposal, as sent and as its preview found it."""
    __tablename__ = "proposal_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id_fk: Mapped[int] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(60), default="")
    area: Mapped[str] = mapped_column(String(40), default="")
    request_json: Mapped[str] = mapped_column(Text, default="")       # {action, target, fields}, as sent
    summary: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    records_json: Mapped[str] = mapped_column(Text, default="[]")     # what the preview changed


class NotebookPendingInsert(Base):
    """A line to add to a notebook page that someone has open in the live
    editor (app/lab_notebook.add_note): the server can't edit its Yjs
    document, so the next editor that polls the page claims it, adds it
    through the editor and reports it done."""
    __tablename__ = "notebook_pending_inserts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    time: Mapped[str] = mapped_column(String(5), default="")          # "10:42", as the log line shows it
    via: Mapped[str] = mapped_column(String(80), default="")          # "Claude"
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    claimed_by: Mapped[str] = mapped_column(String(40), default="")   # the editor (sync client id) adding it
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthClient(Base):
    """An assistant app that registered itself to connect (app/oauth.py,
    RFC 7591): claude.ai, ChatGPT, Claude Code…"""
    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(64), default="")    # empty: a public client
    name: Mapped[str] = mapped_column(String(120), default="")
    redirect_uris: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthCode(Base):
    """A one-time authorization code, given after the person said yes."""
    __tablename__ = "oauth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(64), default="")
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    redirect_uri: Mapped[str] = mapped_column(Text, default="")
    code_challenge: Mapped[str] = mapped_column(String(128), default="")
    scope: Mapped[str] = mapped_column(String(80), default="propose")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthGrant(Base):
    """One connection: its access token is an api_tokens row (Read and
    propose), renewed with the refresh token, which changes every time."""
    __tablename__ = "oauth_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    api_token_id_fk: Mapped[int] = mapped_column(ForeignKey("api_tokens.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OAuthLinkCode(Base):
    """A connection code a person makes in Settings, to say yes to an
    assistant from the internet, where the lab shows no sign-in form."""
    __tablename__ = "oauth_link_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id_fk: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Feedback(Base):
    """Something someone in the lab reported from Send feedback
    (app/feedback.py): a problem, an idea or a question. It stays in the
    lab's own database for its admins; it reaches BioManager's makers only
    if someone opens it as a GitHub issue."""
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="problem")    # problem | idea | question
    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[str] = mapped_column(String(300), default="")          # the path it was sent from
    app_version: Mapped[str] = mapped_column(String(40), default="")
    platform: Mapped[str] = mapped_column(String(200), default="")      # desktop or server, and the browser
    status: Mapped[str] = mapped_column(String(20), default="open")     # open | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RecordSignature(Base):
    """A notebook page signed, witnessed, or opened again to amend it
    (app/signatures.py). Signing is the person's choice, page by page.
    A page is locked while its newest "sign" is not followed by an "amend";
    each signature keeps a SHA-256 of the exact title and text signed, and
    the version it made, so it can be checked against the page later."""
    __tablename__ = "record_signatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))                # sign | witness | amend
    meaning: Mapped[str] = mapped_column(String(200), default="")  # what the person says by signing
    reason: Mapped[str] = mapped_column(Text, default="")          # why it is amended
    username: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")     # their name as it was then
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    version_id_fk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookPageInfo(Base):
    """What kind of page it is and where it stands: one row per page, made
    the first time anything here is set."""
    __tablename__ = "notebook_page_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), unique=True, index=True)
    # note | experiment | protocol | meeting | seminar | daily
    kind: Mapped[str] = mapped_column(String(20), default="note", index=True)
    # "" | planned | running | done | failed (experiments)
    status: Mapped[str] = mapped_column(String(20), default="")
    # Comma-wrapped, lower case: ",western,cloning," so one tag is one LIKE.
    tags: Mapped[str] = mapped_column(String(500), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # An experiment started from a protocol: which page, at which version.
    protocol_page_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A meeting note: its series and who presented.
    series_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    presenter: Mapped[str] = mapped_column(String(80), default="")
    # The day a daily log page is for.
    day: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    edited_by: Mapped[str] = mapped_column(String(80), default="")
    # Bumped when the body is replaced from outside the live editor (a
    # restored version, the plain-text fallback): editors open on the old
    # state start again from the saved text.
    collab_generation: Mapped[int] = mapped_column(Integer, default=0)
    # Which live edits the saved body holds: the editor's Yjs state vector
    # (base64) when it saved. A save strictly behind it (a background tab
    # that hasn't caught up) is not written over it (lab_notebook.behind).
    body_state: Mapped[str] = mapped_column(Text, default="", server_default="")
    # A protocol page: the lab's folder it is filed in (NotebookFolder).
    folder_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class LabGroup(Base):
    """A project group: some of the lab's people who share animals, stock,
    databases, to-dos and notebook pages among themselves (app/groups.py)."""
    __tablename__ = "lab_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # What its members may do in it (groups.member_may); its leads and the
    # lab's admins always may. Each starts as the group worked before these.
    may_edit_shared: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    may_change_records: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    may_edit_each_other: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    may_edit_pages: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    may_tick_todos: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    may_share: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())

    members: Mapped[list["LabGroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="LabGroupMember.username")


class LabGroupMember(Base):
    """Someone in a project group. A lead may add and remove its members and
    set what the group may do; one who may not edit (can_edit off) only
    sees what is shared with the group."""
    __tablename__ = "lab_group_members"
    __table_args__ = (UniqueConstraint("group_id_fk", "username", name="uq_lab_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id_fk: Mapped[int] = mapped_column(ForeignKey("lab_groups.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    lead: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    can_edit: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    group: Mapped[LabGroup] = relationship(back_populates="members")


class NotebookShare(Base):
    """Someone else who may open a page. username "*" is the whole lab,
    "group:<id>" a project group's members."""
    __tablename__ = "notebook_shares"
    __table_args__ = (UniqueConstraint("page_id_fk", "username", name="uq_notebook_share"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(10), default="view")  # view | edit
    shared_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookVersion(Base):
    """A saved state of a page. Edits by one person within a few minutes
    fold into one "auto" version; "manual" ones are saved on purpose,
    "release" ones number a protocol (v1, v2, ...), "restore" marks a
    return to an older one."""
    __tablename__ = "notebook_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(10), default="auto")
    label: Mapped[str] = mapped_column(String(160), default="")
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saved_by: Mapped[str] = mapped_column(String(80), default="")
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # When the editing session an "auto" version holds began.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotebookSyncUpdate(Base):
    """One change to a page's shared editing state (a Yjs update, base64),
    kept so every open editor can catch up. The id orders them."""
    __tablename__ = "notebook_sync_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    client_id: Mapped[str] = mapped_column(String(40), default="")
    username: Mapped[str] = mapped_column(String(80), default="")
    data: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookPresence(Base):
    """Who has a page open, and where their cursor is (a Yjs awareness
    update, base64). Rows older than a minute are ignored and cleared."""
    __tablename__ = "notebook_presence"
    __table_args__ = (UniqueConstraint("page_id_fk", "client_id", name="uq_notebook_presence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(40))
    username: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class NotebookComment(Base):
    """A comment on a page, optionally on a quoted passage; replies point at
    the first comment of their thread."""
    __tablename__ = "notebook_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id_fk: Mapped[int] = mapped_column(ForeignKey("notebook_pages.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    author: Mapped[str] = mapped_column(String(80), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str] = mapped_column(String(500), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotebookRecipe(Base):
    """A buffer or media recipe in the lab's library. `data` is the recipe
    block's JSON: {"volume", "volumeUnit", "components": [...], "notes"}."""
    __tablename__ = "notebook_recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    owner: Mapped[str] = mapped_column(String(80), default="")
    data: Mapped[str] = mapped_column(Text, default="{}")
    # The lab's folder it is filed in (NotebookFolder), or none.
    folder_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookFolder(Base):
    """A folder in the lab's protocol or recipe library. The lab shares
    them: anyone may make one and file what they may edit in it; its maker
    or an admin renames or deletes it (what was in it is kept, unfiled).
    A protocol's folder is on its page's NotebookPageInfo, a recipe's on
    the recipe."""
    __tablename__ = "notebook_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # protocol | recipe
    name: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotebookMeetingSeries(Base):
    """A recurring meeting (lab meeting, journal club) and its rotation:
    `members` is a JSON list of usernames in presenting order, and
    `next_index` points at whoever presents next."""
    __tablename__ = "notebook_meeting_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    owner: Mapped[str] = mapped_column(String(80), default="")
    members: Mapped[str] = mapped_column(Text, default="[]")
    next_index: Mapped[int] = mapped_column(Integer, default=0)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 = Monday
    time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM
    location: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# ---------------------------------------------------------------------------
# Model-wide table settings. Keep this block last in the file.
#
# Every integer primary key uses SQLite AUTOINCREMENT, so an id is never
# handed out twice: without it SQLite reuses the highest id after that row
# is deleted, and old references (audit entries, links, printed labels)
# silently point at the new record (the option is SQLite-only).
#
# Every foreign key is DEFERRABLE INITIALLY DEFERRED: references are checked
# when the transaction commits, not statement by statement. The ORM only
# orders an UPDATE that unlinks a child before the DELETE of its parent
# when a relationship() ties the two, so "unlink, then delete" (or undo
# re-inserting a row its restored links point at) would otherwise fail at
# random. What matters is that nothing dangles once committed.
#
# The listener covers tables defined anywhere, later ones included;
# app/integrity.py upgrades existing databases to match.
# ---------------------------------------------------------------------------
from sqlalchemy import event as _event  # noqa: E402


def _autoincrement_everywhere(tables) -> None:
    for _table in tables:
        _table.dialect_options["sqlite"]["autoincrement"] = True
        for _constraint in _table.foreign_key_constraints:
            _constraint.deferrable, _constraint.initially = True, "DEFERRED"
            for _fk in _constraint.elements:
                _fk.deferrable, _fk.initially = True, "DEFERRED"


_autoincrement_everywhere(Base.metadata.tables.values())


@_event.listens_for(Base.metadata, "before_create")
def _autoincrement_before_create(metadata, connection, tables=(), **kw) -> None:
    _autoincrement_everywhere(tables or metadata.tables.values())
