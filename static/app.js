/* AI Job Agent — Frontend */
"use strict";

document.body.setAttribute('data-js-loaded', '');
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ============================ DASHBOARD ============================ */
let statusChart = null, monthlyChart = null;

async function loadDashboard() {
  try {
    const s = await api("/api/dashboard/stats");
    // Stats cards
    $("#dash-stats").innerHTML = `
      <div class="stat-card"><div class="stat-num">${s.total}</div><div class="stat-label">Stellen Gesamt</div></div>
      <div class="stat-card green"><div class="stat-num">${s.applied}</div><div class="stat-label">Aktiv (Beworben+)</div></div>
      <div class="stat-card orange"><div class="stat-num">${s.interviews}</div><div class="stat-label">Im Gespräch</div></div>
      <div class="stat-card"><div class="stat-num">${s.offers}/${s.rejected}</div><div class="stat-label">Offer / Absagen</div></div>`;
    // Charts
    const labels = Object.keys(s.status_dist);
    const data = Object.values(s.status_dist);
    const colors = ["#4361ee","#2563eb","#0d9488","#d97706","#ea580c","#c2410c","#7c3aed","#059669","#dc2626","#6b7280"];
    const ctx1 = document.getElementById("chart-status")?.getContext("2d");
    if (ctx1) {
      if (statusChart) statusChart.destroy();
      statusChart = new Chart(ctx1, { type: "doughnut", data: { labels, datasets: [{ data, backgroundColor: colors.slice(0, labels.length), borderWidth: 0 }] }, options: { plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10 } } } } } });
    }
    const ctx2 = document.getElementById("chart-monthly")?.getContext("2d");
    if (ctx2) {
      if (monthlyChart) monthlyChart.destroy();
      monthlyChart = new Chart(ctx2, { type: "bar", data: { labels: s.monthly_labels, datasets: [{ data: s.monthly_counts, backgroundColor: "#4361ee", borderRadius: 4 }] }, options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 5 } } } } });
    }
    // Recs
    const recs = s.top_recs || [];
    $("#dash-recs").innerHTML = recs.length
      ? recs.map(r => `<div class="rec-item"><span class="rec-score">${r.match_score}%</span> ${esc(r.company)} — ${esc(r.title)} · 📍${esc(r.location||"")}</div>`).join("")
      : '<span class="muted">Noch keine bewerteten Empfehlungen. Gehe zu 🔍 Jobsuche und bewerte Stellen.</span>';
  } catch (e) { /* silent */ }
}

// Manuell Stellen suchen & bewerten (früher: täglicher Cron)
$("#btn-rec-refresh")?.addEventListener("click", async () => {
  const btn = $("#btn-rec-refresh");
  btn.disabled = true;
  btn.textContent = "⏳ Suche läuft (2-4 Min)…";
  $("#rec-status").textContent = "";
  try {
    const res = await api("/api/recommend/run", { method: "POST" });
    $("#rec-status").textContent = `✅ ${res.new} neue Stellen in Merkliste hinzugefügt. Siehe 📋 Kanban.`;
    toast(`✅ ${res.new} neue Empfehlungen`);
    refreshJobs();
    loadDashboard();
  } catch (err) {
    $("#rec-status").textContent = "❌ " + err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 Stellen suchen & bewerten";
  }
});

// Such-Präferenzen laden & speichern
async function loadPrefs() {
  try {
    const s = await api("/api/settings");
    const set = (id, key) => { const el = $(id); if (el) el.value = s[key] || ""; };
    set("#pref-companies", "prefer_companies");
    set("#pref-locations", "prefer_locations");
    set("#pref-keywords", "prefer_keywords");
    set("#pref-exclude", "exclude_keywords");
  } catch (e) { /* silent */ }
}
$("#btn-save-prefs")?.addEventListener("click", async () => {
  try {
    await api("/api/settings", { method: "PUT", body: {
      prefer_companies: $("#pref-companies").value.trim(),
      prefer_locations: $("#pref-locations").value.trim(),
      prefer_keywords: $("#pref-keywords").value.trim(),
      exclude_keywords: $("#pref-exclude").value.trim(),
    }});
    $("#pref-status").textContent = "✅ Gespeichert — gilt ab der nächsten Suche.";
    toast("Präferenzen gespeichert ✓");
  } catch (err) { $("#pref-status").textContent = "❌ " + err.message; }
});

// Load dashboard when tab is shown
const dashObserver = new MutationObserver(() => {
  if (!$("#tab-dashboard")?.hidden) loadDashboard();
});
dashObserver.observe(document.getElementById("tab-dashboard"), { attributes: true, attributeFilter: ["hidden"] });

