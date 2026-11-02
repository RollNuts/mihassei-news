/* Per-series reader progress. Explicit opt-in and on-device only.
 * Never contacts APIs or automatically records a page view as a completed read.
 */
(() => {
  "use strict";

  const PREFIX = "veripsa:serial-read:v1:";
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const MAX_EPISODES = 200;
  const seriesNodes = Array.from(document.querySelectorAll("[data-serial-progress-series]"));
  const currentNodes = Array.from(document.querySelectorAll("[data-serial-progress-current]"));

  function validSlug(value) {
    return typeof value === "string" && SLUG.test(value);
  }

  function episodesFor(node) {
    try {
      const value = JSON.parse(node.getAttribute("data-serial-episodes") || "[]");
      if (!Array.isArray(value) || !value.length || value.length > MAX_EPISODES) return [];
      const seen = new Set();
      for (const ep of value) {
        if (!ep || !validSlug(ep.slug) || !Number.isInteger(ep.number) ||
            ep.number < 1 || seen.has(ep.slug)) return [];
        seen.add(ep.slug);
      }
      return value;
    } catch (_error) {
      return [];
    }
  }

  function readLocal(series) {
    if (!validSlug(series)) return { ok: false, read: [] };
    try {
      const raw = window.localStorage.getItem(PREFIX + series);
      if (raw === null) return { ok: true, read: [] };
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return { ok: true, read: [] };
      const read = parsed.filter(validSlug).slice(0, MAX_EPISODES);
      return { ok: true, read: Array.from(new Set(read)) };
    } catch (_error) {
      return { ok: false, read: [] };
    }
  }

  function renderSeries(node) {
    const series = node.getAttribute("data-series-id") || "";
    const episodes = episodesFor(node);
    if (!validSlug(series) || !episodes.length) return;
    const state = readLocal(series);
    const completed = new Set(state.read);
    const count = state.ok ? episodes.filter(ep => completed.has(ep.slug)).length : 0;
    const summary = node.querySelector("[data-serial-progress-summary]");
    const continuation = node.querySelector("[data-serial-progress-continue]");
    if (summary) {
      summary.textContent = state.ok ?
        "この端末で " + count + " / " + episodes.length + " " + (node.getAttribute("data-kind") === "fiction" ? "話" : "回") + "を読了" :
        "読了記録を利用できません";
    }

    const next = episodes.find(ep => !completed.has(ep.slug));
    if (continuation) {
      const hasProgress = state.ok && count > 0 && next;
      continuation.hidden = !hasProgress;
      if (hasProgress) {
        const unit = node.getAttribute("data-kind") === "fiction" ? "話" : "回";
        continuation.textContent = "続きから読む（第" + next.number + unit + "） →";
        continuation.setAttribute("href", "/serials/" + series + "/" + next.slug + "/");
      }
    }

    const complete = node.querySelector("[data-serial-progress-complete]");
    if (complete) complete.hidden = !(state.ok && count > 0 && count === episodes.length);

    for (const item of node.querySelectorAll("[data-serial-episode-state]")) {
      const epSlug = item.getAttribute("data-episode") || "";
      item.textContent = state.ok && completed.has(epSlug) && episodes.some(ep => ep.slug === epSlug) ?
        "この端末で読了済み" : "";
    }
  }

  function renderCurrent(node) {
    const series = node.getAttribute("data-series-id") || "";
    const episode = node.getAttribute("data-episode-id") || "";
    const button = node.querySelector("[data-serial-progress-toggle]");
    const status = node.querySelector("[data-serial-progress-status]");
    if (!validSlug(series) || !validSlug(episode) || !button || !status) return;

    function draw(message) {
      const state = readLocal(series);
      const checked = state.ok && state.read.includes(episode);
      button.disabled = !state.ok;
      button.setAttribute("aria-pressed", checked ? "true" : "false");
      button.textContent = checked ? "読了を取り消す" : "この回を読了にする";
      status.textContent = message ||
        (!state.ok ? "このブラウザでは読了記録を保存できません。" :
         checked ? "この端末に読了を保存しています。" :
         "読み終えたらボタンを押してください。閲覧だけでは記録されません。");
    }

    button.addEventListener("click", () => {
      const state = readLocal(series);
      if (!state.ok) return draw("ブラウザの保存領域にアクセスできません。");
      const next = new Set(state.read);
      if (next.has(episode)) next.delete(episode);
      else next.add(episode);
      try {
        // The reader explicitly requested this write with the button.
        window.localStorage.setItem(PREFIX + series,
                                    JSON.stringify(Array.from(next).slice(0, MAX_EPISODES)));
        draw(next.has(episode) ? "読了をこの端末に記録しました。" : "読了記録を取り消しました。");
        seriesNodes.forEach(renderSeries);
      } catch (_error) {
        draw("保存できませんでした。ブラウザの保存設定をご確認ください。");
      }
    });
    draw();
  }

  seriesNodes.forEach(renderSeries);
  currentNodes.forEach(renderCurrent);
  if (typeof window.addEventListener === "function") {
    window.addEventListener("storage", (event) => {
      if (!event.key || !event.key.startsWith(PREFIX)) return;
      seriesNodes.forEach(renderSeries);
      currentNodes.forEach(node => {
        const button = node.querySelector("[data-serial-progress-toggle]");
        const status = node.querySelector("[data-serial-progress-status]");
        const series = node.getAttribute("data-series-id") || "";
        const episode = node.getAttribute("data-episode-id") || "";
        if (!button || !status) return;
        const state = readLocal(series);
        const checked = state.ok && state.read.includes(episode);
        button.disabled = !state.ok;
        button.setAttribute("aria-pressed", checked ? "true" : "false");
        button.textContent = checked ? "読了を取り消す" : "この回を読了にする";
        status.textContent = checked ? "この端末に読了を保存しています。" :
          "読み終えたらボタンを押してください。";
      });
    });
  }
})();
