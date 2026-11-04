'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(dataset = {}) {
  const handlers = new Map();
  const attrs = new Map();
  return {
    dataset, hidden: false, value: '', textContent: '', open: false,
    classList: { toggle: (name, on) => attrs.set('class:' + name, on) },
    setAttribute: (name, value) => attrs.set(name, String(value)),
    getAttribute: name => attrs.get(name),
    addEventListener: (name, fn) => handlers.set(name, fn),
    emit: name => { assert.ok(handlers.has(name)); handlers.get(name)(); },
    focus() {},
  };
}

const input = element();
const decade = element(); decade.value = 'すべて';
const count = element(), empty = element(), reset = element();
const tagPanel = element();
const categories = ['すべて', '技術', '経済', '社会'].map(value => element({categoryFilter:value}));
const tags = ['すべて', 'AI', '働き方', '動物'].map(value => element({tagFilter:value}));
const cards = [
  element({category:'技術', decade:'2030', tags:'AI|音楽', search:'音楽 AI 友達'}),
  element({category:'経済', decade:'2030', tags:'AI|働き方', search:'AI 部長 働き方'}),
  element({category:'社会', decade:'2030', tags:'イベント|動物', search:'犬 動物 イベント'}),
];
for (const card of cards) card.querySelector = () => null;

const nodes = new Map([
  ['[data-search-input]', input],
  ['[data-decade-filter]', decade],
  ['[data-result-count]', count],
  ['[data-no-results]', empty],
  ['[data-reset-filters]', reset],
  ['[data-tag-panel]', tagPanel],
]);
const document = {
  querySelector: selector => nodes.get(selector) || null,
  querySelectorAll: selector => selector === '[data-archive-card]' ? cards :
    selector === '[data-category-filter]' ? categories :
    selector === '[data-tag-filter]' ? tags : [],
};
const handlers = new Map();
const location = new URL('https://veripsa.com/archive/?tag=AI&src=share#category-feeds');
const window = {
  location,
  history: {
    state: null,
    replaceState(state, _title, url) {
      this.state = state;
      location.href = new URL(url, location.href).href;
    },
  },
  addEventListener(name, fn) { handlers.set(name, fn); },
};
const source = fs.readFileSync(path.join(__dirname, '..', 'assets', 'site.js'), 'utf8');
vm.runInNewContext(source, {document, window, URL, URLSearchParams, Set, String, Number}, {timeout:1000});

const visible = () => cards.filter(card => !card.hidden);
assert.equal(visible().length, 2, 'tag deep link filters the full archive');
assert.equal(tagPanel.open, true, 'deep-linked tags reveal the tag controls');
assert.equal(tags[1].getAttribute('aria-pressed'), 'true');
assert.equal(new URL(location.href).searchParams.get('tag'), 'AI');
assert.equal(new URL(location.href).searchParams.get('src'), 'share');

categories[2].emit('click');
assert.equal(visible().length, 1, 'category and tag filters combine');
assert.equal(visible()[0].dataset.tags, 'AI|働き方');
assert.equal(new URL(location.href).searchParams.get('category'), '経済');
assert.equal(new URL(location.href).searchParams.get('tag'), 'AI');

tags[2].emit('click');
assert.equal(visible().length, 1);
assert.equal(new URL(location.href).searchParams.get('tag'), '働き方');

reset.emit('click');
assert.equal(visible().length, 3, 'reset restores all topics and categories');
const resetUrl = new URL(location.href);
assert.equal(resetUrl.searchParams.has('tag'), false);
assert.equal(resetUrl.searchParams.has('category'), false);
assert.equal(resetUrl.searchParams.get('src'), 'share', 'unrelated campaign data survives reset');

location.href = 'https://veripsa.com/archive/?tag=' + encodeURIComponent('動物') + '&src=history';
window.history.state = null;
handlers.get('popstate')();
assert.equal(visible().length, 1, 'history restores a topic deep link');
assert.equal(visible()[0].dataset.category, '社会');
assert.equal(tags[3].getAttribute('aria-pressed'), 'true');
assert.equal(tagPanel.open, true);

console.log('Archive topic tag deep links and combined filters passed.');
