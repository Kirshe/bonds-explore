// Interaction checks for the real dashboard script, without a browser/socket.
// These verify application logic; they do not substitute for visual browser QA.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { spawnSync } = require("node:child_process");
const vm = require("node:vm");
const { test } = require("node:test");

const response = spawnSync("python", ["-c", "import json,tempfile; from pathlib import Path; from bondscope.store import read_market; t=tempfile.TemporaryDirectory(); print(json.dumps(read_market(Path(t.name)/'ui.sqlite3'))); t.cleanup()"], { encoding: "utf8" });
assert.equal(response.status, 0, response.stderr);
const market = JSON.parse(response.stdout);

function harness(data = market) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: "", innerHTML: "", value: "", hidden: false, disabled: false,
      listeners: {}, attributes: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(name, value) { this.attributes[name] = value; },
      querySelector(child) { return element(selector + " " + child); },
      showModal() { this.open = true; }, close() { this.open = false; },
      click() { this.clicked = true; }, focus() { this.focused = true; },
    });
    return elements.get(selector);
  }
  let lastBlob;
  const context = vm.createContext({
    document: {
      querySelector: element, querySelectorAll: () => [],
      addEventListener() {}, createElement: tag => element(`created:${tag}`),
    },
    fetch: async url => ({ ok: true, json: async () => url === "/api/refresh" ? { ok: false, status: "error", message: "Network unavailable. Existing data retained." } : structuredClone(data) }),
    Blob, URL: { createObjectURL(blob) { lastBlob = blob; return "blob:test"; }, revokeObjectURL() {} },
    setTimeout() {}, console,
  });
  vm.runInContext(fs.readFileSync("web/app.js", "utf8"), context);
  return {
    element,
    async ready() { await new Promise(resolve => setImmediate(resolve)); },
    run(code) { return vm.runInContext(code, context); },
    async fire(selector, name = "click", event = {}) { await element(selector).listeners[name](event); },
    csv: async () => lastBlob.text(),
  };
}

test("dashboard interactions preserve filtering, pagination, provenance and refresh errors", async t => {
  const app = harness();
  await app.ready();

  await t.test("initial overview and first page", () => {
    assert.equal(app.element("#metric-count").textContent, "28");
    assert.match(app.element("#notice-title").textContent, /snapshot/);
    assert.equal((app.element("#bond-rows").innerHTML.match(/<tr>/g) || []).length, 10);
    assert.equal(app.element("#page-label").textContent, "Page 1 of 3");
    assert.equal(app.element("#prev").disabled, true);
  });

  await t.test("pagination moves through actual rows", async () => {
    const firstPage = app.element("#bond-rows").innerHTML;
    await app.fire("#next");
    assert.equal(app.element("#page-label").textContent, "Page 2 of 3");
    assert.notEqual(app.element("#bond-rows").innerHTML, firstPage);
    assert.equal(app.element("#prev").disabled, false);
  });

  await t.test("case-insensitive ISIN search resets pagination", async () => {
    await app.fire("#search", "input", { target: { value: "ine03k307215" } });
    assert.match(app.element("#bond-rows").innerHTML, /Satin Finserv Limited/);
    assert.equal(app.element("#results-count").textContent, "Showing 1–1 of 1 observations");
    assert.equal(app.element("#page-label").textContent, "Page 1 of 1");
  });

  await t.test("empty result and reset", async () => {
    await app.fire("#search", "input", { target: { value: "no-such-issuer" } });
    assert.match(app.element("#bond-rows").innerHTML, /No bonds match/);
    assert.equal(app.element("#export").disabled, true);
    await app.fire("#reset");
    assert.equal(app.run("filteredBonds().length"), 28);
  });

  await t.test("category, payout, and conventional yield filters compose", async () => {
    await app.fire("#category-tabs", "click", { target: { closest: () => ({ dataset: { category: "Corporate / NBFC" } }) } });
    await app.fire("#payout-filter", "change", { target: { value: "Monthly" } });
    await app.fire("#yield-filter", "change", { target: { value: "over9" } });
    assert.equal(app.run("filteredBonds().length"), 4);
    assert.equal(app.run("filteredBonds().every(b => b.payout === 'Monthly' && b.yield_pct > 9)"), true);
    await app.fire("#reset");
  });

  await t.test("yield sorting keeps gold figures outside the comparable results", async () => {
    const event = { target: { closest: () => ({ dataset: { sort: "yield_pct" } }) } };
    await app.fire("thead", "click", event);
    await app.fire("thead", "click", event);
    assert.equal(app.run("filteredBonds()[0].yield_pct"), 11.95);
    assert.equal(app.run("filteredBonds().at(-1).category"), "Sovereign gold");
  });

  await t.test("details retain unknown amounts and snapshot provenance", async () => {
    await app.fire("#bond-rows", "click", { target: { closest: selector => selector === "[data-isin]" ? { dataset: { isin: "INE03K307215" } } : null } });
    assert.equal(app.element("#detail-dialog").open, true);
    const details = app.element("#detail-body").innerHTML;
    assert.match(details, /Manually transcribed source snapshot/);
    assert.match(details, /100,000,00/);
    assert.match(details, /Unverified/);
    assert.match(details, /Not published — not inferred from tenure/);
  });

  await t.test("export includes all filtered pages, provenance and formula escaping", async () => {
    await app.fire("#export");
    let csv = await app.csv();
    assert.equal(csv.trim().split("\r\n").length, 29);
    assert.match(csv, /"data_mode"/);
    assert.match(csv, /"snapshot"/);
    app.run("state.market.bonds[0].issuer = '=1+1'; state.query = '=1+1'");
    await app.fire("#export");
    csv = await app.csv();
    assert.match(csv, /"'=1\+1"/);
    assert.equal(csv.trim().split("\r\n").length, 2);
    await app.fire("#reset");
  });

  await t.test("failed refresh shows the error, retains data and restores controls", async () => {
    await app.fire("#refresh");
    assert.match(app.element("#feedback").textContent, /Existing data retained/);
    assert.equal(app.element("#refresh").disabled, false);
    assert.equal(app.element("#metric-count").textContent, "28");
    assert.match(app.element("#notice-title").textContent, /snapshot/);
  });
});

