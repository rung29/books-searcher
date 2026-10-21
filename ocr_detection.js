(function (global) {
  "use strict";

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function parseGeminiJson(value) {
    if (value && typeof value === "object") return value;

    const text = String(value || "")
      .trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/\s*```$/, "")
      .trim();
    if (!text) throw new Error("Gemini 沒有回傳 JSON 資料");

    try {
      return JSON.parse(text);
    } catch (error) {
      const objectStart = text.indexOf("{");
      const objectEnd = text.lastIndexOf("}");
      const arrayStart = text.indexOf("[");
      const arrayEnd = text.lastIndexOf("]");
      const candidates = [];
      if (objectStart >= 0 && objectEnd > objectStart) {
        candidates.push(text.slice(objectStart, objectEnd + 1));
      }
      if (arrayStart >= 0 && arrayEnd > arrayStart) {
        candidates.push(text.slice(arrayStart, arrayEnd + 1));
      }
      for (const candidate of candidates) {
        try {
          return JSON.parse(candidate);
        } catch (_) {
          // Try the next JSON-looking section.
        }
      }
      throw new Error("Gemini 回傳的物件偵測資料不是有效 JSON");
    }
  }

  function normalizeBox(value) {
    if (!Array.isArray(value) || value.length < 4) return null;
    const coordinates = value.slice(0, 4).map(Number);
    if (coordinates.some(coordinate => !Number.isFinite(coordinate))) return null;

    const ymin = clamp(Math.min(coordinates[0], coordinates[2]), 0, 1000);
    const xmin = clamp(Math.min(coordinates[1], coordinates[3]), 0, 1000);
    const ymax = clamp(Math.max(coordinates[0], coordinates[2]), 0, 1000);
    const xmax = clamp(Math.max(coordinates[1], coordinates[3]), 0, 1000);
    if (ymax - ymin < 5 || xmax - xmin < 5) return null;
    return [ymin, xmin, ymax, xmax].map(Math.round);
  }

  function normalizeDetections(payload, maximum = 12) {
    const source = Array.isArray(payload)
      ? payload
      : payload && (payload.books || payload.boxes || payload.items);
    if (!Array.isArray(source)) return [];

    return source
      .map(item => ({ box_2d: normalizeBox(item && (item.box_2d || item.bbox || item.box)) }))
      .filter(item => item.box_2d)
      .sort((left, right) => left.box_2d[1] - right.box_2d[1] || left.box_2d[0] - right.box_2d[0])
      .slice(0, Math.max(1, Number(maximum) || 12))
      .map((item, index) => ({ id: index + 1, box_2d: item.box_2d }));
  }

  function normalizeConfidence(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    return clamp(numeric > 1 && numeric <= 100 ? numeric / 100 : numeric, 0, 1);
  }

  function cleanTitle(value) {
    return String(value || "")
      .replace(/^\s*(?:[-*•]\s*|\[\d+\]\s*|\d+[.)、:]\s+)/, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 160);
  }

  function normalizeRecognitions(payload, detections) {
    const source = Array.isArray(payload)
      ? payload
      : payload && (payload.books || payload.items || payload.results);
    const byId = new Map();
    if (Array.isArray(source)) {
      source.forEach((item, index) => {
        const id = Number(item && item.id) || index + 1;
        if (!Number.isInteger(id) || id < 1 || byId.has(id)) return;
        byId.set(id, {
          id,
          title: cleanTitle(item && (item.title || item.text || item.label)),
          confidence: normalizeConfidence(item && item.confidence)
        });
      });
    }

    return (detections || []).map(detection => {
      const recognized = byId.get(detection.id);
      return {
        id: detection.id,
        box_2d: detection.box_2d,
        title: recognized ? recognized.title : "",
        confidence: recognized ? recognized.confidence : 0
      };
    });
  }

  const api = {
    parseGeminiJson,
    normalizeBox,
    normalizeDetections,
    normalizeRecognitions
  };
  global.BookOcrDetection = api;

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
