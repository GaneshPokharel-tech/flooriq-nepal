/*
 * Legacy/inactive dashboard asset.
 * Current broker dashboard behavior is primarily implemented in app/templates/dashboard.html.
 * Do not assume edits in this file affect the live broker dashboard unless it is explicitly re-included.
 */

(function () {
  const API_BASE = (window.FLOORIQ_API_BASE || "").replace(/\/+$/, "");

  function apiUrl(path, params) {
    const u = new URL((API_BASE ? API_BASE : "") + path, window.location.origin);
    if (API_BASE) {
      // If API_BASE is absolute, URL(base, origin) may not behave as expected; handle below
      try {
        const abs = new URL(API_BASE + path);
        if (params) Object.entries(params).forEach(([k, v]) => v != null && v !== "" && abs.searchParams.set(k, String(v)));
        return abs.toString();
      } catch (_) {}
    }
    if (params) Object.entries(params).forEach(([k, v]) => v != null && v !== "" && u.searchParams.set(k, String(v)));
    return u.toString();
  }

  function qs(id) { return document.getElementById(id); }

  function setErr(el, msg) {
    if (!el) return;
    if (!msg) { el.style.display = "none"; el.textContent = ""; return; }
    el.style.display = "block";
    el.textContent = msg;
  }

  function fmtNum(x) {
    if (x == null) return "—";
    const n = Number(x);
    if (Number.isFinite(n)) {
      return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }
    return String(x);
  }

  function anomalyDetailsSummary(a) {
    const d = a?.details;
    if (!d || typeof d !== "object") return String(d ?? "");

    const label = a?.anomaly_type || "";

    if (label === "wash_candidate_roundtrip") {
      const sym = d.symbol || "—";
      const cp = d.counterparty ?? "—";
      const risk = d.risk_band || "—";
      const hp = d.historical_pair_days ?? 0;
      const hc = d.heuristic_components || {};
      const lag = hc.time_delta_minutes ?? "—";
      const px = hc.price_deviation_pct ?? "—";
      return `symbol: ${sym} | counterparty: ${cp} | risk: ${risk} | lag(min): ${lag} | px_dev%: ${px} | historical_pair_days: ${hp}`;
    }

    if (label === "wash_candidate_self_trade") {
      const sym = d.symbol || "—";
      const risk = d.risk_band || "—";
      const hc = d.heuristic_components || {};
      const cnt = hc.self_trade_count ?? "—";
      const amt = hc.total_self_amount ?? "—";
      return `symbol: ${sym} | risk: ${risk} | self_trades: ${cnt} | total_amount: ${fmtNum(amt)}`;
    }

    if (label === "broker_net_amount_spike") {
      const z = d.z ?? "—";
      const net = d.net_amount ?? "—";
      return `z_score: ${z} | net_amount: ${fmtNum(net)}`;
    }

    if (label === "wash_counterparty_dominance") {
      const cp = d.counterparty ?? "—";
      const share = d.share == null ? "—" : `${fmtNum(Number(d.share) * 100)}%`;
      const turnover = d.broker_turnover ?? "—";
      const linked = d.with_counterparty_amount ?? "—";
      return `counterparty: ${cp} | share: ${share} | broker_turnover: ${fmtNum(turnover)} | linked_amount: ${fmtNum(linked)}`;
    }

    if (label === "isolation_forest_anomaly") {
      const score = d.if_score ?? "—";
      const threshold = d.threshold ?? "—";
      return `if_score: ${score} | threshold: ${threshold} | model: IsolationForest`;
    }

    return JSON.stringify(d);
  }

  function pillRisk(risk) {
    const r = (risk || "").toUpperCase();
    const cls = r === "HIGH" ? "high" : (r === "MEDIUM" ? "medium" : (r === "LOW" ? "low" : ""));
    const label = r || "—";
    return `<span class="pill ${cls}">${label}</span>`;
  }

  async function fetchJson(url) {
    const res = await fetch(url, { headers: { "accept": "application/json" } });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) {}
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) ? (data.detail || data.message) : text;
      throw new Error(detail || ("HTTP " + res.status));
    }
    return data;
  }


  async function refreshData() {
    const btn = qs("refreshDataBtn");
    const errEl = qs("brokerErr");
    const msgEl = qs("brokerMsg");
    setErr(errEl, "");
    if (msgEl) { msgEl.style.display = "none"; msgEl.textContent = ""; }

    if (btn) btn.disabled = true;

    try {
      const url = apiUrl(`/admin/refresh`);
      const res = await fetch(url, { method: "POST", headers: { "accept": "application/json" } });
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (_) {}
      if (!res.ok) {
        const detail = (data && (data.detail || data.message)) ? (data.detail || data.message) : text;
        throw new Error(detail || ("HTTP " + res.status));
      }
      if (msgEl) {
        msgEl.style.display = "block";
        msgEl.textContent = "Refresh trigger भयो। GitHub Actions मा run सुरु भएको छ।";
      }
    } catch (e) {
      setErr(errEl, String(e.message || e));
    } finally {
      if (btn) btn.disabled = false;
    }
  }


  // Broker Insights
  async function loadBroker() {
    const brokerId = Number(qs("brokerId").value);
    const business_date = (qs("brokerDate").value || "").trim();

    const errEl = qs("brokerErr");
    setErr(errEl, "");
    const msgEl = qs("brokerMsg");
    if (msgEl) { msgEl.style.display = "none"; msgEl.textContent = ""; }

    if (!brokerId || brokerId < 1) {
      setErr(errEl, "Broker ID चाहिन्छ (positive integer).");
      return;
    }

    qs("loadBrokerBtn").disabled = true;

    try {
      const url = apiUrl(`/insights/broker/${brokerId}`, { business_date: business_date || undefined });
      const payload = await fetchJson(url);

      const risk = payload.risk;
      const hhi = payload.concentration && payload.concentration.hhi;
      const top1 = payload.concentration && payload.concentration.top1_share_pct;

      qs("brokerRisk").innerHTML = pillRisk(risk);
      qs("brokerHHI").textContent = (hhi == null) ? "—" : String(hhi);
      qs("brokerTop1").textContent = (top1 == null) ? "—" : (String(top1) + "%");

      const sum = payload.summary || {};
      const netAmt = sum.net_amount;
      const buyAmt = sum.total_buy_amount;
      const sellAmt = sum.total_sell_amount;
      const tBuy = sum.trades_buy_count;
      const tSell = sum.trades_sell_count;
      qs("brokerNetAmount").textContent = fmtNum(netAmt);
      qs("brokerBuyAmount").textContent = fmtNum(buyAmt);
      qs("brokerSellAmount").textContent = fmtNum(sellAmt);
      qs("brokerTradesBuy").textContent = fmtNum(tBuy);
      qs("brokerTradesSell").textContent = fmtNum(tSell);

      const topSymbolsBody = qs("brokerTopSymbols");
      topSymbolsBody.innerHTML = "";
      const topSyms = (payload.concentration && payload.concentration.top_symbols) || [];
      if (!topSyms.length) {
        topSymbolsBody.innerHTML = `<tr><td colspan="3" class="muted">No data</td></tr>`;
      } else {
        topSyms.forEach(row => {
          topSymbolsBody.insertAdjacentHTML("beforeend",
            `<tr>
              <td class="mono">${row.symbol || ""}</td>
              <td>${fmtNum(row.amount)}</td>
              <td>${fmtNum(row.share_pct)}%</td>
            </tr>`
          );
        });
      }

      const meta = qs("brokerMeta");
      meta.textContent = `business_date: ${payload.business_date} | prev: ${payload.prev_business_date || "—"} | top1: ${payload.concentration?.top1_symbol || "—"}`;

      const anBody = qs("brokerAnomalies");
      anBody.innerHTML = "";
      const anomalies = payload.anomalies || [];
      if (!anomalies.length) {
        anBody.innerHTML = `<tr><td colspan="3" class="muted">No anomalies</td></tr>`;
      } else {
        anomalies.slice(0, 20).forEach(a => {
          const details = anomalyDetailsSummary(a);
          anBody.insertAdjacentHTML("beforeend",
            `<tr>
              <td>${a.anomaly_label || a.anomaly_type || ""}</td>
              <td>${fmtNum(a.score)}</td>
              <td class="muted">${details}</td>
            </tr>`
          );
        });
      }

      const sfBody = qs("brokerSectorFlows");
      sfBody.innerHTML = "";
      const flows = payload.sector_flows || [];
      if (!flows.length) {
        sfBody.innerHTML = `<tr><td colspan="4" class="muted">No sector flow data</td></tr>`;
      } else {
        flows.forEach(f => {
          sfBody.insertAdjacentHTML("beforeend",
            `<tr>
              <td>${f.sector}</td>
              <td>${fmtNum(f.net_amount)}</td>
              <td>${fmtNum(f.prev_net_amount)}</td>
              <td>${fmtNum(f.delta_net_amount)}</td>
            </tr>`
          );
        });
      }
    } catch (e) {
      setErr(errEl, String(e.message || e));
    } finally {
      qs("loadBrokerBtn").disabled = false;
    }
  }

  function clearBroker() {
    setErr(qs("brokerErr"), "");
    qs("brokerRisk").textContent = "—";
    qs("brokerHHI").textContent = "—";
    qs("brokerTop1").textContent = "—";
    qs("brokerNetAmount").textContent = "—";
    qs("brokerBuyAmount").textContent = "—";
    qs("brokerSellAmount").textContent = "—";
    qs("brokerTradesBuy").textContent = "—";
    qs("brokerTradesSell").textContent = "—";
    qs("brokerTopSymbols").innerHTML = "";
    qs("brokerAnomalies").innerHTML = "";
    qs("brokerSectorFlows").innerHTML = "";
    qs("brokerMeta").textContent = "";
  }

  
  async function downloadPdf() {
    const bid = Number(qs("brokerId").value);
    const business_date = (qs("brokerDate").value || "").trim();
    const errEl = qs("brokerErr");
    setErr(errEl, "");

    if (!bid || bid < 1) {
      setErr(errEl, "Broker ID चाहिन्छ (positive integer).");
      return;
    }

    const url = apiUrl(`/reports/broker/${bid}.pdf`, { business_date: business_date || undefined });
    window.open(url, "_blank", "noopener,noreferrer");
  }


