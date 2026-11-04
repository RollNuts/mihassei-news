'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the complete production player against media/DOM events, without a network
// or a browser dependency. Playback promises can remain pending to cover races.
const source = fs.readFileSync(path.join(__dirname, '..', 'assets', 'news-broadcast.js'), 'utf8');
const cues = [
  { at: 0, label: '全景', text: '毎朝「お知らせなし」と投稿' },
  { at: 4, label: '注目', text: '投稿を忘れた朝に客が殺到' },
  { at: 8, label: 'その後', text: 'パンは昼前に完売した' },
];

function eventTarget() {
  const listeners = new Map();
  return {
    addEventListener(type, callback, options = {}) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push({ callback, once: options.once === true });
    },
    emit(type) {
      for (const entry of [...(listeners.get(type) || [])]) {
        if (entry.once) listeners.set(type, listeners.get(type).filter(item => item !== entry));
        entry.callback({ type, target: this });
      }
    },
  };
}

function fixture({ homepage = true, reduced = false, saveData = false,
  observer = true, playResults = [], captions = true } = {}) {
  const frames = new Map();
  const watchers = [];
  const pending = [];
  const classes = new Set();
  let frameId = 0;
  const label = { textContent: '全景' };
  const caption = { textContent: '毎朝「お知らせなし」と投稿' };
  const counter = { textContent: '01 / 03' };
  const progress = { style: {} };
  const panel = {
    animations: 0,
    animate() { this.animations += 1; return { cancel() {} }; },
  };
  const overlay = {
    hidden: false,
    dataset: { cues: JSON.stringify(cues) },
    querySelector(selector) {
      return {
        '[data-broadcast-label]': label,
        '[data-broadcast-text]': caption,
        '[data-broadcast-counter]': counter,
        '[data-broadcast-progress]': progress,
        '[data-broadcast-caption]': panel,
      }[selector] || null;
    },
  };
  const fallback = { hidden: true };
  const mediaSource = eventTarget();
  const video = Object.assign(eventTarget(), {
    paused: true, hidden: false, controls: true, muted: false,
    currentTime: 0, duration: 12, playCalls: 0, pauseCalls: 0,
    querySelector(selector) { return selector === 'source' ? mediaSource : null; },
    play() {
      this.playCalls += 1;
      const result = playResults.shift() || 'allowed';
      if (result === 'blocked') {
        return Promise.reject(Object.assign(new Error('User gesture required'), { name: 'NotAllowedError' }));
      }
      this.paused = false;
      this.emit('play');
      if (result === 'pending') {
        return new Promise((resolve, reject) => pending.push({ resolve, reject }));
      }
      this.emit('playing');
      return Promise.resolve();
    },
    pause() {
      this.pauseCalls += 1;
      if (this.paused) return;
      this.paused = true;
      this.emit('pause');
    },
  });
  const shell = {
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
    },
    hasAttribute(name) { return homepage && name === 'data-news-broadcast'; },
    querySelector(selector) {
      return {
        video,
        '[data-broadcast-overlay]': captions ? overlay : null,
        '[data-video-fallback]': fallback,
      }[selector] || null;
    },
  };
  const document = Object.assign(eventTarget(), {
    hidden: false,
    querySelectorAll() { return [shell]; },
  });
  class IntersectionObserver {
    constructor(callback) { this.callback = callback; watchers.push(this); }
    observe(target) { this.target = target; }
  }
  const window = {
    matchMedia() { return { matches: reduced }; },
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  if (observer) window.IntersectionObserver = IntersectionObserver;
  vm.runInNewContext(source, {
    document, window, navigator: { connection: { saveData } }, IntersectionObserver,
  }, { filename: 'news-broadcast.js' });
  return {
    video, mediaSource, label, caption, counter, progress, panel, overlay,
    fallback, frames, pending, classes,
    view(ratio) {
      for (const watcher of watchers) watcher.callback([
        { target: watcher.target, isIntersecting: ratio > 0, intersectionRatio: ratio },
      ]);
    },
    visibility(hidden) { document.hidden = hidden; document.emit('visibilitychange'); },
    clock(time, event = 'timeupdate') { video.currentTime = time; video.emit(event); },
    frame(time) {
      video.currentTime = time;
      for (const [id, callback] of [...frames]) { frames.delete(id); callback(); }
    },
  };
}

async function settled() {
  await Promise.resolve();
  await Promise.resolve();
}

test('captions follow exact shot boundaries, backward seeking and looping', () => {
  const ui = fixture({ homepage: false });
  assert.equal(ui.caption.textContent, cues[0].text);
  ui.clock(3.99);
  assert.equal(ui.caption.textContent, cues[0].text);
  ui.clock(4);
  assert.equal(ui.label.textContent, cues[1].label);
  assert.equal(ui.caption.textContent, cues[1].text);
  assert.equal(ui.counter.textContent, '02 / 03');
  ui.clock(8);
  assert.equal(ui.caption.textContent, cues[2].text);
  assert.equal(ui.counter.textContent, '03 / 03');
  ui.clock(1, 'seeked');
  assert.equal(ui.caption.textContent, cues[0].text);
  ui.clock(12, 'ended');
  assert.equal(ui.progress.style.transform, 'scaleX(1)');
  ui.clock(0);
  assert.equal(ui.caption.textContent, cues[0].text);
  assert.equal(ui.counter.textContent, '01 / 03');
  assert.equal(ui.progress.style.transform, 'scaleX(0)');
  assert.equal(ui.video.playCalls, 0, 'article media must remain manual');
});

