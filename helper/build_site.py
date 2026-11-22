"""Turns the stored inspections and violations into small files the site can load fast.

data/site/index.json      every inspection, compact rows (search across full history)
data/site/recent.json     newest 300 inspections (first paint on the home page)
data/site/est/<no>.json   one file per establishment: its visits and decoded findings
data/site/practice.json   this week's practice report for the Learn page
data/site/examples.json   one recent real finding per course module
data/site/items_seen.json item numbers and titles exactly as the portal prints them
data/inspections.csv, data/violations.csv   plain copies for spreadsheets
"""
import csv
import datetime as dt
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(DATA, "site")


# Course modules (ServSafe Food Handler order; also the five topics in 410 ILCS 625/3.06)
MODULES = {
    "1": list(range(1, 3)) + list(range(11, 15)) + list(range(25, 33)) + [37, 57],
    "2": list(range(3, 11)) + [40],
    "3": [15, 17, 38, 39, 42, 43, 44, 45, 46, 58],
    "4": list(range(18, 25)) + list(range(33, 37)),
    "5": [16, 41] + list(range(47, 57)),
}


FORM_CHANGE = "2019-01-01"


def is_older(r):
    return r["date"] < FORM_CHANGE


def label(r):
    if is_older(r):
        return "Older report form"
    risk, rep, grp = r.get("risk"), r.get("repeat"), r.get("grp")
    if risk is None or rep is None or grp is None:
        return "Not available"
    if risk >= 3 or rep > 0:
        return "Needs attention"
    if risk + grp > 0:
        return "Minor issues"
    return "No issues written up"