// Sector Rotation
  async function loadSectors() {
    const business_date = (qs("sectorDate").value || "").trim();
    const errEl = qs("sectorErr");
    setErr(errEl, "");
    qs("loadSectorsBtn").disabled = true;

    try {
      const url = apiUrl(`/insights/sectors/rotation`, { business_date: business_date || undefined, limit: 10 });
      const payload = await fetchJson(url);

      const body = qs("sectorRotation");
      body.innerHTML = "";
      const rows = payload.data || payload.sectors || payload || [];
      // Expect payload like {disclaimer, business_date, prev_business_date, data:[...]}
      const data = payload.data || [];
      if (!data.length) {
        body.innerHTML = `<tr><td colspan="3" class="muted">No data</td></tr>`;
      } else {
        data.forEach(r => {
          body.insertAdjacentHTML("beforeend",
            `<tr>
              <td>${r.sector}</td>
              <td>${fmtNum(r.total_amount)}</td>
              <td>${fmtNum(r.delta_amount)}</td>
            </tr>`
          );
        });
      }
      qs("sectorMeta").textContent = `business_date: ${payload.business_date || "—"} | prev: ${payload.prev_business_date || "—"}`;
    } catch (e) {
      setErr(errEl, String(e.message || e));
    } finally {
      qs("loadSectorsBtn").disabled = false;
    }
  }

  // Top Brokers
  async function loadTopBrokers() {
    const metric = qs("topBrokersMetric").value;
    qs("topBrokersMetricCol").textContent = metric;

    const errEl = qs("topBrokersErr");
    setErr(errEl, "");
    qs("loadTopBrokersBtn").disabled = true;

    try {
      const url = apiUrl(`/insights/brokers/top`, { metric, limit: 10 });
      const payload = await fetchJson(url);

      const body = qs("topBrokers");
      body.innerHTML = "";
      const data = payload.data || [];
      if (!data.length) {
        body.innerHTML = `<tr><td colspan="2" class="muted">No data</td></tr>`;
      } else {
        data.forEach(r => {
          body.insertAdjacentHTML("beforeend",
            `<tr>
              <td class="mono" data-broker="${r.broker_id}" style="cursor:pointer;">${r.broker_id}</td>
              <td>${fmtNum(r[metric])}</td>
            </tr>`
          );
        });
      }
    } catch (e) {
      setErr(errEl, String(e.message || e));
    } finally {
      qs("loadTopBrokersBtn").disabled = false;
    }
  }

  // Top Symbols
  async function loadTopSymbols() {
    const metric = qs("topSymbolsMetric").value;
    qs("topSymbolsMetricCol").textContent = metric;

    const errEl = qs("topSymbolsErr");
    setErr(errEl, "");
    qs("loadTopSymbolsBtn").disabled = true;

    try {
      const url = apiUrl(`/insights/symbols/top`, { metric, limit: 10 });
      const payload = await fetchJson(url);

      const body = qs("topSymbols");
      body.innerHTML = "";
      const data = payload.data || [];
      if (!data.length) {
        body.innerHTML = `<tr><td colspan="2" class="muted">No data</td></tr>`;
      } else {
        data.forEach(r => {
          body.insertAdjacentHTML("beforeend",
            `<tr>
              <td class="mono"><a href="/insights/symbol/${r.stock_symbol}" target="_blank" rel="noreferrer">${r.stock_symbol}</a></td>
              <td>${fmtNum(r[metric])}</td>
            </tr>`
          );
        });
      }
    } catch (e) {
      setErr(errEl, String(e.message || e));
    } finally {
      qs("loadTopSymbolsBtn").disabled = false;
    }
  }

  // Wire up
  qs("loadBrokerBtn").addEventListener("click", loadBroker);
  qs("refreshDataBtn").addEventListener("click", refreshData);
  qs("clearBrokerBtn").addEventListener("click", clearBroker);
  qs("downloadPdfBtn").addEventListener("click", downloadPdf);
  qs("loadSectorsBtn").addEventListener("click", loadSectors);
  qs("loadTopBrokersBtn").addEventListener("click", loadTopBrokers);

  // Click a top-broker row to load broker insights
  qs("topBrokers").addEventListener("click", (e) => {
    const td = e.target && e.target.closest ? e.target.closest("td[data-broker]") : null;
    if (!td) return;
    const bid = td.getAttribute("data-broker");
    const sel = qs("brokerId");
    if (sel) sel.value = bid;
    loadBroker();
  });
  qs("loadTopSymbolsBtn").addEventListener("click", loadTopSymbols);

  // Small quality-of-life: Enter triggers broker load
  qs("brokerId").addEventListener("change", () => { if (qs("brokerId").value) loadBroker(); });
  qs("brokerId").addEventListener("keydown", (e) => { if (e.key === "Enter") loadBroker(); });
  qs("brokerDate").addEventListener("keydown", (e) => { if (e.key === "Enter") loadBroker(); });
  qs("sectorDate").addEventListener("keydown", (e) => { if (e.key === "Enter") loadSectors(); });

  // Auto-load widgets (non-blocking)
  loadSectors().catch(() => {});
  loadTopBrokers().catch(() => {});
  loadTopSymbols().catch(() => {});
})();

