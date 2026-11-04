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

function element(tag) {
  const attributes = new Map();
  return Object.assign(eventTarget(), {
    tagName: tag.toUpperCase(), dataset: {},
    setAttribute(name, value) { attributes.set(name, String(value)); if (name === 'src') this.src = value; },
    getAttribute(name) { return attributes.get(name) ?? null; },
    removeAttribute(name) { attributes.delete(name); if (name === 'src') this.src = ''; },
    ...(tag === 'track' ? { track: { mode: 'disabled' } } : {}),
  });
}

function fixture({ homepage = true, reduced = false, saveData = false,
  observer = true, playResults = [], captions = true, playlist = null,
  storage = new Map(), storageThrows = false, nativeTrack = false } = {}) {
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
  let mediaSource = element('source');
  mediaSource.src = playlist?.[0]?.video_url || '/clips/current.mp4';
  let subtitles = nativeTrack ? element('track') : null;
  if (subtitles) subtitles.setAttribute('src', playlist?.[0]?.track_url || '/clips/current.vtt');
  const buttons = (playlist || []).map((item, index) => Object.assign(element('button'), {
    dataset: { broadcastChoice: String(index) },
  }));
  const headline = { textContent: playlist?.[0]?.title || '', href: '' };
  const articleLink = { href: '' };
  const choices = { hidden: true };
  const poster = { src: '' };
  const program = playlist ? {
    dataset: { broadcastPlaylist: JSON.stringify(playlist) },
    querySelectorAll() { return buttons; },
    querySelector(selector) {
      return {
        '[data-broadcast-headline]': headline,
        '[data-broadcast-article-link]': articleLink,
        '[data-broadcast-choices]': choices,
      }[selector] || null;
    },
  } : null;
  const video = Object.assign(element('video'), {
    paused: true, hidden: false, controls: true, muted: false,
    currentTime: 0, duration: 12, playCalls: 0, pauseCalls: 0,
    error: null, loadCalls: 0, webkitPresentationMode: 'inline',
    querySelector(selector) {
      return selector === 'source' ? mediaSource : selector === '[data-broadcast-track]' ? subtitles : null;
    },
    appendChild(child) { subtitles = child; return child; },
    replaceChild(child, previous) { assert.equal(previous, mediaSource); mediaSource = child; return previous; },
    load() {
      this.loadCalls += 1;
      this.paused = true;
      this.currentTime = 0;
      this.error = null;
      this.duration = playlist?.find(item => item.video_url === mediaSource.src)?.duration_seconds || 12;
      this.emit('loadstart');
      this.emit('loadedmetadata');
    },
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
    closest() { return program; },
    querySelector(selector) {
      return {
        video,
        '[data-broadcast-overlay]': captions ? overlay : null,
        '[data-video-fallback]': fallback,
        '.broadcast-poster': poster,
      }[selector] || null;
    },
  };
  const document = Object.assign(eventTarget(), {
    hidden: false,
    querySelectorAll() { return [shell]; },
    createElement: element,
  });
  class IntersectionObserver {
    constructor(callback) { this.callback = callback; watchers.push(this); }
    observe(target) { this.target = target; }
  }
  const window = {
    matchMedia() { return { matches: reduced }; },
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
    localStorage: {
      getItem(key) { if (storageThrows) throw new Error('Storage disabled'); return storage.get(key) || null; },
      setItem(key, value) { if (storageThrows) throw new Error('Storage disabled'); storage.set(key, value); },
    },
  };
  if (observer) window.IntersectionObserver = IntersectionObserver;
  vm.runInNewContext(source, {
    document, window, navigator: { connection: { saveData } }, IntersectionObserver,
  }, { filename: 'news-broadcast.js' });
  return {
    video, label, caption, counter, progress, panel, overlay,
    fallback, frames, pending, classes, storage, choices, headline, articleLink, buttons,
    get mediaSource() { return mediaSource; },
    get subtitles() { return subtitles; },
    choose(index) { buttons[index].emit('click'); },
    fullscreen(enabled) { document.fullscreenElement = enabled ? video : null; document.emit('fullscreenchange'); },
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
    if (target === 'video') ui.video.error = { code: 4 };
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

const playlist = [
  { slug: 'newest-story', key: 'newest-story:aaaaaaaaaaaa', title: '新しい映像の記事', category: '社会',
    video_url: '/clips/newest-story.mp4?v=aaaaaaaaaaaa', poster_url: '/generated/newest-story.jpg',
    article_url: '/articles/newest-story/', track_url: '/clips/newest-story.vtt?v=111111111111',
    duration_seconds: 12, cues },
  { slug: 'another-story', key: 'another-story:bbbbbbbbbbbb', title: '別の記事', category: 'スポーツ',
    video_url: '/clips/another-story.mp4?v=bbbbbbbbbbbb', poster_url: '/generated/another-story.jpg',
    article_url: '/articles/another-story/', track_url: '/clips/another-story.vtt?v=222222222222',
    duration_seconds: 12, cues: cues.map(cue => ({ ...cue, text: '別の記事：' + cue.text })) },
  { slug: 'legacy-story', key: 'legacy-story:cccccccccccc', title: '旧形式の記事', category: '技術',
    video_url: '/clips/legacy-story.mp4?v=cccccccccccc', poster_url: '/generated/legacy-story.jpg',
    article_url: '/articles/legacy-story/', track_url: '', duration_seconds: 4, cues: [] },
];

test('return visits prefer unseen videos, then the least recently played; new deliveries win', async () => {
  const storage = new Map();
  for (const index of [0, 1, 2, 0]) {
    const ui = fixture({ playlist, storage });
    assert.equal(ui.mediaSource.src, playlist[index].video_url);
    ui.view(1);
    await settled();
    assert.equal(JSON.parse(storage.get('mihassei:broadcast-seen:v1'))[0], playlist[index].key);
  }
  const fresh = { ...playlist[0], key: 'newest-story:dddddddddddd', video_url: '/clips/newest-story.mp4?v=dddddddddddd' };
  const ui = fixture({ playlist: [fresh, ...playlist.slice(1)], storage });
  assert.equal(ui.mediaSource.src, fresh.video_url);
  assert.equal(ui.video.playCalls, 0, 'reading history must not start an unseen offscreen video');
});

test('explicit choices update the whole story and retain a manual pause', async () => {
  const ui = fixture({ playlist, nativeTrack: true });
  ui.view(1);
  await settled();
  ui.video.pause();
  ui.choose(1);
  assert.equal(ui.video.playCalls, 1);
  assert.equal(ui.video.paused, true);
  assert.equal(ui.mediaSource.src, playlist[1].video_url);
  assert.equal(ui.headline.textContent, playlist[1].title);
  assert.equal(ui.articleLink.href, playlist[1].article_url);
  assert.equal(ui.video.poster, playlist[1].poster_url);
  assert.equal(ui.caption.textContent, playlist[1].cues[0].text);
  assert.equal(ui.subtitles.getAttribute('src'), playlist[1].track_url);
  assert.equal(ui.subtitles.track.mode, 'hidden');
  assert.equal(ui.buttons[1].getAttribute('aria-pressed'), 'true');
  assert.equal(ui.buttons[0].getAttribute('aria-pressed'), 'false');
  ui.view(0);
  ui.view(1);
  await settled();
  assert.equal(ui.video.playCalls, 1);
  await ui.video.play();
  ui.clock(8);
  assert.equal(ui.caption.textContent, playlist[1].cues[2].text);
  ui.clock(0);
  assert.equal(ui.mediaSource.src, playlist[1].video_url, 'a loop never changes the article');
});

test('a failed video can be replaced and another failure still reveals the correct poster', async () => {
  const ui = fixture({ playlist });
  ui.view(1);
  await settled();
  const oldSource = ui.mediaSource;
  oldSource.emit('error');
  assert.equal(ui.video.hidden, true);
  ui.choose(1);
  assert.equal(ui.video.hidden, false);
  assert.equal(ui.overlay.hidden, false);
  assert.equal(ui.video.poster, playlist[1].poster_url);
  oldSource.emit('error'); // A late event belonging to the detached resource.
  ui.video.emit('error'); // The old media error has been cleared by load().
  assert.equal(ui.video.hidden, false);
  await ui.video.play();
  assert.equal(ui.video.paused, false);
  ui.mediaSource.emit('error');
  assert.equal(ui.video.hidden, true);
  assert.equal(ui.overlay.hidden, true);
  assert.equal(ui.fallback.hidden, false);
});

test('a late rejected play from an old selection cannot stop the new video', async () => {
  const ui = fixture({ playlist, playResults: ['pending', 'allowed'] });
  ui.view(1);
  ui.choose(1);
  ui.pending[0].reject(Object.assign(new Error('Old source replaced'), { name: 'NotSupportedError' }));
  await settled();
  assert.equal(ui.mediaSource.src, playlist[1].video_url);
  assert.equal(ui.video.paused, false);
  assert.equal(ui.video.playCalls, 2);
  assert.equal(ui.classes.has('broadcast-tap-to-play'), false);
  ui.video.pause();
  ui.view(0);
  ui.view(1);
  await settled();
  assert.equal(ui.video.playCalls, 2, 'manual pause still wins after switching sources');
});

test('storage restrictions preserve latest-first playback and explicit choices', async () => {
  const ui = fixture({ playlist, storageThrows: true, saveData: true });
  assert.equal(ui.choices.hidden, false);
  ui.view(1);
  ui.choose(1);
  assert.equal(ui.video.playCalls, 0, 'choosing while paused does not defeat data saving');
  await ui.video.play();
  assert.equal(ui.video.paused, false);
  assert.equal(ui.mediaSource.src, playlist[1].video_url);
});

test('native captions appear in Safari/standard fullscreen and stay hidden inline', () => {
  const ui = fixture({ playlist, nativeTrack: true });
  assert.equal(ui.subtitles.track.mode, 'hidden');
  ui.video.emit('webkitbeginfullscreen');
  assert.equal(ui.subtitles.track.mode, 'showing');
  ui.video.emit('webkitendfullscreen');
  assert.equal(ui.subtitles.track.mode, 'hidden');
  ui.fullscreen(true);
  assert.equal(ui.subtitles.track.mode, 'showing');
  ui.fullscreen(false);
  assert.equal(ui.subtitles.track.mode, 'hidden');
  ui.choose(2);
  assert.equal(ui.counter.textContent, '4秒');
  assert.equal(ui.subtitles.track.mode, 'disabled', 'a legacy item must not keep the previous subtitles');
  ui.choose(1);
  assert.equal(ui.subtitles.getAttribute('src'), playlist[1].track_url);
  assert.equal(ui.subtitles.track.mode, 'hidden');
});
