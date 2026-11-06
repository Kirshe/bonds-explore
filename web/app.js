"use strict";

const $ = selector => document.querySelector(selector);
const state = { market: null, category: "", query: "", yield: "", payout: "", sort: "issuer", direction: 1, page: 1, pageSize: 10 };
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const pct = value => value == null ? "—" : `${Number(value).toFixed(2)}%`;
const formatTime = value => value ? new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " IST" : "Not attempted";
const typeLabel = category => ({ "State government": "State govt", "Corporate / NBFC": "Corporate / NBFC", "Sovereign gold": "Sovereign gold" }[category] || category);
const typeClass = category => category === "Sovereign gold" ? "gold" : category === "Corporate / NBFC" ? "corporate" : "";
const tenureMonths = raw => {
  if (!raw) return null;
  const years = raw.match(/(\d+)\s*Y/i);
  const months = raw.match(/(\d+)\s*Mon/i);
  return years || months ? Number(years?.[1] || 0) * 12 + Number(months?.[1] || 0) : null;
};

async function loadMarket() {
  const response = await fetch("/api/bonds");
  if (!response.ok) throw new Error(`Unable to load listings (HTTP ${response.status}).`);
  state.market = await response.json();
  renderOverview();
  renderTable();
}

function renderOverview() {
  const { overview: data, source } = state.market;
  $("#metric-count").textContent = data.count.toLocaleString("en-IN");
  $("#metric-categories").textContent = `Across ${data.categories.length} bond categories`;
  $("#metric-median").innerHTML = data.median_yield == null ? "—" : `${data.median_yield.toFixed(2)}<small>%</small>`;
  $("#metric-yield-count").textContent = `${data.yield_count} conventional bonds · gold excluded`;
  $("#metric-range").textContent = `${pct(data.min_yield)} – ${pct(data.max_yield)}`;
  $("#metric-source").textContent = `Bondbazaar · ${source.mode === "snapshot" ? "source snapshot" : "public page fetched"}`;
  $("#total-pill").textContent = `${data.count} listings`;
  $("#notice-title").textContent = source.mode === "snapshot" ? "Source snapshot · availability unverified." : source.stale ? "Older collection · availability unverified." : "Public page collected · availability unverified.";
  $("#notice-copy").textContent = `Observed ${formatTime(source.observed_at)}. Source quote date is unknown.`;
  $("#source-time").textContent = formatTime(source.observed_at);
  $("#source-attempt").textContent = formatTime(source.attempted_at);
  $("#source-mode").textContent = source.mode === "snapshot" ? "Snapshot" : source.stale ? "Older than 24h" : "Page fetched";
  $("#source-error").hidden = !source.error;
  $("#source-error").textContent = source.error ? `Last refresh failed; previous data retained. ${source.error}` : "";
  const groups = data.categories.filter(group => group.median_yield != null).sort((a, b) => b.median_yield - a.median_yield);
  const max = Math.max(1, ...groups.map(group => group.median_yield));
  $("#yield-chart").innerHTML = groups.map(group => `<div class="chart-row"><span class="chart-label">${escapeHTML(typeLabel(group.name))}</span><div class="chart-track"><div class="chart-bar" style="width:${(group.median_yield / max * 100).toFixed(1)}%"></div></div><span class="chart-value">${pct(group.median_yield)}</span></div>`).join("");
}

function filteredBonds() {
  if (!state.market) return [];
  const query = state.query.trim().toLowerCase();
  const bonds = state.market.bonds.filter(bond => {
    if (state.category && bond.category !== state.category) return false;
    if (query && !`${bond.issuer} ${bond.isin}`.toLowerCase().includes(query)) return false;
    if (state.payout && bond.payout !== state.payout) return false;
    if (state.yield) {
      // Gold's coupon-like figure must not enter conventional yield filters.
      if (bond.yield_pct == null || bond.category === "Sovereign gold") return false;
      if (state.yield === "under7" && bond.yield_pct >= 7) return false;
      if (state.yield === "7to9" && (bond.yield_pct < 7 || bond.yield_pct > 9)) return false;
      if (state.yield === "over9" && bond.yield_pct <= 9) return false;
    }
    return true;
  });
  return bonds.sort((a, b) => {
    let left = a[state.sort], right = b[state.sort];
    if (state.sort === "tenure_raw") { left = tenureMonths(left); right = tenureMonths(right); }
    if (state.sort === "yield_pct") {
      if (a.category === "Sovereign gold") left = null;
      if (b.category === "Sovereign gold") right = null;
    }
    if (left == null && right == null) return a.isin.localeCompare(b.isin);
    if (left == null) return 1;
    if (right == null) return -1;
    const result = typeof left === "number" ? left - right : String(left).localeCompare(String(right));
    return result * state.direction || a.isin.localeCompare(b.isin);
  });
}

