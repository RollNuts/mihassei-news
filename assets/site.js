(() => {
  const input = document.querySelector('[data-search-input]');
  const cards = [...document.querySelectorAll('[data-archive-card]')];
  if (!input || !cards.length) return;
  const tabs = [...document.querySelectorAll('[data-category-filter]')];
  const decadeSelect = document.querySelector('[data-decade-filter]');
  const count = document.querySelector('[data-result-count]');
  const empty = document.querySelector('[data-no-results]');
  const params = new URLSearchParams(window.location.search);
  let category = params.get('category') || 'すべて';
  const validCategories = new Set(tabs.map(t => t.dataset.categoryFilter));
  if (!validCategories.has(category)) category = 'すべて';

  const normalize = value => String(value || '').toLocaleLowerCase('ja-JP').normalize('NFKC');
  function render() {
    const query = normalize(input.value.trim());
    const decade = decadeSelect?.value || 'すべて';
    let shown = 0;
    for (const card of cards) {
      const matched = (category === 'すべて' || card.dataset.category === category) &&
        (decade === 'すべて' || card.dataset.decade === decade) &&
        (!query || normalize(card.dataset.search).includes(query));
      card.hidden = !matched;
      if (matched) shown++;
    }
    if (count) count.textContent = shown + '件の記事';
    if (empty) empty.hidden = shown !== 0;
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
  tabs.forEach(tab => tab.addEventListener('click', () => {
    category = tab.dataset.categoryFilter;
    render();
  }));
  input.addEventListener('input', render);
  decadeSelect?.addEventListener('change', render);
  render();
})();