// --- Anomalies today widget (appended) ---
(async function attachAnomalies() {
  function qs(id) { return document.getElementById(id); }
  if (!qs("loadAnomaliesBtn")) return;

  const API_BASE = (window.FLOORIQ_API_BASE || "").replace(/\/+$/, "");
  function apiUrl(path, params) {
    if (API_BASE) {
      const u = new URL(API_BASE + path);
      if (params) Object.entries(params).forEach(([k, v]) => v != null && v !== "" && u.searchParams.set(k, String(v)));
      return u.toString();
    }
    const u = new URL(path, window.location.origin);
    if (params) Object.entries(params).forEach(([k, v]) => v != null && v !== "" && u.searchParams.set(k, String(v)));
    return u.toString();
  }
  function setErr(el, msg) {
    if (!el) return;
    if (!msg) { el.style.display = "none"; el.textContent = ""; return; }
    el.style.display = "block";
    el.textContent = msg;
  }
  async function fetchJson(url) {
    const res = await fetch(url, { headers: { "accept": "application/json" } });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) {}
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) ? (data.detail || data.message) : text;
      throw new Error(detail || ("HTTP " + res.status));
    }
    return data;
  }
  function fmtNum(x) {
    if (x == null) return "—";
    const n = Number(x);
    if (Number.isFinite(n)) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return String(x);
  }

  async function loadAnomalies() {
    const errEl = qs("anomaliesErr");
    setErr(errEl, "");
    qs("loadAnomaliesBtn").disabled = true;

    try {
      const url = apiUrl("/anomalies/today", { limit: 10 });
      const payload = await fetchJson(url);
      const body = qs("anomaliesToday");
      body.innerHTML = "";
      const data = payload.data || [];
      if (!data.length) {
        body.innerHTML = `<tr><td colspan="3" class="muted">No anomalies</td></tr>`;
      } else {
        data.forEach(a => {
          body.insertAdjacentHTML("beforeend",
            `<tr>
              <td class="mono">${a.entity_type}:${a.entity_id}</td>
              <td>${a.anomaly_label || a.anomaly_type}</td>
              <td>${fmtNum(a.score)}</td>
            </tr>`
          );
        });
      }
      qs("anomaliesMeta").textContent = `business_date: ${data[0]?.business_date || "—"} | count: ${payload.count || data.length}`;
    } catch (e) {
      setErr(qs("anomaliesErr"), String(e.message || e));
    } finally {
      qs("loadAnomaliesBtn").disabled = false;
    }
  }

  qs("loadAnomaliesBtn").addEventListener("click", loadAnomalies);
  loadAnomalies().catch(() => {});
})();

