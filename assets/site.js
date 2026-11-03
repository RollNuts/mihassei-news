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
  const params = new URLSearchParams(window.location.search);
  let category = params.get('category') || 'すべて';
  const validCategories = new Set(tabs.map(t => t.dataset.categoryFilter));
  if (!validCategories.has(category)) category = 'すべて';

  const normalize = value => String(value || '').toLocaleLowerCase('ja-JP').normalize('NFKC');
  function render() {
    const query = normalize(input.value.trim());
    const decade = decadeSelect?.value || 'すべて';
    const matching = cards.filter(card =>
      (category === 'すべて' || card.dataset.category === category) &&
      (decade === 'すべて' || card.dataset.decade === decade) &&
      (!query || normalize(card.dataset.search).includes(query))
    );
    const matchSet = new Set(matching);
    const shown = Math.min(matching.length,visibleLimit);
    let visible = 0;
    for (const card of cards) {
      const matches = matchSet.has(card);
      card.hidden = !matches || visible >= visibleLimit;
      if (matches) visible++;
    }
    if (count) count.textContent = shown + ' / ' + matching.length + '件の記事を表示';
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
    const url = new URL(window.location.href);
    if (category === 'すべて') url.searchParams.delete('category');
    else url.searchParams.set('category', category);
    window.history.replaceState(null, '', url.pathname + url.search);
  }
  function resetAndRender() {
    visibleLimit = PAGE_SIZE;
    render();
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
    visibleLimit += PAGE_SIZE;
    render();
  });
  render();
})();