function renderTable() {
  const bonds = filteredBonds();
  const pages = Math.max(1, Math.ceil(bonds.length / state.pageSize));
  state.page = Math.min(pages, state.page);
  const start = (state.page - 1) * state.pageSize;
  $("#bond-rows").innerHTML = bonds.slice(start, start + state.pageSize).map(bond => {
    const gold = bond.category === "Sovereign gold";
    const icon = bond.category === "State government" ? "IN" : gold ? "Au" : bond.issuer.split(/\s+/).slice(0, 2).map(word => word[0]).join("");
    return `<tr><td><div class="issuer-cell"><span class="issuer-icon" aria-hidden="true">${escapeHTML(icon)}</span><div><button class="issuer-name" data-isin="${escapeHTML(bond.isin)}">${escapeHTML(bond.issuer)}</button><div class="isin">${escapeHTML(bond.isin)}</div></div></div></td><td><span class="type-badge ${typeClass(bond.category)}">${escapeHTML(typeLabel(bond.category))}</span></td><td><span class="quoted-yield ${gold ? "gold" : ""}" ${gold ? 'title="Gold bond figure is not comparable to conventional bond yield"' : ""}>${gold ? "N/C" : pct(bond.yield_pct)}</span></td><td>${pct(bond.coupon_pct)}</td><td title="Published tenure; may be outdated">${escapeHTML(bond.tenure_raw || "Not published")}</td><td>${escapeHTML(bond.payout === "Semi Annual" ? "Semi annual" : bond.payout || "Unknown")}</td><td><a class="source-label" href="https://www.bondbazaar.com/" target="_blank" rel="noopener noreferrer">${escapeHTML(bond.source)} ↗</a></td><td><button class="detail-button" data-isin="${escapeHTML(bond.isin)}" aria-label="View ${escapeHTML(bond.issuer)} details">↗</button></td></tr>`;
  }).join("") || '<tr><td class="empty" colspan="8">No bonds match these filters.<br><button id="empty-reset">Clear filters to see all listings</button></td></tr>';
  $("#results-count").textContent = bonds.length ? `Showing ${start + 1}–${Math.min(start + state.pageSize, bonds.length)} of ${bonds.length} bonds` : "0 matching bonds";
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
  state.category = state.query = state.yield = state.payout = "";
  state.page = 1;
  $("#search").value = $("#yield-filter").value = $("#payout-filter").value = "";
  updateTabs();
  renderTable();
}

function updateTabs() {
  document.querySelectorAll("[data-category]").forEach(button => {
    const active = button.dataset.category === state.category;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function showDetail(isin) {
  const bond = state.market.bonds.find(bond => bond.isin === isin);
  if (!bond) return;
  const source = state.market.source;
  $("#detail-title").textContent = bond.issuer;
  const rows = [
    ["Bond type", bond.category], ["Credit rating", bond.rating || "Not published"],
    ["Price", bond.price_inr == null ? "Not published" : `₹${bond.price_inr.toLocaleString("en-IN")}`],
    ["Minimum investment", bond.min_investment_inr == null ? "Unverified — see raw source value below" : `₹${bond.min_investment_inr.toLocaleString("en-IN")}`],
    ["Source amount (raw)", bond.min_investment_raw || "Not published"],
    ["Source tenure", bond.tenure_raw || "Not published"],
    ["Maturity date", bond.maturity_date || "Not published — not inferred from tenure"],
    ["Interest payout", bond.payout || "Not published"],
    ["Page observed", formatTime(bond.observed_at)],
    ["Quote timestamp", bond.quote_at ? formatTime(bond.quote_at) : "Not published"],
    ["Collection method", source.mode === "snapshot" ? "Manually transcribed source snapshot" : "Automated public page fetch"],
  ];
  $("#detail-body").innerHTML = `<p class="detail-isin">${escapeHTML(bond.isin)}</p><dl class="detail-stats"><div><dt>Source-quoted yield${bond.category === "Sovereign gold" ? " · not comparable" : ""}</dt><dd>${pct(bond.yield_pct)}</dd></div><div><dt>Annual coupon</dt><dd>${pct(bond.coupon_pct)}</dd></div></dl><dl class="detail-fields">${rows.map(([name, value]) => `<div><dt>${escapeHTML(name)}</dt><dd>${escapeHTML(value)}</dd></div>`).join("")}</dl><p class="detail-warning">The source does not publish a quote date, price or rating here. Its amount formatting is ambiguous, and published tenures may be outdated. Verify availability and terms directly with the platform.</p><a class="detail-source" href="https://www.bondbazaar.com/" target="_blank" rel="noopener noreferrer">View original source ↗</a>`;
  $("#detail-dialog").showModal();
}

function exportCSV() {
  const bonds = filteredBonds();
  if (!bonds.length) return;
  const { source } = state.market;
  const fields = ["isin", "issuer", "category", "yield_pct", "yield_label", "coupon_pct", "rating", "price_inr", "min_investment_inr", "min_investment_raw", "payout", "tenure_raw", "maturity_date", "source", "source_url", "observed_at", "quote_at", "data_mode", "source_status", "source_stale"];
  const cell = value => {
    let text = String(value ?? "");
    if (/^\s*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  const rows = bonds.map(bond => ({ ...bond, data_mode: source.mode, source_status: source.status, source_stale: source.stale }));
  const csv = "\uFEFF" + [fields.map(cell).join(","), ...rows.map(row => fields.map(key => cell(row[key])).join(","))].join("\r\n");
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
    const response = await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const result = await response.json();
    feedback(result.message || "Unexpected refresh response.", result.ok);
    await loadMarket();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.classList.remove("refreshing"); button.querySelector("span").textContent = "Refresh listings"; }
});
$("#search").addEventListener("input", event => { state.query = event.target.value; state.page = 1; renderTable(); });
$("#yield-filter").addEventListener("change", event => { state.yield = event.target.value; state.page = 1; renderTable(); });
$("#payout-filter").addEventListener("change", event => { state.payout = event.target.value; state.page = 1; renderTable(); });
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
  const button = event.target.closest("[data-isin]");
  if (button) showDetail(button.dataset.isin);
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
  $("#notice-title").textContent = "Unable to load public listings.";
  $("#notice-copy").textContent = "The local API did not respond.";
  $("#bond-rows").innerHTML = '<tr><td colspan="8" class="empty">Listings could not be loaded. Reload the page once the server is available.</td></tr>';
  $("#results-count").textContent = "Data unavailable";
  $("#export").disabled = $("#prev").disabled = $("#next").disabled = true;
});
