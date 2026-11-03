"""Comparing an experiment's groups on each day of its readout.

The same methods as the notebook's data sheets (frontend/src/blocks/stats.js),
in Python: Welch's t-test for two groups, one-way ANOVA for more, with
p-values from the regularised incomplete beta function (Numerical Recipes,
3rd ed., §6.4). A count of those still alive (a survival, a phenotype) is
compared as alive against not, by a χ² test of the groups' totals.

Each day is tested on its own and not corrected for the number of days:
the page says so.
"""
from __future__ import annotations

import math


def _betacf(a: float, b: float, x: float) -> float:
    fpmin, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = d if abs(d) > fpmin else fpmin
        c = 1 + aa / c
        c = c if abs(c) > fpmin else fpmin
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = d if abs(d) > fpmin else fpmin
        c = 1 + aa / c
        c = c if abs(c) > fpmin else fpmin
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < eps:
            break
    return h


def inc_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return bt * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x) / b


def t_two_sided(t: float, df: float) -> float:
    if not math.isfinite(t) or df <= 0:
        return float("nan")
    return inc_beta(df / (df + t * t), df / 2, 0.5)


def f_upper(f: float, d1: float, d2: float) -> float:
    if not math.isfinite(f) or f < 0:
        return float("nan")
    return inc_beta(d2 / (d2 + d1 * f), d2 / 2, d1 / 2)


