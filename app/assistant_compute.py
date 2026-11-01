"""What an assistant asks BioManager to work out, rather than working it
out itself (the compute tools in app/assistant_tools.py, over app/api.py).

BioManager computes; the model talks. Anything with a right answer — a
count, an odds, a sample size, a date — is this module's, from the lab's
own records, so no number that ends up in a record is the model's
arithmetic. Everything here only reads, and says in words what it assumed:

* availability(): animals that fit a genotype, sex and age on a day, and
  are free (not in an active experiment, not breeders).
* breeding_plan(): Mendelian odds of a wanted genotype per mating, the
  lab's own litter size, how many matings to set up and by when.
* randomize(): animals into groups, balanced by sex, litter (or cage) and
  weight, with a seed that gives the same allocation again.
* power(): animals per group for two groups (t-test) or more (one-way
  ANOVA), with the SD from the lab's own readouts if asked.
* reorder_forecast(): when stock kept as a number runs out, from how fast
  its quantity went down in the change history.
* lab_checks(): the weekly review's checks, each with what would fix it
  (the animals' own from app/welfare.py).
* experiment_summary(): an experiment's facts for a methods paragraph,
  and the ARRIVE 2.0 Essential 10 with what the records say and lack.

The pure parts (the genotype model, the power calculation, allocation,
usage rates) take plain values: tests/test_assistant_compute.py tests them
on their own; tests/test_api_compute.py the endpoints.
"""
from __future__ import annotations

import math
import random
import re
import secrets
from datetime import date, datetime, timedelta

from flask import request, url_for
from sqlalchemy import func, select

from . import exp_stats, lab

# ---------------------------------------------------------------- the genotype model
#
# Each locus (a gene's floxed allele, a knockout, a Cre or reporter
# transgene) is counted as copies of the allele that matters: 0 (wild type
# or non-carrier), 1 (het, or hemizygous for a transgene), 2 (hom). A parent
# passes it on with odds copies/2, so a pup's copies are the sum of two coin
# flips. Loci are autosomal and unlinked (assorting independently), which
# is the textbook Cre × flox case; X-linked or linked loci are not modelled,
# and the plan says so.

COPIES = {"wt": 0, "het": 1, "hom": 2}
# What a wanted genotype may be: one state, or a carrier (het or hom, as for
# "Cre-positive"), or anything.
WANTS = {"wt": {0}, "het": {1}, "hom": {2}, "carrier": {1, 2}, "any": {0, 1, 2}}
SAY = {"wt": "wt", "het": "het", "hom": "hom", "carrier": "carrier", "any": "any",
       "+/+": "wt", "wild type": "wt", "wildtype": "wt", "negative": "wt", "neg": "wt", "non-carrier": "wt",
       "+/-": "het", "heterozygous": "het", "hemizygous": "het", "hemi": "het",
       "-/-": "hom", "homozygous": "hom",
       "positive": "carrier", "pos": "carrier", "+": "carrier"}
MAX_LOCI = 5             # 3⁵ genotypes a parent: 59,049 pairs to weigh, still quick


def state(value, wanted: bool = False) -> str:
    """wt, het or hom (or, for what is wanted, carrier or any) from the
    ways people say it; ValueError for anything else."""
    said = SAY.get(str(value or "").strip().lower())
    if said is None or (not wanted and said not in COPIES):
        allowed = "wt, het, hom, carrier or any" if wanted else "wt, het or hom"
        raise ValueError(f"“{value}” isn't a genotype here: say {allowed}.")
    return said


def locus_odds(mother: int, father: int) -> dict[int, float]:
    """A pup's copies at one locus, from its parents' copies."""
    a, b = mother / 2, father / 2
    return {0: (1 - a) * (1 - b), 1: a * (1 - b) + b * (1 - a), 2: a * b}


def cross_odds(mother: dict, father: dict, want: dict) -> float:
    """The odds that one pup is what `want` asks at every locus; a locus a
    parent's genotype leaves out is wild type."""
    p = 1.0
    for locus, wanted in want.items():
        odds = locus_odds(COPIES[mother.get(locus, "wt")], COPIES[father.get(locus, "wt")])
        p *= sum(odds[c] for c in WANTS[wanted])
    return p


def label(genotype: dict, loci) -> str:
    """{"Cre": "het", "flox": "hom"} as "Cre het; flox hom"."""
    return "; ".join(f"{locus} {genotype.get(locus, 'wt')}" for locus in loci)


def best_crosses(want: dict, limit: int = 6) -> list[dict]:
    """Every pair of parent genotypes at these loci, best odds first (and,
    at equal odds, the one needing fewest copies in the parents: hets are
    easier to keep than homs)."""
    loci = list(want)
    states = [dict(zip(loci, combo)) for combo in _product(list(COPIES), len(loci))]
    out = []
    for mother in states:
        for father in states:
            p = cross_odds(mother, father, want)
            if p > 0:
                copies = sum(COPIES[s] for s in (*mother.values(), *father.values()))
                out.append((-p, copies, label(mother, loci), label(father, loci), p))
    out.sort()
    seen, best = set(), []
    for _neg, _copies, m, f, p in out:
        pair = tuple(sorted((m, f)))     # the same pair either way round
        if pair in seen:
            continue
        seen.add(pair)
        best.append({"female": m, "male": f, "odds": round(p, 4)})
        if len(best) == limit:
            break
    return best


def _product(items: list, n: int) -> list[tuple]:
    out = [()]
    for _ in range(n):
        out = [t + (i,) for t in out for i in items]
    return out


# How a genotype is written in the lab ("DBH-Cre; Ai14", "Foxp3 fl/fl",
# "Cre+/-", "KO -/-") read at one locus. The side of a slash that is "+",
# "wt" or "0" is the wild-type allele; any other side (including the
# locus's own name, taken out first) is the allele that matters.
WT_SIDES = {"+", "wt", "0", "w"}
HOM_WORDS, HET_WORDS, WT_WORDS = {"hom", "homozygous"}, {"het", "hemi", "heterozygous", "hemizygous"}, \
    {"wt", "neg", "negative", "wild type", "wildtype", "-", "non-carrier"}
# A genotype that is only a state, with no gene named: "het", "fl/+", "-/-".
BARE = re.compile(r"\s*(?:(?:\+|-|0|wt|fl|flox|ko|tg)\s*/\s*(?:\+|-|0|wt|fl|flox|ko|tg)|het|hom|wt|hemi|"
                  r"heterozygous|homozygous|hemizygous)\s*")


def read_locus(genotype: str, locus: str, only_locus: bool = False) -> tuple[str, bool]:
    """(wt/het/hom, sure) for one locus of a genotype as the lab wrote it.
    Not written at all: wild type, not sure. A transgene written with no
    count ("DBH-Cre", "Cre+"): het (hemizygous), not sure. `only_locus`:
    the plan has one locus, so a bare "het" or "fl/fl" is about it."""
    parts = [p.strip() for p in re.split(r"[;,]", genotype or "") if p.strip()]
    hits = [p for p in parts if locus.lower() in p.lower()]
    if not hits:
        if only_locus and len(parts) == 1 and BARE.fullmatch(parts[0].lower()):
            return _read_state(parts[0]) or "wt", False
        return "wt", False
    rest = re.sub(re.escape(locus), "", hits[0], count=1, flags=re.I).strip().lower()
    rest = rest.strip("() ")
    found = _read_state(rest)
    if found is not None:
        return found, True
    return "het", False          # the name alone, or "+": a carrier, taken as hemizygous


def _read_state(text: str) -> str | None:
    text = " ".join(text.lower().split())
    if "/" in text:
        left, _, right = text.partition("/")
        left, right = left.strip().strip("()"), right.strip().strip("()")
        copies = (left not in WT_SIDES) + (right not in WT_SIDES)
        return ("wt", "het", "hom")[copies]
    words = set(re.split(r"[\s()]+", text)) - {""}
    if words & HOM_WORDS:
        return "hom"
    if words & HET_WORDS:
        return "het"
    if text in WT_WORDS or words & {"wt", "neg", "negative"}:
        return "wt"
    return None


# ---------------------------------------------------------------- odds of enough pups

