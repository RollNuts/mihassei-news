"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "bookmarks.js"), "utf8");
const KEY = "mihassei-saved-stories-v1";

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.disabled = true;
    this.hidden = false;
    this.textContent = "";
  }
  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }
  click() {
    if (!this.disabled) this.listeners.click?.();
  }
  setAttribute(key, value) {
    this.attrs[key] = String(value);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  append(...children) {
    this.children.push(...children);
  }
}

function createPage({initial = null, blocked = false, twoButtons = true, confirmResult = true} = {}) {
  const store = new Map();
  if (initial !== null) store.set(KEY, typeof initial === "string" ? initial : JSON.stringify(initial));
  const writes = [];
  const storage = {
    getItem(key) {
      if (blocked) throw new Error("Storage unavailable");
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (blocked) throw new Error("Storage unavailable");
      writes.push([key, value]);
      store.set(key, value);
    }
  };
  const first = new Element({
    bookmarkSlug: "robot-job-hopping",
    bookmarkTitle: "工場ロボット17台が一斉退職",
    bookmarkCategory: "経済"
  });
  const second = new Element({...first.dataset});
  const buttons = twoButtons ? [first, second] : [first];
  const status = new Element();
  const list = new Element();
  const empty = new Element();
  const clear = new Element();
  const total = new Element();
  const events = {};
  const nodes = {
    "[data-saved-list]": list,
    "[data-saved-empty]": empty,
    "[data-clear-bookmarks]": clear
  };
  const groups = {
    "[data-bookmark-slug]": buttons,
    "[data-bookmark-count]": [total],
    "[data-bookmark-status]": [status]
  };
  const document = {
    querySelector: selector => nodes[selector] || null,
    querySelectorAll: selector => groups[selector] || [],
    createElement: () => new Element()
  };
  const window = {
    addEventListener(type, handler) { events[type] = handler; },
    confirm: () => confirmResult
  };
  const ctx = {document, window, localStorage: storage, Date, Set, JSON, Number};
  vm.runInNewContext(source, ctx, {timeout: 1500});
  return {store, writes, buttons, list, status, empty, clear, total, events};
}

test("bookmark stays opt-in and saves one published story across controls", () => {
  const page = createPage();
  assert.equal(page.writes.length, 0, "Do not save reading history automatically");
  assert.equal(page.buttons[0].disabled, false);
  assert.equal(page.buttons[1].disabled, false);
  page.buttons[0].click();
  const items = JSON.parse(page.store.get(KEY));
  assert.equal(items.length, 1);
  assert.equal(items[0].slug, "robot-job-hopping");
  assert.equal(items[0].title, "工場ロボット17台が一斉退職");
  assert.equal(items[0].category, "経済");
  assert.equal(page.list.children.length, 1);
  assert.equal(page.buttons[0].attrs["aria-pressed"], "true");
  assert.equal(page.buttons[1].attrs["aria-pressed"], "true");
  assert.equal(page.empty.hidden, true);
  assert.equal(page.total.hidden, false);
  assert.equal(page.clear.hidden, false);
  page.buttons[1].click();
  assert.deepEqual(JSON.parse(page.store.get(KEY)), []);
  assert.equal(page.buttons[0].attrs["aria-pressed"], "false");
  assert.equal(page.list.children.length, 0);
  assert.equal(page.empty.hidden, false);
  assert.equal(page.total.hidden, true);
  assert.equal(page.clear.hidden, true);
});

test("malicious and malformed local storage cannot inject unsafe links or HTML", () => {
  const initial = [
    {slug:"javascript:alert(1)", title:"bad", category:"技術", savedAt:1},
    {slug:"safe-article", title:"<img src=x onerror=alert(1)>", category:"不明", savedAt:1},
    {slug:"safe-article", title:"duplicate", category:"技術", savedAt:2},
    {slug:"../unsafe", title:"traversal", category:"技術", savedAt:3},
    null, "garbage"
  ];
  const page = createPage({initial, twoButtons: false});
  assert.equal(page.list.children.length, 1);
  const li = page.list.children[0];
  const content = li.children[0];
  const articleLink = content.children[1];
  assert.equal(articleLink.href, "/articles/safe-article/");
  assert.equal(articleLink.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(content.children[0].textContent, "創作ニュース ／ 創作ニュース");
  assert.equal(page.writes.length, 0, "Even dirty storage never gets sent or rewritten without action");
  assert.equal(source.includes("innerHTML"), false);
  assert.equal(source.includes("fetch("), false);
  assert.equal(source.includes("XMLHttpRequest"), false);
});

test("corrupt storage is recoverable on explicit save", () => {
  const page = createPage({initial:"{malformed"});
  assert.equal(page.list.children.length, 0);
  assert.equal(page.writes.length, 0);
  page.buttons[0].click();
  assert.equal(JSON.parse(page.store.get(KEY)).length, 1);
});

test("storage blocked disables save controls and doesn't claim it saved", () => {
  const page = createPage({blocked:true});
  assert.equal(page.buttons[0].disabled, true);
  page.buttons[0].click();
  assert.equal(page.writes.length, 0);
  assert.match(page.status.textContent, /利用できません/);
});

test("clear all requires an explicit confirmation", () => {
  const initial = [{slug:"saved",title:"保存した記事",category:"社会",savedAt:20}];
  const rejected = createPage({initial,confirmResult:false});
  rejected.clear.click();
  assert.equal(JSON.parse(rejected.store.get(KEY)).length, 1);
  const accepted = createPage({initial,confirmResult:true});
  accepted.clear.click();
  assert.deepEqual(JSON.parse(accepted.store.get(KEY)), []);
  assert.equal(accepted.clear.hidden, true);
});

test("cross-tab storage event refreshes the saved list without network calls", () => {
  const page = createPage({twoButtons:false});
  page.store.set(KEY, JSON.stringify([{
    slug:"robot-job-hopping",title:"ロボット記事",category:"経済",savedAt:50
  }]));
  page.events.storage({key:KEY});
  assert.equal(page.list.children.length, 1);
  assert.equal(page.buttons[0].attrs["aria-pressed"], "true");
});

test("maximum 100 saved entries prevents unbounded local storage growth", () => {
  const items = Array.from({length:100}, (_,i)=>({
    slug:"story-"+i, title:"記事"+i, category:"社会", savedAt:i
  }));
  const page = createPage({initial:items});
  page.buttons[0].click();
  assert.equal(page.writes.length,0);
  assert.match(page.status.textContent,/最大100件/);
});
