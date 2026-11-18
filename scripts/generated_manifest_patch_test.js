"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const patch = require("./generated_manifest_patch.js");
const slug = "new-photo", imageSource = '{ "seed": 18446744073709551615, "note": "brace } and \\\" quote" }';
const input = manifestSource => ({ manifestSource, slug, imageSource });

test("preserves existing source, nested 64-bit seeds, whitespace and the raw new record", () => {
  const old = '{\r\n "version":1, "images": {"old-photo":{"seed":937335361030604073,'
    + '"previous_generation":{"seed":8233372666452194521},"note":"} \\\" images"}\r\n },'
    + ' "rejected":{"old-trial":{"trial_seed":4016510582796096107}}, "extra":[{},[1]]\r\n}\n';
  const result = patch.appendImage(input(old));
  const inserted = ',\n    "new-photo": ' + imageSource + '\n  ';
  assert.equal(result.replace(inserted, ""), old, "every pre-existing character survives unchanged");
  assert.ok(result.includes(imageSource), "new record's numeric literals also remain verbatim");
  const decoded = JSON.parse(result);
  assert.deepEqual(Object.keys(decoded.images), ["old-photo", slug]);
  assert.deepEqual(decoded.rejected, JSON.parse(old).rejected);
});

test("locates the top-level images member despite strings, nested decoys and escaped keys", () => {
  const old = '{"rejected":{"images":{"x":1}},"version":1,"note":"images } [ {",'
    + '"ima\\u0067es": { },"later":{"images":[]}}';
  const result = patch.appendImage(input(old));
  assert.deepEqual(Object.keys(JSON.parse(result).images), [slug]);
  assert.ok(result.includes('"ima\\u0067es": { '));
  assert.ok(result.endsWith('},"later":{"images":[]}}'));
  assert.equal(result.replace('\n    "new-photo": ' + imageSource + '\n  ', ""), old);
});

test("actual current manifest keeps all existing literal bytes when appending a fixture", () => {
  const source = fs.readFileSync(path.join(__dirname, "../assets/generated/manifest.json"), "utf8");
  const result = patch.appendImage(input(source));
  assert.equal(result.replace(',\n    "new-photo": ' + imageSource + '\n  ', ""), source);
  const before = JSON.parse(source), after = JSON.parse(result);
  delete after.images[slug];
  assert.deepEqual(after, before);
});

test("refuses replacement and decoded duplicate member names", () => {
  assert.throws(() => patch.appendImage(input('{"version":1,"images":{"new-photo":{}},"rejected":{}}')), /already exists/);
  for (const source of [
    '{"version":1,"images":{},"ima\\u0067es":{},"rejected":{}}',
    '{"version":1,"images":{"x":{},"\\u0078":{}},"rejected":{}}',
    '{"version":1,"images":{},"rejected":{"x":{"seed":1,"seed":2}}}'
  ]) assert.throws(() => patch.appendImage(input(source)), /Duplicate/);
  assert.throws(() => patch.appendImage({ ...input('{"version":1,"images":{},"rejected":{}}'),
    imageSource: '{"sha256":"first","sha256":"second"}' }), /Duplicate/);
});

test("fails closed on malformed JSON, schema, slug and non-object records", () => {
  for (const source of [null, "", '{"version":1,}', '[]', '{}',
    '{"version":2,"images":{},"rejected":{}}',
    '{"version":1,"images":[],"rejected":{}}',
    '{"version":1,"images":{},"rejected":[]}'])
    assert.throws(() => patch.appendImage(input(source)));
  const valid = '{"version":1,"images":{},"rejected":{}}';
  for (const record of [null, "", "[]", '"text"', "null", "1", "{bad}"])
    assert.throws(() => patch.appendImage({ ...input(valid), imageSource: record }));
  for (const badSlug of ["", "../photo", "a_b", "A", "x\n", null])
    assert.throws(() => patch.appendImage({ ...input(valid), slug: badSlug }), /slug/);
});

test("loads in plain V8 without CommonJS or Node globals and never mutates its inputs", () => {
  const source = fs.readFileSync(path.join(__dirname, "generated_manifest_patch.js"), "utf8");
  const pure = new Function(source + "\nreturn GeneratedManifestPatch;")();
  const args = Object.freeze(input('{"version":1,"images":{},"rejected":{}}'));
  assert.equal(pure.appendImage(args), patch.appendImage(args));
  assert.deepEqual(Object.keys(pure), ["appendImage"]);
});