def binomial_at_least(n: int, p: float, k: int) -> float:
    """P(X ≥ k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if p <= 0 or n < k:
        return 0.0
    if p >= 1:
        return 1.0
    below = 0.0
    for i in range(k):
        below += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * math.log(p) + (n - i) * math.log1p(-p))
    return max(0.0, 1.0 - below)


def matings_needed(count: int, p: float, pups_per_litter: float, confidence: float = 0.9,
                   most: int = 200) -> dict:
    """How many litters for `count` pups that are what is wanted (odds `p`
    each): on average, and to have them with `confidence`."""
    if p <= 0 or pups_per_litter <= 0:
        return {"expected": None, "sure": None}
    per_litter = pups_per_litter * p
    expected = math.ceil(count / per_litter)
    sure = None
    for m in range(1, most + 1):
        if binomial_at_least(int(math.floor(m * pups_per_litter)), p, count) >= confidence:
            sure = m
            break
    return {"expected": expected, "sure": sure, "wanted_per_litter": round(per_litter, 2)}


# ---------------------------------------------------------------- power (no scipy)
#
# Exact power from the noncentral F distribution: P(F' > F_crit), with
# F' ~ F(d1, d2, λ). Its CDF is a Poisson(λ/2)-weighted sum of regularised
# incomplete beta functions (exp_stats.inc_beta, the same one the
# experiment page's tests use). A two-sided two-sample t-test is the
# d1 = 1 case (t² = F), with λ = n·d²/2 for n per group.

def f_critical(alpha: float, d1: float, d2: float) -> float:
    """The F that only `alpha` of F(d1, d2) lies above."""
    lo, hi = 0.0, 1.0
    while exp_stats.f_upper(hi, d1, d2) > alpha:
        hi *= 2
        if hi > 1e8:
            break
    for _ in range(200):
        mid = (lo + hi) / 2
        if exp_stats.f_upper(mid, d1, d2) > alpha:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10 * max(1.0, hi):
            break
    return (lo + hi) / 2


def noncentral_f_cdf(f: float, d1: float, d2: float, lam: float) -> float:
    if f <= 0:
        return 0.0
    x = d1 * f / (d1 * f + d2)
    if lam <= 0:
        return exp_stats.inc_beta(x, d1 / 2, d2 / 2)
    half = lam / 2
    top = int(half + 12 * math.sqrt(half) + 60)
    total = 0.0
    for j in range(top + 1):
        weight = math.exp(-half + j * math.log(half) - math.lgamma(j + 1))
        if weight < 1e-17:              # a tail that adds nothing
            if j > half:
                break
            continue
        total += weight * exp_stats.inc_beta(x, d1 / 2 + j, d2 / 2)
    return min(1.0, total)


def anova_power(n: int, k: int, spread: float, alpha: float) -> float:
    """Power with n per group in k groups, where spread = Σ(μᵢ − μ̄)²/σ²
    (λ = n · spread)."""
    d1, d2 = k - 1, k * (n - 1)
    return 1 - noncentral_f_cdf(f_critical(alpha, d1, d2), d1, d2, n * spread)


def n_per_group(k: int, spread: float, alpha: float = 0.05, power: float = 0.8, most: int = 20000) -> dict:
    """The fewest per group (at least 2) that reach `power`."""
    if spread <= 0:
        raise ValueError("The groups don't differ: there is nothing to detect.")
    lo, hi = 1, 2                       # power(lo) < target ≤ power(hi), once hi is found
    while anova_power(hi, k, spread, alpha) < power:
        if hi >= most:
            raise ValueError(f"That effect is too small to detect with fewer than {most} animals a group.")
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if anova_power(mid, k, spread, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return {"n": hi, "power": round(anova_power(hi, k, spread, alpha), 4)}


def spread_of(k: int, sd: float, difference: float | None = None, means: list | None = None) -> float:
    """Σ(μᵢ − μ̄)²/σ²: from the expected means, or from the smallest
    difference worth finding (two groups that far apart and any others
    midway: the least favourable case, so n is enough whatever the rest do)."""
    if sd <= 0:
        raise ValueError("sd must be more than 0.")
    if means:
        mu = sum(means) / len(means)
        return sum((m - mu) ** 2 for m in means) / sd ** 2
    if difference is None:
        raise ValueError("Give the difference worth detecting (or each group's expected mean).")
    return difference ** 2 / (2 * sd ** 2)


def pooled_sd(groups: list[list[float]]) -> tuple[float | None, int]:
    """The within-group SD pooled over groups with two or more values, and
    its degrees of freedom."""
    ss, df = 0.0, 0
    for values in groups:
        if len(values) >= 2:
            mu = sum(values) / len(values)
            ss += sum((v - mu) ** 2 for v in values)
            df += len(values) - 1
    return (math.sqrt(ss / df) if df else None), df


def power(k: int = 2, difference: float | None = None, sd: float | None = None, means: list | None = None,
          d: float | None = None, alpha: float = 0.05, target: float = 0.8, attrition: float = 0.0) -> dict:
    """Animals per group, the test it is for, and every assumption."""
    if k < 2:
        raise ValueError("Compare at least two groups.")
    if means is not None and len(means) != k:
        raise ValueError(f"Give {k} means, one per group.")
    if not 0 < alpha < 0.5 or not 0.5 <= target < 1:
        raise ValueError("alpha is between 0 and 0.5 (0.05), power between 0.5 and 1 (0.8).")
    if not 0 <= attrition < 0.9:
        raise ValueError("attrition is a fraction lost, from 0 up to 0.9.")
    if d is not None:
        if d <= 0:
            raise ValueError("The effect size d must be more than 0.")
        spread, sd_used = d ** 2 / 2, None
    else:
        if sd is None:
            raise ValueError("Give the SD (or an experiment whose readouts give it), or the effect size d.")
        spread, sd_used = spread_of(k, sd, difference, means), sd
    found = n_per_group(k, spread, alpha, target)
    n = found["n"]
    enrol = math.ceil(n / (1 - attrition)) if attrition else n
    test = ("two-sided two-sample t-test, planned with the same SD in both groups" if k == 2
            else f"one-way ANOVA, {k} groups")
    out = {"n_per_group": n, "total": n * k, "enrol_per_group": enrol, "enrol_total": enrol * k,
           "achieved_power": found["power"], "test": test, "alpha": alpha, "power": target,
           "effect": {"cohens_d": round(math.sqrt(2 * spread), 4)} if k == 2
           else {"cohens_f": round(math.sqrt(spread / k), 4)},
           "method": "Exact power from the noncentral F distribution (t² = F for two groups), "
                     "λ = n·Σ(μᵢ − μ̄)²/σ², the smallest n per group reaching the power.",
           "assumptions": ["Normally distributed readout with the same SD in every group.",
                           "Equal group sizes; animals independent (the animal is the experimental unit)."]}
    if k == 2:
        out["assumptions"].append("The test run on the results is Welch's t-test (what an experiment's page and "
                                  "experiment_new's planned_test welch use), which doesn't assume equal SDs. Planned "
                                  "with one SD and equal group sizes, it has practically the same power as this "
                                  "calculation, so the two agree; if the groups' SDs are expected to differ much, "
                                  "plan with the larger SD.")
    if k > 2 and not means and d is None:
        out["assumptions"].append("Only the smallest difference worth detecting was given: two groups that far "
                                  "apart and the others midway, the least favourable case.")
    if sd_used is not None:
        out["sd"] = sd_used
    if attrition:
        out["assumptions"].append(f"{attrition:.0%} of animals lost before the end: enrol {enrol} per group.")
    return out


# ---------------------------------------------------------------- allocation

def allocate(animals: list[dict], groups: list[dict], seed: int) -> dict:
    """Animals into groups by stratified minimisation (Pocock and Simon),
    seeded.

    animals: {"key", "sex", "stratum" (a litter or cage), "weight" (or None)};
    groups: {"name", "size"}; the sizes may add up to fewer than there are
    animals (the rest, picked by the seed, are spares). The animals are
    taken sex by sex and stratum by stratum (in an order the seed picks),
    heaviest first; each goes to the group with room where it leaves the
    groups least unequal in sex, then stratum, then mean weight, then size,
    and the seed breaks ties, so like animals are shared out at random. The
    same animals, groups and seed give the same answer."""
    rng = random.Random(seed)
    sizes = [g["size"] for g in groups]
    if sum(sizes) > len(animals):
        raise ValueError(f"The groups want {sum(sizes)} animals; there are {len(animals)}.")
    shuffled = sorted(animals, key=lambda a: str(a["key"]))
    rng.shuffle(shuffled)
    spares = shuffled[sum(sizes):]                 # the seed's choice, before anything is balanced
    # Like with like: each sex together, each litter (or cage) within it in
    # an order the seed picks, heaviest first; so the groups take turns.
    cells: dict[tuple, list[dict]] = {}
    for a in shuffled[:sum(sizes)]:
        cells.setdefault((a["sex"], a["stratum"]), []).append(a)
    for cell in cells.values():
        cell.sort(key=lambda a: (a.get("weight") is None, -(a.get("weight") or 0)))
    keys = list(cells)
    rng.shuffle(keys)
    keys.sort(key=lambda c: c[0])
    order = [a for key in keys for a in cells[key]]
    k = len(groups)
    members: list[list[dict]] = [[] for _ in range(k)]
    weights = [a["weight"] for a in animals if a.get("weight") is not None]
    mean_all = sum(weights) / len(weights) if weights else None
    sd_all = (math.sqrt(sum((w - mean_all) ** 2 for w in weights) / (len(weights) - 1))
              if len(weights) > 1 else 0) or 1.0

    def spread(values: list[float]) -> float:
        return max(values) - min(values) if values else 0.0

    def score(g: int, a: dict) -> tuple:
        """How unequal the groups would be with `a` in group g: sex first,
        then its stratum, then mean weight, then size."""
        trial = [m + ([a] if i == g else []) for i, m in enumerate(members)]
        sex = spread([sum(1 for x in m if x["sex"] == a["sex"]) / sizes[i] for i, m in enumerate(trial)])
        stratum = spread([sum(1 for x in m if x["stratum"] == a["stratum"]) / sizes[i] for i, m in enumerate(trial)])
        means = [sum(x["weight"] for x in m if x.get("weight") is not None)
                 / max(1, sum(1 for x in m if x.get("weight") is not None))
                 for m in trial if any(x.get("weight") is not None for x in m)]
        weight = spread(means) / sd_all if mean_all is not None and len(means) == k else 0.0
        fill = spread([len(m) / sizes[i] for i, m in enumerate(trial)])
        return tuple(round(v, 9) for v in (sex, stratum, weight, fill))

    for a in order:
        room = [g for g in range(k) if len(members[g]) < sizes[g]]
        ties = [rng.random() for _ in range(k)]
        best = min(room, key=lambda g: (score(g, a), ties[g]))
        members[best].append(a)
    return {"groups": [{"name": groups[i]["name"], "animals": members[i]} for i in range(k)], "spares": spares}


def balance(allocated: list[dict]) -> dict:
    """Each group's n, sexes, strata and weight (mean ± SD), and whether the
    weights differ (one-way ANOVA, as a check only)."""
    rows, samples = [], []
    for g in allocated:
        animals = g["animals"]
        ws = [a["weight"] for a in animals if a.get("weight") is not None]
        mean = sum(ws) / len(ws) if ws else None
        sd = math.sqrt(sum((w - mean) ** 2 for w in ws) / (len(ws) - 1)) if len(ws) > 1 else None
        sexes: dict[str, int] = {}
        strata: dict[str, int] = {}
        for a in animals:
            sexes[a["sex"] or "?"] = sexes.get(a["sex"] or "?", 0) + 1
            strata[a["stratum"] or "?"] = strata.get(a["stratum"] or "?", 0) + 1
        rows.append({"group": g["name"], "n": len(animals), "sex": sexes, "strata": strata,
                     "weight_mean": round(mean, 2) if mean is not None else None,
                     "weight_sd": round(sd, 2) if sd is not None else None, "weighed": len(ws)})
        samples.append(ws)
    check = None
    if len(samples) >= 2 and all(len(s) >= 2 for s in samples):
        check = exp_stats.anova(samples) if len(samples) > 2 else exp_stats.welch(*samples)
        check = {"test": check["test"], "p": round(check["p"], 4)}
    means = [r["weight_mean"] for r in rows if r["weight_mean"] is not None]
    return {"groups": rows, "weight_difference": round(max(means) - min(means), 2) if len(means) > 1 else None,
            "weight_test": check}


def split_sizes(total: int, names: list[str], rng: random.Random) -> list[int]:
    """Equal groups; when they can't be, which get one more is the seed's choice."""
    base, extra = divmod(total, len(names))
    lucky = set(rng.sample(range(len(names)), extra))
    return [base + (1 if i in lucky else 0) for i in range(len(names))]


# ---------------------------------------------------------------- usage

def usage_rate(changes: list[tuple[datetime, float, float]], since: datetime, now: datetime) -> dict | None:
    """How fast a quantity goes down, a day: what was used (each fall in
    the quantity; a rise is a restock, not use) over the days it was
    watched. None with no fall, or under a week of history."""
    used = sum(max(0.0, before - after) for _t, before, after in changes)
    falls = sum(1 for _t, before, after in changes if after < before)
    days = (now - since).total_seconds() / 86400
    if not falls or days < 7:
        return None
    return {"per_day": used / days, "used": used, "days": round(days, 1), "falls": falls}


# ---------------------------------------------------------------- a difference with its interval

def t_quantile(p_two_sided: float, df: float) -> float:
    """The t with `p_two_sided` of t(df) beyond ±t."""
    lo, hi = 0.0, 1.0
    while exp_stats.t_two_sided(hi, df) > p_two_sided:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if exp_stats.t_two_sided(mid, df) > p_two_sided:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def difference_ci(a: list[float], b: list[float], level: float = 0.95) -> dict:
    """Mean of a minus mean of b, with its Welch confidence interval."""
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1) / len(a)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return {"difference": round(ma - mb, 4), "ci": [round(ma - mb, 4)] * 2, "level": level}
    df = (va + vb) ** 2 / ((va ** 2 / (len(a) - 1) if va else 0) + (vb ** 2 / (len(b) - 1) if vb else 0) or 1e-300)
    half = t_quantile(1 - level, df) * se
    return {"difference": round(ma - mb, 4), "ci": [round(ma - mb - half, 4), round(ma - mb + half, 4)],
            "level": level}


# ---------------------------------------------------------------- ARRIVE 2.0, the Essential 10

ARRIVE = ("Study design", "Sample size", "Inclusion and exclusion criteria", "Randomisation", "Blinding",
          "Outcome measures", "Statistical methods", "Experimental animals", "Experimental procedures", "Results")
CONTROL_WORDS = re.compile(r"control|vehicle|sham|saline|pbs|placebo|untreated|ctrl|\bwt\b|wild[ -]?type", re.I)


def _said(text: str, pattern: str) -> str:
    """The sentence of the experiment's own text that mentions `pattern`."""
    for sentence in re.split(r"(?<=[.!?\n])\s+", text or ""):
        if re.search(pattern, sentence, re.I):
            return " ".join(sentence.split())[:300]
    return ""


