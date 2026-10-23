"""How old an animal is for its kind: the colour of the dot before its ID.

Young animals show blue, adults green and old ones red (a dead or removed
one stays grey, whatever its age). The bands are the ones labs go by:

    mouse      young under 8 weeks, old past 30 weeks
    zebrafish  young under 3 months post fertilisation, old past 18 months

Fly vials and worm plates go by their own timing (app/stock_service.py
unit_stage), and an expired reagent's dot is red too (inventory). Any other
organism database may set its own bands in days in Configure (a plant
database: a seedling under 14 days, senescent past 70); `for_module` reads
them, else the preset's.
"""

# kind: (young below this many days, old above this many, how to say each)
BANDS = {
    "mouse": (56, 210, "8 weeks", "30 weeks"),
    "zebrafish": (90, 548, "3 months", "18 months"),
}


def stage(kind: str, age_days) -> str:
    """"young", "adult" or "old" for an animal `age_days` old; "" when its
    kind has no bands or its age is not known."""
    band = BANDS.get(kind)
    if band is None or age_days is None or age_days == "":
        return ""
    days = int(age_days)
    young, old = band[0], band[1]
    if days < young:
        return "young"
    if days > old:
        return "old"
    return "adult"


def for_module(module_view, age_days) -> str:
    """A configurable database's stage: its own bands (module_view.stage_bands,
    in days), else its preset's, else none."""
    bands = getattr(module_view, "stage_bands", None)
    if bands is None:
        return stage(getattr(module_view.row, "preset_key", "") or "", age_days)
    if age_days is None or age_days == "":
        return ""
    days = int(age_days)
    return "young" if days < bands[0] else "old" if days > bands[1] else "adult"


def describe_module(module_view, which: str) -> str:
    """What the colour means in a database with its own bands, in the page's
    language: "young, under 14 days"."""
    bands = getattr(module_view, "stage_bands", None)
    if bands is None:
        return describe(getattr(module_view.row, "preset_key", "") or "", which)
    from .i18n import gettext
    young, old = bands
    return {"young": gettext("young, under %(n)s days", n=young),
            "adult": gettext("adult, %(young)s to %(old)s days", young=young, old=old),
            "old": gettext("older than %(n)s days", n=old)}.get(which, "")


def describe(kind: str, which: str) -> str:
    """What the colour means, for the dot's tooltip: "adult, 8 to 30 weeks"."""
    band = BANDS.get(kind)
    if band is None or not which:
        return ""
    young, old = band[2], band[3]
    return {"young": f"young, under {young}", "adult": f"adult, {young} to {old}",
            "old": f"older than {old}"}.get(which, "")
