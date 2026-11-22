// Shared code for every page: header, freshness bar, data loading, formatting.
(function () {
  // Set this to "owner/repo" if the site is not served from owner.github.io/repo.
  const REPO_OVERRIDE = "";

  function repo() {
    if (REPO_OVERRIDE) return REPO_OVERRIDE;
    const m = location.hostname.match(/^([^.]+)\.github\.io$/);
    const seg = location.pathname.split("/").filter(Boolean)[0];
    return m && seg ? `${m[1]}/${seg}` : "";
  }

  const S = (window.SFI = {});
  S.bust = Date.now();
  S.meta = null;

  S.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  S.getJSON = async function (path, fresh) {
    const url = path + (fresh ? (path.includes("?") ? "&" : "?") + "t=" + S.bust : "");
    const r = await fetch(url, { cache: fresh ? "no-store" : "default" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  };

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  S.date = function (iso) {
    if (!iso) return "not available";
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return `${MONTHS[m - 1]} ${d}, ${y}`;
  };
  S.dateTime = function (iso) {
    if (!iso) return "not available";
    return new Date(iso).toLocaleString("en-US", { timeZone: "America/Chicago", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) + " Central";
  };
  S.daysAgo = (iso) => Math.floor((Date.now() - new Date(iso.slice(0, 10) + "T12:00:00").getTime()) / 864e5);

  S.labelClass = (l) => ({ "Needs attention": "attn", "Minor issues": "minor", "No issues written up": "none", "Older report form": "old" }[l] || "na");
  S.labelHTML = (l) => `<span class="label ${S.labelClass(l)}">${S.esc(l || "Not available")}</span>`;
  S.n = (v) => (v === null || v === undefined ? "not available" : v);
  S.slug = (no) => String(no || "unknown").replace(/[^0-9A-Za-z-]/g, "_");
  S.placeHref = (no, visit) => `place.html?id=${encodeURIComponent(no)}${visit ? "&visit=" + visit : ""}`;
  S.plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  // Row arrays -> objects
  S.rows = (pack) => pack.rows.map((r) => {
    const o = Object.fromEntries(pack.cols.map((c, i) => [c, r[i]]));
    if (pack.places) pack.place_cols.forEach((c, i) => (o[c] = pack.places[o.p][i]));
    return o;
  });

  // ---------- page chrome ----------
  function header(active) {
    const links = [["index.html", "Look up a place"], ["learn.html", "Learn"], ["about.html", "About the data"]];
    return `<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
  <a class="brand" href="index.html">Springfield Food Inspections<small>Sangamon County inspection reports, in plain words</small></a>
  <nav aria-label="Main">${links.map(([h, t]) => `<a href="${h}"${h === active ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>
</div></header>
<div class="fresh" role="region" aria-label="Data freshness"><div class="wrap" id="fresh">Loading data status…</div></div>`;
  }

  function footer() {
    return `<footer><div class="wrap">
<p>Source: <a href="https://public.cdpehs.com/ILEnvPbl/ESTABLISHMENT/ShowESTABLISHMENTTablePage.aspx?ESTTST_CTY=sJRNEgWYzzs%3d">Sangamon County Department of Public Health (SCDPH) public inspection portal</a>. The county's records are the official source. This site copies them nightly and adds plain-language explanations.</p>
<p>One inspection is a snapshot of one day. Problems found may have been fixed since.</p>
<p>A learning project of the Illinois Department of Public Health Food Protection Program. <a href="about.html">How this site works</a>.</p>
</div></footer>`;
  }

  S.renderFresh = function () {
    const el = document.getElementById("fresh");
    const m = S.meta;
    if (!m) { el.innerHTML = `<span class="err">Data status is not available right now.</span>`; return; }
    const ageH = (Date.now() - new Date(m.last_run).getTime()) / 36e5;
    const stale = ageH > 24;
    const r = repo();
    const runUrl = r ? `https://github.com/${r}/actions/workflows/update.yml` : "about.html#refresh";
    el.innerHTML = `
      <span>Updated <strong>${S.dateTime(m.last_run)}</strong>${stale ? ' <span class="stale">(more than a day ago)</span>' : ""}</span>
      <span>${(m.total_inspections || 0).toLocaleString()} inspections on file</span>
      <span>Newest: ${S.date(m.newest_inspection_date)}</span>
      <span class="actions">
        <button type="button" id="refreshBtn">Refresh now</button>
        ${stale ? `<a class="btn primary" id="runBtn" href="${runUrl}" target="_blank" rel="noopener">Run update now</a>` : ""}
      </span>
      <span class="sr" aria-live="polite" id="freshMsg"></span>`;
    document.getElementById("refreshBtn").onclick = S.refresh;
    const rb = document.getElementById("runBtn");
    if (rb) rb.addEventListener("click", () => S.pollForUpdate(m.last_run));
  };

  S.loadMeta = async function (fresh) {
    try { S.meta = await S.getJSON("data/meta.json", true); } catch (e) { S.meta = null; }
    S.renderFresh();
    return S.meta;
  };

  // Refresh now: re-read the JSON files (cache busted). No scraping, no AI, free.
  S.refresh = async function () {
    const b = document.getElementById("refreshBtn");
    if (b) { b.disabled = true; b.textContent = "Refreshing…"; }
    const before = S.meta && S.meta.last_run;
    S.bust = Date.now();
    await S.loadMeta(true);
    document.dispatchEvent(new CustomEvent("sfi:refresh"));
    const msg = document.getElementById("freshMsg");
    if (msg) msg.textContent = S.meta && S.meta.last_run !== before ? "New data loaded." : "Already showing the latest data.";
  };

  // After someone starts the workflow on GitHub, check meta.json every 30 s for up to 40 min.
  S.pollForUpdate = function (since) {
    const el = document.getElementById("fresh");
    const note = document.createElement("span");
    note.setAttribute("aria-live", "polite");
    note.textContent = "Waiting for the update to finish. This page will refresh itself.";
    el.appendChild(note);
    let tries = 0;
    const t = setInterval(async () => {
      tries++;
      S.bust = Date.now();
      try {
        const m = await S.getJSON("data/meta.json", true);
        if (m.last_run !== since) { clearInterval(t); S.meta = m; S.renderFresh(); document.dispatchEvent(new CustomEvent("sfi:refresh")); }
      } catch (e) { /* keep waiting */ }
      if (tries > 80) { clearInterval(t); note.textContent = "Still waiting. Check the update on GitHub, then press Refresh now."; }
    }, 30000);
  };

  S.page = function (active) {
    document.body.insertAdjacentHTML("afterbegin", header(active));
    document.body.insertAdjacentHTML("beforeend", footer());
    return S.loadMeta();
  };
})();