def arrive(facts: dict) -> list[dict]:
    """Each Essential 10 item: what the records say, and what is missing
    for the methods (never filled in by guessing)."""
    text = facts.get("text", "")
    groups = facts["animals"]["groups"]
    out = []

    def item(n: int, recorded: list, missing: list):
        recorded = [r for r in recorded if r]
        out.append({"item": n, "name": ARRIVE[n - 1], "recorded": recorded, "missing": missing,
                    "status": "missing" if not recorded else ("partial" if missing else "recorded")})

    names = [g["group"] for g in groups]
    controls = [n for n in names if CONTROL_WORDS.search(n or "")]
    item(1, ["Groups: " + ", ".join(f"{g['group']} (n = {g['n']})" for g in groups) + "." if groups else "",
             f"Control group: {', '.join(controls)}." if controls else ""],
         (["Which group is the control."] if not controls else [])
         + ["The experimental unit (each animal, or a cage or tank of them).",
            "Whether the design is between animals, within animals or factorial."])
    justified = _said(text, r"power|sample size")
    item(2, [f"{facts['animals']['total']} animals: " + ", ".join(f"{g['group']} {g['n']}" for g in groups) + "."
             if groups else "", justified],
         [] if justified else ["How the sample size was decided (power_analysis gives a calculation)."])
    ended = facts["animals"].get("ended", 0)
    criteria = _said(text, r"exclu|inclu|humane end")
    item(3, [f"{ended} animal(s) died or were taken out during the experiment." if ended else
             "No animal died or was taken out during the experiment in the records.", criteria],
         ([] if criteria else ["Inclusion and exclusion criteria set before the start, and any animal or "
                               "data point left out of the analysis, with why."]))
    randomised = _said(text, r"random")
    item(4, [randomised], [] if randomised else ["How animals were allocated to groups (randomize gives a seeded "
                                                 "allocation), and the order of treatments and measurements."])
    blinded = _said(text, r"blind|mask")
    item(5, [blinded], [] if blinded else ["Who knew the group allocation at each stage (allocation, the "
                                           "experiment, outcome assessment, analysis)."])
    readout = facts["readouts"]
    item(6, [f"{readout['label']}" + (f" ({readout['unit']})" if readout.get("unit") else "")
             + f", measured on {readout['days_measured']} day(s)" + (f" from {readout['first']} to {readout['last']}"
                                                                      if readout.get("first") else "") + "."
             if readout.get("days_measured") else "",
             ("Secondary measures: " + "; ".join(
                 f"{m['label']}" + (f" ({m['unit']})" if m.get("unit") else "")
                 + (f", {m['expression']}" if m.get("expression") else "") + f", measured on {m['days_measured']} day(s)" for m in readout["other_measures"] if m["days_measured"])
              + ".") if any(m["days_measured"] for m in readout.get("other_measures") or []) else ""],
         ["Which outcome is primary, and how it was measured (and by whom)."])
    tests = facts["statistics"]["tests"]
    item(7, [f"Groups compared on each day by {', '.join(tests)} (each day on its own, not corrected for the number "
             "of days)." if tests else ""],
         ["The software, the unit of analysis, and how the test's assumptions were checked."])
    a = facts["animals"]
    age = a.get("age_at_start_days")
    weight = a.get("weight_at_start_g")
    item(8, [a["species"],
             ("Genotypes: " + ", ".join(f"{k} ({v})" for k, v in a["genotypes"].items()) + ".") if a["genotypes"] else "",
             ("Sex: " + ", ".join(f"{k} {v}" for k, v in a["sex"].items()) + ".") if a["sex"] else "",
             f"Age at the start: {age['min']}–{age['max']} days (median {age['median']})." if age else "",
             f"Weight at the start: {weight['min']}–{weight['max']} g (mean {weight['mean']})." if weight else ""],
         ["Where the animals came from (supplier or the lab's own colony), their health and immune status, and "
          "any earlier procedures."] + ([] if age else ["Their age."]) + ([] if a["sex"] else ["Their sex."]))
    steps = facts["procedures"]
    item(9, [f"{s['what']}: days {s['days']}" + (f", group {s['group']}" if s.get("group") else "")
             + (f"; done on {s['done']} day(s)" if s.get("done") else "") + (f"; lots {', '.join(s['lots'])}"
                                                                          if s.get("lots") else "") + "."
             for s in steps],
         ["Where and when each procedure was done (time of day), the housing and husbandry, anaesthesia, "
          "analgesia and humane endpoints, as they apply."])
    results = facts["statistics"].get("last_day")
    item(10, [f"On day {results['day']} ({results['date']}): " + "; ".join(
        f"{g['group']} n = {g['n']}, mean {g['value']}" + (f" ± SEM {g['sem']}" if g.get("sem") is not None else "")
        for g in results["groups"]) + (f"; {results['test']} p = {results['p']}" if results.get("p") is not None
                                       else "") + (f"; difference {results['difference']['difference']} "
                                                   f"(95% CI {results['difference']['ci'][0]} to "
                                                   f"{results['difference']['ci'][1]})"
                                                   if results.get("difference") else "") + "."
              if results else ""],
         ["Results for every analysis, with the n in each and an effect size with its confidence interval."]
         if not results or not results.get("difference") else ["The n analysed per group if any were left out."])
    return out


# ================================================================ against the lab's records

BUSY = ("planned", "active", "paused")   # an experiment whose animals are taken (a planned one has them booked)
PAIRING_TO_BIRTH = 21               # days from setting up a mating to the litter
LITTER_INTERVAL = 28                # days between a pair's litters (mated again after each)
MOST_LITTERS = 6                    # litters counted from one mating at most
FALLBACK_LITTER = 6.0               # mice, when the lab has too few litters to say
MIN_LITTERS = 3


def _base() -> str:
    return request.host_url.rstrip("/")


def _mouse_url(m) -> str:
    return _base() + url_for("colony", view="mice", scope="all", q=m.mouse_id)


def _cage_url(c) -> str:
    return _base() + url_for("colony", view="cages", scope="all", q=c.cage_id)