test('animation frames use media time and stop when the reader pauses', async () => {
  const ui = fixture({ homepage: false });
  await ui.video.play();
  ui.frame(4.5);
  assert.equal(ui.caption.textContent, cues[1].text);
  assert.equal(ui.progress.style.transform, 'scaleX(0.375)');
  assert.equal(ui.frames.size, 1);
  ui.video.pause();
  assert.equal(ui.frames.size, 0);
  assert.equal(ui.classes.has('broadcast-playing'), false);
  ui.clock(9, 'seeked');
  assert.equal(ui.caption.textContent, cues[2].text, 'paused seeking still updates the caption');
  assert.equal(ui.frames.size, 0);
});

test('a reader pause survives scrolling and changing tabs until manual play', async () => {
  const ui = fixture();
  ui.view(0.2);
  assert.equal(ui.video.playCalls, 0, 'a tiny visible edge must not autoplay');
  ui.view(0.35);
  await settled();
  assert.equal(ui.video.playCalls, 1);
  assert.equal(ui.video.muted, true);
  ui.video.pause();
  ui.view(0);
  ui.view(1);
  ui.visibility(true);
  ui.visibility(false);
  await settled();
  assert.equal(ui.video.playCalls, 1);
  assert.equal(ui.video.paused, true);
  await ui.video.play();
  assert.equal(ui.video.paused, false);
  assert.equal(ui.classes.has('broadcast-playing'), true);
});

test('automatic offscreen and background pauses may resume when visible again', async () => {
  const ui = fixture();
  ui.view(1);
  await settled();
  ui.view(0);
  assert.equal(ui.video.paused, true);
  assert.equal(ui.frames.size, 0);
  ui.view(1);
  await settled();
  assert.equal(ui.video.playCalls, 2);
  assert.equal(ui.video.paused, false);
  ui.visibility(true);
  assert.equal(ui.video.paused, true);
  ui.visibility(false);
  await settled();
  assert.equal(ui.video.playCalls, 3);
  assert.equal(ui.video.paused, false);
});

test('reduced motion and data saving prevent autoplay but preserve manual playback', async () => {
  for (const options of [{ reduced: true }, { saveData: true }]) {
    const ui = fixture(options);
    ui.view(1);
    ui.visibility(true);
    ui.visibility(false);
    await settled();
    assert.equal(ui.video.playCalls, 0);
    assert.equal(ui.video.controls, true);
    await ui.video.play();
    ui.clock(8);
    assert.equal(ui.video.paused, false);
    assert.equal(ui.caption.textContent, cues[2].text);
    if (options.reduced) assert.equal(ui.panel.animations, 0);
  }
});

test('blocked autoplay is not retried on scroll and can be recovered with a tap', async () => {
  const ui = fixture({ playResults: ['blocked', 'allowed'] });
  ui.view(1);
  await settled();
  assert.equal(ui.video.hidden, false);
  assert.equal(ui.video.controls, true);
  assert.equal(ui.classes.has('broadcast-tap-to-play'), true);
  ui.view(0);
  ui.view(1);
  await settled();
  assert.equal(ui.video.playCalls, 1);
  await ui.video.play();
  assert.equal(ui.video.paused, false);
  assert.equal(ui.classes.has('broadcast-tap-to-play'), false);
  ui.clock(4);
  assert.equal(ui.caption.textContent, cues[1].text);
});

test('without viewport observation the player stays manual', async () => {
  const ui = fixture({ observer: false });
  ui.visibility(false);
  await settled();
  assert.equal(ui.video.playCalls, 0);
  await ui.video.play();
  assert.equal(ui.video.paused, false);
});

test('video and source failures reveal the still image and stop all playback', async () => {
  for (const target of ['video', 'mediaSource']) {
    const ui = fixture();
    ui.view(1);
    await settled();
    ui[target].emit('error');
    assert.equal(ui.video.hidden, true);
    assert.equal(ui.video.paused, true);
    assert.equal(ui.fallback.hidden, false);
    assert.equal(ui.overlay.hidden, true);
    assert.equal(ui.frames.size, 0);
    assert.equal(ui.classes.has('broadcast-video-failed'), true);
    ui.view(0);
    ui.view(1);
    await settled();
    assert.equal(ui.video.playCalls, 1);
  }
});

test('legacy clips without captions retain playback and still-image recovery', async () => {
  const ui = fixture({ homepage: false, captions: false });
  ui.video.duration = 4;
  await ui.video.play();
  ui.clock(3);
  assert.equal(ui.video.paused, false);
  ui.mediaSource.emit('error');
  assert.equal(ui.fallback.hidden, false);
  assert.equal(ui.video.hidden, true);
});

test('an interrupted pending autoplay recovers after a quick return into view', async () => {
  const ui = fixture({ playResults: ['pending', 'allowed'] });
  ui.view(1);
  assert.equal(ui.pending.length, 1);
  ui.view(0);
  assert.equal(ui.video.paused, true);
  ui.view(1); // Still waiting for the first play request to report its abort.
  ui.pending[0].reject(Object.assign(new Error('Interrupted by pause'), { name: 'AbortError' }));
  await settled();
  assert.equal(ui.video.playCalls, 2);
  assert.equal(ui.video.paused, false);
  assert.equal(ui.classes.has('broadcast-tap-to-play'), false);
});
