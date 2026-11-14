(() => {
  let state = {
    data: null,
    activeModule: "A",
    search: "",
    pType: "ALL",
    verify: "ALL",
    sortBy: "default",
    selectedDeal: null,
    secondsUntilNext: 7200,
  };

  const esc = (str) =>
    String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function showToast(text) {
    const el = document.getElementById("toast-msg");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.classList.remove("show"), 3600);
  }

  function formatDuration(sec) {
    const s = Math.max(0, Math.floor(sec));
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const r = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${r}`;
  }

  function pTypeBadgeClass(pType) {
    if (pType === "真正免费" || pType === "学生/教育免费") return "badge-free";
    if (pType === "免费试用") return "badge-trial";
    return "badge-threshold";
  }

  function getActiveList() {
    if (!state.data) return { title: "", desc: "", items: [] };
    const mods = state.data.modules;
    if (mods[state.activeModule]) {
      const m = mods[state.activeModule];
      return { title: `${m.id}. ${m.name}`, desc: m.subtitle, items: [...m.items] };
    }
    if (state.activeModule === "SW") {
      return {
        title: "软件数字服务与全球精选优惠",
        desc: "覆盖正版软件买断/黑五史低、隐私工具、游戏喜加一与全球旅行 eSIM 流量福利",
        items: [...(state.data.extra_software_global || [])],
      };
    }
    if (state.activeModule === "ALL") {
      return {
        title: "全量有效情报主数据库",
        desc: "汇总全部七大监控领域内所有在效优惠活动，支持多维度组合检索",
        items: [...(state.data.all_active || [])],
      };
    }
    return { title: "采集工作流与全球监控信源", desc: "每2小时自动巡检、URL验活、指纹去重与过期自动归档运行日志", items: [] };
  }

  function renderKPIs() {
    if (!state.data) return;
    const k = state.data.kpis;
    document.getElementById("kpi-active").textContent = k.active_count;
    document.getElementById("kpi-today").textContent = k.today_new_count;
    document.getElementById("kpi-free").textContent = k.truly_free_count;
    document.getElementById("kpi-expiring").textContent = k.expiring_soon_count;
    document.getElementById("kpi-pending").textContent = k.pending_verify_count;
    document.getElementById("kpi-usd").textContent = `$${Number(k.total_verified_value_usd).toLocaleString()}`;
    document.getElementById("kpi-sources-meta").textContent = `实时监控 ${k.monitored_sources_count} 个全球信源`;
    document.getElementById("kpi-archived-meta").textContent = `已自动归档 ${k.archived_count} 条过期活动`;
    document.getElementById("header-sync-meta").textContent = `最后全量核验：${state.data.generated_at} · 每2小时增量巡检已激活`;

    for (const key of ["A", "B", "C", "D", "E", "F", "G", "H"]) {
      const el = document.getElementById(`cnt-${key}`);
      if (el && state.data.modules[key]) {
        el.textContent = state.data.modules[key].items.length;
      }
    }
    document.getElementById("cnt-SW").textContent = (state.data.extra_software_global || []).length;
    document.getElementById("cnt-ALL").textContent = (state.data.all_active || []).length;
    document.getElementById("cnt-OPS").textContent = (state.data.monitored_sources || []).length;

    const sheetBtn = document.getElementById("btn-open-sheets");
    if (state.data.config && state.data.config.spreadsheet_url) {
      sheetBtn.href = state.data.config.spreadsheet_url;
      sheetBtn.style.display = "inline-flex";
    }
  }

  function renderOpsView() {
    const container = document.getElementById("ops-container");
    const logs = state.data.scan_logs || [];
    const sources = state.data.monitored_sources || [];
    const sheetUrl = state.data.config?.spreadsheet_url || "";

    container.innerHTML = `
      <div class="ops-grid">
        <div class="ops-card">
          <h3>自动化工作流引擎状态（9项核心能力）</h3>
          <table class="ops-table">
            <thead><tr><th>自动化环节</th><th>实现策略</th><th>当前状态</th></tr></thead>
            <tbody>
              <tr><td>定时采集调度</td><td>后台守护线程每 7200 秒（2h）自动巡检 + GitHub Actions 云端 Cron 配置</td><td><span class="badge badge-free">运行中</span></td></tr>
              <tr><td>重复优惠拦截</td><td>URL 规范化（剥离 UTM 参数）+ SHA-256 指纹 + 标题序列相似度 &ge;0.86 拦截</td><td><span class="badge badge-free">已启用</span></td></tr>
              <tr><td>活动有效性检测</td><td>多线程 HTTP 状态码探活 + 页面「ended/expired」语义扫描</td><td><span class="badge badge-free">已启用</span></td></tr>
              <tr><td>截止时间提取</td><td>中英文日期正则解析器自动转为 ISO YYYY-MM-DD 并计算剩余天数</td><td><span class="badge badge-free">已启用</span></td></tr>
              <tr><td>门槛与隐藏条件</td><td>严格区分真正免费 / 免费试用 / 需充值 / 需交易量 / 需邀请 + 自动续费预警</td><td><span class="badge badge-free">严格执行</span></td></tr>
              <tr><td>价值评分与过期归档</td><td>0–100 加权性价比评分优先排序；逾期自动移入 H. 已结束活动档案</td><td><span class="badge badge-free">已启用</span></td></tr>
              <tr><td>云端数据库同步</td><td>SQLite 持久化存储 + Google Sheets 10分表实时云端同步 ${sheetUrl ? `<a class="link-inline" href="${esc(sheetUrl)}" target="_blank" rel="noopener">查看表格 ↗</a>` : ""}</td><td><span class="badge badge-free">已打通</span></td></tr>
            </tbody>
          </table>

          <h3 style="margin-top:20px;">最近自动巡检日志</h3>
          <table class="ops-table">
            <thead><tr><th>完成时间</th><th>触发方式</th><th>巡检摘要</th></tr></thead>
            <tbody>
              ${logs.map((l) => `
                <tr>
                  <td style="white-space:nowrap;">${esc(l.finished_at)}</td>
                  <td><span class="badge badge-cat">${esc(l.trigger_mode)}</span></td>
                  <td>${esc(l.notes)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>

        <div class="ops-card">
          <h3>全球监控信源矩阵（${sources.length} 个官方与高信噪比源）</h3>
          <table class="ops-table">
            <thead><tr><th>监控源名称</th><th>监控领域</th><th>类型</th><th>最近状态</th></tr></thead>
            <tbody>
              ${sources.map((s) => `
                <tr>
                  <td><a class="link-inline" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a></td>
                  <td>${esc(s.category)}</td>
                  <td><span class="badge badge-cat">${esc(s.feed_type)}</span></td>
                  <td>${esc(s.last_status)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function renderMainContent() {
    if (!state.data) return;

    document.querySelectorAll(".module-nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mod === state.activeModule);
    });

    const { title, desc, items } = getActiveList();
    document.getElementById("current-module-title").textContent = title;
    document.getElementById("current-module-desc").textContent = desc;

    const dealsContainer = document.getElementById("deals-container");
    const opsContainer = document.getElementById("ops-container");
    const filterBar = document.getElementById("deal-filter-bar");

    if (state.activeModule === "OPS") {
      dealsContainer.style.display = "none";
      filterBar.style.display = "none";
      opsContainer.style.display = "block";
      document.getElementById("current-module-count").textContent = `${(state.data.monitored_sources || []).length} 个活跃信源`;
      renderOpsView();
      return;
    }

    dealsContainer.style.display = "grid";
    filterBar.style.display = "flex";
    opsContainer.style.display = "none";

    let filtered = items.filter((d) => {
      if (state.pType !== "ALL" && d.participation_type !== state.pType) return false;
      if (state.verify !== "ALL" && d.verification_status !== state.verify) return false;
      if (state.search) {
        const q = state.search.toLowerCase();
        const hay = `${d.title} ${d.category} ${d.offer_summary} ${d.conditions} ${d.regions} ${d.participation_type}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (state.sortBy === "score") {
      filtered.sort((a, b) => b.value_score - a.value_score || b.estimated_value_usd - a.estimated_value_usd);
    } else if (state.sortBy === "usd") {
      filtered.sort((a, b) => b.estimated_value_usd - a.estimated_value_usd || b.value_score - a.value_score);
    } else if (state.sortBy === "deadline") {
      filtered.sort((a, b) => {
        const da = a.days_left === null || a.days_left < 0 ? 9999 : a.days_left;
        const db = b.days_left === null || b.days_left < 0 ? 9999 : b.days_left;
        return da - db;
      });
    }

    document.getElementById("current-module-count").textContent = `共 ${filtered.length} 条情报`;

    if (filtered.length === 0) {
      dealsContainer.innerHTML = `
        <div class="ops-card" style="grid-column: 1 / -1; text-align:center; padding: 36px;">
          <p style="margin:0;color:var(--text-muted);">当前筛选条件下暂无匹配优惠情报，请尝试放宽筛选条件或切换到「∑ 全量有效情报库」。</p>
        </div>
      `;
      return;
    }

    dealsContainer.innerHTML = filtered
      .map((d, idx) => {
        const isExpired = d.status === "expired";
        const rankPrefix = state.activeModule === "B" ? `#${idx + 1} ` : "";
        let countdownBadge = "";
        if (isExpired) {
          countdownBadge = `<span class="badge badge-expired">已结束归档</span>`;
        } else if (d.days_left !== null && d.days_left >= 0 && d.days_left <= 48) {
          countdownBadge = `<span class="badge badge-urgent">${d.days_left === 0 ? "今日截止!" : `剩 ${d.days_left} 天截止`}</span>`;
        }

        const verifyBadge =
          d.verification_status === "已核实"
            ? `<span class="badge badge-free">✓ 已核实</span>`
            : `<span class="badge badge-pending">! 待核实</span>`;

        return `
          <article class="deal-card ${isExpired ? "expired-card" : ""}" data-deal-id="${d.id}">
            <div>
              <div class="card-badges">
                <span class="badge badge-cat">${esc(d.category)}</span>
                <span class="badge ${pTypeBadgeClass(d.participation_type)}">${esc(d.participation_type)}</span>
                ${verifyBadge}
                ${countdownBadge}
              </div>
              <h3 class="deal-title">${esc(rankPrefix + d.title)}</h3>
              <p class="deal-summary">${esc(d.offer_summary)}</p>
            </div>

            <div style="display:flex;flex-direction:column;gap:10px;">
              <div class="deal-value-box">
                <span class="value-highlight">价值：${esc(d.actual_value_text)}</span>
                <span class="score-chip">指数 ${d.value_score}/100</span>
              </div>

              <div class="card-meta-grid">
                <div class="meta-item"><strong>适用地区：</strong>${esc(d.regions.slice(0, 22))}${d.regions.length > 22 ? "…" : ""}</div>
                <div class="meta-item"><strong>截止时间：</strong>${esc(d.end_date)}</div>
              </div>

              <div class="risk-preview">⚠️ 风险提示：${esc(d.risk_warning)}</div>

              <div class="card-footer">
                <span>验证于 ${esc(d.last_verified_at)}</span>
                <span class="link-inline">查看完整12项情报档案 →</span>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    dealsContainer.querySelectorAll(".deal-card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = Number(card.dataset.dealId);
        openDealDrawer(id);
      });
    });
  }

  function findDealById(id) {
    if (!state.data) return null;
    const all = [...(state.data.all_active || []), ...(state.data.modules?.H?.items || [])];
    return all.find((x) => x.id === id) || null;
  }

  function openDealDrawer(id) {
    const d = findDealById(id);
    if (!d) return;
    state.selectedDeal = d;

    const badgesEl = document.getElementById("drawer-badges");
    badgesEl.innerHTML = `
      <span class="badge badge-cat">${esc(d.category)}</span>
      <span class="badge ${pTypeBadgeClass(d.participation_type)}">${esc(d.participation_type)}</span>
      <span class="badge ${d.verification_status === "已核实" ? "badge-free" : "badge-pending"}">${esc(d.verification_status)}</span>
      ${d.status === "expired" ? '<span class="badge badge-expired">已归档</span>' : `<span class="badge badge-cat">性价比指数 ${d.value_score}/100</span>`}
    `;
    document.getElementById("drawer-deal-title").textContent = d.title;
    document.getElementById("drawer-official-btn").href = d.official_url;

    const fields = [
      ["1. 活动名称", d.title],
      ["2. 所属类别与性质", `${d.category} ｜ 门槛类型：${d.participation_type}`],
      ["3. 优惠内容", d.offer_summary],
      ["4. 实际价值", `${d.actual_value_text}\n（系统折算参考值：$${d.estimated_value_usd} USD ｜ 综合优先级评分：${d.value_score}/100）`],
      ["5. 参与条件（含资格与隐藏限制）", d.conditions],
      ["6. 适用国家或地区", d.regions],
      ["7. 开始及截止时间", `${d.time_window_text}${d.days_left !== null && d.days_left >= 0 ? `（距离截止还有 ${d.days_left} 天）` : ""}`],
      ["8. 参与步骤", d.steps],
      ["9. 官方活动链接", `<a class="link-inline" href="${esc(d.official_url)}" target="_blank" rel="noopener noreferrer">${esc(d.official_url)}</a>`, true],
      ["10. 信息来源", d.source_url ? `<a class="link-inline" href="${esc(d.source_url)}" target="_blank" rel="noopener noreferrer">${esc(d.source_info)}</a>` : esc(d.source_info), true],
      ["11. 最后验证时间", `${d.last_verified_at} ｜ 核实状态：${d.verification_status} ｜ 探活状态：HTTP ${d.http_status || 200}`],
      ["12. 风险提示（防反撸必读）", d.risk_warning, false, true],
    ];

    document.getElementById("drawer-fields").innerHTML = fields
      .map(
        ([label, val, isHtml, isRisk]) => `
        <div class="field-row ${isRisk ? "risk-box" : ""}">
          <div class="field-label">${esc(label)}</div>
          <div class="field-val">${isHtml ? val : esc(val)}</div>
        </div>
      `
      )
      .join("");

    document.getElementById("detail-backdrop").classList.add("open");
  }

  async function fetchDashboard() {
    try {
      const resp = await fetch("/api/dashboard");
      const json = await resp.json();
      state.data = json;
      if (json.scheduler) {
        state.secondsUntilNext = json.scheduler.seconds_until_next;
      }
      renderKPIs();
      renderMainContent();
    } catch (err) {
      showToast("加载情报库失败，请检查本地服务状态");
    }
  }

  // Event Listeners
  document.querySelectorAll(".module-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeModule = btn.dataset.mod;
      renderMainContent();
    });
  });

  document.getElementById("filter-search").addEventListener("input", (e) => {
    state.search = e.target.value.trim();
    renderMainContent();
  });

  document.getElementById("filter-ptype").addEventListener("change", (e) => {
    state.pType = e.target.value;
    renderMainContent();
  });

  document.getElementById("filter-verify").addEventListener("change", (e) => {
    state.verify = e.target.value;
    renderMainContent();
  });

  document.getElementById("filter-sort").addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    renderMainContent();
  });

  document.getElementById("btn-close-drawer").addEventListener("click", () => {
    document.getElementById("detail-backdrop").classList.remove("open");
  });

  document.getElementById("detail-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "detail-backdrop") {
      document.getElementById("detail-backdrop").classList.remove("open");
    }
  });

  document.getElementById("drawer-toggle-verify").addEventListener("click", async () => {
    if (!state.selectedDeal) return;
    await fetch("/api/deals/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.selectedDeal.id, action: "toggle_verify" }),
    });
    await fetchDashboard();
    openDealDrawer(state.selectedDeal.id);
    showToast("已更新该条情报的核实状态");
  });

  document.getElementById("drawer-archive-btn").addEventListener("click", async () => {
    if (!state.selectedDeal) return;
    await fetch("/api/deals/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.selectedDeal.id, action: "archive" }),
    });
    document.getElementById("detail-backdrop").classList.remove("open");
    await fetchDashboard();
    showToast("已将该活动归档至「H. 已结束活动档案」");
  });

  document.getElementById("btn-run-scan").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "全网巡检中…";
    showToast("已启动 20 个全球信源扫描与官方链接验活…");
    try {
      const resp = await fetch("/api/scan", { method: "POST" });
      const data = await resp.json();
      await fetchDashboard();
      showToast(data.result?.notes || "全网巡检已完成");
    } catch {
      showToast("巡检请求超时或异常");
    } finally {
      btn.disabled = false;
      btn.textContent = "立即巡检验活";
    }
  });

  document.getElementById("btn-sync-sheets").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "云端同步中…";
    try {
      const resp = await fetch("/api/sync-sheets", { method: "POST" });
      const data = await resp.json();
      if (data.ok) {
        await fetchDashboard();
        showToast("已成功同步至 Google Sheets 10分表云端数据库");
      } else {
        showToast("云端同步异常：" + (data.error || ""));
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "同步 Google 表格";
    }
  });

  // Add Modal
  const addBackdrop = document.getElementById("add-modal-backdrop");
  document.getElementById("btn-open-add").addEventListener("click", () => addBackdrop.classList.add("open"));
  document.getElementById("btn-close-add").addEventListener("click", () => addBackdrop.classList.remove("open"));
  document.getElementById("btn-cancel-add").addEventListener("click", () => addBackdrop.classList.remove("open"));

  document.getElementById("add-deal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      title: document.getElementById("input-title").value,
      official_url: document.getElementById("input-url").value,
      category: document.getElementById("input-category").value,
      verification_status: document.getElementById("input-verify").value,
      actual_value_text: document.getElementById("input-val-text").value,
      end_date: document.getElementById("input-end-date").value,
      offer_summary: document.getElementById("input-summary").value,
      risk_warning: document.getElementById("input-risk").value,
    };
    const resp = await fetch("/api/deals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const res = await resp.json();
    if (!res.ok) {
      showToast(res.error || "录入失败");
      return;
    }
    e.target.reset();
    addBackdrop.classList.remove("open");
    state.activeModule = "A";
    await fetchDashboard();
    showToast("新情报已通过去重校验并入库！");
  });

  // Theme Toggle
  const savedTheme = localStorage.getItem("gdh_theme") || "dark";
  document.documentElement.setAttribute("data-theme", savedTheme);
  document.getElementById("btn-theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", cur);
    localStorage.setItem("gdh_theme", cur);
  });

  // Countdown Ticker
  setInterval(() => {
    state.secondsUntilNext = Math.max(0, state.secondsUntilNext - 1);
    document.getElementById("countdown-readout").textContent = `每2h巡检｜下次: ${formatDuration(state.secondsUntilNext)}`;
    if (state.secondsUntilNext === 0) {
      fetchDashboard();
    }
  }, 1000);

  fetchDashboard();
})();