def _terms(genotype: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[;,]", genotype or "") if t.strip()]


def _day(raw, default: date | None = None) -> date | None:
    if raw in (None, ""):
        return default
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise ValueError(f"“{raw}” isn't a date: write 2026-10-11.") from None


def latest_weights(session, mouse_ids, on: date | None = None) -> dict[int, tuple[float, date]]:
    """Each mouse's last weight on or before `on` (any day without it)."""
    from .models import MouseWeight
    out: dict[int, tuple[float, date]] = {}
    ids = list(mouse_ids)
    for start in range(0, len(ids), 500):
        stmt = select(MouseWeight).where(MouseWeight.mouse_id_fk.in_(ids[start:start + 500]))
        if on is not None:
            stmt = stmt.where(MouseWeight.weigh_date <= on)
        for w in session.scalars(stmt):
            if w.mouse_id_fk not in out or w.weigh_date > out[w.mouse_id_fk][1]:
                out[w.mouse_id_fk] = (w.grams, w.weigh_date)
    return out


def _busy_mice(session) -> dict[int, str]:
    """Mouse row → the experiment (planned, active or paused) it is in."""
    from .models import Experiment, ExperimentMouse
    return {mid: name for mid, name in session.execute(
        select(ExperimentMouse.mouse_id_fk, Experiment.name).join(Experiment, ExperimentMouse.experiment_id_fk == Experiment.id)
        .where(Experiment.status.in_(BUSY)))}


# ---------------------------------------------------------------- availability

def availability(session, species: str = "mouse", genotype: str = "", sex: str = "", age_min_days: int | None = None,
                 age_max_days: int | None = None, on=None, include_in_experiments: bool = False,
                 include_breeders: bool = False, limit: int = 200) -> dict:
    """Living animals that fit, on a day: how many, and which."""
    on = _day(on, date.today())
    sex = (sex or "").strip().upper()
    if sex not in ("", "F", "M"):
        raise ValueError("sex is F, M or left out.")
    if age_min_days is not None and age_max_days is not None and age_min_days > age_max_days:
        raise ValueError("age_min_days is more than age_max_days.")
    if species in ("zebrafish", "fish"):
        return _fish_availability(session, genotype, sex, age_min_days, age_max_days, on,
                                  include_in_experiments, limit)
    if species not in ("mouse", "mice"):
        raise ValueError("species is mouse or zebrafish.")
    if not lab.request_features().get("colony", True):
        raise LookupError("The mouse colony is switched off for this lab.")
    from sqlalchemy.orm import selectinload
    from .access import yours_to_use
    from .models import MouseRecord
    from .services import holds_breeders, mouse_is_active
    terms = _terms(genotype)
    busy = _busy_mice(session)
    stmt = select(MouseRecord).options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter)) \
        .where(MouseRecord.date_of_death.is_(None))
    if sex:
        stmt = stmt.where(func.upper(MouseRecord.gender) == sex)
    found, left = [], {"in_experiments": 0, "breeders": 0, "age_unknown": 0}
    for m in session.scalars(stmt.order_by(MouseRecord.mouse_id)):
        if not mouse_is_active(m):
            continue
        text = " ".join([m.genotype or "", m.transgene_1, m.transgene_2, m.transgene_3, m.transgene_4]).lower()
        if any(t not in text for t in terms):
            continue
        born = m.litter.date_of_birth if m.litter else None
        age = (on - born).days if born else None
        if age is not None and age < 0:
            continue
        if age_min_days is not None or age_max_days is not None:
            if age is None:
                left["age_unknown"] += 1
                continue
            if (age_min_days is not None and age < age_min_days) or (age_max_days is not None and age > age_max_days):
                continue
        if m.cage is not None and holds_breeders(m.cage.purpose) and not include_breeders:
            left["breeders"] += 1
            continue
        if m.id in busy and not include_in_experiments:
            left["in_experiments"] += 1
            continue
        found.append((m, born, age))
    weights = latest_weights(session, [m.id for m, _b, _a in found[:limit]], on)
    by_sex: dict[str, int] = {}
    by_genotype: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    for m, _b, _a in found:
        by_sex[m.gender or "?"] = by_sex.get(m.gender or "?", 0) + 1
        by_genotype[m.genotype or "(none)"] = by_genotype.get(m.genotype or "(none)", 0) + 1
        by_owner[m.owner or "(none)"] = by_owner.get(m.owner or "(none)", 0) + 1
    animals = [{"mouse": m.mouse_id, "ref": {"kind": "mouse", "id": m.id}, "sex": m.gender or "",
                "genotype": m.genotype or "", "born": born.isoformat() if born else None, "age_days": age,
                "age_weeks": round(age / 7, 1) if age is not None else None,
                "cage": m.cage.cage_id if m.cage else None, "purpose": m.cage.purpose if m.cage else "",
                "owner": m.owner or "", "yours_to_use": yours_to_use(m),
                "weight_g": weights[m.id][0] if m.id in weights else None,
                "in_experiment": busy.get(m.id), "url": _mouse_url(m)} for m, born, age in found[:limit]]
    notes = [f"Ages are on {on.isoformat()}, from each mouse's litter's date of birth."]
    if on > date.today():
        notes.append("That day is still to come: the mice are counted as if all are alive then.")
    if not include_breeders:
        notes.append("Mice in Breeder and Breeding cages are left out (include_breeders to count them).")
    if not include_in_experiments:
        notes.append("Mice in a planned, active or paused experiment are left out (include_in_experiments to count "
                     "them).")
    return {"species": "mouse", "on": on.isoformat(), "count": len(found), "by_sex": by_sex,
            "by_genotype": by_genotype, "by_owner": by_owner, "left_out": left, "animals": animals,
            "listed": len(animals), "notes": notes}


def _fish_availability(session, genotype, sex, age_min_days, age_max_days, on, include_in_experiments, limit) -> dict:
    if not lab.request_features().get("zebrafish", True):
        raise LookupError("Zebrafish is switched off for this lab.")
    from sqlalchemy.orm import selectinload
    from .models import Experiment, ExperimentSubject, FishRecord
    terms = _terms(genotype)
    busy = {sid for (sid,) in session.execute(
        select(ExperimentSubject.subject_id).join(Experiment, ExperimentSubject.experiment_id_fk == Experiment.id)
        .where(ExperimentSubject.subject_kind == "fish", Experiment.status.in_(BUSY)))}
    found, left = [], {"in_experiments": 0, "age_unknown": 0, "sex_not_known": 0}
    for f in session.scalars(select(FishRecord).options(selectinload(FishRecord.tank), selectinload(FishRecord.line))
                             .where(FishRecord.status == "alive").order_by(FishRecord.id)):
        if f.tank is None or not f.tank.active:
            continue
        text = " ".join([f.genotype or "", f.line.name if f.line else ""]).lower()
        if any(t not in text for t in terms):
            continue
        if sex and (f.sex or "").upper() != sex:
            if (f.sex or "").lower() in ("mixed", "unknown", ""):
                left["sex_not_known"] += f.count or 0
            continue
        born = f.date_of_fertilization
        age = (on - born).days if born else None
        if age is not None and age < 0:
            continue
        if age_min_days is not None or age_max_days is not None:
            if age is None:
                left["age_unknown"] += f.count or 0
                continue
            if (age_min_days is not None and age < age_min_days) or (age_max_days is not None and age > age_max_days):
                continue
        if f.id in busy and not include_in_experiments:
            left["in_experiments"] += f.count or 0
            continue
        found.append((f, born, age))
    by_sex: dict[str, int] = {}
    for f, _b, _a in found:
        by_sex[f.sex or "?"] = by_sex.get(f.sex or "?", 0) + (f.count or 0)
    return {"species": "zebrafish", "on": on.isoformat(), "count": sum(f.count or 0 for f, _b, _a in found),
            "rows": len(found), "by_sex": by_sex, "left_out": left,
            "animals": [{"fish": f.id, "ref": {"kind": "fish", "id": f.id}, "tank": f.tank.tank_id,
                         "tank_ref": {"kind": "tank", "id": f.tank.id}, "line": f.line.name if f.line else "",
                         "genotype": f.genotype or "", "sex": f.sex or "", "count": f.count,
                         "fertilised": born.isoformat() if born else None, "age_days": age}
                        for f, born, age in found[:limit]],
            "notes": [f"Ages are on {on.isoformat()}, from each row's date of fertilisation; counts are fish, "
                      "a row holding as many as its count."]}


# ---------------------------------------------------------------- breeding plan

def lab_litter_size(session, loci: list[str]) -> dict:
    """The lab's own mean litter size, this line's when it has enough
    litters (pups whose genotype names one of the loci), and how many of a
    weaned litter's pups it kept a record of."""
    from .models import LitterRecord, MouseRecord
    rows = session.execute(select(LitterRecord.id, LitterRecord.total_pups, LitterRecord.weaned_on)
                           .where(LitterRecord.total_pups > 0)).all()
    line_ids = set()
    if loci:
        clause = None
        for locus in loci:
            match = MouseRecord.genotype.ilike(f"%{locus}%")
            clause = match if clause is None else (clause | match)
        line_ids = {lid for (lid,) in session.execute(select(MouseRecord.litter_id_fk).where(clause).distinct())}
    line = [r for r in rows if r.id in line_ids]
    if len(line) >= MIN_LITTERS:
        chosen, source = line, f"the mean of this line's {len(line)} litters (pups whose genotype names " \
                               f"{' or '.join(loci)})"
    elif len(rows) >= MIN_LITTERS:
        chosen, source = rows, f"the mean of the lab's {len(rows)} litters (too few of this line's to use)"
    else:
        return {"mean": FALLBACK_LITTER, "litters": len(rows), "kept": 1.0,
                "source": f"{FALLBACK_LITTER:g} pups, a usual mouse litter: the lab has recorded fewer than "
                          f"{MIN_LITTERS} litters with a pup count",
                "kept_source": "every pup born taken as weaned (too few weaned litters to say)"}
    mean = sum(r.total_pups for r in chosen) / len(chosen)
    weaned = [r for r in chosen if r.weaned_on is not None]
    kept, kept_source = 1.0, "every pup born taken as weaned (too few weaned litters to say)"
    if len(weaned) >= MIN_LITTERS:
        counts = dict(session.execute(select(MouseRecord.litter_id_fk, func.count(MouseRecord.id))
                                      .where(MouseRecord.litter_id_fk.in_([r.id for r in weaned]))
                                      .group_by(MouseRecord.litter_id_fk)).all())
        born = sum(r.total_pups for r in weaned)
        kept = min(1.0, sum(min(counts.get(r.id, 0), r.total_pups) for r in weaned) / born) if born else 1.0
        kept_source = f"of {born} pups born in {len(weaned)} weaned litters, the share with a mouse record"
    return {"mean": round(mean, 2), "litters": len(chosen), "kept": round(kept, 3), "source": source,
            "kept_source": kept_source}


# Strains and backgrounds, which a lab writes in a mouse's genotype beside
# its alleles ("C57BL/6J; Cre") and which lab_overview's transgenes_in_use
# lists with them: not loci. An inbred strain breeds true, so a strain
# wanted means both parents of it, never a het or wt call.
BACKGROUND = re.compile(r"c57bl(/\w+)?|b6[jn]?|balb/?c\w*|129[\w/]*|fvb(/n\w*)?|dba/?[12]\w*|cd-?1|icr|c3h(/\w+)?|"
                        r"nod(/\w+)?|nsg|nod[ .-]?scid[\w ]*|sjl(/\w+)?|swiss( webster)?|cba(/\w+)?|a/j|nzb(/\w+)?|"
                        r"nzw(/\w+)?|akr(/\w+)?|cast/eij|pwk/phj|wsb/eij|outbred|mixed( background)?")