def _gamma_q(a: float, x: float) -> float:
    """The upper regularised incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    if x < a + 1:
        term = total = 1 / a
        n = a
        for _ in range(500):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 3e-14:
                break
        return 1 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b, c, d = x + 1 - a, 1 / 1e-300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = d if abs(d) > 1e-300 else 1e-300
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 3e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_upper(x: float, df: int) -> float:
    return _gamma_q(df / 2, x / 2)


def _mean(xs):
    return sum(xs) / len(xs)


def _var(xs):
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch(a: list[float], b: list[float]) -> dict:
    va, vb = _var(a) / len(a), _var(b) / len(b)
    if va + vb == 0:
        return {"test": "Welch's t-test", "p": 1.0 if _mean(a) == _mean(b) else 0.0}
    t = (_mean(a) - _mean(b)) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / ((va ** 2 / (len(a) - 1) if va else 0) + (vb ** 2 / (len(b) - 1) if vb else 0) or 1e-300)
    return {"test": "Welch's t-test", "p": t_two_sided(t, df)}


def anova(groups: list[list[float]]) -> dict:
    everything = [x for g in groups for x in g]
    grand = _mean(everything)
    k, n = len(groups), len(everything)
    ssb = sum(len(g) * (_mean(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - _mean(g)) ** 2 for x in g) for g in groups)
    if ssw == 0:
        return {"test": "One-way ANOVA", "p": 1.0 if ssb == 0 else 0.0}
    f = (ssb / (k - 1)) / (ssw / (n - k))
    return {"test": "One-way ANOVA", "p": f_upper(f, k - 1, n - k)}


def chi_square(counts: list[tuple[float, float]]) -> dict:
    """(yes, no) per group: are the proportions the same?"""
    rows = [(y, n) for y, n in counts if y + n > 0]
    total = sum(y + n for y, n in rows)
    yes = sum(y for y, _n in rows)
    if len(rows) < 2 or total == 0 or yes in (0, total):
        return {"test": "χ² test", "p": 1.0}
    x2 = 0.0
    for y, n in rows:
        size = y + n
        for observed, share in ((y, yes / total), (n, 1 - yes / total)):
            expected = size * share
            if expected:
                x2 += (observed - expected) ** 2 / expected
    return {"test": "χ² test", "p": chi2_upper(x2, len(rows) - 1)}


def stars(p: float) -> str:
    if p is None or not math.isfinite(p):
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def by_day(table: dict, percent: bool = False) -> list[dict]:
    """For each day of the readout: each group's n, mean and SEM, and the
    test between the groups, when at least two groups have enough."""
    fraction = table["readout"]["kind"] == "fraction"
    groups = sorted({r["group"] for r in table["rows"]})
    out = []
    for i, day_iso in enumerate(table["dates"]):
        summaries, samples, counts = [], [], []
        for g in groups:
            # An animal left out of the analysis (its reason in "left_out") is not counted.
            rows = [r for r in table["rows"] if r["group"] == g and r["values"][i] is not None and not r.get("left_out")]
            vals = [(r["pct"][i] if percent else r["values"][i]) for r in rows]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            if fraction:
                alive = sum(r["values"][i] for r in rows)
                start = sum(r["start"] or 0 for r in rows)
                counts.append((alive, max(0.0, start - alive)))
                summaries.append({"group": g or "All", "n": start, "value": round(alive / start * 100, 1) if start else None,
                                  "alive": alive})
            else:
                m = _mean(vals)
                sem = math.sqrt(_var(vals) / len(vals)) if len(vals) > 1 else None
                summaries.append({"group": g or "All", "n": len(vals), "value": round(m, 3),
                                  "sem": round(sem, 3) if sem is not None else None})
                samples.append(vals)
        result = None
        if fraction and len(counts) >= 2 and all(y + n for y, n in counts):
            result = chi_square(counts)
        elif not fraction and len(samples) >= 2 and all(len(s) >= 2 for s in samples):
            result = welch(*samples) if len(samples) == 2 else anova(samples)
        out.append({"date": day_iso, "day": table["days"][i], "groups": summaries,
                    "test": result["test"] if result else "", "p": result["p"] if result else None,
                    "stars": stars(result["p"]) if result else ""})
    return out


def logrank(table: dict) -> dict | None:
    """Survival over the whole experiment, group against group: the
    log-rank test on the counts each day (a survival readout: how many of
    each group's start are alive). An animal's or a vial's deaths on a day
    are the drop since its last count; a day it was not counted keeps its
    last count. None when fewer than two groups have a start and a count."""
    groups: dict[str, list] = {}
    for r in table["rows"]:
        if r.get("left_out") or not r.get("start"):
            continue
        groups.setdefault(r["group"] or "All", []).append(r)
    if len(groups) < 2:
        return None
    days = len(table["dates"])
    # Each group's alive count on each day, carried forward over a day not counted.
    alive: dict[str, list[float]] = {}
    for g, rows in groups.items():
        series = []
        for i in range(days):
            total = 0.0
            for r in rows:
                last = r["start"]
                for j in range(i + 1):
                    if r["values"][j] is not None:
                        last = min(last, r["values"][j])
                total += last
            series.append(total)
        alive[g] = series
    start = {g: float(sum(r["start"] for r in rows)) for g, rows in groups.items()}
    observed = {g: 0.0 for g in groups}
    expected = {g: 0.0 for g in groups}
    var2 = 0.0
    names = sorted(groups)
    for i in range(days):
        at_risk = {g: (alive[g][i - 1] if i else start[g]) for g in names}
        deaths = {g: max(0.0, at_risk[g] - alive[g][i]) for g in names}
        n = sum(at_risk.values())
        d = sum(deaths.values())
        if n <= 0 or d <= 0:
            continue
        for g in names:
            observed[g] += deaths[g]
            expected[g] += d * at_risk[g] / n
        if len(names) == 2 and n > 1:
            n1, n2 = at_risk[names[0]], at_risk[names[1]]
            var2 += d * (n1 / n) * (n2 / n) * (n - d) / (n - 1)
    if not any(observed.values()):
        return {"test": "Log-rank test", "p": 1.0, "stars": "ns", "groups": [
            {"group": g, "start": start[g], "deaths": 0, "expected": 0} for g in names]}
    if len(names) == 2 and var2 > 0:
        x2 = (observed[names[0]] - expected[names[0]]) ** 2 / var2
    else:
        x2 = sum((observed[g] - expected[g]) ** 2 / expected[g] for g in names if expected[g] > 0)
    p = chi2_upper(x2, len(names) - 1)
    return {"test": "Log-rank test", "p": p, "stars": stars(p), "groups": [
        {"group": g, "start": start[g], "deaths": round(observed[g], 1), "expected": round(expected[g], 1)} for g in names]}
