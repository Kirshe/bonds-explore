"use strict";

const $ = selector => document.querySelector(selector);
const state = { market: null, category: "", query: "", yield: "", payout: "", source: "", basis: "", status: "active", sort: "issuer", direction: 1, page: 1, pageSize: 10 };
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const pct = value => value == null ? "—" : `${Number(value).toFixed(2)}%`;
const formatTime = value => !value ? "Not published" : /^\d{4}-\d{2}-\d{2}$/.test(value) ? value + " (date only)" : new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " IST";
const typeLabel = category => ({ "State government": "State govt", "Central government": "Central govt", "Corporate / NBFC": "Corporate / NBFC", "Sovereign gold": "Sovereign gold" }[category] || category);
const typeClass = category => category === "Sovereign gold" ? "gold" : category === "Corporate / NBFC" ? "corporate" : "";
const safeURL = value => /^https:\/\//i.test(String(value)) ? escapeHTML(value) : "#methodology";
const inactive = bond => ["matured", "redeemed"].includes(bond.instrument_status);
const money = value => value == null ? "Not published" : `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 4 })}`;

async function loadMarket() {
  const response = await fetch("/api/bonds");
  if (!response.ok) throw new Error(`Unable to load observations (HTTP ${response.status}).`);
  state.market = await response.json();
  $("#source-filter").innerHTML = '<option value="">All sources</option>' + state.market.sources.map(source => `<option value="${escapeHTML(source.id)}">${escapeHTML(source.name)}</option>`).join("");
  $("#source-filter").value = state.source;
  $("#basis-filter").innerHTML = '<option value="">All yield bases</option>' + state.market.overview.yield_groups.map(group => `<option value="${escapeHTML(group.kind)}">${escapeHTML(group.label)}</option>`).join("") + '<option value="reference">Reference data</option>';
  $("#basis-filter").value = state.basis;
  renderOverview();
  renderSources();
  renderTable();
}

function renderOverview() {
  const { overview: data, sources } = state.market;
  const available = sources.filter(source => source.count > 0);
  const groups = data.yield_groups.filter(group => group.yield_count > 0);
  // Pooling unrelated yield definitions would create a misleading aggregate.
  const selected = state.basis ? data.yield_groups.find(group => group.kind === state.basis) : groups.length === 1 ? groups[0] : null;
  $("#metric-count").textContent = data.count.toLocaleString("en-IN");
  $("#metric-categories").textContent = `${data.observation_count} observations · ${data.categories.length} categories`;
  $("#median-label").textContent = selected ? `Median · ${selected.label}` : "Median · choose a yield basis";
  $("#metric-median").innerHTML = selected?.median_yield == null ? "—" : `${selected.median_yield.toFixed(2)}<small>%</small>`;
  $("#metric-yield-count").textContent = selected ? `${selected.yield_count} conventional observations · same basis` : "Choose a yield basis to compare like for like";
  $("#metric-range").textContent = selected ? `${pct(selected.min_yield)} – ${pct(selected.max_yield)}` : "—";
  $("#source-count").textContent = String(available.length).padStart(2, "0");
  $("#metric-source").textContent = `${sources.length} feeds configured · ${sources.filter(source => source.status === "error").length} refresh errors`;
  $("#total-pill").textContent = `${data.observation_count} observations`;
  const snapshot = available.filter(source => source.mode === "snapshot").length;
  $("#notice-title").textContent = snapshot ? "Includes source snapshot data · availability unverified." : "Source observations · availability unverified.";
  $("#notice-copy").textContent = `${available.length}/${sources.length} feeds have data. Quote/report dates are separate from collection times; unknown dates remain unknown.`;
  $("#chart-basis").textContent = selected?.label || "Select a yield basis";
  const categories = selected?.categories.filter(group => group.median_yield != null).sort((a, b) => b.median_yield - a.median_yield) || [];
  const max = Math.max(1, ...categories.map(group => group.median_yield));
  $("#yield-chart").innerHTML = categories.length ? categories.map(group => `<div class="chart-row"><span class="chart-label">${escapeHTML(typeLabel(group.name))}</span><div class="chart-track"><div class="chart-bar" style="width:${(group.median_yield / max * 100).toFixed(1)}%"></div></div><span class="chart-value">${pct(group.median_yield)}</span></div>`).join("") : '<p class="chart-footnote">Select a yield basis with collected observations. Unrelated yield definitions are shown separately.</p>';
}