def _plain(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def lab_strains(session) -> set[str]:
    """The lab's strains' backgrounds (Strains' Background column), and any
    strain whose name is a background itself, as plain lower-case text."""
    from .models import StrainRecord
    out = set()
    for name, background in session.execute(select(StrainRecord.strain_name, StrainRecord.strain_background)):
        if _plain(background):
            out.add(_plain(background))
        if _plain(name) and (_plain(name) == _plain(background) or BACKGROUND.fullmatch(_plain(name))):
            out.add(_plain(name))
    return out


def is_strain(name: str, known: set[str]) -> bool:
    """Whether a name is a strain or background (C57BL/6J, BALB/c, the
    lab's own backgrounds) rather than an allele or transgene."""
    return _plain(name) in known or BACKGROUND.fullmatch(_plain(name)) is not None


def lab_breeders(session, loci: list[str], today: date, strains: list[str] | None = None) -> list[dict]:
    """Living mice in Breeder and Breeding cages (of every strain asked,
    as their genotype names it), each read at the loci."""
    from sqlalchemy.orm import selectinload
    from .models import CageRecord, MouseRecord
    from .services import SAC_THRESHOLD_WEEKS, holds_breeders, is_breeding_cage, mouse_is_active, split_genotype
    known = lab_strains(session)
    busy = _busy_mice(session)
    out = []
    for m in session.scalars(select(MouseRecord).join(CageRecord, MouseRecord.cage_id_fk == CageRecord.id)
                             .options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter))
                             .where(MouseRecord.date_of_death.is_(None)).order_by(MouseRecord.mouse_id)):
        if not (mouse_is_active(m) and holds_breeders(m.cage.purpose)) or (m.gender or "").upper() not in ("F", "M"):
            continue
        parts = split_genotype(m.genotype or "; ".join(filter(None, [m.transgene_1, m.transgene_2])))
        if any(_plain(s) not in {_plain(p) for p in parts} for s in strains or []):
            continue
        alleles = "; ".join(p for p in parts if not is_strain(p, known))    # a strain is no locus's state
        read = {locus: read_locus(alleles, locus, only_locus=len(loci) == 1) for locus in loci}
        # What else it carries: its pups may carry that too, which the genotype asked for leaves out.
        # (A bare "fl/fl" alone is the one locus asked, as read_locus takes it.)
        lone = len(loci) == 1 and len(parts) == 1
        also = [p for p in parts if not is_strain(p, known) and not any(_plain(lo) in _plain(p) for lo in loci)
                and not (lone and BARE.fullmatch(p.lower()))]
        born = m.litter.date_of_birth if m.litter else None
        weeks = (today - born).days // 7 if born else None
        out.append({"mouse": m.mouse_id, "ref": {"kind": "mouse", "id": m.id}, "sex": m.gender.upper(),
                    "genotype": m.genotype or "", "read_as": {k: v[0] for k, v in read.items()},
                    "sure": all(v[1] for v in read.values()), "also_carries": also,
                    "cage": m.cage.cage_id, "purpose": m.cage.purpose, "mating": is_breeding_cage(m.cage.purpose),
                    "in_experiment": busy.get(m.id),
                    "age_weeks": weeks, "past_breeding_age": weeks is not None and weeks >= SAC_THRESHOLD_WEEKS})
    return out


