/* Local mystery notebook. No analytics event, HTTP request, account or paid API.
   Storage is opt-in: a note is saved only when the reader clicks 保存する. */
(() => {
  "use strict";
  const MAX_LENGTH = 800;
  const KEY_PREFIX = "veripsa:mystery-note:v1:";
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  const setup = (panel) => {
    const serial = panel.getAttribute("data-mystery-serial") || "";
    const episode = panel.getAttribute("data-mystery-episode") || "";
    const input = panel.querySelector("[data-mystery-note-input]");
    const save = panel.querySelector("[data-mystery-note-save]");
    const clear = panel.querySelector("[data-mystery-note-clear]");
    const count = panel.querySelector("[data-mystery-note-count]");
    const status = panel.querySelector("[data-mystery-note-status]");
    if (!SLUG.test(serial) || !SLUG.test(episode) ||
        !input || !save || !clear || !count || !status) return;

    const key = KEY_PREFIX + serial + ":" + episode;
    let storageAvailable = true;
    const announce = (message) => { status.textContent = message; };
    const updateCount = () => {
      count.textContent = String(input.value.length) + " / " + String(MAX_LENGTH) + "文字";
    };
    // Merely opening the page never writes to storage.
    try {
      const previouslySaved = window.localStorage.getItem(key);
      if (previouslySaved !== null) {
        input.value = previouslySaved.slice(0, MAX_LENGTH);
        announce("この端末に保存していた推理メモを読み込みました。");
      }
    } catch (_error) {
      storageAvailable = false;
      announce("このブラウザではメモを保存できません。必要なら文章をコピーして控えてください。");
    }

    updateCount();
    input.addEventListener("input", updateCount);

    save.addEventListener("click", () => {
      if (!storageAvailable) {
        announce("保存できません。ブラウザの保存設定を確認してください。");
        return;
      }
      try {
        const note = input.value.slice(0, MAX_LENGTH);
        window.localStorage.setItem(key, note);
        announce("この端末に保存しました。サイトには送信していません。");
        updateCount();
      } catch (_error) {
        announce("保存できませんでした。必要なら文章をコピーして控えてください。");
      }
    });

    clear.addEventListener("click", () => {
      if (!storageAvailable) {
        announce("ブラウザの保存領域にアクセスできません。");
        return;
      }
      try {
        window.localStorage.removeItem(key);
        input.value = "";
        updateCount();
        announce("この話の保存メモを削除しました。");
      } catch (_error) {
        announce("メモを削除できませんでした。保存設定を確認してください。");
      }
    });
  };

  const panels = document.querySelectorAll("[data-mystery-notebook]");
  panels.forEach(setup);
})();