const STATUSES = [
  { id: "wishlist", label: "📌 Merkliste", color: "#4361ee", row: 1 },
  { id: "applied", label: "✉️ Beworben", color: "#2563eb", row: 1 },
  { id: "confirmed", label: "✅ Bestätigt", color: "#0d9488", row: 1 },
  { id: "offer", label: "🎉 Angebot", color: "#059669", row: 1 },
  { id: "rejected", label: "❌ Absage", color: "#dc2626", row: 1 },
  { id: "withdrawn", label: "↩️ Zurückgezogen", color: "#6b7280", row: 1 },
  { id: "interview_1", label: "🗣️ Erstgespräch", color: "#d97706", row: 2 },
  { id: "interview_2", label: "🗣️ Zweitgespräch", color: "#ea580c", row: 2 },
  { id: "interview_3", label: "🗣️ Drittgespräch", color: "#c2410c", row: 2 },
  { id: "assessment", label: "🧩 Assessment", color: "#7c3aed", row: 2 },
];
const KIND_LABEL = { anschreiben: "Anschreiben", lebenslauf: "Lebenslauf", interview: "Interview" };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let jobs = [];
let currentJob = null;

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 3200);
}

function scoreBadge(score) {
  if (score === null || score === undefined) return '<span class="badge none">–</span>';
  const cls = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
  return `<span class="badge ${cls}">${score}%</span>`;
}

/* ============================ TABS ============================ */
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");
  document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = true));
  $("#tab-" + btn.dataset.tab).hidden = false;
  if (btn.dataset.tab === "kanban") renderKanban();
});

/* ============================ KANBAN ============================ */
function renderKanban() {
  const board = $("#kanban");
  const counts = {};
  STATUSES.forEach((s) => (counts[s.id] = 0));
  const q = ($("#kb-search")?.value || "").trim().toLowerCase();
  const t = $("#kb-type")?.value || "";
  let filtered = q
    ? jobs.filter(j => (j.company + " " + j.title + " " + (j.location || "") + " " + (j.notes || "")).toLowerCase().includes(q))
    : jobs;
  if (t) filtered = filtered.filter(j => j.job_type === t);
  filtered.forEach((j) => (counts[j.status] = (counts[j.status] || 0) + 1));
  $("#kanban-stats").textContent = `${filtered.length}/${jobs.length} Stellen · ${counts.applied} beworben`;

  function renderRow(rowId) {
    const cols = STATUSES.filter(s => s.row === rowId);
    return `<div class="kanban-row">${cols.map((s) => `
      <div class="column" data-status="${s.id}" style="--col:${s.color}">
        <div class="col-head">
          <span>${s.label}</span><span class="count">${counts[s.id] || 0}</span>
        </div>
        <div class="col-body">
          ${filtered.filter((j) => j.status === s.id).map(jobCard).join("") || '<div class="muted col-empty">—</div>'}
        </div>
      </div>`).join("")}</div>`;
  }
  board.innerHTML = renderRow(1) + renderRow(2);

  // Click on column header → open status list view
  board.querySelectorAll(".col-head").forEach((head) => {
    head.style.cursor = "pointer";
    head.title = "Alle in dieser Liste anzeigen";
    head.addEventListener("click", (e) => {
      e.stopPropagation();
      openStatusView(head.closest(".column").dataset.status);
    });
  });

  board.querySelectorAll(".column").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("dragover"); });
    col.addEventListener("dragleave", () => col.classList.remove("dragover"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      col.classList.remove("dragover");
      const id = e.dataTransfer.getData("text/plain");
      if (!id) return;
      const job = jobs.find((j) => j.id == id);
      if (!job || job.status === col.dataset.status) return;
      try {
        await api(`/api/jobs/${id}`, { method: "PATCH", body: { status: col.dataset.status } });
        job.status = col.dataset.status;
        renderKanban();
        toast(`→ ${STATUSES.find((s) => s.id === col.dataset.status).label}`);
      } catch (err) { toast(err.message, true); }
    });
  });
}