def litters_by(today: date, born_from: date | None, born_by: date) -> int:
    """How many litters one mating set up today can give with pups born in
    the window: the first PAIRING_TO_BIRTH days on, then one every
    LITTER_INTERVAL days (0 when the window has closed)."""
    first = today + timedelta(days=PAIRING_TO_BIRTH)
    if first > born_by:
        return 0
    n = 1 + (born_by - first).days // LITTER_INTERVAL
    if born_from is not None and born_from > first:
        n -= -(-(born_from - first).days // LITTER_INTERVAL)       # litters born before the window don't count
    return max(0, min(n, MOST_LITTERS))


def breeding_plan(session, want: dict, sex: str = "", count: int = 1, age_min_days: int | None = None,
                  age_max_days: int | None = None, by=None, crosses: list | None = None, confidence: float = 0.9,
                  today: date | None = None, litters_per_mating: int | None = None) -> dict:
    """How to breed `count` mice of a genotype (and sex), aged within a
    window on a day: the odds per mating, how many matings (each giving
    `litters_per_mating` litters, else as many as fit before `by`, else
    one), the matings already under way, and by when."""
    from .litter_dates import weaning_offset
    wean_days = weaning_offset(session)
    today = today or date.today()
    if not isinstance(want, dict) or not want:
        raise ValueError("Say the genotype wanted at each locus, e.g. {\"Cre\": \"carrier\", \"Foxp3\": \"hom\"}.")
    known, strains, loci_wanted = lab_strains(session), [], {}
    for key, value in want.items():
        key = str(key).strip()
        if not key:
            continue
        if is_strain(key, known):
            if SAY.get(str(value or "").strip().lower()) not in ("hom", "carrier", "any"):
                raise ValueError(f"{key} is a strain (a background), not an allele or transgene: an inbred strain "
                                 f"breeds true, so it has no het or wt. Ask for {{\"{key}\": \"hom\"}} to have both "
                                 f"parents {key}, or name the allele or transgene you mean (Cre, Foxp3 fl…).")
            strains.append(key)
        else:
            loci_wanted[key] = state(value, wanted=True)
    want = loci_wanted
    if not want and not strains:
        raise ValueError("Say the genotype wanted at each locus, e.g. {\"Cre\": \"carrier\", \"Foxp3\": \"hom\"}.")
    if len(want) > MAX_LOCI:
        raise ValueError(f"Plan one to {MAX_LOCI} loci at a time.")
    loci = list(want)

    def named(genotype: dict) -> str:
        """A parent as the plan says it: its strain, then each locus."""
        return "; ".join(filter(None, [*strains, label(genotype, loci)]))
    sex = (sex or "").strip().upper()
    if sex not in ("", "F", "M"):
        raise ValueError("sex is F, M or left out.")
    if count < 1:
        raise ValueError("count is how many mice are wanted, at least 1.")
    if not 0.5 <= confidence < 1:
        raise ValueError("confidence is between 0.5 and 0.99.")
    by = _day(by)
    litter = lab_litter_size(session, loci or strains)
    pups = litter["mean"] * litter["kept"]
    p_sex = 0.5 if sex else 1.0
    if litters_per_mating is not None and not 1 <= litters_per_mating <= MOST_LITTERS:
        raise ValueError(f"litters_per_mating is 1 to {MOST_LITTERS}.")
    born_from = born_by = None
    if by is not None:
        born_by = by - timedelta(days=age_min_days if age_min_days is not None else wean_days)
        born_from = by - timedelta(days=age_max_days) if age_max_days is not None else None
    litters = litters_per_mating or (max(1, litters_by(today, born_from, born_by)) if born_by else 1)

    plans = []
    if crosses:
        for c in crosses:
            female = {locus: state((c.get("female") or {}).get(locus, "wt")) for locus in loci}
            male = {locus: state((c.get("male") or {}).get(locus, "wt")) for locus in loci}
            plans.append({"female": named(female), "male": named(male),
                          "odds": cross_odds(female, male, want), "females": None, "males": None})
        breeders = []
    else:
        breeders = lab_breeders(session, loci, today, strains)
        kinds: dict[tuple[str, str], list[dict]] = {}
        for b in breeders:
            kinds.setdefault((b["sex"], named(b["read_as"]), tuple(b["also_carries"])), []).append(b)
        for (fsex, flabel, falso), females in kinds.items():
            if fsex != "F":
                continue
            for (msex, mlabel, malso), males in kinds.items():
                if msex != "M":
                    continue
                odds = cross_odds(females[0]["read_as"], males[0]["read_as"], want)
                if odds > 0:
                    # A mating under way: a Breeding cage with one of each kind.
                    fcages = {b["cage"] for b in females if b["mating"]}
                    mcages = {b["cage"] for b in males if b["mating"]}
                    free = lambda mice: [b for b in mice if not b["mating"] and not b["in_experiment"]]  # noqa: E731
                    plans.append({"female": flabel + "".join(f" + {a}" for a in falso),
                                  "male": mlabel + "".join(f" + {a}" for a in malso), "odds": odds,
                                  "also_carries": sorted(set(falso) | set(malso)),
                                  "females": len(free(females)), "males": len(free(males)),
                                  "matings_under_way": len(fcages & mcages),
                                  "cages_under_way": sorted(fcages & mcages),
                                  "in_experiments": [{"mouse": b["mouse"], "experiment": b["in_experiment"]}
                                                     for b in females + males if b["in_experiment"]],
                                  "_kinds": ((fsex, flabel, falso), (msex, mlabel, malso)),
                                  "female_mice": [b["mouse"] for b in females][:20],
                                  "male_mice": [b["mouse"] for b in males][:20],
                                  "past_breeding_age": [b["mouse"] for b in females + males if b["past_breeding_age"]]})
    for plan in plans:
        p = plan["odds"] * p_sex
        need = matings_needed(count, p, pups * litters, confidence)
        plan.update({"odds": round(plan["odds"], 4), "odds_with_sex": round(p, 4), "matings_on_average": need["expected"],
                     "matings": need["sure"], "litters_per_mating": litters,
                     "wanted_per_litter": round(need["wanted_per_litter"] / litters, 2)
                     if need.get("wanted_per_litter") is not None else None})
        if plan["females"] is not None and need["sure"] is not None:
            to_set_up = max(0, need["sure"] - plan["matings_under_way"])
            plan["to_set_up"] = to_set_up
            plan["females_short"] = max(0, to_set_up - plan["females"])
            plan["males_short"] = max(0, -(-to_set_up // 2) - plan["males"])     # a male with up to two females
    # The same breeders can't be in two matings at once: crosses listed
    # that would need them say so.
    clashes = []
    shown = sorted(plans, key=lambda x: (x["matings"] is None, bool(x.get("also_carries")),
                                         x["matings"] or 0, -x["odds"]))[:12]
    uses: dict[tuple, list[int]] = {}
    for i, plan in enumerate(shown):
        for kind in plan.get("_kinds") or ():
            uses.setdefault(kind, []).append(i)
    for kind, idx in uses.items():
        if len(idx) > 1:
            mice = next(b for b in breeders if (b["sex"], named(b["read_as"]), tuple(b["also_carries"])) == kind)
            sex_word = "females" if kind[0] == "F" else "males"
            clashes.append({"breeders": kind[1] + "".join(f" + {a}" for a in kind[2]), "sex": kind[0],
                            "crosses": [i + 1 for i in idx],
                            "note": f"Crosses {', '.join(str(i + 1) for i in idx)} would use the same {sex_word} "
                                    f"({mice['mouse']}…): each can be in one mating at a time, so set up one of "
                                    "them, or split the breeders between them."})
    for plan in plans:
        plan.pop("_kinds", None)
    plans = shown
    # Crosses whose pups carry nothing beyond the genotype asked come first (sorted above).

    timing = None
    if by is not None:
        set_up_by = born_by - timedelta(days=PAIRING_TO_BIRTH)
        timing = {"born_from": born_from.isoformat() if born_from else None, "born_by": born_by.isoformat(),
                  "set_up_from": max(born_from - timedelta(days=PAIRING_TO_BIRTH), today).isoformat()
                  if born_from else None,
                  "set_up_by": set_up_by.isoformat(),
                  "wean_from": (born_from + timedelta(days=wean_days)).isoformat() if born_from else None,
                  "wean_by": (born_by + timedelta(days=wean_days)).isoformat(),
                  "genotype_by": (born_by + timedelta(days=lab.genotyping_day(session))).isoformat(),
                  "too_late": set_up_by < today}
        if timing["too_late"]:
            timing["note"] = (f"The matings had to be set up by {set_up_by.isoformat()}, before today: pups born now "
                              "will be too young then. Look at availability for mice the lab already has, or a later date.")
    assumptions = [
        "Mendelian inheritance; each locus autosomal and unlinked from the others (not X- or Y-linked).",
        "Each parent's genotype is read from what the lab wrote: an unwritten locus is wild type, a transgene "
        "written without het or hom is hemizygous (het). Mice marked sure: false were read that way; check them.",
        f"Litter size: {litter['source']}; pups that reach weaning: {litter['kept_source']}.",
        (f"{litters} litters counted per mating, as asked" if litters_per_mating else
         f"{litters} litter{'s' if litters > 1 else ''} counted per mating: as many as are born in the window"
         f" (the first {PAIRING_TO_BIRTH} days after setting it up, then one every {LITTER_INTERVAL} days)"
         if born_by else "One litter counted per mating (say by, or litters_per_mating, to count more)")
        + f"; {PAIRING_TO_BIRTH} days from setting a mating up to the birth (19–21 days' gestation), weaning at "
        f"P{wean_days}." + (" Half the pups taken as each sex." if sex else ""),
        f"matings: enough to have {count} with {confidence:.0%} confidence (binomial); matings_on_average: "
        "enough on average. males_short counts a male with up to two females at a time (a trio).",
        "A breeder that carries more than the loci asked (also_carries) can pass that on too; those crosses "
        "come after the ones that don't.",
        "matings_under_way: Breeding cages that already hold a female and a male of the cross, counted as matings "
        "already set up (to_set_up is what is left); their litters born before now aren't counted. females and "
        "males count only the breeders free to pair: not in a Breeding cage now, and not in a planned, active or "
        "paused experiment (in_experiments lists those). clashes: crosses listed that would need the same breeders.",
    ]
    for strain in strains:
        assumptions.insert(0, f"{strain} is a strain (a background), not a locus: an inbred strain breeds true, so "
                              f"both parents are {strain} and every pup is (odds 1 for it). "
                              + ("Only breeders whose genotype names it were counted." if not crosses else
                                 "The crosses given are taken to be of it."))
    if not plans:
        assumptions.append("No pair of the lab's breeders (living mice in Breeder and Breeding cages) can give this "
                           "genotype as their genotypes read: best_possible lists the crosses that would.")
    best = best_crosses(want)
    for cross in best:
        cross["female"] = "; ".join(filter(None, [*strains, cross["female"]]))
        cross["male"] = "; ".join(filter(None, [*strains, cross["male"]]))
    return {"want": want, "strains": strains, "sex": sex or "any", "count": count, "litter_size": litter["mean"],
            "pups_weaned_per_litter": round(pups, 2), "litters_per_mating": litters, "crosses": plans[:12],
            "clashes": clashes,
            "best_possible": best, "timing": timing,
            "breeders": breeders[:200], "assumptions": assumptions}


# ---------------------------------------------------------------- randomisation

def randomize(session, animals: list, groups: list, seed: int | None = None, by: str = "litter",
              experiment: int | None = None) -> dict:
    """Mice into named groups, balanced, seeded; changes nothing."""
    from . import actions
    from .services import mouse_is_active
    if not isinstance(animals, list) or not animals:
        raise ValueError("animals is a list of mice: their numbers or references.")
    if not isinstance(groups, list) or len(groups) < 2:
        raise ValueError("groups is a list of at least two group names (or {name, size}).")
    if by not in ("litter", "cage"):
        raise ValueError("by is litter or cage.")
    mice, seen, left = [], set(), []
    for ref in animals[:2000]:
        try:
            m = actions.find(session, "mouse", ref)
        except actions.ActionError as e:
            raise ValueError(str(e)) from None
        if m.id in seen:
            continue
        seen.add(m.id)
        if mouse_is_active(m):
            mice.append(m)
        else:
            left.append(m.mouse_id)
    specs = [g if isinstance(g, dict) else {"name": str(g)} for g in groups]
    names = [str(g.get("name") or "").strip() for g in specs]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("Each group needs a name of its own.")
    seed = int(seed) if seed not in (None, "") else secrets.randbelow(1_000_000)
    given = [g.get("size") for g in specs]
    if any(s is not None for s in given):
        if any(not isinstance(s, int) or s < 1 for s in given):
            raise ValueError("Give every group a size (a whole number), or none.")
        sizes = given
    else:
        sizes = split_sizes(len(mice), names, random.Random(seed + 1))
        if min(sizes) < 1:
            raise ValueError("Fewer mice than groups.")
    weights = latest_weights(session, [m.id for m in mice], date.today())
    rows = {}
    for m in mice:
        stratum = (m.litter.litter_id if m.litter else "") if by == "litter" else (m.cage.cage_id if m.cage else "")
        rows[m.id] = {"key": m.mouse_id, "sex": (m.gender or "").upper(), "stratum": stratum,
                      "weight": weights[m.id][0] if m.id in weights else None, "mouse": m}
    result = allocate(list(rows.values()), [{"name": n, "size": s} for n, s in zip(names, sizes)], seed)

    def shown(a: dict) -> dict:
        m = a["mouse"]
        return {"mouse": m.mouse_id, "ref": {"kind": "mouse", "id": m.id}, "sex": a["sex"],
                "litter": m.litter.litter_id if m.litter else None, "cage": m.cage.cage_id if m.cage else None,
                "weight_g": a["weight"]}

    allocation = [{"group": g["name"], "mice": sorted(a["key"] for a in g["animals"]),
                   "animals": sorted((shown(a) for a in g["animals"]), key=lambda x: x["mouse"])}
                  for g in result["groups"]]
    out = {"seed": seed, "by": by, "allocation": allocation, "spares": sorted(a["key"] for a in result["spares"]),
           "left_out_not_alive": left, "balance": balance(result["groups"]),
           "method": f"Stratified minimisation (Pocock and Simon): the mice sex by sex and {by} by {by}, heaviest "
                     "first, each to the group with room that leaves the groups least unequal in sex, then "
                     f"{by}, then mean weight, then size; the seed breaks ties (and picks any spares). The same "
                     "mice, groups and seed give the same allocation.",
           # experiment_new's groups, as they are: a new design takes these and the seed straight.
           "groups": [{"name": g["group"], "animals": [a["ref"] for a in g["animals"]]} for g in allocation],
           "note": "Nothing has changed. For a new design, pass groups and seed as they are to experiment_new (its "
                   "groups and seed fields). For an existing experiment, propose experiment_add_animals, one change "
                   "per group (propose, when experiment is given)."}
    if experiment is not None:
        out["propose"] = [{"action": "experiment_add_animals", "target": {"kind": "experiment", "id": int(experiment)},
                           "fields": {"mice": g["mice"], "group": g["group"]}} for g in allocation if g["mice"]]
    return out


# ---------------------------------------------------------------- power with the lab's own SD

def lab_sd(session, experiment_id: int, day=None) -> dict:
    """The pooled within-group SD of an experiment's readout on one day: the
    day asked (its day number or date), or the one with most readings."""
    from . import experiments as xp
    from .models import Experiment
    exp = session.get(Experiment, int(experiment_id))
    place = xp.place_for(session, exp.db or "colony") if exp is not None else None
    if place is None:
        raise LookupError(f"There is no experiment {experiment_id}.")
    table = xp.readout_table(session, exp, place)
    readout = table["readout"]
    if readout["kind"] == "fraction":
        raise ValueError(f"{readout['label']} is a count of animals, which has no SD: give the SD, or the "
                         "proportions, yourself.")
    if not table["dates"]:
        raise ValueError(f"Experiment {exp.name} has no readings yet.")

    def groups_on(i: int) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for r in table["rows"]:
            if r["values"][i] is not None:
                out.setdefault(r["group"] or "(no group)", []).append(r["values"][i])
        return out

    if day not in (None, ""):
        wanted = str(day)
        matches = [i for i, (d, n) in enumerate(zip(table["dates"], table["days"]))
                   if d == wanted[:10] or str(n) == wanted]
        if not matches:
            raise ValueError(f"Experiment {exp.name} has no readings on {day}; it has days "
                             f"{', '.join(str(n) for n in table['days'] if n is not None)}.")
        index = matches[0]
    else:
        index = max(range(len(table["dates"])), key=lambda i: (pooled_sd(list(groups_on(i).values()))[1], i))
    on = groups_on(index)
    sd, df = pooled_sd(list(on.values()))
    if sd is None:
        raise ValueError(f"Experiment {exp.name} has too few readings on that day for an SD (two or more in a group).")
    return {"sd": round(sd, 4), "df": df, "experiment": {"id": exp.id, "name": exp.name},
            "readout": readout["label"], "unit": readout.get("unit", ""), "date": table["dates"][index],
            "day": table["days"][index], "groups": {g: len(v) for g, v in on.items()},
            "how": "The within-group SD pooled over the groups on that day."}


def power_analysis(session, k: int = 2, difference=None, sd=None, means=None, d=None, alpha: float = 0.05,
                   target: float = 0.8, attrition: float = 0.0, experiment: int | None = None, day=None) -> dict:
    source = None
    if sd is None and d is None and experiment is not None:
        source = lab_sd(session, experiment, day)
        sd = source["sd"]
    out = power(k, difference, sd, means, d, alpha, target, attrition)
    if source:
        out["sd_from"] = source
    elif sd is not None:
        out["sd_from"] = "given"
    return out


# ---------------------------------------------------------------- reorder forecast

def reorder_forecast(session, within_days: int = 30, lead_days: int = 14, window_days: int = 180,
                     now: datetime | None = None) -> dict:
    """Stock kept as a number: how fast it is used (from the falls in its
    quantity in the change history), when it runs out, when to reorder."""
    import json as _json
    from . import inventory_service as isvc
    from .models import AuditEntry, InventoryItem
    now = now or datetime.utcnow()
    today = now.date()
    since = now - timedelta(days=window_days)
    modules = {m.id: m for m in isvc.list_modules(session) if m.kind != "orders"}
    items = []
    for item in session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk.in_(list(modules) or [0]))):
        quantity = isvc.amount(item.quantity)
        if quantity is not None and (item.status or "").strip().lower() not in isvc.TERMINAL_STATUSES:
            items.append((item, quantity))
    changes: dict[int, list] = {}
    ids = [i.id for i, _q in items]
    for start in range(0, len(ids), 500):
        for e in session.scalars(select(AuditEntry).where(
                AuditEntry.table_name == "inventory_items", AuditEntry.action == "update",
                AuditEntry.record_id.in_(ids[start:start + 500]), AuditEntry.changed_at >= since)
                .order_by(AuditEntry.id)):
            try:
                pair = (_json.loads(e.changes_json or "{}").get("changes") or {}).get("quantity")
            except ValueError:
                pair = None
            if pair:
                before, after = isvc.amount(pair[0]), isvc.amount(pair[1])
                if before is not None and after is not None:
                    changes.setdefault(e.record_id, []).append((e.changed_at, before, after))
    out, unknown = [], []
    for item, quantity in items:
        start = max(since, item.created_at or since)
        rate = usage_rate(changes.get(item.id, []), start, now)
        if rate is None:
            unknown.append({"ref": {"kind": "inventory_item", "id": item.id}, "name": item.name or "",
                            "database": modules[item.module_id_fk].label, "number": item.number})
            continue
        days_left = quantity / rate["per_day"] if rate["per_day"] else None
        if days_left is None or days_left > within_days:
            continue
        runs_out = today + timedelta(days=int(days_left))
        reorder_by = runs_out - timedelta(days=lead_days)
        module = modules[item.module_id_fk]
        out.append({"database": module.label, "number": item.number, "ref": {"kind": "inventory_item", "id": item.id},
                    "name": item.name or "", "quantity": quantity, "unit": item.unit or "", "low_at": item.low_at,
                    "status": item.status or "", "used_per_week": round(rate["per_day"] * 7, 3),
                    "days_left": round(days_left, 1), "runs_out_on": runs_out.isoformat(),
                    "reorder_by": reorder_by.isoformat(), "reorder_now": reorder_by <= today,
                    "history": {"used": rate["used"], "over_days": rate["days"], "falls": rate["falls"]},
                    "url": _base() + url_for("inventory.module", key=module.key, open=item.id)})
    out.sort(key=lambda x: x["days_left"])
    return {"today": today.isoformat(), "within_days": within_days, "lead_days": lead_days, "items": out,
            "without_enough_history": unknown,
            "method": f"Use is each fall in the quantity over the last {window_days} days (rises are restocks), over "
                      "the days the item was watched (from when it was added, if later); days left = quantity ÷ "
                      f"use a day; reorder {lead_days} days before it runs out. Items with no fall, or under a week "
                      "of history, are left out."}


# ---------------------------------------------------------------- the weekly review

SEVERITY = ("high", "medium", "low", "info")


def lab_checks(session, weight_loss_pct: float | None = None, breeder_weeks: int | None = None, unsigned_days: int = 14,
               expiring_days: int = 30, empty_days: int = 7, scope: str = "lab", today: date | None = None) -> dict:
    """The weekly review's checks: each its id, severity, count and items
    (with links, and `propose`, the change that would fix one, where the
    action catalogue has it). `weight_loss_pct` left out: the lab's own
    (Settings → General)."""
    from . import access, lookup
    from .services import SAC_THRESHOLD_WEEKS
    today = today or date.today()
    breeder_weeks = breeder_weeks or SAC_THRESHOLD_WEEKS
    if scope not in ("lab", "mine"):
        raise ValueError("scope is lab or mine.")
    me = access.username()
    mine = scope == "mine"
    features = lab.request_features()
    checks = []

    def check(cid: str, title: str, severity: str, items: list, fix: str = "", note: str = ""):
        checks.append({"id": cid, "title": title, "severity": severity, "count": len(items), "items": items,
                       "fix": fix, **({"note": note} if note else {})})

    overdue = [i for i in lookup.due(session, 0) if i["status"] == "overdue"
               and (not mine or i.get("owner") in ("", me))]
    check("overdue_jobs", "Overdue jobs", "high", overdue,
          "Each item's propose records it done; send the ones the person did as one proposal.")
    # The animals that may need a look (app/welfare.py): what the records show, never a diagnosis.
    from . import welfare
    for c in welfare.checks(session, today, me if mine else None, weight_loss_pct, features):
        for item in c["items"]:
            for key in ("url", "experiment_url"):
                if item.get(key):
                    item[key] = _base() + item[key]
        check(c["id"], c["title"], c["severity"], c["items"], c["fix"])
    if features.get("colony", True):
        check("empty_cages", "Empty cages still set up (a purpose or a rack place)", "low",
              _empty_cages(session, today, empty_days, me if mine else None),
              "Retire them: each item's propose clears the purpose and takes the cage out of its rack, as Retire does.")
        check("old_breeders", f"Breeders {breeder_weeks} weeks or older", "medium",
              _old_breeders(session, today, breeder_weeks, me if mine else None),
              "Plan their replacements (breeding_plan); retiring a breeder is the person's decision.")
    if features.get("notebook", True):
        items, note = _unsigned_pages(session, me, today, unsigned_days)
        check("unsigned_pages", f"Your notebook pages unsigned for more than {unsigned_days} days", "low", items,
              "Signing needs the person: they sign each page in the notebook.", note)
    from . import inventory_service as isvc
    attention = isvc.attention_items(session, days=expiring_days, limit=500)
    supplies = [{"database": a["module"], "number": a["number"], "ref": {"kind": "inventory_item", "id": a["id"]},
                 "name": a["name"], "status": a["status"] or "", "quantity": a["quantity"],
                 "expires_on": a["expires_on"].isoformat() if a["expires_on"] else None, "expiry": a["expiry"],
                 "low": a["low"], "url": _base() + url_for("inventory.module", key=a["key"], open=a["id"])}
                for a in attention]
    check("expired_reagents", "Reagents past their expiry date", "high", [s for s in supplies if s["expiry"] == "expired"],
          "Discard or replace them; nothing is proposed for this.")
    check("expiring_reagents", f"Reagents expiring within {expiring_days} days", "medium",
          [s for s in supplies if s["expiry"] == "soon"], "Order replacements in time (reorder_forecast).")
    check("low_stock", "Reagents marked low", "medium", [s for s in supplies if s["low"] and s["expiry"] != "expired"],
          "Reorder (reorder_forecast says when each runs out).")
    from .models import Proposal
    waiting = session.scalars(select(Proposal).where(Proposal.owner_username == me, Proposal.status == "pending",
                                                     (Proposal.expires_at.is_(None)) | (Proposal.expires_at > datetime.utcnow()))
                              .order_by(Proposal.id)).all()
    check("proposals_waiting", "Your proposals waiting for approval", "info",
          [{"id": p.id, "summary": p.summary or "", "source": p.source or "",
            "made": p.created_at.isoformat(timespec="seconds") + "Z",
            "url": _base() + url_for("proposals.page") + f"#proposal-{p.id}"} for p in waiting],
          "The person approves or discards each on Proposed changes.")
    checks.sort(key=lambda c: (SEVERITY.index(c["severity"]), c["id"]))
    return {"today": today.isoformat(), "scope": scope, "checks": checks,
            "summary": {s: _distinct(c for c in checks if c["severity"] == s) for s in SEVERITY},
            "summary_note": "Records needing attention at each severity, each once though it may be in two "
                              "checks (expired and overdue, expiring and low); each check keeps its own count."}


def _distinct(checks) -> int:
    """How many items these checks hold, a record in two of them once: by
    its reference, as often as the check that has it most (two overdue jobs
    on one cage are two)."""
    most: dict = {}
    for c in checks:
        seen: dict = {}
        for n, item in enumerate(c["items"]):
            ref = item.get("ref")
            key = (ref.get("kind"), ref.get("id")) if isinstance(ref, dict) else (c["id"], n)
            seen[key] = seen.get(key, 0) + 1
        for key, times in seen.items():
            most[key] = max(most.get(key, 0), times)
    return sum(most.values())


def _empty_cages(session, today: date, days: int, owner: str | None) -> list[dict]:
    from sqlalchemy.orm import selectinload
    from .models import CageRecord
    from .services import cage_is_active
    stmt = select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack)).where(
        (func.coalesce(CageRecord.purpose, "") != "") | CageRecord.rack_id_fk.is_not(None),
        CageRecord.created_at < datetime.combine(today - timedelta(days=days), datetime.min.time()))
    if owner:
        stmt = stmt.where(CageRecord.owner == owner)
    out = []
    from .positions import natural_sorted
    for c in natural_sorted(session.scalars(stmt), key=lambda c: c.cage_id):
        if cage_is_active(c):
            continue
        ref = {"kind": "cage", "id": c.id}
        out.append({"cage": c.cage_id, "ref": ref, "purpose": c.purpose or "", "rack": c.rack.name if c.rack else None,
                    "owner": c.owner or "", "url": _cage_url(c),
                    "propose": {"action": "cage_change", "target": ref, "fields": {"purpose": "", "rack": ""}}})
    return out


