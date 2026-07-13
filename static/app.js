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

  const progressPanel = document.querySelector("[data-library-progress]");
  const libraryCards = Array.from(document.querySelectorAll("[data-library-card]"));
  if (!progressPanel || libraryCards.length === 0) {
    return;
  }

  const progressTitle = progressPanel.querySelector("[data-progress-title]");
  const progressDetail = progressPanel.querySelector("[data-progress-detail]");
  const progressBar = progressPanel.querySelector("[data-progress-bar]");

  function setProgress(done, total, title) {
    const percent = total === 0 ? 100 : Math.round((done / total) * 100);
    progressBar.style.width = `${percent}%`;
    progressDetail.textContent = title
      ? `已完成 ${done}/${total}，目前：${title}`
      : `已完成 ${done}/${total}`;
  }

  function renderLibraryResult(card, result) {
    const hasHolding = Boolean(result.has_holding);
    const items = Array.isArray(result.items) ? result.items : [];
    const statusClass = hasHolding ? "ok" : "muted";
    const statusText = hasHolding ? "伸港有館藏" : "伸港無館藏";
    const details = items.length
      ? `<ul>${items
          .map((item) => `<li>${escapeHtml(item.call_number || "-")} / ${escapeHtml(item.status || "-")}</li>`)
          .join("")}</ul>`
      : result.error
        ? `<p>${escapeHtml(result.error)}</p>`
        : "<p>-</p>";

    card.innerHTML = `<strong class="${statusClass}">${statusText}</strong>${details}`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function queryLibraryStatus(card) {
    const title = card.dataset.title || "";
    card.innerHTML = '<strong class="muted">查詢中</strong><p>正在查詢館藏...</p>';

    const response = await fetch("/api/library-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const result = await response.json();
    renderLibraryResult(card, result);
    return title;
  }

  async function runLibraryQueue() {
    let done = 0;
    setProgress(done, libraryCards.length);

    for (const card of libraryCards) {
      const title = card.dataset.title || "";
      setProgress(done, libraryCards.length, title);
      try {
        await queryLibraryStatus(card);
      } catch (error) {
        card.innerHTML = '<strong class="muted">查詢失敗</strong><p>請稍後再試。</p>';
      }
      done += 1;
      setProgress(done, libraryCards.length, title);
    }

    progressTitle.textContent = "伸港圖書館館藏查詢完成";
  }

  runLibraryQueue();
})();