function jobCard(j) {
  const JOB_TYPE_LABEL = {
    praktikum: { text: "Praktikum", cls: "jt-praktikum" },
    pflichtpraktikum: { text: "Pflichtpraktikum", cls: "jt-pflicht" },
    werkstudent: { text: "Werkstudent", cls: "jt-werkstudent" },
    junior: { text: "Junior", cls: "jt-junior" },
    trainee: { text: "Trainee", cls: "jt-trainee" },
    abschlussarbeit: { text: "Abschlussarbeit", cls: "jt-abschluss" },
  };
  const jt = JOB_TYPE_LABEL[j.job_type];
  return `
  <div class="job-card" draggable="true" data-id="${j.id}">
    <div class="jc-top">
      <span class="jc-company">${esc(j.company || "Unbekannt")}</span>
      ${scoreBadge(j.match_score)}
    </div>
    <div class="jc-title">${esc(j.title || "")}</div>
    ${jt ? `<span class="job-type-tag ${jt.cls}">${jt.text}</span>` : ""}
    <div class="jc-meta">
      ${j.location ? `<span>📍 ${esc(j.location)}</span>` : ""}
      ${j.deadline ? `<span>⏰ ${esc(j.deadline)}</span>` : ""}
    </div>
  </div>`;
}

document.addEventListener("dragstart", (e) => {
  const card = e.target.closest(".job-card");
  if (!card) return;
  e.dataTransfer.setData("text/plain", card.dataset.id);
  card.classList.add("dragging");
});
document.addEventListener("dragend", (e) => e.target.closest(".job-card")?.classList.remove("dragging"));
document.addEventListener("click", (e) => {
  const card = e.target.closest(".job-card");
  if (card) openModal(Number(card.dataset.id));
});

// ---------- Status view (big list per status) ----------
let _statusView = null;
function openStatusView(statusId) {
  const st = STATUSES.find((s) => s.id === statusId);
  if (!st) return;
  _statusView = statusId;
  $("#status-modal-title").textContent = st.label;
  $("#status-search").value = "";
  renderStatusView();
  $("#status-modal").hidden = false;
}
function renderStatusView() {
  const q = ($("#status-search").value || "").trim().toLowerCase();
  const list = jobs.filter((j) => j.status === _statusView && (!q || (j.company + " " + j.title + " " + (j.location || "")).toLowerCase().includes(q)));
  $("#status-list").innerHTML = list.length
    ? list.map((j) => `
      <div class="status-item" data-open-job="${j.id}">
        <span class="si-score">${j.match_score ? j.match_score + "%" : "–"}</span>
        <div class="si-main">
          <div class="si-company">${esc(j.company)}</div>
          <div class="si-title">${esc(j.title || "")}</div>
        </div>
        <span class="si-loc">${esc(j.location || "")}</span>
        <button class="si-del" data-del-job="${j.id}" title="Löschen">🗑️</button>
      </div>`).join("")
    : '<div class="muted" style="padding:20px;text-align:center">Keine passenden Einträge</div>';
}
$("#status-search")?.addEventListener("input", renderStatusView);
document.addEventListener("click", (e) => {
  const open = e.target.closest("[data-open-job]");
  if (open) { openModal(Number(open.dataset.openJob)); return; }
  const del = e.target.closest("[data-del-job]");
  if (del) {
    e.stopPropagation();
    const id = Number(del.dataset.delJob);
    if (confirm("Diesen Eintrag wirklich löschen?")) {
      (async () => {
        try {
          await api(`/api/jobs/${id}`, { method: "DELETE" });
          jobs = jobs.filter((j) => j.id !== id);
          renderStatusView();
          renderKanban();
          refreshJobs();
          toast("Gelöscht 🗑️");
        } catch (err) { toast(err.message, true); }
      })();
    }
  }
  const close = e.target.closest("[data-close-status]");
  if (close) $("#status-modal").hidden = true;
});

// ---------- Trash zone (drag to delete) ----------
const trashZone = $("#trash-zone");
if (trashZone) {
  trashZone.addEventListener("dragover", (e) => {
    if (e.dataTransfer.types.includes("text/plain")) { e.preventDefault(); trashZone.classList.add("over"); }
  });
  trashZone.addEventListener("dragleave", () => trashZone.classList.remove("over"));
  trashZone.addEventListener("drop", (e) => {
    e.preventDefault();
    trashZone.classList.remove("over");
    const id = e.dataTransfer.getData("text/plain");
    if (!id) return;
    if (!confirm("Diesen Eintrag wirklich löschen?")) return;
    (async () => {
      try {
        await api(`/api/jobs/${id}`, { method: "DELETE" });
        jobs = jobs.filter((j) => j.id != id);
        renderKanban();
        refreshJobs();
        toast("Gelöscht 🗑️");
      } catch (err) { toast(err.message, true); }
    })();
  });
  // Click trash = clear? No — keep it drag-only to avoid accidents.
}

$("#kb-search")?.addEventListener("input", () => renderKanban());
$("#kb-type")?.addEventListener("change", () => renderKanban());
document.addEventListener("keydown", (e) => {
  // Ctrl/Cmd+F fokussiert die Kanban-Suche
  if ((e.metaKey || e.ctrlKey) && e.key === "f" && !$("#tab-kanban").hidden) {
    e.preventDefault();
    $("#kb-search").focus();
  }
});

