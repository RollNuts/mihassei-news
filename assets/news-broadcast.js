/* One cost-free homepage preview; captions always follow the actual video clock. */
(() => {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const saveData = navigator.connection?.saveData === true;

  document.querySelectorAll('[data-video-shell]').forEach(shell => {
    const video = shell.querySelector('video');
    if (!video) return;
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
    video.addEventListener('error', fail, { once: true });
    video.querySelector('source')?.addEventListener('error', fail, { once: true });
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
      video.muted = true;
      playPending = true;
      automaticallyInterrupted = false;
      const request = video.play();
      if (request?.then) {
        request.then(() => {
          playPending = false;
          update(); // Visibility may have changed while playback was loading.
        }, error => {
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
    video.addEventListener('pause', () => {
      if (automaticPause) automaticPause = false;
      else readerPaused = true;
    });
    video.addEventListener('play', () => {
      readerPaused = false;
      autoplayBlocked = false;
      shell.classList.remove('broadcast-tap-to-play');
    });
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
