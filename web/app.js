/* Redy front end: filters, job list and a per-browser application tracker. No build step. */
(() => {
  "use strict";

  const PAGE = 60;
  const LEVEL_LABEL = { mid: "Mid-Senior", senior: "Senior", staff: "Staff+", mgmt: "Manager+", unk: "Not stated" };
  const LEVEL_ORDER = { mgmt: 5, staff: 4, senior: 3, mid: 2, unk: 1 };
  const THRESHOLD = { mid: ["mid", "senior", "staff", "mgmt"], senior: ["senior", "staff", "mgmt"], staff: ["staff", "mgmt"], mgmt: ["mgmt"] };
  const METROS = {
    remote: /remote|anywhere/i,
    bay: /san francisco|\bsf\b|bay area|palo alto|mountain view|menlo park|redwood city|san mateo|sunnyvale|san jose|oakland|berkeley|foster city|santa clara|san carlos|burlingame|emeryville|cupertino|los gatos|milpitas/i,
    nyc: /new york|\bnyc\b|brooklyn|manhattan/i,
    sea: /seattle|bellevue|redmond|kirkland/i,
    la: /los angeles|santa monica|el segundo|hawthorne|culver city|torrance|long beach|venice|pasadena|irvine|costa mesa|carson|huntington beach/i,
    bos: /boston|cambridge, ma|somerville|waltham/i,
    aus: /austin/i,
    chi: /chicago/i,
  };
  const STAGES = ["Seed / Early", "Series A–B", "Series C+", "Growth"];
  const AGES = [["any", "Any time"], ["7", "7 days"], ["30", "30 days"], ["90", "90 days"]];
  const PAYS = [["0", "Any"], ["150000", "$150k+"], ["200000", "$200k+"], ["250000", "$250k+"], ["300000", "$300k+"]];
  const TRACK_STAGES = ["Saved", "Applied", "Recruiter screen", "Interviewing", "Offer", "Rejected", "Withdrawn"];
  const DEFAULTS = { q: "", level: "mid", role: "all", loc: "all", sort: "new", backer: [], stage: [], sub: [], age: "any", pay: "0", unk: true, visa: false, paid: false };

  const $ = (id) => document.getElementById(id);
  const store = {
    // "reedy." and "staffline." are previous names: read them so saved jobs and filters survive the rename
    get(k, fallback) { try { const v = localStorage.getItem("redy." + k) ?? localStorage.getItem("reedy." + k) ?? localStorage.getItem("staffline." + k); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
    set(k, v) { try { localStorage.setItem("redy." + k, JSON.stringify(v)); } catch { /* storage blocked: state lives for this visit only */ } },
  };

  /* Filters live in the URL so any view can be shared or bookmarked; the last view is also remembered locally. */
  const LIST_KEYS = ["backer", "stage", "sub"];
  const BOOL_KEYS = ["unk", "visa", "paid"];
  function fromURL() {
    const p = new URLSearchParams(location.search);
    if (![...p.keys()].length) return null;
    const out = { ...DEFAULTS };
    for (const key of Object.keys(DEFAULTS)) {
      if (!p.has(key)) continue;
      const v = p.get(key);
      if (LIST_KEYS.includes(key)) out[key] = v ? v.split(",") : [];
      else if (BOOL_KEYS.includes(key)) out[key] = v === "1";
      else out[key] = v;
    }
    return out;
  }
  function toURL() {
    const p = new URLSearchParams();
    for (const [key, def] of Object.entries(DEFAULTS)) {
      const v = state[key];
      if (LIST_KEYS.includes(key)) { if (v.length) p.set(key, v.join(",")); }
      else if (BOOL_KEYS.includes(key)) { if (v !== def) p.set(key, v ? "1" : "0"); }
      else if (v !== def) p.set(key, v);
    }
    if (track !== "startup") p.set("track", track);
    const qs = p.toString().replace(/%2C/g, ",").replace(/%2F/g, "/");
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
  }

  let SOURCES = { startup: null, tech: null, traditional: null };
  let JOBS = [], COMPANIES = { startup: {}, tech: {}, traditional: {} };
  let state = fromURL() || { ...DEFAULTS, ...store.get("filters", {}) };
  let saved = store.get("saved", {});
  let seen = store.get("seen", {});
  let shown = PAGE, track = new URLSearchParams(location.search).get("track") || "startup";
  if (!["startup", "tech", "traditional"].includes(track)) track = "startup";

  function inflate(data, trackName) {
    const companies = data.companies || {};
    COMPANIES[trackName] = companies;
    const now = Date.now();
    return (data.jobs || []).map((j) => {
      const c = companies[j.c] || {};
      return { ...j, track: trackName, co: c, days: j.at ? Math.max(0, Math.floor((now - Date.parse(j.at)) / 864e5)) : null,
               hay: (j.t + " " + (c.n || "") + " " + j.sub + " " + (c.ind || "") + " " + (c.bio || "")).toLowerCase() };
    });
  }

  async function load() {
    const inline = document.getElementById("jobs-data");
    const inlineTech = document.getElementById("tech-data");
    SOURCES.startup = inline ? JSON.parse(inline.textContent) : await (await fetch("data/jobs.json", { cache: "no-cache" })).json();
    const inlineTraditional = document.getElementById("traditional-data");
    try {
      SOURCES.tech = inlineTech ? JSON.parse(inlineTech.textContent) : await (await fetch("data/tech.json", { cache: "no-cache" })).json();
    } catch {
      SOURCES.tech = { companies: {}, jobs: [] };
    }
    try {
      SOURCES.traditional = inlineTraditional ? JSON.parse(inlineTraditional.textContent) : await (await fetch("data/traditional.json", { cache: "no-cache" })).json();
    } catch {
      SOURCES.traditional = { companies: {}, jobs: [] };
    }
    JOBS = [...inflate(SOURCES.startup, "startup"), ...inflate(SOURCES.tech, "tech"), ...inflate(SOURCES.traditional, "traditional")];
    track = trackForCompany(state.q);
    buildChips();
    syncControls();
    route();
  }

  function boardJobs() {
    return JOBS.filter((j) => j.track === track);
  }

  /* A company search follows the company onto its track. "pin" on Startups would otherwise
     stay with Pindrop and Pinecone and never show Pinterest. */
  function trackForCompany(query) {
    const q = (query || "").trim().toLowerCase();
    if (q.length < 2) return track;
    const scores = { startup: 0, tech: 0, traditional: 0 };
    const jobs = {};
    for (const j of JOBS) jobs[j.track + "\0" + j.c] = (jobs[j.track + "\0" + j.c] || 0) + 1;
    const seen = new Set();
    for (const j of JOBS) {
      const key = j.track + "\0" + j.c;
      if (seen.has(key)) continue;
      seen.add(key);
      const name = ((j.co && j.co.n) || "").toLowerCase();
      if (!name) continue;
      const words = name.split(/[^a-z0-9]+/).filter(Boolean);
      const prefix = name.startsWith(q) || words.some((word) => word.startsWith(q));
      if (!prefix && !name.includes(q)) continue;
      scores[j.track] += (prefix ? 10 : 1) * jobs[key];
    }
    let best = track;
    let bestScore = scores[track] || 0;
    const here = scores[track] || 0;
    for (const name of ["startup", "tech", "traditional"]) {
      const score = scores[name] || 0;
      if (name !== track && score > bestScore && (here === 0 || score >= here * 2)) {
        best = name;
        bestScore = score;
      }
    }
    return best;
  }

  /* ---------------- filtering */
  function matches(j, skipSub) {
    const okLevels = THRESHOLD[state.level];
    if (!(okLevels.includes(j.lvl) || (state.unk && j.lvl === "unk"))) return false;
    if (state.role !== "all" && j.fam !== state.role) return false;
    if (state.loc !== "all") {
      const re = METROS[state.loc];
      if (!(j.loc.some((l) => re.test(l)) || (state.loc === "remote" && j.rem))) return false;
    }
    if (track === "startup") {
      if (state.backer.length && !state.backer.some((b) => (j.co.backers || []).includes(b))) return false;
      if (state.stage.length && !state.stage.includes(j.co.sb)) return false;
    }
    if (!skipSub && state.sub.length && !state.sub.includes(j.sub)) return false;
    if (state.age !== "any" && (j.days == null || j.days > +state.age)) return false;
    if (+state.pay && !(j.pay && j.pay[1] >= +state.pay)) return false;
    if (state.paid && !j.pay) return false;
    if (track === "startup" && state.visa && j.visa !== "sponsor") return false;
    if (state.q) {
      for (const tok of state.q.toLowerCase().split(/\s+/).filter(Boolean)) if (!j.hay.includes(tok)) return false;
    }
    return true;
  }

  function sorted(list) {
    const by = {
      new: (a, b) => (b.at || "").localeCompare(a.at || ""),
      pay: (a, b) => ((b.pay && b.pay[1]) || 0) - ((a.pay && a.pay[1]) || 0) || (b.at || "").localeCompare(a.at || ""),
      level: (a, b) => LEVEL_ORDER[b.lvl] - LEVEL_ORDER[a.lvl] || (b.at || "").localeCompare(a.at || ""),
    }[state.sort];
    return list.slice().sort((a, b) => (a.closed === true) - (b.closed === true) || by(a, b));
  }

  /* ---------------- formatting */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const k = (n) => "$" + Math.round(n / 1000) + "k";
  const payText = (p) => (p ? (p[0] === p[1] ? k(p[0]) : k(p[0]) + "–" + k(p[1])) : "");
  function ago(d) {
    if (d == null) return "";
    if (d === 0) return "today";
    if (d === 1) return "yesterday";
    if (d < 30) return d + "d ago";
    if (d < 365) return Math.round(d / 30) + "mo ago";
    return Math.round(d / 365) + "y ago";
  }
  function todayISO() {
    const n = new Date();
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-${String(n.getDate()).padStart(2, "0")}`;
  }
  const followDue = (entry) => !!(entry && entry.follow && entry.follow <= todayISO());
  function hue(s) { let h = 0; for (const ch of s || "") h = (h * 31 + ch.charCodeAt(0)) % 360; return h; }
  const ICON = {
    title: '<svg viewBox="0 0 16 16"><path d="m5.5 4.5-3.5 3.5 3.5 3.5M10.5 4.5 14 8l-3.5 3.5"/></svg>',
    co: '<svg viewBox="0 0 16 16"><rect x="3" y="2" width="10" height="12" rx="1.2"/><path d="M6 5h1M9 5h1M6 8h1M9 8h1M7 14v-3h2v3"/></svg>',
    lvl: '<svg viewBox="0 0 16 16"><path d="M3 13V9M8 13V6M13 13V3"/></svg>',
    pay: '<svg viewBox="0 0 16 16"><rect x="1.5" y="4" width="13" height="8" rx="1.4"/><circle cx="8" cy="8" r="1.6"/></svg>',
    loc: '<svg viewBox="0 0 16 16"><path d="M8 14s4.5-4 4.5-7.3a4.5 4.5 0 0 0-9 0C3.5 10 8 14 8 14Z"/><circle cx="8" cy="6.6" r="1.5"/></svg>',
    added: '<svg viewBox="0 0 16 16"><rect x="2" y="3" width="12" height="11" rx="1.6"/><path d="M2 6.5h12M5.5 1.8v2.4M10.5 1.8v2.4"/></svg>',
    status: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.2"/><path d="M5.6 8.1 7.2 9.7 10.5 6.4"/></svg>',
    stage: '<svg viewBox="0 0 16 16"><path d="M2 4h12M2 8h8M2 12h5"/></svg>',
  };
  const star = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m8 1.8 1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';

  // Logo chain: square favicon from the company domain -> the board's own logo -> initials
  window.__logoFail = (img) => {
    const alt = img.dataset.alt;
    if (alt) { img.removeAttribute("data-alt"); img.classList.add("alt"); img.src = alt; } else img.remove();
  };
  function logo(c) {
    const initials = esc((c.n || "?").replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase());
    const fav = c.d ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(c.d)}&sz=64` : "";
    const [src, alt] = fav ? [fav, c.logo || ""] : [c.logo || "", ""];
    const img = !window.__BUNDLED__ && src
      ? `<img src="${esc(src)}"${alt ? ` data-alt="${esc(alt)}"` : ""} alt="" loading="lazy" onload="if(!this.classList.contains('alt')&&this.naturalWidth<32)__logoFail(this)" onerror="__logoFail(this)">`
      : "";
    return `<span class="logo" style="color:hsl(${hue(c.n)} 45% 42%)" aria-hidden="true">${initials}${img}</span>`;
  }
  const isRemote = (j) => j.rem && !j.loc.some((l) => /remote/i.test(l));
  const locSummary = (j) => {
    if (!j.loc.length) return j.rem ? "Remote" : "";
    return esc(j.loc[0]) + (j.loc.length > 1 ? ` <span class="loc-more">+${j.loc.length - 1}</span>` : "");
  };

  function head(trackerMode) {
    const sortTh = (key, icon, label) =>
      `<button class="sort-btn" type="button" data-sort="${key}" aria-pressed="${state.sort === key}">${ICON[icon]}${label}</button>`;
    const plain = (icon, label) => `<span class="th">${ICON[icon]}${label}</span>`;
    return `<colgroup><col><col class="c-co"><col class="c-lvl"><col class="c-pay"><col class="c-loc"><col class="c-status">${trackerMode ? '<col class="c-stage">' : '<col class="c-added">'}<col class="c-save"></colgroup>
      <thead><tr>
        <th scope="col">${plain("title", "Title")}</th>
        <th scope="col">${plain("co", "Company")}</th>
        <th scope="col">${trackerMode ? plain("lvl", "Level") : sortTh("level", "lvl", "Level")}</th>
        <th scope="col">${trackerMode ? plain("pay", "Pay") : sortTh("pay", "pay", "Pay")}</th>
        <th scope="col" class="th-loc">${plain("loc", "Location")}</th>
        <th scope="col">${plain("status", "Status")}</th>
        <th scope="col">${trackerMode ? plain("stage", "Stage") : sortTh("new", "added", "Updated")}</th>
        <th scope="col"><span class="sr">Save</span></th>
      </tr></thead>`;
  }

  function jobRow(j, trackerMode) {
    const c = j.co;
    const backer = (c.backers || []).map((b) => (b === "YC" && c.batch ? "YC " + c.batch : b)).join(" · ");
    const coMeta = [c.stage, backer].filter(Boolean).join(" · ");
    const allLocs = esc(j.loc.join(" / ") + (isRemote(j) ? " / Remote OK" : ""));
    const isSaved = !!saved[j.id];
    const status = j.closed
      ? { key: "closed", label: "Closed", title: "No longer listed on the company job board" }
      : j.chg
        ? { key: "chg", label: "Changed", title: "Still listed, but the description changed" }
        : j.chk
          ? { key: "open", label: "Open", title: "Still listed, and the description matches the last check" }
          : { key: "open", label: "Open", title: "Listed on the company job board in the latest crawl" };
    const tags = [
      !trackerMode && !j.closed && j.days != null && j.days <= 1 ? '<span class="tag new">New</span>' : "",
      j.yoe ? `<span class="tag">${j.yoe}+ yrs</span>` : "",
      j.visa === "sponsor" ? '<span class="tag visa">Visa</span>' : "",
    ].join("");
    const entry = trackerMode ? saved[j.id] : null;
    const due = followDue(entry);
    const last = trackerMode
      ? `<td class="td-stage"><select class="stage-sel" data-stage="${j.id}" aria-label="Application stage for ${esc(j.t)}">${TRACK_STAGES.map((s) => `<option${entry.stage === s ? " selected" : ""}>${s}</option>`).join("")}</select></td>
         <td class="td-save"><button class="rm" type="button" data-remove="${j.id}" aria-label="Remove ${esc(j.t)} from tracker" title="Remove">&times;</button></td>`
      : `<td class="td-added added"${j.at ? ` title="Updated ${esc(j.at)}"` : ""}>${ago(j.days)}</td>
         <td class="td-save"><button class="save" type="button" data-save="${j.id}" aria-pressed="${isSaved}" aria-label="${isSaved ? "Remove from tracker" : "Save to tracker"}: ${esc(j.t)}" title="${isSaved ? "Saved" : "Save"}">${star}</button></td>`;
    const dates = trackerMode
      ? `<div class="track-when"><label>Applied <input type="date" data-applied="${j.id}" value="${esc(entry.applied || "")}" aria-label="Date applied for ${esc(j.t)}"></label><label class="${due ? "due" : ""}">Follow up <input type="date" data-follow="${j.id}" value="${esc(entry.follow || "")}" aria-label="Follow-up date for ${esc(j.t)}"></label></div>`
      : "";
    const cls = [j.closed && "closed", due && "due", !trackerMode && seen[j.id] && "seen"].filter(Boolean).join(" ");
    return `<tr${cls ? ` class="${cls}"` : ""}>
      <td class="td-title"><div class="t-cell">${logo(c)}<div class="t-main">
        <a class="title" href="${esc(j.url)}" target="_blank" rel="noopener" data-open="${j.id}">${esc(j.t)}</a>${tags ? `<span class="tags">${tags}</span>` : ""}
        <div class="t-loc" title="${allLocs}">${locSummary(j)}${isRemote(j) ? " · Remote OK" : ""}</div>
        ${trackerMode ? `<input class="note" data-note="${j.id}" value="${esc(entry.note || "")}" placeholder="Next step, recruiter, referral…" aria-label="Notes for ${esc(j.t)}">${dates}` : ""}
      </div></div></td>
      <td class="td-co">${j.c ? `<button class="co-name" type="button" data-company="${esc(j.c)}" data-track="${esc(j.track || "startup")}">${esc(c.n)}</button>` : `<span class="co-name">${esc(c.n)}</span>`}${coMeta ? `<span class="co-meta" title="${esc(coMeta)}">${esc(coMeta)}</span>` : ""}</td>
      <td class="td-lvl"><span class="lvl lvl-${j.lvl}">${LEVEL_LABEL[j.lvl]}</span></td>
      <td class="td-pay">${j.pay ? `<span class="pay">${payText(j.pay)}</span>` : '<span class="dash">—</span>'}</td>
      <td class="td-loc" title="${allLocs}"><span class="loc">${locSummary(j)}</span>${isRemote(j) ? '<span class="remote">Remote OK</span>' : ""}</td>
      <td class="td-status"><span class="tag ${status.key}" title="${esc(status.title)}">${status.label}</span></td>
      ${last}
    </tr>`;
  }

  /* ---------------- views */
  const TRACK_NAME = { startup: "Startups", tech: "Tech", traditional: "Traditional industry" };
  const TAGLINE = {
    startup: "Senior+ engineering and product roles at a16z and YC startups, straight from each company's own job board.",
    tech: "Senior+ engineering and product roles at US public tech companies and top unicorns, straight from each company's own job board.",
    traditional: "Senior+ engineering and product roles at recognizable US banks, retailers, healthcare, and industrial companies, straight from each company's own job board.",
  };

  function renderSpec() {
    const base = boardJobs().filter((j) => matches(j, true));
    const counts = {};
    for (const j of base) counts[j.sub] = (counts[j.sub] || 0) + 1;
    const subs = Object.keys(counts).sort((a, b) => (a === "Other Eng") - (b === "Other Eng") || counts[b] - counts[a]);
    for (const s of state.sub) if (!(s in counts)) subs.push(s);
    const btn = (val, label, n, on) =>
      `<button class="spec-btn" type="button" data-spec="${esc(val)}" aria-pressed="${on}">${esc(label)}<span class="n">${(n || 0).toLocaleString()}</span></button>`;
    $("spec").innerHTML = btn("", "All", base.length, !state.sub.length) + subs.map((s) => btn(s, s, counts[s], state.sub.includes(s))).join("");
  }

  function renderJobs() {
    $("page-title").textContent = TRACK_NAME[track];
    $("job-controls").hidden = false;
    $("spec").hidden = false;
    $("share").hidden = false;
    $("tagline").hidden = false;
    $("tagline").textContent = TAGLINE[track];
    renderSpec();
    const all = boardJobs();
    if (!all.length) {
      $("meta").textContent = "No roles yet";
      $("content").innerHTML = `<div class="empty"><h2>No roles yet</h2><p>These companies are on the list. Openings appear when their job board is one we can read.</p></div>`;
      return;
    }
    const list = sorted(all.filter((j) => matches(j)));
    const cos = new Set(list.map((j) => j.c)).size;
    const fresh = list.filter((j) => j.days != null && j.days <= 1 && !j.closed).length;
    const closed = list.filter((j) => j.closed).length;
    const changed = list.filter((j) => j.chg).length;
    $("meta").innerHTML = `<b>${list.length.toLocaleString()}</b> roles at <b>${cos.toLocaleString()}</b> companies` +
      (fresh ? ` · <span class="fresh">${fresh.toLocaleString()} new since yesterday</span>` : "") +
      (closed ? ` · ${closed.toLocaleString()} closed` : "") +
      (changed ? ` · ${changed.toLocaleString()} description changed` : "");
    if (!list.length) {
      $("content").innerHTML = `<div class="empty"><h2>No roles match these filters</h2><p>Try a wider location, a lower level, or clear the search. <button class="link-btn" type="button" data-reset>Reset filters</button></p></div>`;
      return;
    }
    $("content").innerHTML = `<table class="jobs">${head(false)}<tbody>${list.slice(0, shown).map((j) => jobRow(j)).join("")}</tbody></table>` +
      (list.length > shown ? `<button class="load-more" type="button" id="load-more">Show ${Math.min(PAGE, list.length - shown)} more · ${(list.length - shown).toLocaleString()} left</button>` : "");
  }

  function renderTracker() {
    $("job-controls").hidden = true;
    $("spec").hidden = true;
    $("share").hidden = true;
    $("tagline").hidden = true;
    $("page-title").textContent = "Tracker";
    const byId = new Map(JOBS.map((j) => [j.id, j]));
    const items = Object.entries(saved).map(([id, s]) => byId.get(id) || { ...s.job, id, co: s.job.co, loc: s.job.loc || [], closed: true })
      .sort((a, b) => {
        const ad = followDue(saved[a.id]) ? saved[a.id].follow : "";
        const bd = followDue(saved[b.id]) ? saved[b.id].follow : "";
        if (ad !== bd) return ad ? (bd ? ad.localeCompare(bd) : -1) : 1;
        return TRACK_STAGES.indexOf(saved[a.id].stage) - TRACK_STAGES.indexOf(saved[b.id].stage) || (saved[b.id].ts || 0) - (saved[a.id].ts || 0);
      });
    const counts = TRACK_STAGES.slice(0, 5).map((s) => `<div class="stage-card"><b>${items.filter((j) => saved[j.id].stage === s).length}</b><span>${s}</span></div>`).join("");
    const due = items.filter((j) => followDue(saved[j.id])).length;
    $("meta").innerHTML = `<b>${items.length}</b> tracked · saved in this browser` + (due ? ` · <b>${due}</b> to follow up` : "");
    $("content").innerHTML = items.length
      ? `<div class="pipeline">${counts}</div><table class="jobs">${head(true)}<tbody>${items.map((j) => jobRow(j, true)).join("")}</tbody></table>`
      : `<div class="empty"><h2>Your tracker is empty</h2><p>Star any role on the job board and it lands here. Move it through Applied, Recruiter screen, Interviewing and Offer. The applied date fills in when you mark Applied, and a follow-up date brings that role back to the top.</p></div>`;
  }

  function openCompany(key, trackName) {
    const c = (COMPANIES[trackName] || COMPANIES.startup)[key];
    if (!c) return;
    const backer = (c.backers || []).map((b) => (b === "YC" && c.batch ? "YC " + c.batch : b)).join(" · ");
    const meta = [c.stage, backer, c.hc ? Number(c.hc).toLocaleString() + " people" : ""].filter(Boolean).join(" · ");
    const facts = [];
    if (c.ceo) facts.push(["CEO", c.ceo]);
    else if (c.founders) facts.push(["Founders", c.founders]);
    if (c.yr) facts.push(["Founded", String(c.yr)]);
    const hasProfile = c.bio || c.ceo || c.founders || c.yr;
    const miss = hasProfile ? "" : (c.stage || c.hc
      ? `<p class="co-miss">No public description for this company yet. Stage and headcount are all that's listed.</p>`
      : `<p class="co-miss">No public description for this company yet.</p>`);
    const dlg = $("co-card");
    dlg.innerHTML = `<div class="co-card-top">${logo(c)}<div class="co-card-id"><h2>${esc(c.n)}</h2>${meta ? `<p>${esc(meta)}</p>` : ""}</div><button class="co-x" type="button" data-co-close aria-label="Close">&times;</button></div>` +
      (c.bio ? `<p class="co-bio">${esc(c.bio)}</p>` : "") +
      (facts.length ? `<dl class="co-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : "") +
      miss;
    if (!dlg.open) dlg.showModal();
  }

  function render() {
    $("saved-count").textContent = Object.keys(saved).length;
    $("startup-count").textContent = JOBS.filter((j) => j.track === "startup").length.toLocaleString();
    $("tech-count").textContent = JOBS.filter((j) => j.track === "tech").length.toLocaleString();
    $("traditional-count").textContent = JOBS.filter((j) => j.track === "traditional").length.toLocaleString();
    document.querySelectorAll(".startup-only").forEach((el) => { el.hidden = track !== "startup"; });
    const tracker = location.hash === "#tracker";
    $("view-jobs").toggleAttribute("aria-current", !tracker);
    if (!tracker) $("view-jobs").setAttribute("aria-current", "page");
    $("view-tracker").toggleAttribute("aria-current", tracker);
    if (tracker) $("view-tracker").setAttribute("aria-current", "page");
    document.querySelectorAll(".tracks [data-track]").forEach((t) => {
      if (!tracker && t.dataset.track === track) t.setAttribute("aria-current", "true"); else t.removeAttribute("aria-current");
    });
    tracker ? renderTracker() : renderJobs();
    const startupFilters = track === "startup" ? state.backer.length + state.stage.length + state.visa : 0;
    const extra = startupFilters + (state.age !== "any") + (+state.pay > 0) + state.paid + !state.unk;
    $("more-count").hidden = !extra;
    $("more-count").textContent = extra;
    const viewing = location.hash === "#tracker" ? "startup" : track;
    const src = SOURCES[viewing] || SOURCES.startup;
    if (src && src.generated_at) {
      const mins = Math.max(0, Math.round((Date.now() - Date.parse(src.generated_at)) / 6e4));
      const rel = mins < 60 ? `${mins || 1} min ago` : mins < 48 * 60 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
      $("live").hidden = false;
      $("live").classList.toggle("stale", mins > 6 * 60);
      $("live-text").textContent = `Updated ${rel}`;
      const label = { startup: "a16z and YC companies", tech: "tech companies", traditional: "traditional companies" }[viewing] || "companies";
      const n = Object.keys(COMPANIES[viewing] || {}).length;
      $("foot-sync").textContent = `Updated ${new Date(src.generated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${n.toLocaleString()} ${label}`;
    }
  }
  const route = () => { shown = PAGE; render(); toURL(); };

  /* ---------------- controls */
  function chipGroup(el, key, options, multi) {
    el.innerHTML = options.map(([v, label]) => `<button class="chip-toggle" type="button" data-key="${key}" data-val="${esc(v)}" data-multi="${multi ? 1 : 0}">${esc(label)}</button>`).join("");
  }
  function buildChips() {
    chipGroup($("c-backer"), "backer", [["a16z", "a16z"], ["YC", "Y Combinator"]], true);
    chipGroup($("c-stage"), "stage", STAGES.map((s) => [s, s]), true);
    chipGroup($("c-age"), "age", AGES, false);
    chipGroup($("c-pay"), "pay", PAYS, false);
  }
  function syncControls() {
    $("q").value = state.q;
    for (const [id, key] of [["f-level", "level"], ["f-role", "role"], ["f-loc", "loc"], ["f-sort", "sort"]]) $(id).value = state[key];
    for (const [id, key] of [["f-unk", "unk"], ["f-visa", "visa"], ["f-paid", "paid"]]) $(id).checked = state[key];
    document.querySelectorAll(".chip-toggle").forEach((b) => {
      const v = state[b.dataset.key];
      b.setAttribute("aria-pressed", Array.isArray(v) ? v.includes(b.dataset.val) : String(v) === b.dataset.val);
    });
  }
  function update(patch) {
    state = { ...state, ...patch };
    if ("q" in patch) track = trackForCompany(state.q);
    store.set("filters", state);
    shown = PAGE;
    syncControls();
    render();
    toURL();
  }

  let qTimer;
  $("q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => update({ q: e.target.value.trim() }), 120); });
  for (const [id, key] of [["f-level", "level"], ["f-role", "role"], ["f-loc", "loc"], ["f-sort", "sort"]]) $(id).addEventListener("change", (e) => update({ [key]: e.target.value }));
  for (const [id, key] of [["f-unk", "unk"], ["f-visa", "visa"], ["f-paid", "paid"]]) $(id).addEventListener("change", (e) => update({ [key]: e.target.checked }));
  /* filters popover */
  const setMore = (open) => {
    const pop = $("more");
    pop.hidden = !open;
    $("more-toggle").setAttribute("aria-expanded", open);
    if (open && matchMedia("(min-width: 761px)").matches) {
      // keep the popover inside the viewport: shift it left when the Filters button sits near the right edge
      pop.style.left = "0px";
      const overflow = pop.getBoundingClientRect().right - (document.documentElement.clientWidth - 16);
      if (overflow > 0) pop.style.left = -overflow + "px";
    } else pop.style.left = "";
  };
  $("more-toggle").addEventListener("click", () => setMore($("more").hidden));
  $("more-done").addEventListener("click", () => setMore(false));
  document.addEventListener("pointerdown", (e) => { if (!$("more").hidden && !e.target.closest(".more-wrap")) setMore(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { setMore(false); setDrawer(false); } });
  $("reset").addEventListener("click", () => update({ ...DEFAULTS }));

  /* mobile drawer */
  const setDrawer = (open) => { $("side").classList.toggle("open", open); $("scrim").hidden = !open; $("menu").setAttribute("aria-expanded", open); };
  $("menu").addEventListener("click", () => setDrawer(true));
  $("scrim").addEventListener("click", () => setDrawer(false));

  /* navigation */
  const toJobs = () => { if (location.hash) history.replaceState(null, "", location.pathname + location.search); route(); };
  $("view-jobs").addEventListener("click", () => { setDrawer(false); toJobs(); });
  $("view-tracker").addEventListener("click", () => { setDrawer(false); location.hash = "tracker"; });
  window.addEventListener("hashchange", route);
  document.querySelector(".tracks").addEventListener("click", (e) => {
    const b = e.target.closest("[data-track]");
    if (!b) return;
    track = b.dataset.track;
    setDrawer(false);
    toJobs();
  });

  /* toast */
  let toastTimer;
  function toast(text, actions = []) {
    const t = $("toast");
    t.innerHTML = `<span>${esc(text)}</span>` + actions.map((a, i) => `<button type="button" data-toast="${i}">${esc(a.label)}</button>`).join("");
    t.hidden = false;
    t.classList.remove("show");
    void t.offsetWidth;
    t.classList.add("show");
    t.onclick = (e) => {
      const b = e.target.closest("[data-toast]");
      if (!b) return;
      actions[+b.dataset.toast].run();
      t.hidden = true;
    };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
  }

  /* share */
  $("share").addEventListener("click", async () => {
    toURL();
    try { await navigator.clipboard.writeText(location.href); toast("Link copied. Anyone with it sees this exact search."); }
    catch { toast("Copy the address bar to share this search."); }
  });

  /* "/" focuses search */
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^(input|textarea|select)$/i.test(document.activeElement?.tagName || "")) return;
    if ($("job-controls").hidden) return;
    e.preventDefault();
    $("q").focus();
  });

  /* theme */
  const root = document.documentElement;
  const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const paintTheme = () => { $("theme-label").textContent = isDark() ? "Light mode" : "Dark mode"; };
  const savedTheme = store.get("theme", null);
  if (savedTheme) root.dataset.theme = savedTheme;
  paintTheme();
  $("theme").addEventListener("click", () => { root.dataset.theme = isDark() ? "light" : "dark"; store.set("theme", root.dataset.theme); paintTheme(); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", paintTheme);

  $("co-card").addEventListener("click", (e) => { if (e.target === $("co-card")) $("co-card").close(); });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-co-close]")) { $("co-card").close(); return; }
    const coBtn = e.target.closest("[data-company]");
    if (coBtn) { openCompany(coBtn.dataset.company, coBtn.dataset.track || "startup"); return; }
    const spec = e.target.closest("[data-spec]");
    if (spec) {
      const v = spec.dataset.spec;
      update({ sub: !v ? [] : state.sub.includes(v) ? state.sub.filter((x) => x !== v) : [...state.sub, v] });
      return;
    }
    const open = e.target.closest("[data-open]");
    if (open) {
      seen[open.dataset.open] = Date.now();
      store.set("seen", seen);
      open.closest("tr")?.classList.add("seen");
      return;
    }
    const sortBtn = e.target.closest("[data-sort]");
    if (sortBtn) { update({ sort: sortBtn.dataset.sort }); return; }
    const chip = e.target.closest(".chip-toggle");
    if (chip) {
      const { key, val, multi } = chip.dataset;
      if (multi === "1") {
        const cur = state[key];
        update({ [key]: cur.includes(val) ? cur.filter((x) => x !== val) : [...cur, val] });
      } else update({ [key]: val });
      return;
    }
    const sv = e.target.closest("[data-save]");
    if (sv) {
      const id = sv.dataset.save;
      const setSaved = (entry) => {
        if (entry) saved[id] = entry; else delete saved[id];
        store.set("saved", saved);
        document.querySelectorAll(`[data-save="${id}"]`).forEach((b) => b.setAttribute("aria-pressed", !!entry));
        $("saved-count").textContent = Object.keys(saved).length;
      };
      if (saved[id]) {
        const prev = saved[id];
        setSaved(null);
        toast("Removed from Tracker", [{ label: "Undo", run: () => setSaved(prev) }]);
      } else {
        const j = JOBS.find((x) => x.id === id);
        setSaved({ stage: "Saved", ts: Date.now(), note: "", job: { t: j.t, url: j.url, lvl: j.lvl, yoe: j.yoe, pay: j.pay, loc: j.loc, rem: j.rem, at: j.at, sub: j.sub, co: j.co } });
        toast(`Saved · ${j.co.n || ""}`, [{ label: "Open Tracker", run: () => { location.hash = "tracker"; } }, { label: "Undo", run: () => setSaved(null) }]);
      }
      return;
    }
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      const id = rm.dataset.remove, prev = saved[id];
      delete saved[id]; store.set("saved", saved); render();
      toast("Removed from Tracker", [{ label: "Undo", run: () => { saved[id] = prev; store.set("saved", saved); render(); } }]);
      return;
    }
    if (e.target.closest("#load-more")) { shown += PAGE; render(); return; }
    if (e.target.closest("[data-reset]")) update({ ...DEFAULTS });
  });
  document.addEventListener("change", (e) => {
    const st = e.target.closest("[data-stage]");
    if (st) {
      const row = saved[st.dataset.stage];
      row.stage = st.value;
      if (st.value === "Applied" && !row.applied) row.applied = todayISO();
      store.set("saved", saved);
      render();
      return;
    }
    const applied = e.target.closest("[data-applied]");
    if (applied) { saved[applied.dataset.applied].applied = applied.value; store.set("saved", saved); }
    const follow = e.target.closest("[data-follow]");
    if (follow) { saved[follow.dataset.follow].follow = follow.value; store.set("saved", saved); render(); }
  });
  document.addEventListener("input", (e) => {
    const n = e.target.closest("[data-note]");
    if (n) { saved[n.dataset.note].note = n.value; store.set("saved", saved); }
  });

  load().catch((err) => {
    $("content").innerHTML = `<div class="empty"><h2>Jobs didn't load</h2><p>The job list couldn't be read (${esc(err.message)}). Refresh the page to try again.</p></div>`;
  });
})();