$("#btn-add-job").addEventListener("click", async () => {
  try {
    const job = await api("/api/jobs", { method: "POST", body: {} });
    jobs.unshift(job);
    renderKanban();
    openModal(job.id, true);
  } catch (err) { toast(err.message, true); }
});

async function refreshJobs() {
  jobs = await api("/api/jobs");
  renderKanban();
}

/* ============================ MODAL ============================ */
const overlay = $("#modal-overlay");

async function openModal(id, isNew = false) {
  try {
    currentJob = await api(`/api/jobs/${id}`);
    if (isNew) {
      // Neu angelegte Stelle: Felder ausklappen lassen
      currentJob.title = "";
    }
    fillModal();
    overlay.hidden = false;
  } catch (err) { toast(err.message, true); }
}

function fillModal() {
  const j = currentJob;
  $("#m-title").textContent = j.title || "Neue Stelle";
  $("#m-company").textContent = `${j.company || ""}${j.location ? " · " + j.location : ""}`;
  $("#m-detail").innerHTML = `
    ${j.location ? "<dt>Ort</dt><dd>" + esc(j.location) + "</dd>" : ""}
    ${j.salary ? "<dt>Gehalt</dt><dd>" + esc(j.salary) + "</dd>" : ""}
    ${j.deadline ? "<dt>Deadline</dt><dd>" + esc(j.deadline) + "</dd>" : ""}
    ${j.url ? '<dt>Quelle</dt><dd><a href="' + esc(j.url) + '" target="_blank" rel="noopener">' + esc(j.url) + "</a></dd>" : ""}
    ${j.description ? "<dt>Beschreibung</dt><dd>" + esc(j.description) + "</dd>" : ""}
    ${j.notes ? "<dt>Notizen</dt><dd>" + esc(j.notes) + "</dd>" : ""}`;
  $("#m-status").innerHTML = STATUSES.map((s) =>
    `<option value="${s.id}" ${j.status === s.id ? "selected" : ""}>${s.label}</option>`).join("");
  $("#m-notes").value = j.notes || "";
  renderMatch(j);
  loadDrafts(j.id);
  loadTimeline(j.id);
}

function renderMatch(j) {
  let html = "";
  if (j.match_score !== null && j.match_score !== undefined) {
    let reasons = {};
    try { reasons = JSON.parse(j.match_reasons || "{}"); } catch (_) {}
    html = `<div class="score-line">
      <span class="score-ring" style="color:${j.match_score >= 70 ? "var(--green)" : j.match_score >= 40 ? "var(--orange)" : "var(--red)"}">${j.match_score}%</span>
      <div>${esc(reasons.summary || "")}</div></div>`;
    if (reasons.strengths?.length) html += `<b>Stärken:</b><ul>${reasons.strengths.map((s) => "<li>" + esc(s) + "</li>").join("")}</ul>`;
    if (reasons.gaps?.length) html += `<b>Lücken:</b><ul>${reasons.gaps.map((s) => "<li>" + esc(s) + "</li>").join("")}</ul>`;
    if (reasons.advice) html += `<b>Empfehlung:</b> ${esc(reasons.advice)}`;
  } else {
    html = '<span class="muted">Noch kein Match berechnet.</span>';
  }
  $("#m-match").innerHTML = `<div class="matchbox">${html}</div>`;
}

async function loadDrafts(jobId) {
  const drafts = await api(`/api/jobs/${jobId}/drafts`);
  const box = $("#m-drafts");
  if (!drafts.length) { box.innerHTML = '<span class="muted">Noch keine Dokumente generiert.</span>'; return; }
  box.innerHTML = drafts.map((d) => `
    <div class="draft-item">
      <div class="draft-head">
        <span class="kind">${KIND_LABEL[d.kind] || d.kind}<span class="kind-tag kind-${d.kind}">${esc(d.kind)}</span></span>
        <span>${esc(d.created_at)} <button class="btn ghost" data-copy="${d.id}">📋 Kopieren</button>
        <button class="btn ghost" data-export="${currentJob.id}" data-export-kind="${d.kind}">📥 .docx</button></span>
      </div>
      <div class="draft-body" id="draft-${d.id}">${esc(d.content)}</div>
    </div>`).join("");
}

