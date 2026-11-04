/*
 * "あとで読む" — opt-in, device-local bookmarks for the fictional newspaper.
 * No cookies, network, accounts, server storage or tracking.
 * Render user-controlled storage with textContent only (no HTML injection).
 */
(() => {
  "use strict";

  const KEY = "mihassei-saved-stories-v1";
  const MAX = 100;
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const CATEGORIES = new Set(["社会", "事件", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ"]);
  const buttons = [...document.querySelectorAll("[data-bookmark-slug]")];
  const list = document.querySelector("[data-saved-list]");
  const empty = document.querySelector("[data-saved-empty]");
  const clearAll = document.querySelector("[data-clear-bookmarks]");
  const totals = [...document.querySelectorAll("[data-bookmark-count]")];
  const statuses = [...document.querySelectorAll("[data-bookmark-status]")];

  let available = true;
  let saved = [];
  let renderedListState = "";

  function clean(input) {
    if (!Array.isArray(input)) return [];
    const result = [];
    const seen = new Set();
    for (const item of input) {
      if (!item || typeof item !== "object" ||
          typeof item.slug !== "string" || item.slug.length > 100 ||
          !SLUG.test(item.slug) || seen.has(item.slug) ||
          typeof item.title !== "string" || !item.title.trim()) continue;
      seen.add(item.slug);
      result.push({
        slug: item.slug,
        title: item.title.trim().slice(0, 160),
        category: CATEGORIES.has(item.category) ? item.category : "創作ニュース",
        savedAt: Number.isSafeInteger(item.savedAt) && item.savedAt >= 0 ?
                 item.savedAt : 0
      });
      if (result.length >= MAX) break;
    }
    return result;
  }

  function readSaved() {
    try {
      const source = localStorage.getItem(KEY);
      return source === null ? [] : clean(JSON.parse(source));
    } catch (_) {
      // Corrupt JSON can be replaced on the next user-initiated save.
      // Browser-blocked storage disables all buttons rather than faking success.
      try { localStorage.getItem(KEY); } catch (blocked) { available = false; }
      return [];
    }
  }

  function announce(message) {
    for (const status of statuses) status.textContent = message;
  }

  function writeSaved(next, message) {
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
      saved = next;
      render();
      announce(message);
      return true;
    } catch (_) {
      available = false;
      render();
      announce("このブラウザでは記事を保存できません。ストレージの設定をご確認ください。");
      return false;
    }
  }

  function focusSavedPosition(index) {
    const controls = list ? [...list.querySelectorAll(".saved-entry-remove")].filter(button => !button.disabled) : [];
    const target = controls[Math.min(index, controls.length - 1)] ||
                   document.querySelector(".saved-controls a");
    target?.focus();
  }

  function remove(slug, trigger = null) {
    const restoreFocus = trigger && document.activeElement === trigger;
    const index = Math.max(0, saved.findIndex(item => item.slug === slug));
    // A restored page or a delayed storage event may still show an older list.
    // Remove only this article from the latest list, preserving newer saves.
    reloadSaved();
    if (!available) return;
    if (writeSaved(saved.filter(item => item.slug !== slug), "保存を解除しました。") && restoreFocus)
      focusSavedPosition(index);
  }

  function makeEntry(item) {
    const li = document.createElement("li");
    li.className = "saved-entry";

    const content = document.createElement("div");
    content.className = "saved-entry-copy";
    const category = document.createElement("span");
    category.className = "saved-entry-category";
    category.textContent = item.category;
    const link = document.createElement("a");
    link.className = "saved-entry-title";
    link.href = "/articles/" + item.slug + "/";
    link.textContent = item.title;
    const date = document.createElement("small");
    date.className = "saved-entry-date";
    date.textContent = item.savedAt > 0 ?
      "保存日：" + new Date(item.savedAt).toLocaleDateString("ja-JP") : "このブラウザに保存";
    content.append(category, link, date);

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.disabled = !available;
    removeButton.className = "saved-entry-remove";
    removeButton.textContent = "保存を解除";
    removeButton.setAttribute("aria-label", "「" + item.title + "」の保存を解除");
    removeButton.addEventListener("click", () => remove(item.slug, removeButton));
    li.append(content, removeButton);
    return li;
  }

  function render() {
    const slugs = new Set(saved.map(item => item.slug));
    for (const button of buttons) {
      const slug = button.dataset.bookmarkSlug || "";
      const valid = SLUG.test(slug) && slug.length <= 100 &&
                    typeof button.dataset.bookmarkTitle === "string" &&
                    button.dataset.bookmarkTitle.trim().length > 0;
      const on = slugs.has(slug);
      button.disabled = !available || !valid;
      button.setAttribute("aria-pressed", String(on));
      button.textContent = on ? "★ 保存済み・解除する" : "☆ あとで読む";
      button.setAttribute("aria-label", on ? "この記事の保存を解除する" : "この記事をあとで読むために保存");
    }
    for (const total of totals) {
      total.textContent = saved.length ? "（" + saved.length + "）" : "";
      total.hidden = !saved.length;
    }
    if (list) {
      const currentListState = JSON.stringify([available, saved]);
      // BFCache restores the focused article link too. Keep its DOM when the
      // saved list is unchanged instead of dropping focus on every return.
      if (currentListState !== renderedListState) {
        list.replaceChildren(...saved.map(makeEntry));
        renderedListState = currentListState;
      }
      if (empty) empty.hidden = saved.length !== 0;
      if (clearAll) {
        clearAll.hidden = saved.length === 0;
        clearAll.disabled = !available;
      }
    }
  }

  function reloadSaved(message = "") {
    saved = readSaved();
    render();
    if (!available) announce("このブラウザは保存機能を利用できません。");
    else if (message) announce(message);
  }

  reloadSaved();

  for (const button of buttons) {
    button.addEventListener("click", () => {
      if (!available || button.disabled) return;
      reloadSaved(); // Read latest state before mutating, including another tab.
      if (!available) return;
      const slug = button.dataset.bookmarkSlug;
      if (saved.some(item => item.slug === slug)) {
        remove(slug);
        return;
      }
      if (saved.length >= MAX) {
        announce("保存は最大100件です。保存一覧から不要な記事を解除してください。");
        return;
      }
      const title = button.dataset.bookmarkTitle.trim().slice(0, 160);
      const category = CATEGORIES.has(button.dataset.bookmarkCategory) ?
                       button.dataset.bookmarkCategory : "創作ニュース";
      writeSaved([{slug, title, category, savedAt: Date.now()}, ...saved],
                 "あとで読むに保存しました。");
    });
  }

  clearAll?.addEventListener("click", () => {
    if (!available || !saved.length) return;
    if (typeof window.confirm === "function" &&
        !window.confirm("保存した記事をすべて解除しますか？")) return;
    const restoreFocus = document.activeElement === clearAll;
    writeSaved([], "保存した記事をすべて解除しました。");
    if (restoreFocus) focusSavedPosition(0);
  });

  window.addEventListener?.("storage", event => {
    if ((!event.storageArea || event.storageArea === localStorage) &&
        (event.key === KEY || event.key === null)) {
      reloadSaved("別のタブで保存一覧が更新されました。");
    }
  });
  window.addEventListener?.("pageshow", event => {
    if (event.persisted) reloadSaved();
  });
})();
