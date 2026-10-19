const test = require("node:test");
const assert = require("node:assert/strict");

const matcher = require("./ocr_matcher.js");

test("mergeCatalogs removes duplicate titles while preserving resources", () => {
  const merged = matcher.mergeCatalogs(
    [{ title: "神犬奇兵", range: "國小中年級", certification: "可認證", call_numbers: ["J 874"] }],
    [{ title: "神犬奇兵", range: "國小中年級", certification: "可認證", online_resources: ["https://example.test/ebook"] }]
  );

  assert.equal(merged.length, 1);
  assert.deepEqual(merged[0].online_resources, ["https://example.test/ebook"]);
  assert.deepEqual(merged[0].call_numbers, ["J 874"]);
  assert.equal(merged[0].source_files.length, 2);
});

test("matching recognizes full and series title text", () => {
  const books = [
    { title: "屁屁偵探讀本8：被怪盜盯上的新娘", range: "國小低年級", certification: "可認證" },
    { title: "神犬奇兵", range: "國小中年級", certification: "可認證" }
  ];
  const lines = [
    { text: "屁屁偵探讀本8", bbox: { x0: 1, y0: 2, x1: 3, y1: 4 } },
    { text: "神犬奇兵" }
  ];

  const matches = matcher.findMatches(lines, books);
  assert.equal(matches.length, 2);
  assert.equal(matches[0].book.title, "神犬奇兵");
  assert.equal(matches[0].reason, "完整書名");
  assert.equal(matches[1].reason, "系列書名");
});

test("non-certified records are excluded from the first-pass catalog", () => {
  const merged = matcher.mergeCatalogs([
    { title: "不列入", range: "國小低年級", certification: "不可認證" },
    { title: "列入", range: "國小低年級", certification: "可認證" }
  ]);

  assert.deepEqual(merged.map(book => book.title), ["列入"]);
});