def _old_breeders(session, today: date, weeks: int, owner: str | None) -> list[dict]:
    from sqlalchemy.orm import selectinload
    from .models import CageRecord, LitterRecord, MouseRecord
    from .services import holds_breeders, mouse_is_active
    stmt = select(MouseRecord).join(CageRecord, MouseRecord.cage_id_fk == CageRecord.id) \
        .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id) \
        .options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter)) \
        .where(MouseRecord.date_of_death.is_(None), LitterRecord.date_of_birth <= today - timedelta(weeks=weeks))
    if owner:
        stmt = stmt.where(MouseRecord.owner == owner)
    out = []
    for m in session.scalars(stmt.order_by(LitterRecord.date_of_birth, MouseRecord.mouse_id)):
        if not (mouse_is_active(m) and holds_breeders(m.cage.purpose)):
            continue
        out.append({"mouse": m.mouse_id, "ref": {"kind": "mouse", "id": m.id}, "sex": m.gender or "",
                    "genotype": m.genotype or "", "born": m.litter.date_of_birth.isoformat(),
                    "age_weeks": (today - m.litter.date_of_birth).days // 7, "cage": m.cage.cage_id,
                    "purpose": m.cage.purpose, "owner": m.owner or "", "url": _mouse_url(m)})
    return out


