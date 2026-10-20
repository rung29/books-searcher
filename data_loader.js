(function (global) {
  "use strict";

  const config = global.BOOK_SEARCHER_DATA_CONFIG || {};
  const timeoutMs = Number(config.timeoutMs) || 6000;
  const noticeDurationMs = Number(config.noticeDurationMs) || 3000;
  let noticeTimer = null;

  function configuredSupabaseBaseUrl() {
    const value = String(config.supabasePublicBaseUrl || "").trim();
    if (!value || value.includes("YOUR_PROJECT_REF")) {
      return "";
    }
    return value.replace(/\/+$/, "");
  }

  function cacheBusted(url) {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}v=${Date.now()}`;
  }

  async function fetchJson(url) {
    const controller = new AbortController();
    const timer = global.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(cacheBusted(url), {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      if (!Array.isArray(data)) {
        throw new Error("JSON 根節點不是陣列");
      }
      return data;
    } finally {
      global.clearTimeout(timer);
    }
  }

  function showSourceNotice(message, isFallback) {
    let notice = document.getElementById("data-source-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "data-source-notice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      notice.style.cssText = [
        "position:fixed",
        "top:16px",
        "left:50%",
        "z-index:9999",
        "max-width:calc(100vw - 32px)",
        "padding:10px 16px",
        "border-radius:999px",
        "font:700 14px/1.4 'Noto Sans TC',sans-serif",
        "box-shadow:0 8px 28px rgba(20,40,60,.2)",
        "transform:translate(-50%,-12px)",
        "opacity:0",
        "transition:opacity .2s ease,transform .2s ease",
        "pointer-events:none",
      ].join(";");
      document.body.appendChild(notice);
    }

    notice.textContent = message;
    notice.style.background = isFallback ? "#fff1d6" : "#dff7ed";
    notice.style.color = isFallback ? "#8a4b08" : "#116149";
    notice.style.border = isFallback
      ? "1px solid rgba(214,126,24,.35)"
      : "1px solid rgba(24,145,105,.35)";

    global.clearTimeout(noticeTimer);
    global.requestAnimationFrame(() => {
      notice.style.opacity = "1";
      notice.style.transform = "translate(-50%,0)";
    });
    noticeTimer = global.setTimeout(() => {
      notice.style.opacity = "0";
      notice.style.transform = "translate(-50%,-12px)";
    }, noticeDurationMs);
  }

  async function loadSupabase(fileName) {
    const supabaseBaseUrl = configuredSupabaseBaseUrl();
    if (!supabaseBaseUrl) {
      throw new Error("Supabase 公開網址尚未設定");
    }
    const data = await fetchJson(`${supabaseBaseUrl}/${fileName}`);
    showSourceNotice(`資料來源：Supabase（${fileName}）`, false);
    return { data, source: "supabase", fileName };
  }

  async function loadLocal(fileName) {
    const data = await fetchJson(fileName);
    showSourceNotice(`資料來源：GitHub Pages 備援（${fileName}）`, true);
    return { data, source: "github", fileName };
  }

  async function load(fileName) {
    const supabaseBaseUrl = configuredSupabaseBaseUrl();
    try {
      return await loadSupabase(fileName);
    } catch (supabaseError) {
      if (supabaseBaseUrl) {
        console.warn(`Supabase ${fileName} 讀取失敗，改用 GitHub Pages 備援。`, supabaseError);
      }
    }

    try {
      return await loadLocal(fileName);
    } catch (localError) {
      throw new Error(`Supabase 與 GitHub Pages 都無法讀取 ${fileName}`, {
        cause: localError,
      });
    }
  }

  global.BookDataLoader = { load, loadLocal, loadSupabase };
})(window);
