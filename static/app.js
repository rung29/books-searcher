(function () {
  const overlay = document.querySelector("[data-loading-overlay]");
  const overlayMessage = document.querySelector("[data-loading-message]");

  document.querySelectorAll("form[data-progress-message]").forEach((form) => {
    form.addEventListener("submit", () => {
      if (!overlay) {
        return;
      }
      overlayMessage.textContent = form.dataset.progressMessage || "處理中...";
      overlay.hidden = false;
    });
  });

  document.querySelectorAll("[data-reset-search]").forEach((form) => {
    form.addEventListener("submit", () => {
      Object.keys(localStorage)
        .filter((key) => key.startsWith("library:"))
        .forEach((key) => localStorage.removeItem(key));
    });
  });

  const table = document.querySelector("[data-cache-key]");
  const rows = Array.from(document.querySelectorAll("[data-library-row]"));
  const progressPanel = document.querySelector("[data-library-progress]");
  if (!table || !progressPanel || rows.length === 0) {
    return;
  }

  const cacheKey = table.dataset.cacheKey;
  const progressTitle = progressPanel.querySelector("[data-progress-title]");
  const progressDetail = progressPanel.querySelector("[data-progress-detail]");
  const progressBar = progressPanel.querySelector("[data-progress-bar]");
  const concurrency = 4;
  const cache = readCache(cacheKey);

  function readCache(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || "{}");
    } catch (error) {
      return {};
    }
  }

  function writeCache() {
    localStorage.setItem(cacheKey, JSON.stringify(cache));
  }

  function setProgress(done, total, title) {
    const percent = total === 0 ? 100 : Math.round((done / total) * 100);
    progressBar.style.width = `${percent}%`;
    progressDetail.textContent = title
      ? `已完成 ${done}/${total}，目前：${title}`
      : `已完成 ${done}/${total}`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function statusClass(status) {
    return status && status.includes("在架") ? "status-yes" : "status-no";
  }

  function renderResult(row, result) {
    const holdingCell = row.querySelector("[data-library-holding]");
    const callCell = row.querySelector("[data-library-call]");
    const statusCell = row.querySelector("[data-library-status]");
    const items = Array.isArray(result.items) ? result.items : [];

    holdingCell.innerHTML = result.has_holding
      ? '<span class="status-pill status-yes">有館藏</span>'
      : '<span class="status-pill status-no">無館藏</span>';

    if (items.length === 0) {
      callCell.textContent = "-";
      statusCell.textContent = result.error || "-";
      return;
    }

    callCell.innerHTML = items
      .map((item) => `<div>${escapeHtml(item.call_number || "-")}</div>`)
      .join("");
    statusCell.innerHTML = items
      .map((item) => {
        const status = item.status || "-";
        return `<div><span class="status-pill ${statusClass(status)}">${escapeHtml(status)}</span></div>`;
      })
      .join("");
  }

  async function queryStatus(row) {
    const index = row.dataset.index;
    const title = row.dataset.title || "";
    const cached = cache[index];
    if (cached && cached.title === title) {
      renderResult(row, cached.result);
      return { title, cached: true };
    }

    row.querySelector("[data-library-holding]").innerHTML = '<span class="status-pill muted-pill">查詢中</span>';
    const response = await fetch("/api/library-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const result = await response.json();
    cache[index] = { title, result };
    writeCache();
    renderResult(row, result);
    return { title, cached: false };
  }

  async function worker(queue, total, state) {
    while (queue.length > 0) {
      const row = queue.shift();
      const title = row.dataset.title || "";
      setProgress(state.done, total, title);
      try {
        await queryStatus(row);
      } catch (error) {
        renderResult(row, { has_holding: false, items: [], error: "查詢失敗" });
      }
      state.done += 1;
      setProgress(state.done, total, title);
    }
  }

  async function run() {
    const queue = rows.slice();
    const state = { done: 0 };
    setProgress(0, rows.length);

    await Promise.all(
      Array.from({ length: Math.min(concurrency, queue.length) }, () => worker(queue, rows.length, state))
    );

    progressTitle.textContent = "伸港圖書館館藏查詢完成";
  }

  run();
})();