function renderSources() {
  $("#source-list").innerHTML = state.market.sources.map(source => `<article class="source-card"><div class="source-card-head"><strong>${escapeHTML(source.name)}</strong><span class="pill ${source.status === "error" || source.mode === "snapshot" ? "amber" : "neutral"}">${escapeHTML(source.mode === "empty" ? "No records" : source.mode)}</span></div><dl><div><dt>Records / role</dt><dd>${source.count} / ${escapeHTML(source.role)}</dd></div><div><dt>Collected</dt><dd>${source.observed_at ? formatTime(source.observed_at) : "Not collected"}${source.observed_at && source.stale ? " · older than 24h" : ""}</dd></div><div><dt>Last fetch attempt</dt><dd>${source.attempted_at ? formatTime(source.attempted_at) : "Not attempted"}</dd></div><div><dt>Collection status</dt><dd>${escapeHTML(source.status)}</dd></div></dl>${source.error ? `<p class="source-error">${escapeHTML(source.error)}. Previous records retained.</p>` : ""}${source.note ? `<p class="source-note">${escapeHTML(source.note)}</p>` : ""}<a href="${safeURL(source.url)}" target="_blank" rel="noopener noreferrer">Original source ↗</a></article>`).join("");
}

function filteredBonds() {
  if (!state.market) return [];
  const query = state.query.trim().toLowerCase();
  return state.market.bonds.filter(bond => {
    if (state.category && bond.category !== state.category) return false;
    if (query && !`${bond.issuer} ${bond.isin || ""}`.toLowerCase().includes(query)) return false;
    if (state.payout && bond.payout !== state.payout) return false;
    if (state.source && bond.source_id !== state.source) return false;
    if (state.basis && bond.yield_kind !== state.basis) return false;
    if (state.status === "active" && inactive(bond)) return false;
    if (state.status === "defaulted" && bond.instrument_status !== "defaulted") return false;
    if (state.yield) {
      if (bond.yield_pct == null || bond.category === "Sovereign gold") return false;
      if (state.yield === "under7" && bond.yield_pct >= 7) return false;
      if (state.yield === "7to9" && (bond.yield_pct < 7 || bond.yield_pct > 9)) return false;
      if (state.yield === "over9" && bond.yield_pct <= 9) return false;
    }
    return true;
  }).sort((a, b) => {
    let left = a[state.sort], right = b[state.sort];
    if (state.sort === "yield_pct") {
      if (a.category === "Sovereign gold") left = null;
      if (b.category === "Sovereign gold") right = null;
      // With all bases visible, sort within basis groups, never rank an
      // indicative face-value calculation against a traded yield.
      if (left != null && right != null && a.yield_kind !== b.yield_kind) return a.yield_kind.localeCompare(b.yield_kind);
    }
    if (left == null && right == null) return a.id.localeCompare(b.id);
    if (left == null) return 1;
    if (right == null) return -1;
    const result = typeof left === "number" ? left - right : String(left).localeCompare(String(right));
    return result * state.direction || a.id.localeCompare(b.id);
  });
}