def slug(est_no):
    return re.sub(r"[^0-9A-Za-z-]", "_", est_no or "unknown")


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def build(store, county):
    global FORM_CHANGE
    FORM_CHANGE = county.get("form_change_date", FORM_CHANGE)
    rows = sorted(store.insp.values(), key=lambda r: (r["date"], int(r["id"])), reverse=True)
    for r in rows:
        r["label"] = label(r)
        r["older"] = is_older(r)
        if r["older"]:
            r["n"] = len(store.viol.get(r["id"], [])) if r.get("details_checked") else None
        else:
            r["n"] = (r["risk"] or 0) + (r["grp"] or 0)

    # index + recent: place details stored once, visits point at them by position
    place_cols = ["est_no", "name", "address", "city", "zip", "est_type"]
    visit_cols = ["id", "date", "visit", "risk", "repeat", "grp", "label", "older", "n"]

    def pack(sub):
        pos, places, out = {}, [], []
        for r in sub:
            if r["est_no"] not in pos:
                pos[r["est_no"]] = len(places)
                places.append([r.get(c) for c in place_cols])
            out.append([r.get(c) for c in visit_cols] + [pos[r["est_no"]]])
        return {"place_cols": place_cols, "places": places, "cols": visit_cols + ["p"], "rows": out}

    dump(os.path.join(SITE, "index.json"), pack(rows))
    dump(os.path.join(SITE, "recent.json"), pack(rows[:300]))

    # per establishment
    by_est = {}
    for r in rows:
        by_est.setdefault(r["est_no"], []).append(r)
    est_dir = os.path.join(SITE, "est")
    os.makedirs(est_dir, exist_ok=True)
    wanted = set()
    for est_no, visits in by_est.items():
        latest = visits[0]
        out = {
            "est_no": est_no,
            "est_id": latest.get("est_id"),
            "name": latest["name"],
            "est_type": latest["est_type"],
            "address": latest["address"],
            "city": latest["city"],
            "state": latest["state"],
            "zip": latest["zip"],
            "visits": [],
        }
        for v in visits:
            out["visits"].append({
                "id": v["id"], "date": v["date"], "visit": v["visit"], "type_raw": v["type_raw"],
                "risk": v["risk"], "repeat": v["repeat"], "grp": v["grp"], "label": v["label"],
                "detail_url": v.get("detail_url"),
                "older": v["older"],
                "fetched": bool(v.get("details_checked")) if v["older"] else bool(v.get("violations_fetched")),
                "findings": [
                    {k: f.get(k) for k in ("item", "title", "repeat", "comment", "citation", "citation_text")}
                    for f in store.viol.get(v["id"], [])
                ],
            })
        fn = slug(est_no) + ".json"
        wanted.add(fn)
        dump(os.path.join(est_dir, fn), out)
    for fn in os.listdir(est_dir):
        if fn.endswith(".json") and fn not in wanted:
            os.remove(os.path.join(est_dir, fn))

    # items exactly as the portal prints them (to check the plain-language table against)
    seen = {}
    for iid, vs in store.viol.items():
        if iid in store.insp and is_older(store.insp[iid]):
            continue
        for v in vs:
            if v.get("item") is not None:
                t = seen.setdefault(str(v["item"]), {})
                t[v["title"]] = t.get(v["title"], 0) + 1
    dump(os.path.join(SITE, "items_seen.json"), seen)

    # one recent example finding per module
    examples = {}
    for mod, items in MODULES.items():
        for r in rows:
            if r["older"]:
                break
            hit = None
            for f in store.viol.get(r["id"], []):
                c = (f.get("comment") or "").strip()
                if f.get("item") in items and 60 <= len(c) <= 450:
                    hit = f
                    break
            if hit:
                examples[mod] = {
                    "date": r["date"], "est_type": r["est_type"], "est_no": r["est_no"],
                    "item": hit["item"], "title": hit["title"], "comment": hit["comment"],
                    "repeat": hit["repeat"], "citation": hit["citation"],
                }
                break
    dump(os.path.join(SITE, "examples.json"), examples)

    # weekly practice report: a recent routine visit with 3 to 6 findings, both kinds present
    iso = dt.date.today().isocalendar()
    week = f"{iso[0]}-W{iso[1]:02d}"
    pick = store.state.get("practice")
    if not pick or pick.get("week") != week or pick.get("id") not in store.insp:
        newest = rows[0]["date"] if rows else dt.date.today().isoformat()
        cutoff = (dt.date.fromisoformat(newest) - dt.timedelta(days=60)).isoformat()
        cands = []
        for r in rows:
            if r["date"] < cutoff:
                break
            if not r["visit"].startswith("Routine"):
                continue
            fs = [f for f in store.viol.get(r["id"], []) if f.get("item")]
            if 3 <= len(fs) <= 6 and any(f["item"] <= 29 for f in fs) and any(f["item"] >= 30 for f in fs) \
                    and all((f.get("comment") or "").strip() for f in fs):
                cands.append(r["id"])
        cands.sort(key=int)
        pick = {"week": week, "id": cands[(iso[0] * 53 + iso[1]) % len(cands)] if cands else None}
        store.state["practice"] = pick
        json.dump(store.state, open(os.path.join(DATA, "state.json"), "w"), indent=1)
    practice = None
    if pick.get("id"):
        r = store.insp[pick["id"]]
        practice = {
            "week": week, "id": r["id"], "date": r["date"], "est_type": r["est_type"], "est_no": r["est_no"],
            "visit": r["visit"],
            "findings": [{"item": f["item"], "title": f["title"], "comment": f["comment"], "repeat": f["repeat"]}
                         for f in store.viol.get(r["id"], []) if f.get("item")],
        }
    dump(os.path.join(SITE, "practice.json"), practice or {})

    # last 12 months of findings by form item, for the Learn page
    newest = rows[0]["date"] if rows else dt.date.today().isoformat()
    yr = (dt.date.fromisoformat(newest) - dt.timedelta(days=365)).isoformat()
    recent_rows = [r for r in rows if r["date"] > yr and not r["older"]]
    by_item, insp_with = {}, {}
    for r in recent_rows:
        items = set()
        for f in store.viol.get(r["id"], []):
            if f.get("item") is not None:
                by_item[str(f["item"])] = by_item.get(str(f["item"]), 0) + 1
                items.add(f["item"])
        for i in items:
            insp_with[str(i)] = insp_with.get(str(i), 0) + 1
    labels = {}
    for r in recent_rows:
        labels[r["label"]] = labels.get(r["label"], 0) + 1
    dump(os.path.join(SITE, "stats.json"), {
        "from": yr, "to": newest, "inspections": len(recent_rows),
        "routine_inspections": sum(1 for r in recent_rows if r["visit"].startswith("Routine")),
        "findings_by_item": by_item, "inspections_with_item": insp_with, "labels": labels,
    })

    # CSV copies
    icols = ["id", "date", "est_no", "est_id", "name", "address", "city", "state", "zip", "type_raw", "visit",
             "est_type", "repeat", "risk", "grp", "label", "detail_url"]
    if any("inspector" in r for r in rows):
        icols.append("inspector")
    with open(os.path.join(DATA, "inspections.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, icols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    vcols = ["inspection_id", "item", "title", "repeat", "comment", "citation", "citation_text"]
    with open(os.path.join(DATA, "violations.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, vcols, extrasaction="ignore")
        w.writeheader()
        for iid in sorted(store.viol, key=int, reverse=True):
            w.writerows(store.viol[iid])

    years = {}
    for r in rows:
        years[r["date"][:4]] = years.get(r["date"][:4], 0) + 1
    return {
        "total_inspections": len(rows),
        "total_violations": sum(len(v) for v in store.viol.values()),
        "total_establishments": len(by_est),
        "newest_inspection_date": rows[0]["date"] if rows else None,
        "oldest_inspection_date": rows[-1]["date"] if rows else None,
        "inspections_by_year": dict(sorted(years.items())),
        "practice_week": week,
    }