async function loadTimeline(jobId) {
  const log = await api(`/api/jobs/${jobId}/timeline`);
  const box = $("#m-timeline");
  if (!log.length) { box.innerHTML = '<span class="muted">Noch keine Statusänderungen.</span>'; return; }
  const labels = {};
  STATUSES.forEach(s => labels[s.id] = s.label);
  box.innerHTML = log.map(l => `
    <div style="display:flex;gap:8px;padding:4px 0;font-size:12px;border-bottom:1px solid var(--line)">
      <span style="color:var(--muted);min-width:100px">${esc(l.created_at)}</span>
      <span style="min-width:20px;text-align:center">→</span>
      <span>${esc(labels[l.old_status] || l.old_status)} → <b>${esc(labels[l.new_status] || l.new_status)}</b></span>
      <span style="color:var(--muted);margin-left:auto;font-size:10px">${esc(l.source)}</span>
    </div>`).join("");
}

$("#m-close").addEventListener("click", () => { overlay.hidden = true; });
overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

document.querySelector(".m-actions").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ai]");
  if (!btn || !currentJob) return;
  const kind = btn.dataset.ai;
  btn.disabled = true;
  $("#m-spinner").hidden = false;
  $("#m-error").hidden = true;
  try {
    if (kind === "match") {
      const res = await api("/api/ai/match", { method: "POST", body: { job_id: currentJob.id } });
      currentJob.match_score = res.score;
      currentJob.match_reasons = JSON.stringify(res);
      renderMatch(currentJob);
      refreshJobs();
    } else {
      const draft = await api(`/api/ai/${kind}`, { method: "POST", body: { job_id: currentJob.id } });
      await loadDrafts(currentJob.id);
      toast(`${KIND_LABEL[kind] || kind} generiert ✓`);
      // Scroll zum neuen Dokument
      document.querySelector("#m-drafts .draft-item")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  } catch (err) {
    $("#m-error").textContent = err.message;
    $("#m-error").hidden = false;
  } finally {
    btn.disabled = false;
    $("#m-spinner").hidden = true;
  }
});

document.addEventListener("click", async (e) => {
  const copyBtn = e.target.closest("[data-copy]");
  if (copyBtn) {
    const txt = document.getElementById("draft-" + copyBtn.dataset.copy)?.textContent || "";
    try { await navigator.clipboard.writeText(txt); toast("Kopiert ✓"); }
    catch (_) { toast("Kopieren nicht möglich", true); }
  }
  const exportBtn = e.target.closest("[data-export]");
  if (exportBtn) {
    exportBtn.disabled = true;
    try {
      const res = await api(`/api/jobs/${exportBtn.dataset.export}/export/${exportBtn.dataset.exportKind}`, { method: "POST" });
      toast(`Gespeichert: ${res.path}`);
      if (res.path) {
        // Offer to reveal in Finder
        const toastEl = $("#toast");
        const revealBtn = document.createElement("button");
        revealBtn.className = "btn ghost";
        revealBtn.textContent = "📂 Im Finder zeigen";
        revealBtn.style.marginLeft = "8px";
        revealBtn.style.padding = "2px 8px";
        revealBtn.onclick = async () => { try { await api("/api/reveal", { method: "POST", body: { path: res.path } }); } catch (e2) { toast(e2.message, true); } };
        toastEl.appendChild(revealBtn);
      }
    } catch (err) { toast(err.message, true); }
    finally { exportBtn.disabled = false; }
  }
});

$("#m-save-info").addEventListener("click", async () => {
  try {
    currentJob = await api(`/api/jobs/${currentJob.id}`, {
      method: "PATCH",
      body: { status: $("#m-status").value, notes: $("#m-notes").value },
    });
    fillModal();
    refreshJobs();
    toast("Gespeichert ✓");
  } catch (err) { toast(err.message, true); }
});

$("#m-delete").addEventListener("click", async () => {
  if (!confirm("Stelle wirklich löschen?")) return;
  try {
    await api(`/api/jobs/${currentJob.id}`, { method: "DELETE" });
    overlay.hidden = true;
    refreshJobs();
    toast("Gelöscht");
  } catch (err) { toast(err.message, true); }
});

/* ============================ JOBSUCHE ============================ */
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("#s-query").value.trim();
  if (!query) return;
  const box = $("#search-results");
  box.innerHTML = "Suche läuft… ⏳";
  $("#search-errors").textContent = "";
  try {
    const res = await api("/api/search", {
      method: "POST",
      body: { query, location: $("#s-location").value.trim(), source: $("#s-source").value },
    });
    $("#search-errors").textContent = res.errors.join("\n") || "";
    box.innerHTML = res.results.length
      ? res.results.map(searchCard).join("")
      : '<span class="muted">Keine Ergebnisse.</span>';
  } catch (err) { box.innerHTML = ""; $("#search-errors").textContent = err.message; }
});

