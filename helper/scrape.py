#!/usr/bin/env python3
"""Nightly helper: copies public food inspection records from the CDP public portal
into JSON and CSV files that the static site reads.

Usage:
  python helper/scrape.py                 # normal run: backfill (if unfinished) then last N days
  python helper/scrape.py --recent-only   # skip backfill, only refresh the last N days
  python helper/scrape.py --from 2026-10-01 --to 2026-10-31   # one custom window
  python helper/scrape.py --rebuild-only  # just rebuild site files from stored data

Polite by design: one request at a time, a 1 to 2 second pause between requests,
retries with backoff, and it stops (and records an error in meta.json) if the
portal layout changes.
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(ROOT, "helper", "config.json"), encoding="utf-8"))
COUNTY_KEY = os.environ.get("COUNTY", CONFIG["county"])
COUNTY = CONFIG["counties"][COUNTY_KEY]
CODE = COUNTY["cdp_code"]
BASE = CONFIG["portal_base"]
LIST_URL = f"{BASE}ShowESTABLISHMENTTablePage.aspx?ESTTST_CTY={CODE}"
DATA = os.path.join(ROOT, "data")
P = "ctl00$PageContent$"
REPEATER = P + "VW_PUBLIC_ESTINSPTableControlRepeater$"
CITATION_LINE_RE = re.compile(r"(?m)^[ \t]*(?:\d{1,2}-\d{3}\.\d{1,3}|\d{3}\.\d{2,5})(?:\([A-Za-z0-9]{1,4}\))*[ .]")
CITATION_RE = re.compile(r"^\s*(\d{1,2}-\d{3}\.\d{1,3}(?:\([A-Za-z0-9]{1,4}\))*|\d{3}\.\d{2,5}(?:\([A-Za-z0-9]{1,4}\))*)")


class LayoutChanged(Exception):
    """Raised when the portal no longer looks the way this helper expects."""


class Portal:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = CONFIG["user_agent"]
        self.requests_made = 0

    def _pause(self):
        lo, hi = CONFIG["delay_seconds"]
        time.sleep(random.uniform(lo, hi))

    def _req(self, method, url, **kw):
        last = None
        for attempt in range(5):
            if self.requests_made:
                self._pause()
            self.requests_made += 1
            try:
                r = self.s.request(method, url, timeout=120, **kw)
                if r.status_code in (200, 302):
                    return r
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = repr(e)
            wait = 5 * (2 ** attempt)
            print(f"  retry {attempt + 1} after {last}; waiting {wait}s", flush=True)
            time.sleep(wait)
        raise RuntimeError(f"giving up on {url}: {last}")

    @staticmethod
    def form_fields(soup):
        form = soup.find("form", id="aspnetForm")
        if form is None:
            raise LayoutChanged("list page has no aspnetForm")
        d = {}
        for i in form.find_all("input"):
            if i.get("type") in ("image", "submit", "button", "checkbox", "radio"):
                continue
            if i.get("name"):
                d[i["name"]] = i.get("value", "")
        for sel in form.find_all("select"):
            o = sel.find("option", selected=True) or sel.find("option")
            d[sel["name"]] = o.get("value") if o else ""
        for need in ("__VIEWSTATE", P + "INSPECTION_DATEFromFilter", P + "Pagination$_PageSize"):
            if need not in d:
                raise LayoutChanged(f"form field {need} missing")
        return d

    def search(self, start, end):
        """Return (soup, fields) for the list filtered to [start, end], page size raised."""
        r = self._req("GET", LIST_URL)
        soup = BeautifulSoup(r.text, "html.parser")
        d = self.form_fields(soup)
        d[P + "INSPECTION_DATEFromFilter"] = start.strftime("%m/%d/%Y")
        d[P + "INSPECTION_DATEToFilter"] = end.strftime("%m/%d/%Y")
        d["__EVENTTARGET"] = P + "SearchButton1$_Button"
        d["__EVENTARGUMENT"] = ""
        r = self._req("POST", LIST_URL, data=d)
        soup = BeautifulSoup(r.text, "html.parser")
        d = self.form_fields(soup)
        total = self.total_items(soup)
        if total > 10:
            d[P + "Pagination$_PageSize"] = str(CONFIG["page_size"])
            d["__EVENTTARGET"] = P + "Pagination$_PageSizeButton"
            r = self._req("POST", LIST_URL, data=d)
            soup = BeautifulSoup(r.text, "html.parser")
            d = self.form_fields(soup)
        return soup, d, total

    def next_page(self, d):
        d = dict(d)
        d[P + "Pagination$_NextPage.x"] = "5"
        d[P + "Pagination$_NextPage.y"] = "5"
        d["__EVENTTARGET"] = ""
        r = self._req("POST", LIST_URL, data=d)
        soup = BeautifulSoup(r.text, "html.parser")
        return soup, self.form_fields(soup)

    @staticmethod
    def total_items(soup):
        el = soup.find(id="ctl00_PageContent_Pagination__TotalItems")
        if el is None:
            if soup.find(string=re.compile(r"No records found", re.I)):
                return 0
            raise LayoutChanged("total item counter missing")
        return int(re.sub(r"\D", "", el.get_text()) or 0)

    @staticmethod
    def current_page(soup):
        el = soup.find(id="ctl00_PageContent_Pagination__CurrentPage")
        tp = soup.find(id="ctl00_PageContent_Pagination__TotalPages")
        return int(el.get("value", "1")), int(re.sub(r"\D", "", tp.get_text()) or 1)

    def details_location(self, d, row_ctl):
        dd = dict(d)
        dd["__EVENTTARGET"] = f"{REPEATER}{row_ctl}$ViewDetailsButton"
        dd["__EVENTARGUMENT"] = ""
        r = self._req("POST", LIST_URL, data=dd, allow_redirects=False)
        loc = r.headers.get("Location")
        if r.status_code != 302 or not loc or "ShowViolationTablePage" not in loc:
            raise LayoutChanged(f"Details for {row_ctl} did not redirect to a violation page")
        q = parse_qs(urlparse(loc).query)
        return {
            "est_id": q["ESTABLISHMENT"][0],
            "id": q["VIOLATION_INSPECTION"][0],
        }

    def violations(self, est_id, insp_id):
        r = self._req("GET", detail_url(est_id, insp_id))
        return parse_detail(r.text)


def detail_url(est_id, insp_id):
    return (f"{BASE}ShowViolationTablePage.aspx?ESTABLISHMENT={est_id}&ESTTST_CTY={CODE}"
            f"&VIOLATION_INSPECTION={insp_id}&INSPECTION_NN={insp_id}")


def cell_lines(td):
    parts = []
    for el in td.contents:
        if getattr(el, "name", None) == "br":
            parts.append("\n")
        else:
            parts.append(el.get_text() if hasattr(el, "get_text") else str(el))
    return [x.strip() for x in "".join(parts).split("\n") if x.strip()]


VISIT_MAP = [
    (re.compile(r"^Original and Complaint$|^Routine & Complaint$"), "Routine + complaint"),
    (re.compile(r"^Routine & \d\w\w Re-Inspect$"), "Routine + follow-up"),
    (re.compile(r"^Original$"), "Routine"),
    (re.compile(r"Re-Inspect"), "Follow-up"),
    (re.compile(r"^Complaint$"), "Complaint"),
    (re.compile(r"^Spot Check$"), "Spot check"),
    (re.compile(r"^Educational$"), "Educational"),
]


def plain_visit(raw):
    for rx, label in VISIT_MAP:
        if rx.search(raw or ""):
            return label
    return raw or "Not available"


def parse_list_rows(soup):
    rows = []
    links = soup.select("a[id$='_ViewDetailsButton']")
    for a in links:
        m = re.search(r"Repeater_(ctl\d+)_ViewDetailsButton", a["id"])
        tr = a.find_parent("tr")
        while tr is not None and len(tr.find_all("td", recursive=False)) < 11:
            tr = tr.find_parent("tr")
        if tr is None or not m:
            raise LayoutChanged("could not find the row around a Details link")
        tds = tr.find_all("td", recursive=False)
        date_txt = tds[3].get_text(strip=True)
        num_name = cell_lines(tds[4])
        addr = cell_lines(tds[5])
        types = cell_lines(tds[6])
        city_line = addr[1] if len(addr) > 1 else ""
        cm = re.match(r"^(.*?)\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?$", city_line)
        try:
            date = dt.datetime.strptime(date_txt, "%m/%d/%Y").date().isoformat()
        except ValueError:
            raise LayoutChanged(f"unexpected date '{date_txt}'")

        def num(td):
            t = td.get_text(strip=True)
            if t == "":
                return 0  # the portal leaves the cell blank when the count is zero
            return int(t) if t.isdigit() else None

        row = {
            "row_ctl": m.group(1),
            "date": date,
            "est_no": num_name[0] if num_name else "",
            "name": " ".join(num_name[1:]) if len(num_name) > 1 else "",
            "address": addr[0] if addr else "",
            "city": cm.group(1).strip() if cm else city_line,
            "state": cm.group(2) if cm else "",
            "zip": cm.group(3) if cm else "",
            "type_raw": types[0] if types else "",
            "visit": plain_visit(types[0] if types else ""),
            "est_type": types[1] if len(types) > 1 else "",
            "repeat": num(tds[8]),
            "risk": num(tds[9]),
            "grp": num(tds[10]),
        }
        if CONFIG.get("store_inspector_names"):
            row["inspector"] = tds[7].get_text(strip=True)
        rows.append(row)
    return rows


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find(string=re.compile("Violations for this Inspection")):
        raise LayoutChanged("detail page missing 'Violations for this Inspection'")
    out = []
    for tr in soup.find_all("tr", id=re.compile(r"VW_INSP_VIOLTableControlRepeater_ctl\d+_")):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            raise LayoutChanged("violation row has fewer than 3 cells")
        item_txt = tds[0].get_text(strip=True)
        title = tds[1].get_text(" ", strip=True)
        body = tds[2].get_text().replace("\r\n", "\n").replace("\r", "\n").strip()
        repeat = body.upper().startswith("(REPEAT)")
        if repeat:
            body = body[len("(REPEAT)"):].strip()
        comment, citation, citation_text = body, "", ""
        # The Food Code citation is the last line that starts with a code number (e.g. 3-501.17(A)).
        starts = [m.start() for m in CITATION_LINE_RE.finditer(body)]
        if starts:
            cut = starts[-1]
            citation_text = body[cut:].strip()
            citation = CITATION_RE.match(citation_text).group(1)
            comment = body[:cut].strip()
        mentions_repeat = bool(re.search(r"\brepeat", comment, re.I))
        out.append({
            "item": int(item_txt) if item_txt.isdigit() else None,
            "title": title,
            "repeat": repeat,
            "mentions_repeat": mentions_repeat,
            "comment": comment,
            "citation": citation,
            "citation_text": citation_text,
        })
    return out


# ---------- storage ----------

def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return default


def save(name, obj, compact=False):
    p = os.path.join(DATA, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


class Store:
    def __init__(self):
        self.insp = {r["id"]: r for r in load("inspections.json", [])}
        self.viol = {}
        for v in load("violations.json", []):
            self.viol.setdefault(v["inspection_id"], []).append(v)
        self.by_key = {}
        for r in self.insp.values():
            self.by_key.setdefault(self.key(r), []).append(r["id"])
        self.state = load("state.json", {"backfill_done_through": None, "backfill_complete": False})

    @staticmethod
    def key(r):
        return (r["est_no"], r["date"], r["type_raw"], r["name"])

    def known(self, row, page_keys):
        """Stored copy of this list row if we can match it without a Details request."""
        k = self.key(row)
        ids = self.by_key.get(k, [])
        if len(ids) != 1 or page_keys.get(k, 0) != 1:
            return None
        old = self.insp[ids[0]]
        if not old.get("violations_fetched"):
            return None
        if any(old.get(f) != row.get(f) for f in ("repeat", "risk", "grp", "est_type", "address")):
            return None
        return old

    def needs_detail(self, row):
        old = self.insp.get(row["id"])
        if old is None or not old.get("violations_fetched"):
            return True
        # re-fetch if the counts changed since we stored it
        return any(old.get(k) != row.get(k) for k in ("repeat", "risk", "grp"))

    def flush(self):
        rows = sorted(self.insp.values(), key=lambda r: (r["date"], r["id"]), reverse=True)
        save("inspections.json", rows)
        flat = []
        for iid in sorted(self.viol, key=lambda x: int(x), reverse=True):
            flat.extend(self.viol[iid])
        save("violations.json", flat)
        save("state.json", self.state)


def scrape_window(portal, store, start, end, deadline, stats):
    print(f"window {start} .. {end}", flush=True)
    soup, d, total = portal.search(start, end)
    seen = 0
    while True:
        rows = parse_list_rows(soup)
        if total and not rows:
            raise LayoutChanged("search reported results but no rows parsed")
        page_keys = {}
        for row in rows:
            page_keys[store.key(row)] = page_keys.get(store.key(row), 0) + 1
        for row in rows:
            if time.time() > deadline:
                return False
            ctl = row.pop("row_ctl")
            if store.known(row, page_keys):
                stats["unchanged"] += 1
                seen += 1
                continue
            ids = portal.details_location(d, ctl)
            row.update(ids)
            row["detail_url"] = detail_url(row["est_id"], row["id"])
            has_findings = (row["risk"] or 0) + (row["grp"] or 0) + (row["repeat"] or 0) > 0
            if store.needs_detail(row):
                if has_findings:
                    store.viol[row["id"]] = [
                        dict(v, inspection_id=row["id"]) for v in portal.violations(row["est_id"], row["id"])
                    ]
                    stats["details"] += 1
                else:
                    store.viol.pop(row["id"], None)
                row["violations_fetched"] = True
            else:
                row["violations_fetched"] = True
            if row["id"] not in store.insp:
                stats["added"] += 1
            else:
                stats["updated"] += 1
            store.insp[row["id"]] = row
            store.by_key.setdefault(store.key(row), [])
            if row["id"] not in store.by_key[store.key(row)]:
                store.by_key[store.key(row)].append(row["id"])
            seen += 1
        cur, pages = portal.current_page(soup)
        if cur >= pages:
            break
        soup, d = portal.next_page(d)
        if portal.current_page(soup)[0] != cur + 1:
            raise LayoutChanged("paging did not advance")
    if seen != total:
        stats["errors"].append(f"{start}..{end}: portal said {total}, parsed {seen}")
    stats["windows"].append({"from": str(start), "to": str(end), "portal_total": total, "parsed": seen})
    return True


def month_windows(start, end):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - dt.timedelta(days=1), end)
        cur = nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent-only", action="store_true")
    ap.add_argument("--rebuild-only", action="store_true")
    ap.add_argument("--from", dest="dfrom")
    ap.add_argument("--to", dest="dto")
    ap.add_argument("--budget-minutes", type=float, default=CONFIG["time_budget_minutes"])
    args = ap.parse_args()

    store = Store()
    meta = load("meta.json", {})
    stats = {"added": 0, "updated": 0, "unchanged": 0, "details": 0, "errors": [], "windows": []}
    started = time.time()
    deadline = started + args.budget_minutes * 60
    today = dt.date.today()
    portal = Portal()
    status = "ok"

    try:
        if args.rebuild_only:
            pass
        elif args.dfrom:
            a = dt.date.fromisoformat(args.dfrom)
            b = dt.date.fromisoformat(args.dto) if args.dto else today
            for ws, we in month_windows(a, b):
                if not scrape_window(portal, store, ws, we, deadline, stats):
                    status = "partial (time budget)"
                    break
                store.flush()
        else:
            # 1. recent days first, so fresh inspections always land even during backfill
            rs = today - dt.timedelta(days=CONFIG["recent_days"])
            if not scrape_window(portal, store, rs, today, deadline, stats):
                status = "partial (time budget)"
            store.flush()
            # 2. continue the backfill, newest month first
            if not args.recent_only and not store.state.get("backfill_complete") and status == "ok":
                floor = dt.date.fromisoformat(COUNTY["backfill_start"])
                done = store.state.get("backfill_done_through")
                upper = dt.date.fromisoformat(done) - dt.timedelta(days=1) if done else rs - dt.timedelta(days=1)
                windows = list(month_windows(floor, upper))[::-1]
                for ws, we in windows:
                    if not scrape_window(portal, store, ws, we, deadline, stats):
                        status = "partial (backfill continues next run)"
                        break
                    store.state["backfill_done_through"] = ws.isoformat()
                    store.flush()
                else:
                    if COUNTY.get("backfill_before_start"):
                        if scrape_window(portal, store, dt.date(1990, 1, 1), floor - dt.timedelta(days=1), deadline, stats):
                            store.state["backfill_complete"] = True
                        else:
                            status = "partial (backfill continues next run)"
                    else:
                        store.state["backfill_complete"] = True
                    store.flush()
    except LayoutChanged as e:
        status = "stopped: portal layout changed"
        stats["errors"].append(f"Layout changed: {e}")
        print("LAYOUT CHANGED:", e, file=sys.stderr)
    except Exception as e:  # network trouble etc.: keep what we have
        status = "stopped: error"
        stats["errors"].append(repr(e))
        print("ERROR:", repr(e), file=sys.stderr)
    finally:
        store.flush()

    import build_site
    summary = build_site.build(store, COUNTY)
    now = dt.datetime.now(dt.timezone.utc)
    run_fields = {} if args.rebuild_only else {
        "last_run": now.isoformat(timespec="seconds"),
        "last_run_status": status,
        "last_run_seconds": round(time.time() - started),
        "last_run_requests": portal.requests_made,
        "last_run_added": stats["added"],
        "last_run_updated": stats["updated"],
        "last_run_unchanged": stats["unchanged"],
        "last_run_detail_pages": stats["details"],
        "errors": stats["errors"][-20:],
    }
    meta.update({
        "county": COUNTY_KEY,
        "county_label": COUNTY["label"],
        "agency": COUNTY["agency"],
        "source_url": LIST_URL,
        "complaint_url": COUNTY.get("complaint_url"),
        **run_fields,
        "backfill_complete": store.state.get("backfill_complete", False),
        "backfill_done_through": store.state.get("backfill_done_through"),
        **summary,
    })
    save("meta.json", meta)
    print(json.dumps({k: meta.get(k) for k in ("last_run_status", "total_inspections", "newest_inspection_date",
                                           "last_run_added", "last_run_detail_pages", "errors")}, indent=1))
    # Exit non-zero only on a layout change, so the workflow shows a red X someone will notice.
    if status.startswith("stopped: portal layout"):
        sys.exit(2)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
