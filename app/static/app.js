(() => {
  "use strict";

  const state = {
    catalog: null,
    schemaFilter: "all",
    history: [],
    lastResult: null,
    rowSearch: "",
    sortCol: null,
    sortAsc: true,
    openTables: new Set(["customers", "orders", "subscriptions"]),
    activeJsonRaw: "",
  };

  const el = {
    statusBadge: document.getElementById("status-badge"),
    modeBadge: document.getElementById("mode-badge"),
    dbBadge: document.getElementById("db-badge"),
    btnRefreshSchema: document.getElementById("btn-refresh-schema"),
    btnThemeToggle: document.getElementById("btn-theme-toggle"),
    iconSun: document.getElementById("icon-sun"),
    iconMoon: document.getElementById("icon-moon"),
    schemaCount: document.getElementById("schema-count"),
    schemaSearch: document.getElementById("schema-search"),
    schemaTree: document.getElementById("schema-tree"),
    queryForm: document.getElementById("query-form"),
    promptInput: document.getElementById("prompt-input"),
    maxRowsSelect: document.getElementById("max-rows-select"),
    btnAsk: document.getElementById("btn-ask"),
    btnClearChat: document.getElementById("btn-clear-chat"),
    queryProgress: document.getElementById("query-progress"),
    chatHistoryBar: document.getElementById("chat-history-bar"),
    bannerArea: document.getElementById("banner-area"),
    sqlEditor: document.getElementById("sql-editor"),
    sqlHighlight: document.getElementById("sql-highlight"),
    sqlGutter: document.getElementById("sql-gutter"),
    btnCopySql: document.getElementById("btn-copy-sql"),
    btnRunSql: document.getElementById("btn-run-sql"),
    explanationBar: document.getElementById("explanation-bar"),
    explanationText: document.getElementById("explanation-text"),
    assumptionsList: document.getElementById("assumptions-list"),
    btnToggleTrace: document.getElementById("btn-toggle-trace"),
    pipelineStepper: document.getElementById("pipeline-stepper"),
    traceSummary: document.getElementById("trace-summary"),
    traceBody: document.getElementById("trace-body"),
    resultsMeta: document.getElementById("results-meta"),
    resultsFilterWrap: document.getElementById("results-filter-wrap"),
    resultsFilterInput: document.getElementById("results-filter-input"),
    resultsSummaryBar: document.getElementById("results-summary-bar"),
    resultsContainer: document.getElementById("results-container"),
    btnExportCsv: document.getElementById("btn-export-csv"),
    btnExportJson: document.getElementById("btn-export-json"),
    jsonModal: document.getElementById("json-modal"),
    jsonModalTitle: document.getElementById("json-modal-title"),
    jsonModalContent: document.getElementById("json-modal-content"),
    jsonModalBackdrop: document.getElementById("json-modal-backdrop"),
    btnCopyJson: document.getElementById("btn-copy-json"),
    btnCloseModal: document.getElementById("btn-close-modal"),
    toastContainer: document.getElementById("toast-container"),
  };

  /* ------------------------------------------------------------------------
     Utilities: Escaping, Toasts, Theme, SQL & JSON Syntax Highlighting
     ------------------------------------------------------------------------ */
  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function showToast(message) {
    const node = document.createElement("div");
    node.className = "toast";
    node.textContent = message;
    el.toastContainer.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transition = "opacity 0.2s ease";
      setTimeout(() => node.remove(), 220);
    }, 2200);
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("pg-nl2sql-theme", theme);
    const isDark = theme === "dark";
    el.iconSun.classList.toggle("hidden", !isDark);
    el.iconMoon.classList.toggle("hidden", isDark);
  }

  function initTheme() {
    const saved = localStorage.getItem("pg-nl2sql-theme");
    if (saved === "dark" || saved === "light") {
      applyTheme(saved);
    } else {
      applyTheme("light");
    }
  }

  const SQL_KEYWORDS = new Set([
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS",
    "ON", "AS", "AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE", "WITH", "RECURSIVE",
    "GROUP", "BY", "ORDER", "ASC", "DESC", "NULLS", "FIRST", "LAST", "HAVING", "LIMIT",
    "OFFSET", "DISTINCT", "UNION", "ALL", "CASE", "WHEN", "THEN", "ELSE", "END", "FILTER",
    "OVER", "PARTITION", "BETWEEN", "LIKE", "ILIKE", "ANY", "SOME", "EXISTS", "CAST",
    "INTERVAL", "ARRAY", "CURRENT_DATE", "NOW", "DESC", "ASC",
  ]);

  const SQL_FUNCTIONS = new Set([
    "COALESCE", "SUM", "COUNT", "AVG", "MIN", "MAX", "ARRAY_AGG", "JSONB_AGG",
    "JSONB_ARRAY_ELEMENTS", "JSONB_ARRAY_ELEMENTS_TEXT", "DATE_TRUNC", "EXTRACT",
    "UNNEST", "ROUND", "LOWER", "UPPER", "CONCAT", "STRING_AGG", "TO_CHAR",
  ]);

  function highlightSql(sql) {
    if (!sql) return "";
    // Tokenize comments, single-quoted strings, Postgres operators, numbers, identifiers
    const regex = /(--[^\n]*)|('(?:''|[^'])*')|(->>|->|@>|<@|::|\|\|)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g;
    let out = "";
    let last = 0;
    let match;
    while ((match = regex.exec(sql)) !== null) {
      out += escapeHtml(sql.slice(last, match.index));
      const [full, comment, str, op, num, ident] = match;
      if (comment) {
        out += `<span class="tok-comment">${escapeHtml(comment)}</span>`;
      } else if (str) {
        out += `<span class="tok-str">${escapeHtml(str)}</span>`;
      } else if (op) {
        out += `<span class="tok-op">${escapeHtml(op)}</span>`;
      } else if (num) {
        out += `<span class="tok-num">${escapeHtml(num)}</span>`;
      } else if (ident) {
        const up = ident.toUpperCase();
        if (SQL_KEYWORDS.has(up)) {
          out += `<span class="tok-kw">${escapeHtml(ident)}</span>`;
        } else if (SQL_FUNCTIONS.has(up)) {
          out += `<span class="tok-fn">${escapeHtml(ident)}</span>`;
        } else {
          out += escapeHtml(ident);
        }
      } else {
        out += escapeHtml(full);
      }
      last = regex.lastIndex;
    }
    out += escapeHtml(sql.slice(last));
    // Ensure trailing newline renders cleanly for scroll sync
    return out + "\n";
  }

  function highlightJson(obj) {
    const raw = JSON.stringify(obj, null, 2);
    const regex = /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?)|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    let out = "";
    let last = 0;
    let m;
    while ((m = regex.exec(raw)) !== null) {
      out += escapeHtml(raw.slice(last, m.index));
      const [full, str, isKey, boolNull, num] = m;
      if (str) {
        if (isKey) {
          const keyOnly = str.slice(0, str.length - isKey.length);
          out += `<span class="tok-fn">${escapeHtml(keyOnly)}</span>${escapeHtml(isKey)}`;
        } else {
          out += `<span class="tok-str">${escapeHtml(str)}</span>`;
        }
      } else if (boolNull) {
        out += `<span class="tok-kw">${escapeHtml(boolNull)}</span>`;
      } else if (num) {
        out += `<span class="tok-num">${escapeHtml(num)}</span>`;
      } else {
        out += escapeHtml(full);
      }
      last = regex.lastIndex;
    }
    out += escapeHtml(raw.slice(last));
    return out;
  }

  function syncSqlEditor() {
    const val = el.sqlEditor.value;
    el.sqlHighlight.innerHTML = highlightSql(val);
    const lines = Math.max(1, val.split("\n").length);
    el.sqlGutter.textContent = Array.from({ length: lines }, (_, i) => String(i + 1)).join("\n");
    const hasSql = Boolean(val.trim());
    el.btnCopySql.disabled = !hasSql;
    el.btnRunSql.disabled = !hasSql;
  }

  function syncSqlScroll() {
    el.sqlHighlight.scrollTop = el.sqlEditor.scrollTop;
    el.sqlHighlight.scrollLeft = el.sqlEditor.scrollLeft;
    el.sqlGutter.scrollTop = el.sqlEditor.scrollTop;
  }

  function showBanner(message, type = "error") {
    el.bannerArea.textContent = message;
    el.bannerArea.className = `banner-area banner-${type}`;
    el.bannerArea.classList.remove("hidden");
  }

  function hideBanner() {
    el.bannerArea.classList.add("hidden");
  }

  /* ------------------------------------------------------------------------
     API Calls: Health & Schema Explorer
     ------------------------------------------------------------------------ */
  async function fetchHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      if (data.database?.connected) {
        el.statusBadge.textContent = data.database.transaction_read_only
          ? "Read-Only Connected"
          : "Connected";
        el.statusBadge.className = "meta-badge badge-ok";
        el.dbBadge.textContent = `${data.database.database} · ${data.database.user}`;
      } else {
        el.statusBadge.textContent = "DB Disconnected";
        el.statusBadge.className = "meta-badge badge-err";
        el.dbBadge.textContent = "DB unavailable";
      }
      el.modeBadge.textContent =
        data.mode === "gemini"
          ? `Gemini · ${data.models.generator}`
          : "Demo Mode";
    } catch (_err) {
      el.statusBadge.textContent = "API Error";
      el.statusBadge.className = "meta-badge badge-err";
    }
  }

  async function loadSchema(forceRefresh = false) {
    el.btnRefreshSchema.disabled = true;
    const icon = el.btnRefreshSchema.querySelector(".icon-refresh");
    if (icon) icon.classList.add("spinning");
    try {
      const res = await fetch(forceRefresh ? "/api/schema/refresh" : "/api/schema", {
        method: forceRefresh ? "POST" : "GET",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      state.catalog = forceRefresh ? data.catalog : data;
      renderSchemaTree();
      if (forceRefresh) {
        showToast(`Catalog refreshed (${data.table_count} objects profiled)`);
      }
    } catch (err) {
      el.schemaTree.innerHTML = `<div class="empty-hint">Failed to load schema: ${escapeHtml(err.message)}</div>`;
    } finally {
      el.btnRefreshSchema.disabled = false;
      if (icon) icon.classList.remove("spinning");
    }
  }

  function renderSchemaTree() {
    if (!state.catalog?.tables) return;
    const query = el.schemaSearch.value.trim().toLowerCase();
    const tables = Object.values(state.catalog.tables).sort((a, b) => a.name.localeCompare(b.name));

    const filtered = tables.filter((tbl) => {
      if (state.schemaFilter !== "all" && tbl.table_type !== state.schemaFilter) return false;
      if (!query) return true;
      if (tbl.name.toLowerCase().includes(query)) return true;
      if ((tbl.comment || "").toLowerCase().includes(query)) return true;
      return tbl.columns.some((col) => {
        if (col.name.toLowerCase().includes(query)) return true;
        if (col.data_type.toLowerCase().includes(query)) return true;
        if (col.enum_values.some((v) => v.toLowerCase().includes(query))) return true;
        return Object.keys(col.jsonb_schema || {}).some((k) => k.toLowerCase().includes(query));
      });
    });

    el.schemaCount.textContent = `${filtered.length}`;
    if (!filtered.length) {
      el.schemaTree.innerHTML = `<div class="empty-hint">No matching tables or views.</div>`;
      return;
    }

    el.schemaTree.innerHTML = filtered
      .map((tbl) => {
        const isOpen = query ? true : state.openTables.has(tbl.name);
        const isView = tbl.table_type === "view";
        const badgeClass = isView ? "table-kind-badge table-kind-view" : "table-kind-badge";
        const iconSvg = isView
          ? `<svg class="table-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>`
          : `<svg class="table-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>`;

        const fkTargets = [...new Set((tbl.foreign_keys || []).map((fk) => fk.target_table))];
        const fkHtml = fkTargets.length
          ? `<div class="fk-strip">${fkTargets.map((t) => `<span class="fk-pill">→ ${escapeHtml(t)}</span>`).join("")}</div>`
          : "";

        const colsHtml = isOpen
          ? `<div class="schema-cols">
              ${tbl.comment ? `<p class="table-comment">${escapeHtml(tbl.comment)}</p>` : ""}
              ${fkHtml}
              ${tbl.columns
                .map((col) => {
                  const extras = [];
                  if (col.enum_values?.length) extras.push(`ENUM: ${col.enum_values.join(", ")}`);
                  if (col.distinct_values?.length) extras.push(`Values: ${col.distinct_values.slice(0, 6).join(", ")}`);
                  if (col.jsonb_schema && Object.keys(col.jsonb_schema).length) {
                    const keys = Object.entries(col.jsonb_schema)
                      .slice(0, 6)
                      .map(([k, t]) => `${k}:${t}`)
                      .join(", ");
                    extras.push(`JSONB { ${keys} }`);
                  }
                  if (col.array_element_type && col.sample_values?.length) {
                    extras.push(`Items: ${col.sample_values.slice(0, 5).join(", ")}`);
                  }
                  return `
                    <div class="col-row">
                      <div class="col-main">
                        <span class="col-name">${escapeHtml(col.name)}${col.is_primary_key ? '<span class="col-pk">PK</span>' : ""}</span>
                        <span class="col-type">${escapeHtml(col.data_type)}</span>
                      </div>
                      ${extras.length ? `<div class="col-extra">${escapeHtml(extras.join(" · "))}</div>` : ""}
                    </div>
                  `;
                })
                .join("")}
            </div>`
          : "";

        return `
          <div class="schema-table-item ${isOpen ? "open" : ""}">
            <div class="schema-table-row">
              <button type="button" class="schema-table-btn" data-table="${escapeHtml(tbl.name)}">
                <span class="table-name-wrap">
                  ${iconSvg}
                  <span class="table-name" title="${escapeHtml(tbl.name)}">${escapeHtml(tbl.name)}</span>
                </span>
                <span class="${badgeClass}">${isView ? "view" : `~${tbl.row_estimate}`}</span>
              </button>
              <button type="button" class="quick-query-btn" data-preview="${escapeHtml(tbl.name)}" title="Preview rows from ${escapeHtml(tbl.name)}">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                  <polygon points="6 3 20 12 6 21 6 3"></polygon>
                </svg>
              </button>
            </div>
            ${colsHtml}
          </div>
        `;
      })
      .join("");

    el.schemaTree.querySelectorAll(".schema-table-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const name = btn.dataset.table;
        if (state.openTables.has(name)) state.openTables.delete(name);
        else state.openTables.add(name);
        renderSchemaTree();
      });
    });

    el.schemaTree.querySelectorAll(".quick-query-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const tblName = btn.dataset.preview;
        el.sqlEditor.value = `SELECT *\nFROM ${tblName}\nLIMIT 50`;
        syncSqlEditor();
        executeEditedSql();
      });
    });
  }

  /* ------------------------------------------------------------------------
     History, Trace Stepper & Results Rendering
     ------------------------------------------------------------------------ */
  function renderHistory() {
    if (!state.history.length) {
      el.chatHistoryBar.classList.add("hidden");
      return;
    }
    const userTurns = state.history.filter((m) => m.role === "user");
    el.chatHistoryBar.classList.remove("hidden");
    el.chatHistoryBar.innerHTML =
      `<span class="history-label">Follow-up context (${userTurns.length}):</span>` +
      userTurns
        .slice(-4)
        .map((m) => `<span class="history-pill" title="${escapeHtml(m.content)}">${escapeHtml(m.content)}</span>`)
        .join("");
  }

  function renderTrace(trace, hasError) {
    if (!trace || !Object.keys(trace).length) {
      el.traceSummary.textContent = "Inspect tables & EXPLAIN cost";
      el.traceBody.innerHTML = "";
      return;
    }

    const linked = trace.linked_tables || [];
    const bridges = trace.bridge_tables || [];
    const steps = trace.steps || [];

    const safetyBlocked = trace.safety_gate === "blocked" || steps.some((s) => s.stage === "safety_gate" && s.status === "blocked");
    const pgOk = steps.some((s) => s.stage === "postgres_execution" && s.status === "succeeded") || (trace.mode === "direct_sql" && !hasError);
    const repairs = trace.repair_attempts || 0;

    if (trace.mode === "direct_sql") {
      el.pipelineStepper.innerHTML = `
        <span class="step-pill ${safetyBlocked ? "step-err" : "step-ok"}">AST Gate</span>
        <span class="step-pill ${pgOk ? "step-ok" : "step-err"}">Postgres EXPLAIN &amp; Run</span>
      `;
    } else {
      el.pipelineStepper.innerHTML = `
        <span class="step-pill step-ok">1. Linker (${linked.length})</span>
        <span class="step-pill ${bridges.length ? "step-ok" : ""}">2. FK Graph${bridges.length ? ` (+${bridges.length})` : ""}</span>
        <span class="step-pill ${repairs ? "step-warn" : "step-ok"}">3. SQL Gen${repairs ? ` (${repairs} fix)` : ""}</span>
        <span class="step-pill ${safetyBlocked ? "step-err" : "step-ok"}">4. AST Gate</span>
        <span class="step-pill ${pgOk ? "step-ok" : hasError ? "step-err" : ""}">5. Postgres</span>
      `;
    }

    const summaryParts = [
      trace.explain_total_cost != null ? `EXPLAIN cost ${trace.explain_total_cost}` : null,
      trace.total_ms != null ? `${trace.total_ms} ms total` : null,
    ].filter(Boolean);
    el.traceSummary.textContent = summaryParts.join(" · ") || "Inspect trace details";

    const tableChips = linked
      .map((t) => {
        const isBridge = bridges.includes(t);
        return `<span class="trace-chip ${isBridge ? "trace-chip-bridge" : ""}">${escapeHtml(t)}${isBridge ? " · bridge" : ""}</span>`;
      })
      .join("");

    const joinPathsHtml = (trace.join_paths || []).length
      ? `<div><div class="trace-section-title">Discovered FK Join Paths</div><div class="muted-note" style="font-family:var(--font-mono);font-size:11.5px;">${escapeHtml(trace.join_paths.join("  |  "))}</div></div>`
      : "";

    const stepsHtml = steps
      .map((st) => {
        const details = [];
        if (st.method) details.push(st.method);
        if (st.mode) details.push(st.mode);
        if (st.status) details.push(st.status);
        if (st.applied_limit) details.push(`LIMIT ${st.applied_limit}`);
        if (st.explain_total_cost != null) details.push(`EXPLAIN cost ${st.explain_total_cost}`);
        if (st.row_count != null) details.push(`${st.row_count} rows`);
        if (st.error) details.push(st.error);
        const timing = st.duration_ms ?? st.execution_ms;
        return `
          <div class="trace-step">
            <span><strong>${escapeHtml(st.stage)}</strong> — ${escapeHtml(details.join(" · "))}</span>
            <span class="muted-note" style="font-family:var(--font-mono);">${timing != null ? `${timing} ms` : ""}</span>
          </div>
        `;
      })
      .join("");

    el.traceBody.innerHTML = `
      ${linked.length ? `<div><div class="trace-section-title">Focused Schema Scope</div><div class="trace-chips">${tableChips}</div></div>` : ""}
      ${joinPathsHtml}
      ${stepsHtml ? `<div><div class="trace-section-title">Execution Steps</div><div class="trace-steps">${stepsHtml}</div></div>` : ""}
    `;
  }

  function renderSummaryStrip(data) {
    if (!data?.rows?.length) {
      el.resultsSummaryBar.classList.add("hidden");
      return;
    }
    const metrics = [
      { label: "Rows Returned", value: data.row_count.toLocaleString() },
      { label: "Execution Time", value: `${data.execution_ms} ms` },
    ];
    if (data.trace?.explain_total_cost != null) {
      metrics.push({ label: "EXPLAIN Cost", value: String(data.trace.explain_total_cost) });
    }

    // Highlight up to 2 meaningful monetary / quantity numeric columns
    const numericCols = data.columns.filter((col) => {
      if (col.endsWith("_id") || col === "log_id") return false;
      return data.rows.some((r) => typeof r[col] === "number");
    });

    for (const col of numericCols.slice(0, 2)) {
      const vals = data.rows.map((r) => r[col]).filter((v) => typeof v === "number");
      if (!vals.length) continue;
      const sum = vals.reduce((acc, v) => acc + v, 0);
      const isMoney = /(usd|mrr|revenue|amount|balance|total|price|value)/i.test(col);
      const formatted = isMoney
        ? `$${sum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : sum.toLocaleString(undefined, { maximumFractionDigits: 2 });
      metrics.push({ label: `Sum · ${col}`, value: formatted });
    }

    el.resultsSummaryBar.classList.remove("hidden");
    el.resultsSummaryBar.innerHTML = metrics
      .map(
        (m) => `
          <div class="summary-metric">
            <span class="summary-label">${escapeHtml(m.label)}</span>
            <span class="summary-value">${escapeHtml(m.value)}</span>
          </div>
        `
      )
      .join("");
  }

  function classifyStatusChip(colName, val) {
    if (typeof val !== "string") return null;
    if (!/(status|priority|tier)/i.test(colName)) return null;
    const v = val.toLowerCase();
    if (["active", "delivered", "resolved", "closed", "enterprise"].includes(v)) return "chip-ok";
    if (["urgent", "high", "suspended", "churned", "cancelled", "refunded"].includes(v)) return "chip-err";
    if (["trial", "pending", "in_progress", "waiting_customer", "medium", "growth"].includes(v)) return "chip-warn";
    if (["open", "confirmed", "shipped", "low", "starter"].includes(v)) return "chip-info";
    return null;
  }

  function renderResults(data) {
    state.lastResult = data;
    state.sortCol = null;
    state.sortAsc = true;
    state.rowSearch = "";
    el.resultsFilterInput.value = "";

    const hasRows = Array.isArray(data.rows) && data.columns?.length > 0;
    el.btnExportCsv.disabled = !hasRows;
    el.btnExportJson.disabled = !hasRows;
    el.resultsFilterWrap.classList.toggle("hidden", !hasRows);

    if (data.clarifying_question && !data.sql) {
      showBanner(`Clarification needed: ${data.clarifying_question}`, "warn");
      el.resultsMeta.textContent = "Awaiting clarification";
      el.resultsSummaryBar.classList.add("hidden");
      renderTrace(data.trace, false);
      return;
    }

    if (data.error) showBanner(data.error, "error");
    else hideBanner();

    el.sqlEditor.value = data.sql || "";
    syncSqlEditor();

    if (data.explanation || (data.assumptions && data.assumptions.length)) {
      el.explanationBar.classList.remove("hidden");
      el.explanationText.textContent = data.explanation || "";
      el.assumptionsList.innerHTML = (data.assumptions || [])
        .map((a) => `<li>${escapeHtml(a)}</li>`)
        .join("");
    } else {
      el.explanationBar.classList.add("hidden");
    }

    renderTrace(data.trace, Boolean(data.error));
    renderSummaryStrip(data);

    if (!hasRows) {
      el.resultsMeta.textContent = data.error ? "Execution failed" : "0 rows returned";
      el.resultsContainer.innerHTML = `<div class="empty-state"><p class="empty-state-title">${
        data.error ? "Query blocked or failed" : "No rows returned"
      }</p><p class="muted-note">${
        data.error ? "Adjust the SQL above or try another prompt." : "The query executed cleanly against PostgreSQL with 0 matching rows."
      }</p></div>`;
      return;
    }

    const truncNote = data.truncated ? " · capped at row limit" : "";
    el.resultsMeta.textContent = `${data.row_count} rows · ${data.execution_ms} ms${truncNote}`;
    drawTable();
  }

  function drawTable() {
    const data = state.lastResult;
    if (!data?.columns?.length) return;

    const filterText = state.rowSearch.trim().toLowerCase();
    let rows = data.rows.map((r, originalIndex) => ({ r, originalIndex }));

    if (filterText) {
      rows = rows.filter(({ r }) =>
        data.columns.some((col) => {
          const val = r[col];
          if (val == null) return false;
          const str = typeof val === "object" ? JSON.stringify(val) : String(val);
          return str.toLowerCase().includes(filterText);
        })
      );
    }

    if (state.sortCol !== null) {
      const col = state.sortCol;
      const dir = state.sortAsc ? 1 : -1;
      rows.sort((a, b) => {
        const va = a.r[col];
        const vb = b.r[col];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
        return String(va).localeCompare(String(vb)) * dir;
      });
    }

    const headHtml = data.columns
      .map((col, idx) => {
        const pgType = data.column_types?.[idx] || "";
        const indicator = state.sortCol === col ? (state.sortAsc ? " ↑" : " ↓") : "";
        return `<th data-col="${escapeHtml(col)}">${escapeHtml(col)}${indicator}<span class="th-type">${escapeHtml(pgType)}</span></th>`;
      })
      .join("");

    const bodyHtml = rows.length
      ? rows
          .map(({ r: row, originalIndex }) => {
            const cells = data.columns
              .map((col, colIdx) => {
                const val = row[col];
                const pgType = (data.column_types?.[colIdx] || "").toLowerCase();

                if (val === null || val === undefined) {
                  return `<td><span class="cell-null">NULL</span></td>`;
                }
                if (Array.isArray(val)) {
                  if (!val.length) return `<td><span class="cell-null">[]</span></td>`;
                  const pills = val
                    .slice(0, 6)
                    .map((item) => `<span class="array-pill">${escapeHtml(item)}</span>`)
                    .join("");
                  return `<td><div class="cell-array">${pills}</div></td>`;
                }
                if (typeof val === "object") {
                  const keys = Object.keys(val);
                  const preview = `{ ${keys.slice(0, 2).join(", ")}${keys.length > 2 ? `, +${keys.length - 2}` : ""} }`;
                  return `<td><button type="button" class="json-trigger" data-row="${originalIndex}" data-col="${escapeHtml(col)}">${escapeHtml(preview)}</button></td>`;
                }
                if (typeof val === "number") {
                  const isMoney = /(usd|mrr|revenue|amount|balance|total|price|value)/i.test(col);
                  const formatted = isMoney
                    ? val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                    : val.toLocaleString();
                  return `<td class="cell-num">${escapeHtml(formatted)}</td>`;
                }

                const chipCls = classifyStatusChip(col, val);
                if (chipCls) {
                  return `<td><span class="status-chip ${chipCls}">${escapeHtml(val)}</span></td>`;
                }

                if (["inet", "daterange", "interval", "bytea", "uuid", "timestamptz", "date"].includes(pgType)) {
                  return `<td class="cell-mono" title="${escapeHtml(val)}">${escapeHtml(val)}</td>`;
                }

                return `<td title="${escapeHtml(val)}">${escapeHtml(val)}</td>`;
              })
              .join("");
            return `<tr>${cells}</tr>`;
          })
          .join("")
      : `<tr><td colspan="${data.columns.length}" style="text-align:center;padding:24px;color:var(--text-muted);">No rows match "${escapeHtml(state.rowSearch)}".</td></tr>`;

    el.resultsContainer.innerHTML = `
      <table class="data-table">
        <thead><tr>${headHtml}</tr></thead>
        <tbody>${bodyHtml}</tbody>
      </table>
    `;

    el.resultsContainer.querySelectorAll("th[data-col]").forEach((th) => {
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (state.sortCol === col) state.sortAsc = !state.sortAsc;
        else {
          state.sortCol = col;
          state.sortAsc = true;
        }
        drawTable();
      });
    });

    el.resultsContainer.querySelectorAll(".json-trigger").forEach((btn) => {
      btn.addEventListener("click", () => {
        const row = state.lastResult.rows[Number(btn.dataset.row)];
        const col = btn.dataset.col;
        openJsonModal(col, row[col]);
      });
    });
  }

  function openJsonModal(colName, obj) {
    state.activeJsonRaw = JSON.stringify(obj, null, 2);
    el.jsonModalTitle.textContent = `JSONB · ${colName}`;
    el.jsonModalContent.innerHTML = highlightJson(obj);
    el.jsonModal.classList.remove("hidden");
  }

  function closeJsonModal() {
    el.jsonModal.classList.add("hidden");
  }

  /* ------------------------------------------------------------------------
     Query Execution
     ------------------------------------------------------------------------ */
  function setLoading(isLoading) {
    el.btnAsk.disabled = isLoading;
    el.queryProgress.classList.toggle("hidden", !isLoading);
  }

  async function submitNaturalQuery(prompt) {
    const trimmed = prompt.trim();
    if (!trimmed) return;
    hideBanner();
    setLoading(true);

    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: trimmed,
          history: state.history,
          max_rows: Number(el.maxRowsSelect.value),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      state.history.push({ role: "user", content: trimmed });
      if (data.explanation) {
        state.history.push({ role: "assistant", content: data.explanation });
      }
      renderHistory();
      renderResults(data);
    } catch (err) {
      showBanner(`Request failed: ${err.message}`, "error");
    } finally {
      setLoading(false);
    }
  }

  async function executeEditedSql() {
    const sql = el.sqlEditor.value.trim();
    if (!sql) return;
    hideBanner();
    el.btnRunSql.disabled = true;
    el.queryProgress.classList.remove("hidden");

    try {
      const res = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sql,
          max_rows: Number(el.maxRowsSelect.value),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      renderResults(data);
    } catch (err) {
      showBanner(`Execution failed: ${err.message}`, "error");
    } finally {
      el.btnRunSql.disabled = false;
      el.queryProgress.classList.add("hidden");
    }
  }

  /* ------------------------------------------------------------------------
     Export Helpers
     ------------------------------------------------------------------------ */
  function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function exportCsv() {
    if (!state.lastResult?.columns?.length) return;
    const cols = state.lastResult.columns;
    const csvEscape = (val) => {
      if (val === null || val === undefined) return "";
      const str = typeof val === "object" ? JSON.stringify(val) : String(val);
      return /[",\n]/.test(str) ? `"${str.replaceAll('"', '""')}"` : str;
    };
    const lines = [
      cols.map(csvEscape).join(","),
      ...state.lastResult.rows.map((r) => cols.map((c) => csvEscape(r[c])).join(",")),
    ];
    downloadFile(lines.join("\n"), "pg_nl2sql_results.csv", "text/csv;charset=utf-8");
    showToast("Exported pg_nl2sql_results.csv");
  }

  function exportJson() {
    if (!state.lastResult?.rows) return;
    downloadFile(JSON.stringify(state.lastResult.rows, null, 2), "pg_nl2sql_results.json", "application/json");
    showToast("Exported pg_nl2sql_results.json");
  }

  /* ------------------------------------------------------------------------
     Event Bindings
     ------------------------------------------------------------------------ */
  el.queryForm.addEventListener("submit", (e) => {
    e.preventDefault();
    submitNaturalQuery(el.promptInput.value);
  });

  el.promptInput.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submitNaturalQuery(el.promptInput.value);
    }
  });

  document.querySelectorAll(".starter-card").forEach((card) => {
    card.addEventListener("click", () => {
      el.promptInput.value = card.dataset.prompt;
      submitNaturalQuery(card.dataset.prompt);
    });
  });

  document.querySelectorAll(".seg-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".seg-tab").forEach((t) => {
        t.classList.remove("active");
        t.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      state.schemaFilter = tab.dataset.filter;
      renderSchemaTree();
    });
  });

  el.schemaSearch.addEventListener("input", renderSchemaTree);
  el.btnRefreshSchema.addEventListener("click", () => loadSchema(true));

  el.btnThemeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    showToast(`Switched to ${next} theme`);
  });

  el.btnClearChat.addEventListener("click", () => {
    state.history = [];
    el.promptInput.value = "";
    renderHistory();
    showToast("Cleared follow-up context");
  });

  el.sqlEditor.addEventListener("input", syncSqlEditor);
  el.sqlEditor.addEventListener("scroll", syncSqlScroll);
  el.sqlEditor.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      executeEditedSql();
    }
  });

  el.btnCopySql.addEventListener("click", async () => {
    await navigator.clipboard.writeText(el.sqlEditor.value);
    showToast("SQL copied to clipboard");
  });

  el.btnCopyJson.addEventListener("click", async () => {
    await navigator.clipboard.writeText(state.activeJsonRaw);
    showToast("JSONB payload copied");
  });

  el.btnRunSql.addEventListener("click", executeEditedSql);
  el.btnExportCsv.addEventListener("click", exportCsv);
  el.btnExportJson.addEventListener("click", exportJson);

  el.resultsFilterInput.addEventListener("input", () => {
    state.rowSearch = el.resultsFilterInput.value;
    drawTable();
  });

  el.btnToggleTrace.addEventListener("click", () => {
    const isHidden = el.traceBody.classList.toggle("hidden");
    el.btnToggleTrace.setAttribute("aria-expanded", String(!isHidden));
  });

  el.btnCloseModal.addEventListener("click", closeJsonModal);
  el.jsonModalBackdrop.addEventListener("click", closeJsonModal);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeJsonModal();
  });

  /* ------------------------------------------------------------------------
     Boot
     ------------------------------------------------------------------------ */
  initTheme();
  syncSqlEditor();
  fetchHealth();
  loadSchema(false);
})();
