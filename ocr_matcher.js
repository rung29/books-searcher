(function (global) {
  "use strict";

  const titleFilters = global.BookCatalogFilters
    || (typeof module !== "undefined" && module.exports
      ? require("./catalog_filters.js")
      : null);

  function normalizeOcrText(value) {
    return String(value || "")
      .normalize("NFKC")
      .replace(/臺/g, "台")
      .toLocaleLowerCase("zh-TW")
      .replace(/[^\p{L}\p{N}]/gu, "");
  }

  function certifiedOnly(book) {
    const certification = String(book && book.certification || "").trim();
    return certification === "可認證";
  }

  function mergeCatalogs(...catalogs) {
    const merged = new Map();

    catalogs.flat().forEach((book) => {
      if (!book || !book.title || !certifiedOnly(book)) return;
      const titleKey = normalizeOcrText(book.title);
      const rangeKey = normalizeOcrText(book.range);
      if (!titleKey) return;
      const key = `${titleKey}|${rangeKey}`;
      const existing = merged.get(key);
      if (!existing) {
        merged.set(key, {
          ...book,
          online_resources: [...(book.online_resources || [])],
          source_files: [book.online_resources ? "ebooks_data.json" : "books_with_library_data.json"]
        });
        return;
      }

      existing.online_resources = [
        ...new Set([
          ...(existing.online_resources || []),
          ...(book.online_resources || [])
        ])
      ];
      existing.source_files = [
        ...new Set([
          ...existing.source_files,
          book.online_resources ? "ebooks_data.json" : "books_with_library_data.json"
        ])
      ];
      existing.call_numbers = [
        ...new Set([
          ...(existing.call_numbers || []),
          ...(book.call_numbers || [])
        ])
      ];
      existing.library_statuses = [
        ...new Set([
          ...(existing.library_statuses || []),
          ...(book.library_statuses || [])
        ])
      ];
    });

    return [...merged.values()];
  }

  function levenshtein(left, right) {
    const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
    for (let row = 1; row <= left.length; row += 1) {
      let diagonal = previous[0];
      previous[0] = row;
      for (let column = 1; column <= right.length; column += 1) {
        const above = previous[column];
        const cost = left[row - 1] === right[column - 1] ? 0 : 1;
        previous[column] = Math.min(
          previous[column] + 1,
          previous[column - 1] + 1,
          diagonal + cost
        );
        diagonal = above;
      }
    }
    return previous[right.length];
  }

  function similarity(left, right) {
    if (!left || !right) return 0;
    const distance = levenshtein(left, right);
    return 1 - distance / Math.max(left.length, right.length);
  }

  function scoreLine(lineText, book) {
    const lineKey = normalizeOcrText(lineText);
    const titleKey = normalizeOcrText(book.title);
    const seriesKey = titleFilters ? normalizeOcrText(titleFilters.seriesKey(book.title)) : "";
    if (lineKey.length < 3 || !titleKey) return null;

    if (lineKey.includes(titleKey)) {
      return { score: 1, reason: "完整書名" };
    }
    if (seriesKey && seriesKey.length >= 3 && lineKey.includes(seriesKey)) {
      return { score: 0.93, reason: "系列書名" };
    }
    if (lineKey.length >= 3 && titleKey.includes(lineKey)) {
      return { score: 0.82, reason: "部分書名" };
    }

    const titleSimilarity = similarity(lineKey, titleKey);
    const seriesSimilarity = seriesKey ? similarity(lineKey, seriesKey) : 0;
    const bestSimilarity = Math.max(titleSimilarity, seriesSimilarity);
    if (bestSimilarity >= 0.76 && lineKey.length >= 4) {
      return { score: 0.7 + bestSimilarity * 0.25, reason: "相似書名" };
    }
    return null;
  }

  function extractLines(ocrData) {
    const lines = Array.isArray(ocrData && ocrData.lines)
      ? ocrData.lines
      : String(ocrData && ocrData.text || "")
        .split(/\r?\n/)
        .map(text => ({ text }));

    return lines
      .map((line, index) => ({
        index,
        text: String(line.text || "").trim(),
        bbox: line.bbox || null
      }))
      .filter(line => normalizeOcrText(line.text).length >= 2);
  }

  function findMatches(ocrLines, books, options = {}) {
    const minimumScore = Number(options.minimumScore) || 0.76;
    const matches = new Map();

    (ocrLines || []).forEach((line) => {
      let best = null;
      (books || []).forEach((book) => {
        const scored = scoreLine(line.text, book);
        if (!scored || scored.score < minimumScore) return;
        if (!best || scored.score > best.score) {
          best = { book, ...scored };
        }
      });

      if (!best) return;
      const key = `${normalizeOcrText(best.book.title)}|${normalizeOcrText(best.book.range)}`;
      const existing = matches.get(key);
      if (!existing || best.score > existing.score) {
        matches.set(key, {
          ...best,
          lines: existing ? [...existing.lines, line] : [line]
        });
      } else {
        existing.lines.push(line);
      }
    });

    return [...matches.values()]
      .sort((left, right) => right.score - left.score || left.book.title.localeCompare(right.book.title, "zh-Hant"));
  }

  const api = {
    normalizeOcrText,
    mergeCatalogs,
    scoreLine,
    extractLines,
    findMatches
  };
  global.BookOcrMatcher = api;

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