// --- Sector click -> highlight broker sector_flows (appended) ---
(function attachSectorHighlight() {
  const sectorTbl = document.getElementById("sectorRotation");
  const flowsTbl = document.getElementById("brokerSectorFlows");
  if (!sectorTbl || !flowsTbl) return;

  function clearHighlights() {
    flowsTbl.querySelectorAll("tr").forEach(tr => {
      tr.classList.remove("row-selected");
    });
  }

  sectorTbl.addEventListener("click", (e) => {
    const tr = e.target && e.target.closest ? e.target.closest("tr") : null;
    if (!tr) return;
    const td0 = tr.querySelector("td");
    if (!td0) return;

    const sector = (td0.textContent || "").trim();
    if (!sector) return;

    const badge = document.getElementById("selectedSector");
    if (badge) {
      badge.style.display = "inline-block";
      badge.textContent = "Selected sector: " + sector;
    }

    clearHighlights();

    // Highlight matching sector row in broker flows, if broker data already loaded
    let hit = null;
    flowsTbl.querySelectorAll("tr").forEach(row => {
      const c0 = row.querySelector("td");
      const name = (c0 && c0.textContent ? c0.textContent : "").trim();
      if (name === sector) {
        row.classList.add("row-selected");
        hit = row;
      }
    });

    // Scroll to broker flows section for convenience
    const brokerFlowsCard = document.getElementById("brokerSectorFlows")?.closest(".card");
    if (brokerFlowsCard) brokerFlowsCard.scrollIntoView({ behavior: "smooth", block: "start" });

    // If not found, no-op (broker not loaded yet or sector not in top 10)
    if (!hit) return;
  });
})();

