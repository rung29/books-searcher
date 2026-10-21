const test = require("node:test");
const assert = require("node:assert/strict");

const detection = require("./ocr_detection.js");

test("parseGeminiJson accepts fenced JSON and surrounding text", () => {
  assert.deepEqual(
    detection.parseGeminiJson('```json\n{"books": []}\n```'),
    { books: [] }
  );
  assert.deepEqual(
    detection.parseGeminiJson('結果如下： {"books":[{"id":1}]} 完成'),
    { books: [{ id: 1 }] }
  );
});

test("normalizeDetections clamps, sorts, numbers, and rejects invalid boxes", () => {
  const result = detection.normalizeDetections({
    books: [
      { box_2d: [20, 600, 980, 760] },
      { box_2d: [-20, 50, 1020, 220] },
      { box_2d: [1, 2, 3, 4] },
      { box_2d: ["bad", 10, 500, 100] }
    ]
  });

  assert.deepEqual(result, [
    { id: 1, box_2d: [0, 50, 1000, 220] },
    { id: 2, box_2d: [20, 600, 980, 760] }
  ]);
});

test("normalizeRecognitions maps model output back to every detection", () => {
  const detections = [
    { id: 1, box_2d: [0, 10, 900, 200] },
    { id: 2, box_2d: [0, 220, 900, 400] }
  ];
  const result = detection.normalizeRecognitions({
    books: [{ id: 1, title: "[1] 屁屁偵探  ", confidence: 92 }]
  }, detections);

  assert.deepEqual(result, [
    { id: 1, box_2d: [0, 10, 900, 200], title: "屁屁偵探", confidence: 0.92 },
    { id: 2, box_2d: [0, 220, 900, 400], title: "", confidence: 0 }
  ]);
});

test("normalizeRecognitions preserves titles that genuinely start with numbers", () => {
  const result = detection.normalizeRecognitions(
    { books: [{ id: 1, title: "100層樓的家", confidence: 0.8 }] },
    [{ id: 1, box_2d: [0, 0, 1000, 1000] }]
  );

  assert.equal(result[0].title, "100層樓的家");
});
