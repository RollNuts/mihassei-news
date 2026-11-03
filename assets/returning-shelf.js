/* Optional bookmark-resume strip on the front page.
 * Reuses user-selected "あとで読む" entries. Nothing is tracked or uploaded.
 * No extra requests, timers, cookies, or new storage keys.
 */
(() => {
  "use strict";
  const shelf = document.querySelector("[data-returning-shelf]");
  if (!shelf) return;
  const articleLink = shelf.querySelector("[data-returning-shelf-article]");
  const countNode = shelf.querySelector("[data-returning-shelf-count]");
  if (!articleLink || !countNode) return;
  const KEY = "mihassei-saved-stories-v1";
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  function render() {
    // Blocked storage or malformed data simply leaves the shelf invisible.
    // Other content is entirely readable without this enhancement.
    let input;
    try {
      const raw = window.localStorage.getItem(KEY);
      input = raw === null ? [] : JSON.parse(raw);
    } catch (_) {
      shelf.hidden = true;
      return;
    }
    if (!Array.isArray(input)) {
      shelf.hidden = true;
      return;
    }
    const slugs = new Set();
    const valid = [];
    for (const item of input.slice(0, 100)) {
      if (!item || typeof item !== "object" ||
          typeof item.slug !== "string" ||
          item.slug.length > 100 || !SLUG.test(item.slug) ||
          slugs.has(item.slug) ||
          typeof item.title !== "string" ||
          !item.title.trim()) continue;
      slugs.add(item.slug);
      valid.push(item);
    }
    if (!valid.length) {
      shelf.hidden = true;
      return;
    }
    const first = valid[0];
    articleLink.href = "/articles/" + first.slug + "/";
    articleLink.textContent = first.title.trim().slice(0, 160);
    countNode.textContent = valid.length > 1 ? "ほか" + (valid.length - 1) + "件保存" : "";
    shelf.hidden = false;
  }

  render();
  // If the reader saves an article in another tab, update on return.
  window.addEventListener?.("storage", event => {
    if (event.key === KEY) render();
  });
})();