// --- Copy link button (appended) ---
(function attachCopyLink() {
  const btn = document.getElementById("copyLinkBtn");
  if (!btn) return;

  function qs(id) { return document.getElementById(id); }
  function showMsg(text) {
    const m = qs("brokerMsg");
    if (m) { m.style.display = "block"; m.textContent = text; }
  }

  btn.addEventListener("click", function () {
    const bidEl = qs("brokerId");
    const dateEl = qs("brokerDate");
    const bid = bidEl ? bidEl.value : "";
    const bd = dateEl ? (dateEl.value || "").trim() : "";

    const u = new URL("/dashboard", window.location.origin);
    if (bid) u.searchParams.set("broker_id", String(bid));
    if (bd) u.searchParams.set("business_date", bd);
    const link = u.toString();

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () {
        showMsg("Copied: " + link);
      }).catch(function () {
        showMsg("Copy failed. Link: " + link);
      });
    } else {
      showMsg("Link: " + link);
    }
  });
})();

// --- Auto-load from query params (appended) ---
(function autoLoadFromQuery() {
  try {
    const u = new URL(window.location.href);
    const bid = u.searchParams.get("broker_id");
    const bd = u.searchParams.get("business_date");

    const bidEl = document.getElementById("brokerId");
    const bdEl = document.getElementById("brokerDate");

    if (bidEl && bid) bidEl.value = bid;
    if (bdEl && bd) bdEl.value = bd;

    // Trigger load if broker_id present
    if (bid) {
      const btn = document.getElementById("loadBrokerBtn");
      if (btn) btn.click();
    }
  } catch (e) {}
})();

