# Assistant benchmark: claude-opus-5-5 (skill, effort medium)

**Passes 2 and 3 don't count.** All three passes ran on one database, so the second and third couldn't make their own cages 202, 288 and 291 (cage numbers are unique) and their people talked about the first pass's cages. The 4 missing are those: the assistant declined to record a litter already there and to move mice into another person's cage. Since then each conversation has a database of its own (benchmarks/assistant.py, run_isolated).

Run through the claude CLI (Claude Code, signed-in account), not the API directly.
Tokens: 178 input, 1348374 cache read, 111769 cache write, 16624 output; cost as reported by the CLI: $2.2196.

21 conversations, 2026-10-13.

| Scenario | correct | asked | missing | wrong | error |
| --- | ---: | ---: | ---: | ---: | ---: |
| vague | 3 | 0 | 0 | 0 | 0 |
| litter | 1 | 0 | 2 | 0 | 0 |
| move | 1 | 0 | 2 | 0 | 0 |
| weight | 3 | 0 | 0 | 0 | 0 |
| died | 3 | 0 | 0 | 0 | 0 |
| ambiguous | 0 | 3 | 0 | 0 | 0 |
| unknown | 0 | 3 | 0 | 0 | 0 |
| **All** | **11** | **6** | **4** | **0** | **0** |

## What went wrong

- litter: nothing was proposed
- move: nothing was proposed
- litter: nothing was proposed
- move: nothing was proposed
