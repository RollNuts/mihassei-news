(() => {
  const input = document.querySelector('[data-search-input]');
  const cards = [...document.querySelectorAll('[data-archive-card]')];
  if (!input || !cards.length) return;
  const tabs = [...document.querySelectorAll('[data-category-filter]')];
  const decadeSelect = document.querySelector('[data-decade-filter]');
  const count = document.querySelector('[data-result-count]');
  const empty = document.querySelector('[data-no-results]');
  const resetFilters = document.querySelector('[data-reset-filters]');
  const morePanel = document.querySelector('[data-archive-more]');
  const moreButton = document.querySelector('[data-archive-more-button]');
  const PAGE_SIZE = 18;
  let visibleLimit = PAGE_SIZE;
  const validCategories = new Set(tabs.map(t => t.dataset.categoryFilter));
  const validDecades = new Set(cards.map(card => card.dataset.decade));
  let category = 'すべて';
  const filterKey = () => JSON.stringify([category, input.value.trim(), decadeSelect?.value || 'すべて']);
  // Search and filters are shareable and survive a new visit or browser Back.
  function restoreFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const requestedCategory = params.get('category') || 'すべて';
    category = validCategories.has(requestedCategory) ? requestedCategory : 'すべて';
    input.value = (params.get('q') || '').slice(0, 120);
    if (decadeSelect) {
      const requestedDecade = params.get('decade') || 'すべて';
      decadeSelect.value = validDecades.has(requestedDecade) ? requestedDecade : 'すべて';
    }
    const prior = window.history.state?.mihasseiArchive;
    const maximum = Math.ceil(cards.length / PAGE_SIZE) * PAGE_SIZE;
    visibleLimit = prior?.filters === filterKey() && Number.isSafeInteger(prior.visibleLimit) &&
      prior.visibleLimit >= PAGE_SIZE && prior.visibleLimit <= maximum ? prior.visibleLimit : PAGE_SIZE;
  }
  restoreFiltersFromUrl();

  const normalize = value => String(value || '').toLocaleLowerCase('ja-JP').normalize('NFKC');
  function render(syncUrl = true) {
    const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    const decade = decadeSelect?.value || 'すべて';
    const matching = cards.filter(card => {
      const searchable = normalize(card.dataset.search);
      return (category === 'すべて' || card.dataset.category === category) &&
        (decade === 'すべて' || card.dataset.decade === decade) &&
        terms.every(term => searchable.includes(term));
    });
    const matchSet = new Set(matching);
    const shown = Math.min(matching.length,visibleLimit);
    let visible = 0;
    for (const card of cards) {
      const matches = matchSet.has(card);
      card.hidden = !matches || visible >= visibleLimit;
      if (matches) visible++;
    }
    if (count) {
      count.textContent = matching.length === 0 ? '0件' :
        shown < matching.length ? matching.length + '件中' + shown + '件を表示' :
        matching.length + '件の記事';
    }
    if (empty) empty.hidden = matching.length !== 0;
    if (morePanel) morePanel.hidden = matching.length <= visibleLimit;
    if (moreButton) {
      moreButton.textContent = 'さらに' + Math.min(PAGE_SIZE,Math.max(0,matching.length-visibleLimit)) + '件を見る';
    }
    tabs.forEach(tab => {
      const active = tab.dataset.categoryFilter === category;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', String(active));
    });
    if (syncUrl) {
      const url = new URL(window.location.href);
      if (category === 'すべて') url.searchParams.delete('category');
      else url.searchParams.set('category', category);
      const search = input.value.trim();
      if (search) url.searchParams.set('q', search);
      else url.searchParams.delete('q');
      if (decade === 'すべて') url.searchParams.delete('decade');
      else url.searchParams.set('decade', decade);
      // Keep campaign parameters and links to page anchors intact.
      const state = window.history.state;
      const previous = state && typeof state === 'object' && !Array.isArray(state) ? state : {};
      // Keep the expanded list in this history entry, without adding URL parameters
      // or replacing state belonging to another reader feature.
      try {
        window.history.replaceState({...previous, mihasseiArchive: {
          filters: filterKey(), visibleLimit
        }}, '', url.pathname + url.search + url.hash);
      } catch (_) { /* Filtering remains usable when browser history is unavailable. */ }
    }
  }
  function resetAndRender(syncUrl = true) {
    visibleLimit = PAGE_SIZE;
    render(syncUrl);
  }
  tabs.forEach(tab => tab.addEventListener('click', () => {
    category = tab.dataset.categoryFilter;
    resetAndRender();
  }));
  input.addEventListener('input', resetAndRender);
  decadeSelect?.addEventListener('change', resetAndRender);
  resetFilters?.addEventListener('click', () => {
    input.value = '';
    category = 'すべて';
    if (decadeSelect) decadeSelect.value = 'すべて';
    resetAndRender();
    input.focus?.();
  });
  moreButton?.addEventListener('click', () => {
    const previouslyVisible = new Set(cards.filter(card => !card.hidden));
    visibleLimit += PAGE_SIZE;
    render();
    const firstNew = cards.find(card => !card.hidden && !previouslyVisible.has(card));
    firstNew?.querySelector('h3 a')?.focus();
  });
  window.addEventListener?.('popstate', () => {
    restoreFiltersFromUrl();
    render(false);
  });
  window.addEventListener?.('pageshow', event => {
    if (!event.persisted) return;
    restoreFiltersFromUrl();
    render(false);
  });
  render();
})();