// --- Copy link button v2 (share more state) ---
(function attachCopyLinkV2() {
  var btn0 = document.getElementById("copyLinkBtn");
  if (!btn0) return;

  // Remove any previously attached listeners by cloning
  var btn = btn0.cloneNode(true);
  btn0.parentNode.replaceChild(btn, btn0);

  function qs(id) { return document.getElementById(id); }
  function showMsg(text) {
    var m = qs("brokerMsg");
    if (m) { m.style.display = "block"; m.textContent = text; }
  }

  btn.addEventListener("click", function () {
    var bid = (qs("brokerId") && qs("brokerId").value) ? qs("brokerId").value : "";
    var bd = (qs("brokerDate") && qs("brokerDate").value) ? (qs("brokerDate").value || "").trim() : "";

    var sectorDate = (qs("sectorDate") && qs("sectorDate").value) ? (qs("sectorDate").value || "").trim() : "";
    var tbm = (qs("topBrokersMetric") && qs("topBrokersMetric").value) ? qs("topBrokersMetric").value : "";
    var tsm = (qs("topSymbolsMetric") && qs("topSymbolsMetric").value) ? qs("topSymbolsMetric").value : "";

    var u = new URL("/dashboard", window.location.origin);
    if (bid) u.searchParams.set("broker_id", String(bid));
    if (bd) u.searchParams.set("business_date", bd);
    if (sectorDate) u.searchParams.set("sector_date", sectorDate);
    if (tbm) u.searchParams.set("top_brokers_metric", tbm);
    if (tsm) u.searchParams.set("top_symbols_metric", tsm);

    var link = u.toString();

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () {
        showMsg("Copied: " + link);
      }).catch(function () {
        showMsg("Copy failed. Link: " + link);
      });
    } else {
      showMsg("Link: " + link);
    }
  });
})();

