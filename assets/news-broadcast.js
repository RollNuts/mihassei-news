/* One cost-free homepage preview; captions always follow the actual video clock. */
(() => {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const saveData = navigator.connection?.saveData === true;
  const seenKey = 'mihassei:broadcast-seen:v1';
  const readSeen = () => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(seenKey) || '[]');
      return Array.isArray(saved) ? saved.filter(key =>
        typeof key === 'string' && /^[a-z0-9-]+:[a-f0-9]{12}$/.test(key)).slice(0, 12) : [];
    } catch (_) { return []; }
  };

  const nativeCaptions = video => {
    let element = video.querySelector('[data-broadcast-track]');
    let webkitFullscreen = false;
    const update = () => {
      if (!element?.track) return;
      const fullscreen = webkitFullscreen || video.webkitPresentationMode === 'fullscreen'
        || document.fullscreenElement === video;
      element.track.mode = element.getAttribute('src')
        ? (fullscreen ? 'showing' : 'hidden') : 'disabled';
    };
    element?.addEventListener('load', update);
    video.addEventListener('webkitbeginfullscreen', () => { webkitFullscreen = true; update(); });
    video.addEventListener('webkitendfullscreen', () => { webkitFullscreen = false; update(); });
    video.addEventListener('webkitpresentationmodechanged', () => {
      webkitFullscreen = video.webkitPresentationMode === 'fullscreen';
      update();
    });
    document.addEventListener('fullscreenchange', update);
    update();
    return url => {
      if (!element && url) {
        element = document.createElement('track');
        element.kind = 'subtitles';
        element.srclang = 'ja';
        element.label = '日本語';
        element.setAttribute('data-broadcast-track', '');
        element.addEventListener('load', update);
        video.appendChild(element);
      }
      if (!element) return;
      if (url) element.setAttribute('src', url);
      else element.removeAttribute('src');
      update();
    };
  };

  document.querySelectorAll('[data-video-shell]').forEach(shell => {
    const video = shell.querySelector('video');
    if (!video) return;
    let mediaSource = video.querySelector('source');
    let poster = shell.querySelector('.broadcast-poster');
    let watchedPoster = null;
    const watchPoster = image => {
      if (!image || image === watchedPoster) return;
      watchedPoster = image;
      const original = image.getAttribute('src');
      const fallback = image.getAttribute('data-broadcast-poster-fallback');
      if (!/^\/images\/[a-z0-9-]+\.svg$/.test(fallback || '') || original === fallback) return;
      const fallbackAlt = image.getAttribute('data-broadcast-poster-fallback-alt') || '';
      const recover = () => {
        // A detached image can report an old error after another story is selected.
        if (image !== poster || image.getAttribute('src') !== original) return;
        image.setAttribute('src', fallback);
        image.setAttribute('alt', fallbackAlt);
        video.poster = fallback;
      };
      image.addEventListener('error', recover, { once: true });
      if (image.complete && image.naturalWidth === 0) recover();
    };
    const setNativeCaptions = nativeCaptions(video);
    const overlay = shell.querySelector('[data-broadcast-overlay]');
    const label = overlay?.querySelector('[data-broadcast-label]');
    const caption = overlay?.querySelector('[data-broadcast-text]');
    const counter = overlay?.querySelector('[data-broadcast-counter]');
    const progress = overlay?.querySelector('[data-broadcast-progress]');
    const panel = overlay?.querySelector('[data-broadcast-caption]');
    let cues = [];
    try {
      const parsed = JSON.parse(overlay?.dataset.cues || '[]');
      if (Array.isArray(parsed)) cues = parsed.filter(cue =>
        Number.isFinite(cue.at) && typeof cue.label === 'string' && typeof cue.text === 'string');
    } catch (_) { /* Keep the server-rendered caption when optional data is invalid. */ }
    let activeCue = -1;
    let frame = null;
    let animation = null;
    const sync = () => {
      const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      const duration = video.duration;
      if (progress) progress.style.transform = `scaleX(${Number.isFinite(duration) && duration > 0
        ? Math.max(0, Math.min(1, time / duration)) : 0})`;
      let index = 0;
      for (let i = 0; i < cues.length; i++) if (time >= cues[i].at) index = i;
      if (cues.length && index !== activeCue) {
        activeCue = index;
        if (label) label.textContent = cues[index].label;
        if (caption) caption.textContent = cues[index].text;
        if (counter) counter.textContent = `${String(index + 1).padStart(2, '0')} / ${String(cues.length).padStart(2, '0')}`;
        animation?.cancel();
        if (!reduced && !video.paused && panel?.animate) {
          animation = panel.animate([
            { opacity: 0, transform: 'translateY(8px)' },
            { opacity: 1, transform: 'translateY(0)' }
          ], { duration: 220, easing: 'ease-out' });
        }
      }
    };
    const stopFrames = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      animation?.cancel();
    };
    const tick = () => {
      frame = null;
      sync();
      if (!video.paused && !video.hidden && !document.hidden)
        frame = window.requestAnimationFrame(tick);
    };
    const fail = () => {
      video.hidden = true;
      video.pause();
      stopFrames();
      const fallback = shell.querySelector('[data-video-fallback]');
      if (fallback) fallback.hidden = false;
      if (overlay) overlay.hidden = true;
      shell.classList.add('broadcast-video-failed');
    };
    video.addEventListener('error', () => { if (video.error) fail(); });
    const watchSource = source => source?.addEventListener('error', () => {
      if (source === mediaSource) fail();
    });
    watchSource(mediaSource);
    ['loadedmetadata', 'timeupdate', 'seeked', 'ended'].forEach(event => video.addEventListener(event, sync));
    video.addEventListener('playing', () => {
      stopFrames();
      shell.classList.add('broadcast-playing');
      tick();
    });
    video.addEventListener('pause', () => {
      stopFrames();
      shell.classList.remove('broadcast-playing');
      sync();
    });
    sync();

    // Article players remain manual. The one homepage preview respects a pause
    // chosen by the reader, including after scrolling away or changing tabs.
    if (!shell.hasAttribute('data-news-broadcast')) return;
    const mayAutoplay = !reduced && !saveData;
    let inView = false;
    let readerPaused = false;
    let automaticPause = false;
    let autoplayBlocked = false;
    let playPending = false;
    let automaticallyInterrupted = false;
    let playbackRevision = 0;
    let switchingSource = false;
    const play = () => {
      const revision = playbackRevision;
      video.muted = true;
      playPending = true;
      automaticallyInterrupted = false;
      const request = video.play();
      if (request?.then) {
        request.then(() => {
          if (revision !== playbackRevision) return;
          playPending = false;
          update(); // Visibility may have changed while playback was loading.
        }, error => {
          if (revision !== playbackRevision) return;
          playPending = false;
          // An automatic offscreen pause may interrupt a pending play request.
          const retry = error?.name === 'AbortError' && automaticallyInterrupted;
          automaticallyInterrupted = false;
          if (retry) update();
          else if (error?.name !== 'AbortError') {
            autoplayBlocked = true;
            shell.classList.add('broadcast-tap-to-play');
          }
        });
      } else playPending = false;
    };
    const update = () => {
      if (video.hidden || document.hidden || !inView) {
        if (!video.paused) {
          automaticPause = true;
          if (playPending) automaticallyInterrupted = true;
          video.pause();
        }
        return;
      }
      if (!mayAutoplay || readerPaused || autoplayBlocked || playPending || !video.paused) return;
      play();
    };
    video.addEventListener('pause', () => {
      if (automaticPause || switchingSource || video.ended) automaticPause = false;
      else if (video.paused) readerPaused = true;
    });
    video.addEventListener('play', () => {
      switchingSource = false;
      automaticPause = false;
      readerPaused = false;
      autoplayBlocked = false;
      shell.classList.remove('broadcast-tap-to-play');
    });
    video.addEventListener('loadstart', () => { switchingSource = false; automaticPause = false; });

    // Only the initial selection uses viewing history. Never replace the story
    // on a timer, at a loop boundary, or in response to another tab's storage.
    const program = shell.closest?.('[data-broadcast-playlist]');
    let items = [];
    try {
      const parsed = JSON.parse(program?.dataset.broadcastPlaylist || '[]');
      if (Array.isArray(parsed)) items = parsed.filter(item =>
        typeof item.key === 'string' && typeof item.title === 'string'
        && typeof item.video_url === 'string' && item.video_url.startsWith('/clips/')
        && typeof item.poster_url === 'string' && item.poster_url.startsWith('/generated/')
        && typeof item.article_url === 'string' && item.article_url.startsWith('/articles/')
        && [4, 12].includes(item.duration_seconds)).slice(0, 3);
    } catch (_) { /* Retain the server-rendered player when optional data is invalid. */ }
    if (items.length && mediaSource) {
      const buttons = [...program.querySelectorAll('[data-broadcast-choice]')];
      const headline = program.querySelector('[data-broadcast-headline]');
      const articleLink = program.querySelector('[data-broadcast-article-link]');
      const choices = program.querySelector('[data-broadcast-choices]');
      let selected = 0;
      let remembered = '';
      const choose = (index, initial = false) => {
        if (!items[index] || (!initial && selected === index)) return;
        const changed = selected !== index;
        const resume = !video.paused;
        if (changed) {
          switchingSource = true;
          playbackRevision += 1;
          playPending = false;
          automaticallyInterrupted = false;
          video.pause();
          stopFrames();
        }
        selected = index;
        const item = items[index];
        remembered = '';
        cues = Array.isArray(item.cues) ? item.cues : [];
        activeCue = -1;
        if (label) label.textContent = cues[0]?.label || item.category;
        if (caption) caption.textContent = cues[0]?.text || item.title;
        if (counter) counter.textContent = cues.length ? '01 / 03' : `${item.duration_seconds}秒`;
        if (overlay) overlay.hidden = false;
        if (progress) progress.style.transform = 'scaleX(0)';
        if (headline) { headline.textContent = item.title; headline.href = item.article_url; }
        if (articleLink) articleLink.href = item.article_url;
        video.poster = item.poster_url;
        if (changed && poster) {
          const nextPoster = document.createElement('img');
          nextPoster.className = 'broadcast-poster';
          nextPoster.width = 1200;
          nextPoster.height = 675;
          nextPoster.loading = 'lazy';
          nextPoster.decoding = 'async';
          nextPoster.alt = '';
          nextPoster.setAttribute('src', item.poster_url);
          nextPoster.setAttribute('data-broadcast-poster-fallback', item.poster_fallback_url || '');
          nextPoster.setAttribute('data-broadcast-poster-fallback-alt', item.poster_fallback_alt || '');
          shell.replaceChild(nextPoster, poster);
          poster = nextPoster;
        }
        watchPoster(poster);
        video.setAttribute('aria-label', `${item.title}・${item.duration_seconds}秒の無音演出映像`);
        buttons.forEach(button => button.setAttribute('aria-pressed',
          String(Number(button.dataset.broadcastChoice) === index)));
        setNativeCaptions(item.track_url || '');
        if (changed) {
          // A detached source's delayed error must not hide the newly selected
          // video. Each resource has its own element and guarded listener.
          const nextSource = document.createElement('source');
          nextSource.type = 'video/mp4';
          nextSource.src = item.video_url;
          video.replaceChild(nextSource, mediaSource);
          mediaSource = nextSource;
          watchSource(mediaSource);
          video.hidden = false;
          shell.classList.remove('broadcast-video-failed');
          video.load();
        }
        sync();
        // Choosing another item preserves a reader's pause. A playing video
        // continues only after this explicit click, with the new story at 0s.
        if (!initial && resume && inView && !document.hidden) play();
      };
      // Advance only after a completed preview; never override a reader's pause.
      if (items.length > 1) video.addEventListener('ended', () => {
        if (readerPaused || document.hidden || !inView || !mayAutoplay
            || autoplayBlocked || video.hidden) return;
        choose((selected + 1) % items.length);
        play();
      });
      const seen = readSeen();
      let preferred = items.findIndex(item => !seen.includes(item.key));
      if (preferred < 0) preferred = items.reduce((oldest, item, index) =>
        seen.indexOf(item.key) > seen.indexOf(items[oldest].key) ? index : oldest, 0);
      choose(preferred, true);
      buttons.forEach(button => button.addEventListener('click', () =>
        choose(Number(button.dataset.broadcastChoice))));
      if (choices) choices.hidden = items.length < 2;
      video.addEventListener('playing', () => {
        const key = items[selected].key;
        if (remembered === key) return;
        remembered = key;
        try {
          window.localStorage.setItem(seenKey, JSON.stringify(
            [key, ...readSeen().filter(saved => saved !== key)].slice(0, 12)));
        } catch (_) { /* Playback and explicit choices never require storage. */ }
      });
    }
    // Also protect the server-rendered poster when the optional playlist is absent.
    watchPoster(poster);
    if ('IntersectionObserver' in window) {
      const watcher = new IntersectionObserver(entries => {
        inView = entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.35);
        update();
      }, { threshold: [0, 0.35] });
      watcher.observe(shell);
    }
    // Without IntersectionObserver, leave native controls available rather than
    // downloading and playing an unseen video further down the page.
    document.addEventListener('visibilitychange', update);
  });
})();