function searchCard(r) {
  return `
  <div class="result">
    <div class="r-top">
      <div>
        <h4>${esc(r.title)}</h4>
        <div class="r-meta">
          <span>🏢 ${esc(r.company || "–")}</span>
          <span>📍 ${esc(r.location || "–")}</span>
          ${r.salary ? "<span>💰 " + esc(r.salary) + "</span>" : ""}
          <span>🔖 ${esc(r.source)}</span>
        </div>
      </div>
    </div>
    <div class="r-desc">${esc(r.description)}</div>
    <div class="r-actions">
      <button class="btn primary" data-save="${JSON.stringify(r).replace(/"/g, "&quot;")}">🎯 Bewerten & merken</button>
      <button class="btn" data-save-nomatch="${JSON.stringify(r).replace(/"/g, "&quot;")}">Nur merken</button>
      ${r.url ? `<a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">↗ Original</a>` : ""}
    </div>
  </div>`;
}

async function importAndOpen(jobData, doMatch) {
  try {
    const res = await api("/api/import", { method: "POST", body: { jobs: [{ ...jobData }] } });
    const id = res.ids[0];
    if (doMatch) {
      const m = await api("/api/ai/match", { method: "POST", body: { job_id: id } });
      await api(`/api/jobs/${id}`, { method: "PATCH", body: { match_score: m.score, match_reasons: JSON.stringify(m) } });
    }
    await refreshJobs();
    await openModal(id);
    toast(doMatch ? "Bewertet & gemerkt ✓" : "Gemerkt ✓");
  } catch (err) { toast(err.message, true); }
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-save]");
  if (btn) { importAndOpen(JSON.parse(btn.dataset.save), true); return; }
  const btn2 = e.target.closest("[data-save-nomatch]");
  if (btn2) importAndOpen(JSON.parse(btn2.dataset.save), false);
});

/* ---- JD extrahieren ---- */
$("#extract-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await doExtract($("#e-url").value.trim(), "");
});
$("#e-extract").addEventListener("click", () => doExtract("", $("#e-text").value.trim()));

async function doExtract(url, text) {
  const box = $("#extract-result");
  box.innerHTML = "Extrahieren… ⏳";
  try {
    const r = await api("/api/ai/extract", { method: "POST", body: { url, text } });
    box.innerHTML = `
      <div class="result">
        <h4>${esc(r.title || "—")}</h4>
        <div class="r-meta">
          <span>🏢 ${esc(r.company || "–")}</span>
          <span>📍 ${esc(r.location || "–")}</span>
          ${r.salary ? "<span>💰 " + esc(r.salary) + "</span>" : ""}
          ${r.deadline ? "<span>⏰ " + esc(r.deadline) + "</span>" : ""}
          ${r.employment_type ? "<span>🕒 " + esc(r.employment_type) + "</span>" : ""}
        </div>
        <div class="r-desc" style="max-height:150px">${esc(r.description)}</div>
        ${r.requirements?.length ? "<div class='r-desc'><b>Anforderungen:</b><ul>" + r.requirements.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul></div>" : ""}
        <div class="r-actions">
          <button class="btn primary" id="extract-save">🎯 Bewerten & merken</button>
        </div>
      </div>`;
    $("#extract-save").dataset.job = JSON.stringify({
      company: r.company, title: r.title, location: r.location, url: r.url || url,
      salary: r.salary, deadline: r.deadline, description: r.description,
      source: "extract", notes: (r.requirements || []).join("; "),
    });
    $("#extract-save").addEventListener("click", () => importAndOpen(JSON.parse($("#extract-save").dataset.job), true));
  } catch (err) {
    box.innerHTML = `<div class="errors">${esc(err.message)}</div>`;
  }
}

/* ============================ PROFIL ============================ */
async function loadProfile() {
  const p = await api("/api/profile");
  document.querySelectorAll("#profile-form [name]").forEach((el) => {
    el.value = p[el.name] ?? "";
  });
}
$("#profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {};
  document.querySelectorAll("#profile-form [name]").forEach((el) => (body[el.name] = el.value));
  try {
    await api("/api/profile", { method: "PUT", body });
    $("#profile-saved").textContent = "Gespeichert ✓";
    setTimeout(() => ($("#profile-saved").textContent = ""), 2500);
  } catch (err) { toast(err.message, true); }
});

/* ============================ EINSTELLUNGEN ============================ */
async function loadSettings() {
  const s = await api("/api/settings");
  document.querySelectorAll("#settings-form [name]").forEach((el) => {
    if (el.name in s) el.value = s[el.name];
  });
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {};
  document.querySelectorAll("#settings-form [name]").forEach((el) => (body[el.name] = el.value));
  try {
    await api("/api/settings", { method: "PUT", body });
    toast("Einstellungen gespeichert ✓");
  } catch (err) { toast(err.message, true); }
});
$("#btn-test-key").addEventListener("click", async () => {
  const btn = $("#btn-test-key");
  btn.disabled = true;
  btn.textContent = "Prüfe…";
  try {
    await api("/api/ai/test", { method: "POST" });
    toast("API-Key funktioniert ✓");
  } catch (err) { toast(err.message, true); }
  finally { btn.disabled = false; btn.textContent = "Teste API-Key"; }
});

/* ============================ EMAIL ============================ */
async function loadEmails() {
  try {
    const emails = await api("/api/emails");
    renderEmails(emails);
  } catch (err) { $("#email-errors").textContent = err.message; }
}

const EMAIL_COLORS = {
  interview: { bg: "rgba(243,156,18,.15)", color: "var(--orange)", label: "🎙️ Interview" },
  rejected: { bg: "rgba(231,76,60,.15)", color: "var(--red)", label: "❌ Absage" },
  offer: { bg: "rgba(46,204,113,.2)", color: "var(--green)", label: "🎉 Angebot" },
  confirmed: { bg: "rgba(26,188,156,.15)", color: "var(--green)", label: "✅ Bestätigung" },
  note: { bg: "rgba(79,140,255,.1)", color: "var(--accent)", label: "📝 Rückmeldung" },
  ignore: { bg: "transparent", color: "var(--muted)", label: "Kein Bezug" },
};

function renderEmails(emails) {
  const box = $("#email-list");
  if (!emails.length) { box.innerHTML = '<span class="muted">Noch keine Mails analysiert. Zugangsdaten unten eintragen → "Jetzt prüfen".</span>'; return; }
  const allJobs = window.jobs || [];
  const jobOpts = allJobs.map(j => `<option value="${j.id}">${esc(j.company)} — ${esc(j.title.slice(0,40))}</option>`).join("");
  box.innerHTML = emails.map(e => {
    const cls = EMAIL_COLORS[e.classification] || EMAIL_COLORS.ignore;
    const jobName = allJobs.find(j => j.id == e.job_id);
    const job = jobName ? `→ <b>${esc(jobName.company)}</b>` : (e.job_id ? `→ <b>#${esc(e.job_id)}</b>` : "");
    const applied = e.applied ? '<span style="color:var(--green)">✓ erledigt</span>' : `
      <button class="btn primary" data-apply="${e.id}">Übernehmen</button>
      <button class="btn" data-dismiss="${e.id}">Ignorieren</button>`;
    return `<div class="result" style="border-left:3px solid ${cls.color}">
      <div class="r-top"><div><h4>${esc(e.subject)}</h4>
      <div class="r-meta"><span>📧 ${esc(e.from_addr)}</span><span>🕐 ${esc(e.received_at || e.date || "")}</span></div></div>
      <span style="background:${cls.bg};color:${cls.color};padding:3px 8px;border-radius:4px;font-size:11px;white-space:nowrap">${cls.label} ${job}</span></div>
      <div class="r-desc" style="max-height:80px">${esc(e.summary)}</div>
      <div class="r-actions">${applied}
        <button class="btn ghost" data-edit-mail="${e.id}">✏️ Korrigieren</button></div>
      <div class="mail-edit" id="mail-edit-${e.id}" hidden style="margin-top:8px;padding:10px;background:var(--hover);border-radius:8px">
        <select id="me-cls-${e.id}">
          <option value="rejected" ${e.classification==="rejected"?"selected":""}>❌ Absage</option>
          <option value="interview" ${e.classification==="interview"?"selected":""}>🎙️ Interview</option>
          <option value="offer" ${e.classification==="offer"?"selected":""}>🎉 Angebot</option>
          <option value="confirmed" ${e.classification==="confirmed"?"selected":""}>✅ Bestätigung</option>
          <option value="note" ${e.classification==="note"?"selected":""}>📝 Nachricht</option>
          <option value="ignore" ${e.classification==="ignore"?"selected":""}>🚫 Kein Bezug</option>
        </select>
        <select id="me-job-${e.id}" style="margin-top:6px">
          <option value="">(kein Job / neu erstellen)</option>${jobOpts}
        </select>
        <div style="margin-top:6px">
          <button class="btn primary" data-save-mail="${e.id}">Speichern</button>
          <button class="btn ghost" data-cancel-mail="${e.id}">Abbrechen</button>
        </div>
      </div></div>`;
  }).join("");
}

