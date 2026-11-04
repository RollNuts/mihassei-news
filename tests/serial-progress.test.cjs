const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../assets/serial-progress.js"), "utf8");

function el(attributes = {}, children = {}) {
  const attrs = new Map(Object.entries(attributes));
  return {
    hidden: Object.prototype.hasOwnProperty.call(attributes, "hidden"),
    disabled: false, textContent: "", handlers: {},
    getAttribute(key) { return attrs.has(key) ? attrs.get(key) : null; },
    setAttribute(key, value) { attrs.set(key, String(value)); },
    querySelector(key) { return children[key] || null; },
    querySelectorAll(key) { return children[key] || []; },
    addEventListener(key, fn) { this.handlers[key] = fn; },
    click() { assert.equal(typeof this.handlers.click, "function"); this.handlers.click(); },
  };
}

function store() {
  const values = new Map(), writes = [];
  return {
    values, writes,
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { writes.push([key, value]); values.set(key, value); },
    removeItem(key) { values.delete(key); },
  };
}

function fixture(localStorage, initial = null) {
  const eps = [{ slug: "first-case", number: 1 }, { slug: "second-case", number: 2 }];
  const epJson = JSON.stringify(eps);
  const summary = el(), resume = el({hidden: ""}), complete = el({hidden: ""});
  const ep1 = el({"data-episode": "first-case"});
  const ep2 = el({"data-episode": "second-case"});
  const series = el({
      "data-series-id": "case-files", "data-kind": "fiction", "data-serial-episodes": epJson,
    }, {
      "[data-serial-progress-summary]": summary,
      "[data-serial-progress-continue]": resume,
      "[data-serial-progress-complete]": complete,
      "[data-serial-episode-state]": [ep1, ep2],
    });
  const button = el(), status = el();
  const current = el({
      "data-series-id": "case-files", "data-episode-id": "first-case",
    }, {
      "[data-serial-progress-toggle]": button,
      "[data-serial-progress-status]": status,
    });
  const windowEvents = {};
  const ctx = {
    window: {
      localStorage,
      addEventListener(event, fn) { windowEvents[event] = fn; },
    },
    document: {
      querySelectorAll(name) {
        if (name === "[data-serial-progress-series]") return [series];
        if (name === "[data-serial-progress-current]") return [current];
        return [];
      },
    },
  };
  vm.runInNewContext(source, ctx, {timeout: 1500});
  return {series,summary,resume,complete,ep1,ep2,button,status,windowEvents,eps};
}

test("opening a page is read-only and keeps normal serial links as fallback", () => {
  const s = store();
  const a = fixture(s);
  assert.equal(s.writes.length, 0);
  assert.match(a.summary.textContent, /0 \/ 2 話/);
  assert.equal(a.resume.hidden, true);
  assert.equal(a.complete.hidden, true);
  assert.equal(a.button.getAttribute("aria-pressed"), "false");
  assert.match(a.status.textContent, /このブラウザに保存します/);
});

test("explicit completion unlocks the actual next unread chapter", () => {
  const s = store();
  const a = fixture(s);
  a.button.click();
  assert.equal(s.writes.length, 1);
  assert.equal(s.writes[0][0], "veripsa:serial-read:v1:case-files");
  assert.deepEqual(JSON.parse(s.writes[0][1]), ["first-case"]);
  assert.equal(a.button.getAttribute("aria-pressed"), "true");
  assert.equal(a.resume.hidden, false);
  assert.equal(a.resume.getAttribute("href"), "/serials/case-files/second-case/");
  assert.match(a.resume.textContent, /第2話/);
  assert.match(a.ep1.textContent, /読了済み/);
  assert.equal(a.ep2.textContent, "");
  assert.equal(a.complete.hidden, true);
});

test("read completion can be undone by the same explicit button", () => {
  const s = store(), a = fixture(s);
  a.button.click();
  a.button.click();
  assert.deepEqual(JSON.parse(s.values.get("veripsa:serial-read:v1:case-files")), []);
  assert.equal(a.button.getAttribute("aria-pressed"), "false");
  assert.equal(a.resume.hidden, true);
  assert.equal(a.ep1.textContent, "");
  assert.match(a.status.textContent, /取り消しました/);
});

test("fully completed published chapters show a completion note, not a fake future link", () => {
  const s = store();
  s.values.set("veripsa:serial-read:v1:case-files", JSON.stringify(["first-case", "second-case"]));
  const a = fixture(s);
  assert.match(a.summary.textContent, /2 \/ 2 話/);
  assert.equal(a.resume.hidden, true);
  assert.equal(a.complete.hidden, false);
  assert.equal(s.writes.length, 0);
});

test("malformed stored state is ignored without blocking a new explicit save", () => {
  const s = store();
  s.values.set("veripsa:serial-read:v1:case-files", "{broken json");
  const a = fixture(s);
  assert.equal(a.button.disabled, false);
  a.button.click();
  assert.deepEqual(JSON.parse(s.values.get("veripsa:serial-read:v1:case-files")), ["first-case"]);
});

test("no storage permissions disable completion instead of silently claiming it worked", () => {
  const blocked = {
    getItem() {throw new Error("restricted");},
    setItem() {throw new Error("restricted");},
  };
  const a = fixture(blocked);
  assert.equal(a.button.disabled, true);
  assert.match(a.status.textContent, /保存できません/);
  assert.equal(a.resume.hidden, true);
});

test("progress is not sent off device to any analytics API", () => {
  for(const token of ["fetch(", "XMLHttpRequest", "sendBeacon(", "WebSocket(", "http://", "https://"]) {
    assert.equal(source.includes(token), false, token);
  }
});
