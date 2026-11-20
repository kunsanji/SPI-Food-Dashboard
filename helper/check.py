#!/usr/bin/env python3
"""Acceptance checks against the live portal.

1. 10 random stored inspections: date, visit type and the three counts match the portal list.
2. Stored total matches the portal's count for a date range (default: last full month on file).
3. 3 random detail pages: every violation, repeat marker and comment came through.
Exit code 1 if anything mismatches.
"""
import argparse
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrape  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom")
    ap.add_argument("--to", dest="dto")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    store = scrape.Store()
    portal = scrape.Portal()
    rows = list(store.insp.values())
    problems = []

    # 2. totals for a range
    if a.dfrom:
        start, end = dt.date.fromisoformat(a.dfrom), dt.date.fromisoformat(a.dto)
    else:
        newest = max(dt.date.fromisoformat(r["date"]) for r in rows)
        end = newest.replace(day=1) - dt.timedelta(days=1)
        start = end.replace(day=1)
    soup, d, total = portal.search(start, end)
    stored = sum(1 for r in rows if start.isoformat() <= r["date"] <= end.isoformat())
    print(f"[total] {start}..{end}: portal {total}, stored {stored}", "OK" if total == stored else "MISMATCH")
    if total != stored:
        problems.append("total")

    # 1. ten random inspections, re-read from the portal list for their date
    for r in rnd.sample(rows, min(10, len(rows))):
        day = dt.date.fromisoformat(r["date"])
        soup, d, _ = portal.search(day, day)
        live = [x for x in scrape.parse_list_rows(soup) if x["est_no"] == r["est_no"] and x["type_raw"] == r["type_raw"]]
        ok = any(all(x[k] == r[k] for k in ("date", "type_raw", "repeat", "risk", "grp")) for x in live)
        print(f"[row] {r['id']} {r['date']} {r['name'][:30]!r} {r['type_raw']} r{r['risk']}/rep{r['repeat']}/g{r['grp']}", "OK" if ok else f"MISMATCH {live}")
        if not ok:
            problems.append(r["id"])

    # 3. three detail pages
    with_v = [iid for iid, v in store.viol.items() if v]
    for iid in rnd.sample(with_v, min(3, len(with_v))):
        r = store.insp[iid]
        live = portal.violations(r["est_id"], iid)
        mine = store.viol[iid]
        keys = ("item", "title", "repeat", "comment", "citation")
        ok = [tuple(v[k] for k in keys) for v in live] == [tuple(v[k] for k in keys) for v in mine]
        print(f"[detail] {iid}: portal {len(live)} findings, stored {len(mine)}", "OK" if ok else "MISMATCH")
        if not ok:
            problems.append("detail " + iid)

    print("ALL CHECKS PASSED" if not problems else f"PROBLEMS: {problems}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
