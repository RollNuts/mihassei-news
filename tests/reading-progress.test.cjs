"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the exact production progress implementation in isolation.
// The full engagement file also handles APIs, likes and the fictional clock.
const src = fs.readFileSync("assets/engagement.js", "utf8");
const start = src.indexOf('  const readingBody=document.querySelector(');
const end = src.indexOf('  // Reveal one real archived fictional story;', start);
assert.ok(start > 0 && end > start, "article progress source boundaries");
const code = src.slice(start, end);

function simulate({ hasBody = true, reduced = false } = {}) {
  let y = 0;
  const events = {};
  const nodes = [];
  const reading = hasBody ? {
    getBoundingClientRect() { return { top: 400 - y, height: 600 }; }
  } : null;
  const doc = {
    querySelector(sel) {
      assert.equal(sel, ".article-body, .serial-article-body");
      return reading;
    },
    createElement(tag) {
      assert.equal(tag, "div");
      const node = { style: {}, setAttribute() {} };
      nodes.push(node);
      return node;
    },
    body: { appendChild() {} }
  };
  const win = {
    innerHeight: 700, get scrollY() { return y; },
    requestAnimationFrame(callback) { callback(); },
    addEventListener(name, callback) { events[name] = callback; }
  };
  vm.runInNewContext("(function(){const reduceMotion="+JSON.stringify(reduced)+";\n"+code+"})()", {
    document: doc, window: win
  });
  return {
    get bar() { return nodes[0]; },
    scrollTo(position) {
      y = position;
      if (events.scroll) events.scroll();
    },
    onResize() { if (events.resize) events.resize(); }
  };
}

test("news and serial content, not the footer, determines reading progress", () => {
  const page = simulate();
  assert.equal(page.bar.className, "reading-progress");
  assert.equal(page.bar.style.transform, "scaleX(0.0000)");
  page.scrollTo(350);
  assert.equal(page.bar.style.transform, "scaleX(0.5000)");
  page.scrollTo(650);
  assert.equal(page.bar.style.transform, "scaleX(1.0000)");
  page.scrollTo(800);
  assert.equal(page.bar.style.transform, "scaleX(1.0000)");
  page.scrollTo(350);
  page.onResize();
  assert.equal(page.bar.style.transform, "scaleX(0.5000)");
});

test("no progress UI on index pages or reduced-motion devices", () => {
  assert.equal(simulate({ hasBody: false }).bar, undefined);
  assert.equal(simulate({ reduced: true }).bar, undefined);
  assert.equal(code.includes("fetch("), false);
  assert.equal(code.includes("scrollHeight"), false);
});