def _unsigned_pages(session, me: str, today: date, days: int) -> tuple[list[dict], str]:
    """The person's own pages, unchanged for `days` and not signed, once
    they sign pages at all (signing is each person's choice)."""
    from . import signatures
    from .models import NotebookPage, NotebookTab, RecordSignature
    first = session.scalar(select(func.min(RecordSignature.signed_at)).where(
        RecordSignature.username == me, RecordSignature.action == "sign"))
    if first is None:
        return [], "You haven't signed a notebook page yet, so this isn't checked."
    cutoff = datetime.combine(today - timedelta(days=days), datetime.min.time())
    out = []
    for p in session.scalars(select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id).where(
            NotebookTab.owner_username == me, NotebookPage.updated_at < cutoff, NotebookPage.updated_at >= first,
            func.length(func.coalesce(NotebookPage.body, "")) > 0).order_by(NotebookPage.updated_at)):
        if signatures.is_signed(session, p.id):
            continue
        out.append({"page": p.title or "", "ref": {"kind": "notebook_page", "id": p.id},
                    "changed": p.updated_at.date().isoformat(), "url": _base() + url_for("notebook", page=p.id)})
    return out, "Pages changed since you first signed one."


# ---------------------------------------------------------------- an experiment, for its methods

SPECIES = {"mouse": "Mus musculus (mouse)", "fish": "Danio rerio (zebrafish)", "fly": "Drosophila melanogaster",
           "worm": "Caenorhabditis elegans"}


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"min": round(ordered[0], 1), "max": round(ordered[-1], 1), "median": round(median, 1),
            "mean": round(sum(ordered) / len(ordered), 1), "n": len(ordered)}


def experiment_summary(session, experiment_id: int) -> dict:
    """The facts of an experiment as the records have them, for a methods
    paragraph, and the ARRIVE 2.0 Essential 10."""
    import json as _json
    from . import experiment_steps as xs
    from . import experiments as xp
    from .models import Experiment
    exp = session.get(Experiment, int(experiment_id))
    place = xp.place_for(session, exp.db or "colony") if exp is not None else None
    if place is None:
        raise LookupError(f"There is no experiment {experiment_id}.")
    subs = xp.subjects(session, exp, place)
    table = xp.readout_table(session, exp, place, subs)
    start = exp.start_date

    groups: dict[str, dict] = {}
    sexes: dict[str, int] = {}
    genotypes: dict[str, int] = {}
    ages, ended = [], 0
    for s in subs:
        n = s.start if s.start is not None else 1
        g = groups.setdefault(s.group or "(no group)", {"group": s.group or "(no group)", "n": 0, "sex": {}})
        g["n"] += n
        g["sex"][s.sex or "?"] = g["sex"].get(s.sex or "?", 0) + n
        sexes[s.sex or "?"] = sexes.get(s.sex or "?", 0) + n
        genotypes[s.genotype or "(not written)"] = genotypes.get(s.genotype or "(not written)", 0) + n
        rec = s.record
        if s.kind == "mouse":
            born = rec.litter.date_of_birth if rec.litter else None
            if rec.date_of_death is not None and (start is None or rec.date_of_death >= start):
                ended += 1
        elif s.kind == "fish":
            born = rec.date_of_fertilization
        else:
            born = None
        if born and start:
            ages.append((start - born).days)
    weight_at_start = None
    if place.subject_kind == "mouse" and start:
        weights = latest_weights(session, [s.id for s in subs], start)
        weight_at_start = _spread([w for w, _d in weights.values()])
    animals = {"species": SPECIES.get(place.family, place.label), "database": place.label,
               "total": sum(g["n"] for g in groups.values()), "groups": list(groups.values()), "sex": sexes,
               "genotypes": genotypes, "age_at_start_days": _spread(ages), "weight_at_start_g": weight_at_start,
               "ended": ended, "unit": place.noun}

    procedures = []
    for st in exp.steps:
        info = xs.step_dict(st, place.family, session)
        done = sorted(r.done_on for r in st.records)
        lots, expired = [], []
        for r in st.records:
            try:
                reagent = _json.loads(r.reagent or "{}")
            except ValueError:
                reagent = {}
            if isinstance(reagent, dict) and reagent.get("lot"):
                lot = f"{reagent.get('name') or ''} lot {reagent['lot']}".strip()
                if lot not in lots:
                    lots.append(lot)
                # A lot used after its expiry date, on the day it was used.
                if reagent.get("expires") and reagent["expires"] < r.done_on.isoformat():
                    said = f"{lot}, expired {reagent['expires']}, used {r.done_on.isoformat()}"
                    if said not in expired:
                        expired.append(said)
        what = " ".join(filter(None, [info["agent"] or info["kind_label"], info["dose"], info["route"]]))
        procedures.append({"what": what, "kind": info["kind_label"], "agent": info["agent"], "dose": info["dose"],
                           "route": info["route"], "concentration": info["concentration"], "days": info["days_label"],
                           "group": info["group"], "notes": info["notes"], "done": len(done),
                           "first_done": done[0].isoformat() if done else None,
                           "last_done": done[-1].isoformat() if done else None, "lots": lots,
                           **({"expired_lots": expired} if expired else {})})

    readout = table["readout"]
    measured = [d for i, d in enumerate(table["dates"]) if any(r["values"][i] is not None for r in table["rows"])]
    readouts = {"label": readout["label"], "unit": readout.get("unit", ""), "kind": readout["kind"],
                "days_measured": len(measured), "first": measured[0] if measured else None,
                "last": measured[-1] if measured else None}
    by_day = table["stats"]["raw"]
    tests = sorted({d["test"] for d in by_day if d["test"]})
    last = next((d for d in reversed(by_day) if d["test"]), None)
    last_day = None
    if last:
        last_day = {"date": last["date"], "day": last["day"], "test": last["test"],
                    "p": round(last["p"], 4) if last["p"] is not None else None, "groups": last["groups"]}
        index = table["dates"].index(last["date"])
        samples = {}
        for r in table["rows"]:
            if r["values"][index] is not None:
                samples.setdefault(r["group"] or "All", []).append(r["values"][index])
        if readout["kind"] != "fraction" and len(samples) == 2 and all(len(v) >= 2 for v in samples.values()):
            (ga, a), (gb, b) = sorted(samples.items())
            last_day["difference"] = {**difference_ci(a, b), "of": f"{ga} minus {gb}"}
    # Other measures a plate filled beside the main readout (an ELISA in a tumour experiment): secondary.
    others = []
    for m in xp.measures_of(exp):
        mt = xp.readout_table(session, exp, place, subs, m)
        days = [d for i, d in enumerate(mt["dates"]) if any(r["values"][i] is not None for r in mt["rows"])]
        others.append({"label": m["label"], "unit": m["unit"], "days_measured": len(days),
                       **({"formula": m["formula"], "expression": m["expression"],
                           **({"control": m["control"]} if m.get("per_group") else {})} if m.get("formula") else {}),
                       "first": days[0] if days else None, "last": days[-1] if days else None,
                       "by_day": [{"date": d["date"], "day": d["day"], "test": d["test"],
                                   "p": round(d["p"], 4) if d["p"] is not None else None, "groups": d["groups"]}
                                  for d in mt["stats"]["raw"]]})
    if others:
        readouts["main"] = True
        readouts["other_measures"] = others
    text = "\n".join(filter(None, [exp.description, exp.treatment_plan, *(p["notes"] for p in procedures)]))
    facts = {"animals": animals, "procedures": procedures, "readouts": readouts,
             "statistics": {"tests": tests, "by_day": [{"date": d["date"], "day": d["day"], "test": d["test"],
                                                         "p": round(d["p"], 4) if d["p"] is not None else None,
                                                         "groups": d["groups"]} for d in by_day],
                            "last_day": last_day,
                            "method": "Each day compared on its own: Welch's t-test for two groups, one-way ANOVA "
                                      "for more, a χ² test for counts alive; not corrected for the number of days."},
             "text": text}
    return {"experiment": {"id": exp.id, "name": exp.name, "status": exp.status, "owner": exp.owner_username,
                           "database": place.label, "start_date": start.isoformat() if start else None,
                           "end_date": exp.end_date.isoformat() if exp.end_date else None,
                           "description": exp.description or "", "treatment_plan": exp.treatment_plan or "",
                           "url": _base() + xp.page_url(exp)},
            **{k: v for k, v in facts.items() if k != "text"}, "arrive": arrive(facts),
            "note": "Only what the records say. Anything under missing is for the person to supply; never fill it in."}
