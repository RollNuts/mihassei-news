/* First-party, cost-free player: one homepage preview; full articles stay manual. */
(() => {
  const fail = video => {
    video.hidden = true;
    video.pause();
    const shell = video.closest('[data-video-shell]');
    const fallback = shell?.querySelector('[data-video-fallback]');
    if (fallback) fallback.hidden = false;
    video.closest('[data-news-broadcast]')?.classList.add('broadcast-video-failed');
  };
  document.querySelectorAll('[data-video-shell] video, [data-news-broadcast] video')
    .forEach(video => {
      video.addEventListener('error', () => fail(video), { once: true });
      video.querySelector('source')?.addEventListener('error', () => fail(video), { once: true });
    });
  const screen = document.querySelector('[data-news-broadcast]');
  const video = screen?.querySelector('[data-broadcast-video], .broadcast-video');
  if (!screen || !video) return;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const saveData = navigator.connection?.saveData === true;
  // User intent wins: reduced motion/data saving and failed autoplay mean tap-to-play.
  if (reduced || saveData) return;
  let inView = false;
  const update = () => {
    if (video.hidden || document.hidden || !inView) {
      video.pause();
      return;
    }
    video.muted = true;
    const request = video.play();
    request?.catch?.(() => {
      // iOS Low Power Mode, Safari and browsers can reject autoplay.
      // Leave visible native controls and the real poster for manual playback.
      screen.classList.add('broadcast-tap-to-play');
    });
  };
  if ('IntersectionObserver' in window) {
    const watcher = new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting);
      update();
    }, { threshold: 0.35 });
    watcher.observe(screen);
  } else {
    inView = true;
    update();
  }
  document.addEventListener('visibilitychange', update);
})();
