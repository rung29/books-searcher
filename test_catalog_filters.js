const test = require("node:test");
const assert = require("node:assert/strict");

const filters = require("./catalog_filters.js");

test("grade options are counted and ordered", () => {
  const books = [
    { range: "國小中年級" },
    { range: "自訂年級" },
    { range: "國小低年級" },
    { range: "國小中年級" },
    { range: "" }
  ];

  assert.deepEqual(filters.gradeOptions(books), [
    { value: "國小低年級", label: "國小低年級", count: 1 },
    { value: "國小中年級", label: "國小中年級", count: 2 },
    { value: "自訂年級", label: "自訂年級", count: 1 }
  ]);
});

test("grade filtering uses exact normalized labels", () => {
  const low = { title: "低年級", range: " 國小低年級 " };
  const middle = { title: "中年級", range: "國小中年級" };
  const books = [low, middle];

  assert.deepEqual(filters.filterByGrade(books, "國小低年級"), [low]);
  assert.deepEqual(filters.filterByGrade(books, ""), books);
});

test("series keys ignore subtitles, volume numbers, and edition notes", () => {
  const expected = filters.seriesKey("屁屁偵探讀本");

  assert.equal(filters.seriesKey("屁屁偵探讀本8：被怪盜盯上的新娘"), expected);
  assert.equal(filters.seriesKey("屁屁偵探讀本 第9冊"), expected);
  assert.equal(filters.seriesKey("屁屁偵探讀本 10（新版）"), expected);
});

test("similar groups are recomputed from the selected grade", () => {
  const books = [
    { title: "神奇系列 1", range: "國小低年級" },
    { title: "神奇系列 2", range: "國小中年級" },
    { title: "低年級故事 1", range: "國小低年級" },
    { title: "低年級故事 2", range: "國小低年級" }
  ];

  assert.equal(filters.buildSimilarTitleGroups(books).length, 2);
  const lowBooks = filters.filterByGrade(books, "國小低年級");
  const lowGroups = filters.buildSimilarTitleGroups(lowBooks);

  assert.equal(lowGroups.length, 1);
  assert.equal(lowGroups[0].label, "低年級故事");
  assert.equal(lowGroups[0].count, 2);
  assert.deepEqual(lowGroups[0].books, lowBooks.slice(1));
});