document.addEventListener("click", (e) => {
  const editBtn = e.target.closest("[data-edit-mail]");
  if (editBtn) { document.getElementById("mail-edit-" + editBtn.dataset.editMail).hidden = false; }
  const cancelBtn = e.target.closest("[data-cancel-mail]");
  if (cancelBtn) { document.getElementById("mail-edit-" + cancelBtn.dataset.cancelMail).hidden = true; }
  const saveBtn = e.target.closest("[data-save-mail]");
  if (saveBtn) {
    const id = saveBtn.dataset.saveMail;
    const cls = document.getElementById(`me-cls-${id}`).value;
    const jobId = document.getElementById(`me-job-${id}`).value || null;
    (async () => {
      try {
        await api(`/api/emails/${id}`, { method: "PUT", body: { classification: cls, job_id: jobId } });
        toast("Korrigiert ✓ — erneut Übernehmen klicken");
        loadEmails();
      } catch (err) { toast(err.message, true); }
    })();
  }
});

$("#btn-check-mail").addEventListener("click", async () => {
  $("#email-spinner").hidden = false; $("#email-errors").textContent = "";
  try {
    const res = await api("/api/emails/check", { method: "POST" });
    toast(`${res.count || 0} neue Mails gefunden`);
    await loadEmails();
  } catch (err) { $("#email-errors").textContent = err.message; }
  finally { $("#email-spinner").hidden = true; }
});

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-apply]");
  if (btn) {
    btn.disabled = true;
    try {
      await api(`/api/emails/${btn.dataset.apply}/apply`, { method: "POST" });
      await Promise.all([loadEmails(), refreshJobs()]);
      toast("Übernommen ✓");
    } catch (err) { toast(err.message, true); btn.disabled = false; }
  }
  const btn2 = e.target.closest("[data-dismiss]");
  if (btn2) {
    btn2.disabled = true;
    try {
      await api(`/api/emails/${btn2.dataset.dismiss}/dismiss`, { method: "POST" });
      await loadEmails();
    } catch (err) { toast(err.message, true); btn2.disabled = false; }
  }
});

