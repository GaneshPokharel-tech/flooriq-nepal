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
      throw new Error(`HTTP ${res.status}: ${detail}`);
    }
    return data;
  }

  // Broker Insights
  async function loadBroker() {
    const brokerId = Number(qs("brokerId").value);
    const business_date = (qs("brokerDate").value || "").trim();

    const errEl = qs("brokerErr");
    setErr(errEl, "");

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
      qs("brokerTop1").textContent = (top1 == null) ? "—" : `${top1}%`;

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
          const details = (a.details == null) ? "" : String(a.details);
          anBody.insertAdjacentHTML("beforeend",
            `<tr>
              <td>${a.anomaly_type || ""}</td>
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
    qs("brokerTopSymbols").innerHTML = "";
    qs("brokerAnomalies").innerHTML = "";
    qs("brokerSectorFlows").innerHTML = "";
    qs("brokerMeta").textContent = "";
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
              <td class="mono">${r.broker_id}</td>
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
              <td class="mono">${r.stock_symbol}</td>
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
  qs("clearBrokerBtn").addEventListener("click", clearBroker);
  qs("loadSectorsBtn").addEventListener("click", loadSectors);
  qs("loadTopBrokersBtn").addEventListener("click", loadTopBrokers);
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
      throw new Error(`HTTP ${res.status}: ${detail}`);
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
              <td>${a.anomaly_type}</td>
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
