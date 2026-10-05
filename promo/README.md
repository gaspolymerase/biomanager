# Launch plan

How BioManager goes public: an announcement, then one feature a day for
three weeks on Bilibili, Xiaohongshu, X, Facebook and LinkedIn, each with
a short clip, and a review after the first week. Built on a colleague's
advice: post at a fixed time every day, one highlight each (what others
charge for and BioManager does free, or what is especially handy), make it
good-looking enough that people want to click, put the manual on arXiv, and
count who actually uses it.

| | |
| --- | --- |
| Day 0 | **Thu 1 Oct 2026**, with 1.0 (China's National Day holiday starts that day, so Chinese platforms are quieter in week one) |
| Every day | **08:30 Beijing** (the morning commute, for Xiaohongshu and Bilibili) = 20:30 New York the evening before (X and LinkedIn's evening scroll); a day's date is its Beijing date |
| Days | 0 announcement · 1–7 the strongest features · review · 8–20 the rest · 20 thank-you |
| Calendar | [`posts.json`](posts.json): every day's theme, clip, and text for each platform |

## Before day 0

- [x] **One public repository**: `gaspolymerase/biomanager` holds the
  code, the releases, the issues and the website (`site/`, at
  biomanager.org). The old biomanager-app is archived:
  it redirects, and holds only 0.10.1 for desktop apps from before the move.
- [x] **A PostHog project** (US, project 638885), *Discard client IP data*
  on, its public key in `PROJECT_KEY` in `app/telemetry.py`. Counts start
  with the first release that has it (1.0.1).
- [x] **The version to announce**: 1.0.
- [ ] **Accounts** on the five platforms with the same name and avatar
  (the app icon), a one-line bio, and the website in each profile.
  Xiaohongshu posts write the address out (it isn't clickable there).
- [ ] **A Zenodo DOI**: connect the repository to Zenodo so every release
  is citable from day 0.
- [ ] **Visits to the website.** GitHub Pages counts nothing; the links
  carry `utm_source` per platform, so add a counter that reads them
  (GoatCounter is free and cookieless) to know which platform works.
- [ ] Record every clip and build every pack (the readiness task does both on 30 Sep).

## Every day

At 07:45 a scheduled task builds the day's **pack** in
`promo/out/packs/<date>-day<N>/`: `post.md` with the text for each
platform (links tagged, lengths checked) and the clip in each shape:

| Platform | Video | Text |
| --- | --- | --- |
| X | `x.mp4` 16:9, English title | ≤ 280, one link |
| LinkedIn, Facebook | `linkedin.mp4`, `facebook.mp4` | the longer English text |
| Bilibili | `bilibili.mp4` 16:9, Chinese title; cover `bilibili-cover.png` | title ≤ 80, description with links, tags |
| Xiaohongshu | `xhs.mp4` 3:4 with three points; cover `xhs-cover.png` | title ≤ 20, text, hashtags, no link |

Post them at 08:30 Beijing, 20:30 New York (or schedule a week at a time: Bilibili and
Xiaohongshu's creator centres schedule posts, and Buffer's free plan covers
X, LinkedIn and Facebook). Then answer yesterday's comments. Nothing
posts by itself.

## The clips

`scripts/feature-clips.py` records each one from a fresh demo lab: a
cursor you can follow, typing at a person's pace, and a smooth zoom onto
what matters, framed on a gradient with the title (the look of Screen
Studio, OpenScreen or Recordly, but re-recordable after every release in
one command). The walks are in `promo/clips/`. For a longer Bilibili video
with a voice, record one by hand with one of those tools.

```bash
python scripts/feature-clips.py promo/out              # all
python scripts/feature-clips.py promo/out import --quick   # one, English only, to check
python scripts/post-pack.py --all                      # every day's pack
```

## The paper

Write the user manual as a paper for arXiv (q-bio.QM, cross-listed to
cs.DB): what each database does, the configurable organism engine, how
experiments, notebook and calendar fit together, running a lab server, and
the pilot. Submit from an institutional address (arXiv may ask for an
endorsement). Post it on day 15 once listed; set `links.arxiv` in
`posts.json`. Later: JOSS, once the repository has some public history.

## Counting

- **Installations**: the daily heartbeat (`app/telemetry.py`): one
  anonymous message a day per installation, counts only, which admins can
  see and switch off. PostHog shows installations per day and week,
  desktop or server, and versions.
- **Interest**: stars, release downloads, repository visits and referring
  sites: `python scripts/promo-metrics.py` (every morning, into
  `promo/out/metrics.csv`).

## After a week

On 8 Oct the review task runs `promo-metrics.py --review` against these targets:

| Since launch | Target |
| --- | --- |
| Stars | 30 |
| Release downloads | 50 |
| Installations sending the heartbeat (7 days) | 10 |
| Repository visitors (14 days) | 300 |

- Mostly met: keep going, and lean toward the platform and the features that did best.
- Downloads but few installations kept: **improve the software** first (the first ten minutes, and what the issues say).
- Few people arriving: **spend a little**: boost the best post where it did best
  (Xiaohongshu 薯条, Bilibili 起飞, X or LinkedIn), roughly US$50–100 to
  start, and try free channels not used yet: r/labrats, lab-manager lists,
  Show HN, writing to a few labs directly.