// --- Auto-load from query params v2 (shareable state) ---
(function autoLoadFromQueryV2() {
  try {
    var u = new URL(window.location.href);

    var bid = u.searchParams.get("broker_id");
    var bd = u.searchParams.get("business_date");
    var sectorDate = u.searchParams.get("sector_date");
    var tbm = u.searchParams.get("top_brokers_metric");
    var tsm = u.searchParams.get("top_symbols_metric");

    var bidEl = document.getElementById("brokerId");
    var bdEl = document.getElementById("brokerDate");
    var sectorEl = document.getElementById("sectorDate");
    var tbmEl = document.getElementById("topBrokersMetric");
    var tsmEl = document.getElementById("topSymbolsMetric");

    if (bidEl && bid) bidEl.value = bid;
    if (bdEl && bd) bdEl.value = bd;

    if (sectorEl && sectorDate) sectorEl.value = sectorDate;
    if (tbmEl && tbm) tbmEl.value = tbm;
    if (tsmEl && tsm) tsmEl.value = tsm;

    // Reload widgets if params present
    if (sectorDate) {
      var sbtn = document.getElementById("loadSectorsBtn");
      if (sbtn) sbtn.click();
    }
    if (tbm) {
      var bbtn = document.getElementById("loadTopBrokersBtn");
      if (bbtn) bbtn.click();
    }
    if (tsm) {
      var symbtn = document.getElementById("loadTopSymbolsBtn");
      if (symbtn) symbtn.click();
    }

    // Load broker last (so the right side is already populated)
    if (bid) {
      var btn = document.getElementById("loadBrokerBtn");
      if (btn) btn.click();
    }
  } catch (e) {}
})();

// --- UX polish: auto-sync URL + auto-hide messages ---
(function urlSyncAndMsgAutoHide() {
  function qs(id) { return document.getElementById(id); }

  function currentStateParams() {
    var bid = (qs("brokerId") && qs("brokerId").value) ? qs("brokerId").value : "";
    var bd = (qs("brokerDate") && qs("brokerDate").value) ? (qs("brokerDate").value || "").trim() : "";
    var sectorDate = (qs("sectorDate") && qs("sectorDate").value) ? (qs("sectorDate").value || "").trim() : "";
    var tbm = (qs("topBrokersMetric") && qs("topBrokersMetric").value) ? qs("topBrokersMetric").value : "";
    var tsm = (qs("topSymbolsMetric") && qs("topSymbolsMetric").value) ? qs("topSymbolsMetric").value : "";
    return { bid: bid, bd: bd, sectorDate: sectorDate, tbm: tbm, tsm: tsm };
  }

  function syncUrl() {
    try {
      var st = currentStateParams();
      var u = new URL("/dashboard", window.location.origin);
      if (st.bid) u.searchParams.set("broker_id", String(st.bid));
      if (st.bd) u.searchParams.set("business_date", st.bd);
      if (st.sectorDate) u.searchParams.set("sector_date", st.sectorDate);
      if (st.tbm) u.searchParams.set("top_brokers_metric", st.tbm);
      if (st.tsm) u.searchParams.set("top_symbols_metric", st.tsm);
      window.history.replaceState({}, "", u.toString());
    } catch (e) {}
  }

  // Sync on changes
  ["brokerId","brokerDate","sectorDate","topBrokersMetric","topSymbolsMetric"].forEach(function (id) {
    var el = qs(id);
    if (!el) return;
    el.addEventListener("change", syncUrl);
    el.addEventListener("input", function () {
      // keep it lightweight: only sync for text/date inputs while typing
      if (id === "brokerDate" || id === "sectorDate") syncUrl();
    });
  });

  // Auto-hide brokerMsg after 5s whenever it changes
  var msg = qs("brokerMsg");
  if (msg && window.MutationObserver) {
    var t = null;
    var obs = new MutationObserver(function () {
      if (t) clearTimeout(t);
      if ((msg.textContent || "").trim().length === 0) return;
      msg.style.display = "block";
      t = setTimeout(function () {
        msg.style.display = "none";
        msg.textContent = "";
      }, 5000);
    });
    obs.observe(msg, { childList: true, characterData: true, subtree: true });
  }

  // Initial sync
  syncUrl();
})();