test("multiple sources retain quote identity, yield basis and per-row export provenance", async t => {
  const result = spawnSync("python", ["-c", "import json,tempfile; from pathlib import Path; from bondscope.store import read_market,import_report; t=tempfile.TemporaryDirectory(); db=Path(t.name)/'multi.sqlite3'; import_report(db,'nse',Path('tests/fixtures/nse.csv'),'2026-11-06'); import_report(db,'nsdl',Path('tests/fixtures/nsdl.csv'),'2026-11-06'); import_report(db,'grip',Path('tests/fixtures/grip.html'),'2026-11-06'); print(json.dumps(read_market(db))); t.cleanup()"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const data = JSON.parse(result.stdout);
  const app = harness(data);
  await app.ready();

  await t.test("different yield definitions have no pooled median", () => {
    assert.equal(app.element("#metric-median").innerHTML, "—");
    assert.match(app.element("#metric-yield-count").textContent, /Choose a yield basis/);
    assert.match(app.element("#source-list").innerHTML, /NSDL/);
    assert.match(app.element("#source-list").innerHTML, /Not collected/);
  });

  await t.test("basis filter selects its own statistics and rows", async () => {
    await app.fire("#basis-filter", "change", { target: { value: "indicative" } });
    assert.equal(app.run("filteredBonds().length"), 2);
    assert.match(app.element("#metric-median").innerHTML, /^8\.88/);
    assert.equal(app.run("filteredBonds().every(b => b.source_id === 'grip')"), true);
    await app.fire("#reset");
  });

  await t.test("same ISIN has independent source and yield-basis details", async () => {
    await app.fire("#source-filter", "change", { target: { value: "nse" } });
    assert.equal(app.run("filteredBonds().length"), 2);
    const id = app.run("filteredBonds().find(b => b.yield_kind === 'weighted_ytm').id");
    await app.fire("#bond-rows", "click", { target: { closest: selector => selector === "[data-record-id]" ? { dataset: { recordId: id } } : null } });
    const details = app.element("#detail-body").innerHTML;
    assert.match(details, /Weighted average traded YTM/);
    assert.match(details, /10\.30%/);
    assert.match(details, /101\.1/);
    assert.match(details, /Downloaded report imported locally/);
    assert.match(details, /www\.nseindia\.com/);
    await app.fire("#export");
    const csv = await app.csv();
    assert.equal(csv.trim().split("\r\n").length, 3);
    assert.match(csv, /"weighted_ytm"/);
    assert.match(csv, /"imported"/);
    assert.match(csv, /"price_pct_face"/);
    await app.fire("#reset");
  });

  await t.test("matured rows stay available through an explicit status filter", async () => {
    app.run("state.market.bonds.push({...state.market.bonds[0], id:'matured-test', issuer:'Matured example', instrument_status:'matured'})");
    app.run("state.query = 'Matured example'");
    assert.equal(app.run("filteredBonds().length"), 0);
    await app.fire("#status-filter", "change", { target: { value: "all" } });
    assert.equal(app.run("filteredBonds().length"), 1);
    assert.match(app.element("#bond-rows").innerHTML, /matured/);
  });
});
