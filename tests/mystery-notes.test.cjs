const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "assets", "mystery-notes.js"), "utf8");

function control() {
  return {
    value: "",
    textContent: "",
    listeners: {},
    addEventListener(event, handler) {
      this.listeners[event] = handler;
    },
    emit(event) {
      assert.equal(typeof this.listeners[event], "function", "Missing listener for " + event);
      this.listeners[event]();
    },
  };
}

function storage() {
  const rows = new Map();
  const written = [];
  return {
    rows,
    written,
    getItem(key) { return rows.has(key) ? rows.get(key) : null; },
    setItem(key, value) { rows.set(key, value); written.push({ key, value }); },
    removeItem(key) { rows.delete(key); },
  };
}

function mount(store, episode = "victim-not-yet-dead-01") {
  const elements = {
    "[data-mystery-note-input]": control(),
    "[data-mystery-note-save]": control(),
    "[data-mystery-note-clear]": control(),
    "[data-mystery-note-count]": control(),
    "[data-mystery-note-status]": control(),
  };
  const panel = {
    getAttribute(name) {
      if (name === "data-mystery-serial") return "case-files";
      if (name === "data-mystery-episode") return episode;
      return null;
    },
    querySelector(selector) { return elements[selector] || null; },
  };
  const context = {
    document: {
      querySelectorAll(selector) {
        assert.equal(selector, "[data-mystery-notebook]");
        return [panel];
      },
    },
    window: { localStorage: store },
  };
  vm.runInNewContext(SOURCE, context, { timeout: 1000 });
  return {
    input: elements["[data-mystery-note-input]"],
    save: elements["[data-mystery-note-save]"],
    clear: elements["[data-mystery-note-clear]"],
    count: elements["[data-mystery-note-count]"],
    status: elements["[data-mystery-note-status]"],
  };
}

test("opening an episode only reads local storage, never silently saves", () => {
  const store = storage();
  const x = mount(store);
  assert.equal(store.written.length, 0);
  assert.equal(x.input.value, "");
  assert.match(x.count.textContent, /0 \/ 800/);
});

test("save is explicit and restores only this episode next time", () => {
  const store = storage();
  const first = mount(store);
  first.input.value = "シャッターの内側に置けたのは休日受付の職員かもしれない。";
  first.input.emit("input");
  assert.match(first.count.textContent, /\/ 800文字/);
  assert.equal(store.written.length, 0);
  first.save.emit("click");
  assert.equal(store.written.length, 1);
  assert.equal(store.written[0].key, "veripsa:mystery-note:v1:case-files:victim-not-yet-dead-01");
  assert.equal(store.written[0].value, first.input.value);
  assert.match(first.status.textContent, /サイトには送信していません/);
  const reopened = mount(store);
  assert.equal(reopened.input.value, first.input.value);
  assert.match(reopened.status.textContent, /読み込みました/);
  const other = mount(store, "victim-not-yet-dead-02");
  assert.equal(other.input.value, "");
});

test("saved note can be deleted explicitly", () => {
  const store = storage();
  const x = mount(store);
  x.input.value = "数字に注目";
  x.save.emit("click");
  x.clear.emit("click");
  assert.equal(store.rows.size, 0);
  assert.equal(x.input.value, "");
  assert.match(x.status.textContent, /削除しました/);
});

test("oversized input is limited before storage", () => {
  const store = storage();
  const x = mount(store);
  x.input.value = "a".repeat(900);
  x.save.emit("click");
  assert.equal(store.written[0].value.length, 800);
});

test("private browsing failures show a message rather than crashing", () => {
  const broken = {
    getItem() { throw new Error("access denied"); },
    setItem() { throw new Error("access denied"); },
    removeItem() { throw new Error("access denied"); },
  };
  const x = mount(broken);
  assert.match(x.status.textContent, /保存できません/);
  x.save.emit("click");
  assert.match(x.status.textContent, /保存できません/);
  x.clear.emit("click");
  assert.match(x.status.textContent, /アクセスできません/);
});