function renderTable() {
  const bonds = filteredBonds();
  const pages = Math.max(1, Math.ceil(bonds.length / state.pageSize));
  state.page = Math.min(pages, state.page);
  const start = (state.page - 1) * state.pageSize;
  $("#bond-rows").innerHTML = bonds.slice(start, start + state.pageSize).map(bond => {
    const gold = bond.category === "Sovereign gold";
    const icon = /government/.test(bond.category) ? "IN" : gold ? "Au" : bond.issuer.split(/\s+/).slice(0, 2).map(word => word[0]).join("");
    const status = bond.instrument_status !== "unknown" ? `<span class="row-status ${bond.instrument_status === "defaulted" ? "defaulted" : ""}">${escapeHTML(bond.instrument_status)}</span>` : "";
    return `<tr><td><div class="issuer-cell"><span class="issuer-icon" aria-hidden="true">${escapeHTML(icon)}</span><div><button class="issuer-name" data-record-id="${escapeHTML(bond.id)}">${escapeHTML(bond.issuer)}</button><div class="isin">${escapeHTML(bond.isin || "ISIN not published · source identity")}</div>${status}</div></div></td><td><span class="type-badge ${typeClass(bond.category)}">${escapeHTML(typeLabel(bond.category))}</span></td><td><span class="quoted-yield ${gold ? "gold" : ""}">${gold ? "N/C" : pct(bond.yield_pct)}</span><small class="basis-label">${escapeHTML(bond.yield_label)}</small></td><td>${pct(bond.coupon_pct)}</td><td>${escapeHTML(bond.rating || "Unknown")}</td><td>${escapeHTML(bond.maturity_date || bond.tenure_raw || "Not published")}${!bond.maturity_date && bond.tenure_raw ? '<small class="basis-label">Source tenure · may be outdated</small>' : ""}</td><td>${escapeHTML(bond.payout === "Semi Annual" ? "Semi annual" : bond.payout || "Unknown")}</td><td><a class="source-label" href="${safeURL(bond.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(bond.source)} ↗</a><small class="basis-label">${escapeHTML(bond.data_mode)} · ${escapeHTML(bond.quote_at || bond.report_as_of || "quote date unknown")}</small></td><td><button class="detail-button" data-record-id="${escapeHTML(bond.id)}" aria-label="View ${escapeHTML(bond.issuer)} observation details">↗</button></td></tr>`;
  }).join("") || '<tr><td class="empty" colspan="9">No bonds match these filters.<br><button id="empty-reset">Clear filters to see all observations</button></td></tr>';
  $("#results-count").textContent = bonds.length ? `Showing ${start + 1}–${Math.min(start + state.pageSize, bonds.length)} of ${bonds.length} observations` : "0 matching observations";
  $("#page-label").textContent = `Page ${state.page} of ${pages}`;
  $("#prev").disabled = state.page <= 1;
  $("#next").disabled = state.page >= pages;
  $("#export").disabled = !bonds.length;
  document.querySelectorAll("[data-sort]").forEach(button => {
    const active = state.sort === button.dataset.sort;
    button.closest("th").setAttribute("aria-sort", active ? state.direction === 1 ? "ascending" : "descending" : "none");
    button.querySelector("span").textContent = active ? state.direction === 1 ? "↑" : "↓" : "↕";
  });
}

function resetFilters() {
  state.category = state.query = state.yield = state.payout = state.source = state.basis = "";
  state.status = "active";
  state.page = 1;
  for (const id of ["search", "yield-filter", "payout-filter", "source-filter", "basis-filter"]) $("#" + id).value = "";
  $("#status-filter").value = "active";
  updateTabs();
  renderOverview();
  renderTable();
}