// E-Mail-Settings quick-save
$("#em-save").addEventListener("click", async () => {
  try {
    await api("/api/settings", { method: "PUT", body: {
      email_imap_host: $("#em-host").value.trim(),
      email_imap_port: $("#em-port").value.trim(),
      email_address: $("#em-addr").value.trim(),
      email_password: $("#em-pw").value.trim(),
    }});
    toast("Zugangsdaten gespeichert ✓ — teste Verbindung…");
    // Trigger check immediately
    $("#btn-check-mail").click();
  } catch (err) { toast(err.message, true); }
});

/* ============================ ANALYSE ============================ */
$("#btn-analyze").addEventListener("click", async () => {
  $("#analyze-spinner").hidden = false; $("#analyze-error").hidden = true; $("#analyze-result").innerHTML = "";
  try {
    const data = await api("/api/ai/analyze", { method: "POST" });
    const cards = [
      ["📊","Zusammenfassung", data.summary],
      ["💪","Top-Stärken", (data.top_strengths||[]).map(s=>"• "+esc(s)).join("<br>")],
      ["⚠️","Lücken", (data.key_gaps||[]).map(s=>"• "+esc(s)).join("<br>")],
      ["🎯","Beste Rollen", (data.best_role_categories||[]).map(s=>"• "+esc(s)).join("<br>")],
      ["💡","Empfehlung", data.recommended_focus],
      ["📚","Lernenswerte Skills", (data.skill_recommendations||[]).map(s=>"• "+esc(s)).join("<br>")],
      ["🔍","Suchstrategie", data.search_strategy],
    ];
    $("#analyze-result").innerHTML = cards.map(([icon,title,content]) => `
      <div class="card" style="padding:14px 18px"><h3 style="margin:0 0 6px">${icon} ${title}</h3><p style="font-size:13px;line-height:1.6">${content||""}</p></div>`).join("");
  } catch(e) { $("#analyze-error").textContent = e.message; $("#analyze-error").hidden = false; }
  finally { $("#analyze-spinner").hidden = true; }
});

/* ============================ INIT ============================ */
(async function init() {
  try {
    await Promise.all([refreshJobs(), loadDashboard(), loadPrefs(), loadProfile(), loadSettings()]);
    // Pre-fill email settings form
    const s = await api("/api/settings");
    $("#em-host").value = s.email_imap_host || "imap.qq.com";
    $("#em-port").value = s.email_imap_port || "993";
    $("#em-addr").value = s.email_address || "";
    // password field left empty for security
  } catch (err) {
    toast("Fehler beim Laden: " + err.message, true);
  }
})();
