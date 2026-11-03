"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync("assets/returning-shelf.js", "utf8");

function mount(initial, blocked = false) {
  let value = initial;
  const link = { href: "/saved/", textContent: "" };
  const count = { textContent: "" };
  const shelf = {
    hidden: true,
    querySelector(selector) {
      if (selector === "[data-returning-shelf-article]") return link;
      if (selector === "[data-returning-shelf-count]") return count;
      return null;
    }
  };
  let listener = null;
  const window = {
    localStorage: {
      getItem(key) {
        assert.equal(key, "mihassei-saved-stories-v1");
        if (blocked) throw new Error("Browser storage blocked");
        return value;
      }
    },
    addEventListener(type, callback) {
      if (type === "storage") listener = callback;
    }
  };
  const document = {
    querySelector(selector) {
      return selector === "[data-returning-shelf]" ? shelf : null;
    }
  };
  vm.runInNewContext(script, { window, document });
  return {
    shelf, link, count,
    update(next, key = "mihassei-saved-stories-v1") {
      value = next;
      listener?.({ key });
    }
  };
}

test("the homepage stays untouched for new readers or disabled storage", () => {
  assert.equal(mount(null).shelf.hidden, true);
  assert.equal(mount("not-json").shelf.hidden, true);
  assert.equal(mount(JSON.stringify([])).shelf.hidden, true);
  assert.equal(mount(JSON.stringify([{}])).shelf.hidden, true);
  assert.equal(mount(JSON.stringify([{}]), true).shelf.hidden, true);
});

test("only user-selected bookmarks create a direct resume link", () => {
  const page = mount(JSON.stringify([
    { slug: "a-story", title: "昨日の記事", category: "社会" },
    { slug: "another-story", title: "次の記事", category: "暮らし" }
  ]));
  assert.equal(page.shelf.hidden, false);
  assert.equal(page.link.href, "/articles/a-story/");
  assert.equal(page.link.textContent, "昨日の記事");
  assert.equal(page.count.textContent, "ほか1件保存");
});

test("untrusted bookmark data is never inserted as markup or links", () => {
  const payload = [
    { slug: "../../external", title: "Bad path" },
    { slug: "safe-article", title: "<img src=x onerror=alert(1)>" },
    { slug: "safe-article", title: "Duplicate" },
    { slug: "other-story", title: "Useful story" }
  ];
  const page = mount(JSON.stringify(payload));
  assert.equal(page.link.href, "/articles/safe-article/");
  assert.equal(page.link.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(page.count.textContent, "ほか1件保存");
  assert.equal(script.includes("innerHTML"), false);
  for (const forbidden of ["fetch(", "XMLHttpRequest", "sendBeacon("]) {
    assert.equal(script.includes(forbidden), false);
  }
});

test("cross-tab save or clear redraws the optional shelf", () => {
  const page = mount(null);
  page.update(JSON.stringify([{slug:"now-saved", title:"今保存した記事"}]));
  assert.equal(page.shelf.hidden, false);
  assert.equal(page.link.href, "/articles/now-saved/");
  page.update("[]", "unrelated");
  assert.equal(page.shelf.hidden, false);
  page.update("[]");
  assert.equal(page.shelf.hidden, true);
});