function updateTabs() {
  document.querySelectorAll("[data-category]").forEach(button => {
    const active = button.dataset.category === state.category;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function showDetail(identity) {
  const bond = state.market.bonds.find(bond => bond.id === identity) || state.market.bonds.find(bond => bond.isin === identity);
  if (!bond) return;
  $("#detail-title").textContent = bond.issuer;
  const rows = [
    ["Source", bond.source], ["Bond type", bond.category], ["Status after reference checks", bond.instrument_status],
    ["Status reported by this source", bond.source_instrument_status || "unknown"],
    ["Credit rating", bond.rating || "Not published"], ["Price in rupees", money(bond.price_inr)],
    ["Price per ₹100 face", bond.price_pct_face == null ? "Not published" : String(bond.price_pct_face)],
    ["Price basis", bond.price_basis || "Not published"], ["Face value", money(bond.face_value_inr)],
    ["Minimum investment", bond.min_investment_inr == null ? "Unverified — see raw source value below" : money(bond.min_investment_inr)],
    ["Source amount (raw)", bond.min_investment_raw || "Not published"],
    ["Source tenure", bond.tenure_raw || "Not published"],
    ["Maturity date", bond.maturity_date || "Not published — not inferred from tenure"],
    ["Interest payout", bond.payout || "Not published"],
    ["Page/report collected", formatTime(bond.observed_at)],
    ["Quote timestamp/date", formatTime(bond.quote_at)], ["Report as of", formatTime(bond.report_as_of)],
    ["Collection method", bond.data_mode === "snapshot" ? "Manually transcribed source snapshot" : bond.data_mode === "imported" ? "Downloaded report imported locally" : "Automated public source fetch"],
  ];
  const provenance = Object.entries(bond.field_sources || {}).map(([name, source]) => `<li>${escapeHTML(name)}: ${escapeHTML(source.source_id)} · report ${escapeHTML(source.report_as_of || "date unknown")}</li>`).join("");
  const conflicts = (bond.conflicts || []).map(item => `<li>${escapeHTML(item.field)}: this observation ${escapeHTML(item.value)}; ${escapeHTML(item.source_id)} reports ${escapeHTML(item.reference_value)} (${escapeHTML(item.report_as_of || "date unknown")}).</li>`).join("");
  $("#detail-body").innerHTML = `<p class="detail-isin">${escapeHTML(bond.isin || bond.instrument_id)}</p><dl class="detail-stats"><div><dt>${escapeHTML(bond.yield_label)}${bond.category === "Sovereign gold" ? " · not comparable" : ""}</dt><dd>${pct(bond.yield_pct)}</dd></div><div><dt>Annual coupon</dt><dd>${pct(bond.coupon_pct)}</dd></div></dl><dl class="detail-fields">${rows.map(([name, value]) => `<div><dt>${escapeHTML(name)}</dt><dd>${escapeHTML(value)}</dd></div>`).join("")}</dl>${provenance ? `<p class="detail-subtitle">Reference enrichment</p><ul class="detail-provenance">${provenance}</ul>` : ""}${conflicts ? `<p class="detail-subtitle">Source disagreements</p><ul class="detail-provenance">${conflicts}</ul>` : ""}<p class="detail-warning">A collection time does not establish quote freshness or availability. Indicative yields at face value are not executable prices. Verify terms and current availability with the original source.</p><a class="detail-source" href="${safeURL(bond.source_url)}" target="_blank" rel="noopener noreferrer">View original source ↗</a>`;
  $("#detail-dialog").showModal();
}

function exportCSV() {
  const bonds = filteredBonds();
  if (!bonds.length) return;
  const fields = state.market.export_fields;
  const cell = value => {
    let text = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
    if (/^\s*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  const csv = "\uFEFF" + [fields.map(cell).join(","), ...bonds.map(row => fields.map(key => cell(row[key])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `bondscope-india-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function feedback(message, success = false) {
  $("#feedback").textContent = message;
  $("#feedback").hidden = false;
  $("#feedback").classList.toggle("success", success);
}

$("#refresh").addEventListener("click", async () => {
  const button = $("#refresh");
  button.disabled = true;
  button.classList.add("refreshing");
  button.querySelector("span").textContent = "Collecting…";
  try {
    const response = await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.source ? { source_ids: [state.source] } : {}) });
    const result = await response.json();
    feedback(result.message || result.error || "Unexpected refresh response.", result.ok);
    await loadMarket();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.classList.remove("refreshing"); button.querySelector("span").textContent = "Refresh listings"; }
});
$("#search").addEventListener("input", event => { state.query = event.target.value; state.page = 1; renderTable(); });
for (const [id, field] of [["yield-filter", "yield"], ["payout-filter", "payout"], ["source-filter", "source"], ["basis-filter", "basis"], ["status-filter", "status"]]) {
  $("#" + id).addEventListener("change", event => { state[field] = event.target.value; state.page = 1; renderOverview(); renderTable(); });
}
$("#category-tabs").addEventListener("click", event => {
  const button = event.target.closest("[data-category]");
  if (!button) return;
  state.category = button.dataset.category;
  state.page = 1;
  updateTabs();
  renderTable();
});
$("#reset").addEventListener("click", resetFilters);
$("#export").addEventListener("click", exportCSV);
$("#prev").addEventListener("click", () => { state.page--; renderTable(); });
$("#next").addEventListener("click", () => { state.page++; renderTable(); });
$("thead").addEventListener("click", event => {
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  if (state.sort === button.dataset.sort) state.direction *= -1;
  else { state.sort = button.dataset.sort; state.direction = 1; }
  state.page = 1;
  renderTable();
});
$("#bond-rows").addEventListener("click", event => {
  const button = event.target.closest("[data-record-id]") || event.target.closest("[data-isin]");
  if (button) showDetail(button.dataset.recordId || button.dataset.isin);
  if (event.target.closest("#empty-reset")) resetFilters();
});
$("#learn-button").addEventListener("click", () => $("#learn-dialog").showModal());
document.querySelectorAll("dialog").forEach(dialog => {
  dialog.querySelector(".close-dialog").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); } });
});
document.addEventListener("keydown", event => {
  if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) && !document.querySelector("dialog[open]")) { event.preventDefault(); $("#search").focus(); }
});

loadMarket().catch(error => {
  feedback(`${error.message} Start the app with “python -m bondscope serve” and reload this page.`);
  $("#notice-title").textContent = "Unable to load source observations.";
  $("#notice-copy").textContent = "The local API did not respond.";
  $("#bond-rows").innerHTML = '<tr><td colspan="9" class="empty">Observations could not be loaded. Reload the page once the server is available.</td></tr>';
  $("#results-count").textContent = "Data unavailable";
  $("#export").disabled = $("#prev").disabled = $("#next").disabled = true;
});
