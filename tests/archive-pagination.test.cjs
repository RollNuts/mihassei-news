/* Archive browsing works on hundreds of stories without external packages. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element(dataset={}) {
  const handlers=new Map();
  const props = new Map();
  return {
    dataset, hidden:false, value:'', textContent:'',
    classList:{toggle:(name,on)=>props.set('class:'+name,on)},
    setAttribute:(name,val)=>props.set(name,val),
    getAttribute:(name)=>props.get(name),
    addEventListener:(name,fn)=>handlers.set(name,fn),
    emit:(name)=>{assert.ok(handlers.has(name),'missing event: '+name);handlers.get(name)();}
  };
}

function harness(count, query = '', stories = []) {
  const input=element();
  const decade=element();decade.value='すべて';
  const counter=element(),empty=element(),morePanel=element(),moreButton=element(),resetFilters=element();
  morePanel.hidden=true;
  const cards=Array.from({length:count},(_,i)=>element({
    category:i%3===0?'社会':'経済',
    decade:i%7===0?'2040':'2030',
    search:i%5===0?'ロボット':'未来ニュース',
    ...stories[i],
  }));
  const tabs=['すべて','社会','経済'].map(category=>element({categoryFilter:category}));
  const nodes=new Map([
    ['[data-search-input]',input],['[data-decade-filter]',decade],
    ['[data-result-count]',counter],['[data-no-results]',empty],['[data-reset-filters]',resetFilters],
    ['[data-archive-more]',morePanel],['[data-archive-more-button]',moreButton]
  ]);
  const historyWrites=[];
  const windowHandlers=new Map();
  const document={
    querySelector:(selector)=>nodes.get(selector)||null,
    querySelectorAll:(selector)=>selector==='[data-archive-card]'?cards:
      selector==='[data-category-filter]'?tabs:[],
  };
  const location=new URL('https://veripsa.com/archive/'+query+'#category-feeds');
  const window={location,
    history:{replaceState:(_a,_b,url)=>{
      historyWrites.push(url);
      location.href=new URL(url,location.href).href;
    }},
    addEventListener:(name,fn)=>windowHandlers.set(name,fn),
  };
  const src=fs.readFileSync(path.join(__dirname,'..','assets','site.js'),'utf8');
  vm.runInNewContext(src,{document,window,URL,URLSearchParams,Set,String}, {timeout:1000});
  return {cards,input,decade,counter,empty,morePanel,moreButton,resetFilters,tabs,historyWrites,
    popstate(url){
      location.href=new URL(url,location.href).href;
      assert.ok(windowHandlers.has('popstate'));
      windowHandlers.get('popstate')();
    },
    get url(){return location.pathname+location.search+location.hash;},
    get visible(){return cards.filter(card=>!card.hidden).length;}
  };
}

{
  const h=harness(52);
  assert.equal(h.visible,18,'initial archive shows the latest 18');
  assert.equal(h.morePanel.hidden,false);
  assert.equal(h.counter.textContent,'52件中18件を表示');
  h.moreButton.emit('click');
  assert.equal(h.visible,36,'show-more reveals the next 18');
  assert.equal(h.counter.textContent,'52件中36件を表示');
  h.moreButton.emit('click');
  assert.equal(h.visible,52,'last page shows remainder');
  assert.equal(h.counter.textContent,'52件の記事');
  assert.equal(h.morePanel.hidden,true,'button hidden after final page');
  const writesBeforeBack=h.historyWrites.length;
  h.popstate('/archive/?src=history#category-feeds');
  assert.equal(h.visible,18,'browser history resets pagination to the first page');
  assert.equal(h.historyWrites.length,writesBeforeBack,'history restoration does not rewrite the URL');
  h.tabs[1].emit('click');
  assert.equal(h.visible,18,'changing category resets to the first page');
  assert.ok(h.url.includes('category='));
  assert.equal(h.tabs[1].getAttribute('aria-pressed'),'true');
  h.input.value='ロボット';h.input.emit('input');
  const socialRobot=h.cards.filter(x=>x.dataset.category==='社会' && x.dataset.search==='ロボット').length;
  assert.equal(h.visible,socialRobot,'search covers all records, not only loaded cards');
  assert.equal(h.morePanel.hidden,true);
  h.input.value='絶対に存在しない';h.input.emit('input');
  assert.equal(h.visible,0);
  assert.equal(h.counter.textContent,'0件');
  assert.equal(h.empty.hidden,false);
  h.resetFilters.emit('click');
  assert.equal(h.input.value,'','reset clears the search');
  assert.equal(h.visible,18,'reset restores the first page of all articles');
  assert.equal(h.tabs[0].getAttribute('aria-pressed'),'true');
  assert.equal(h.empty.hidden,true);
  assert.ok(!h.url.includes('category='),'reset clears the category parameter');
  h.tabs[1].emit('click');
  h.decade.value='2040';h.decade.emit('change');
  assert.equal(h.visible,h.cards.filter(x=>x.dataset.category==='社会' && x.dataset.decade==='2040').length);
  h.tabs[0].emit('click');
  assert.ok(!h.url.includes('category='),'all-category removes query param');
  assert.equal(h.tabs[0].getAttribute('aria-pressed'),'true');
}
{
  const h=harness(8);
  assert.equal(h.visible,8);
  assert.equal(h.counter.textContent,'8件の記事');
  assert.equal(h.morePanel.hidden,true,'no unnecessary button for eight stories');
}
{
  const h=harness(52,'?category=%E7%A4%BE%E4%BC%9A&q=%E3%83%AD%E3%83%9C%E3%83%83%E3%83%88&decade=2040&src=share');
  const expected=h.cards.filter(x=>x.dataset.category==='社会' &&
      x.dataset.decade==='2040' && x.dataset.search==='ロボット').length;
  assert.equal(h.input.value,'ロボット','shared search query restored');
  assert.equal(h.decade.value,'2040','shared decade restored');
  assert.equal(h.tabs[1].getAttribute('aria-pressed'),'true');
  assert.equal(h.visible,expected,'deep link filters the complete archive');
  h.input.value='別の検索語';h.input.emit('input');
  assert.ok(h.url.includes('q='),'search query is shareable');
  assert.ok(h.url.includes('decade=2040'),'decade is preserved in shared URL');
  assert.ok(h.url.includes('src=share'),'unrelated campaign query is not lost');
  assert.ok(h.url.endsWith('#category-feeds'),'anchor is preserved');
  h.resetFilters.emit('click');
  assert.ok(!h.url.includes('q=') && !h.url.includes('decade=') &&
            !h.url.includes('category='),'reset cleans filter-only URL parameters');
  assert.ok(h.url.includes('src=share'),'reset preserves unrelated query');
}

const keywordStories=[
  {category:'社会',decade:'2040',search:'音楽を語るAI友達4人'},
  {category:'経済',decade:'2030',search:'ＡＩによる音楽の演奏'},
  {category:'社会',decade:'2040',search:'AIが作る料理'},
  {category:'社会',decade:'2040',search:'音楽教室の話'},
  {category:'社会',decade:'2030',search:'aiが選ぶ音楽'},
];
{
  const h=harness(keywordStories.length,'',keywordStories);
  const expected=[0,1,4].map(i=>keywordStories[i].search);
  for (const query of ['AI 音楽','AI　音楽','　ＡＩ  音楽　','音楽 AI']) {
    h.input.value=query;h.input.emit('input');
    assert.deepEqual(h.cards.filter(card=>!card.hidden).map(card=>card.dataset.search),expected,
      'all normalized keywords must match regardless of spacing or word order');
    assert.equal(h.counter.textContent,'3件の記事');
    assert.equal(new URL('https://veripsa.com'+h.url).searchParams.get('q'),query.trim(),
      'the shareable URL preserves the entered search terms');
  }
  h.tabs[1].emit('click');
  assert.equal(h.visible,2,'category filtering still combines with multiple keywords');
  h.decade.value='2040';h.decade.emit('change');
  assert.equal(h.visible,1,'decade filtering still combines with multiple keywords');
  assert.equal(h.cards[0].hidden,false);
  h.input.value='AI 音楽 存在しない';h.input.emit('input');
  assert.equal(h.visible,0,'a missing keyword excludes partial matches');
  assert.equal(h.counter.textContent,'0件');
  h.resetFilters.emit('click');
  h.input.value=' 　 ';h.input.emit('input');
  assert.equal(h.visible,keywordStories.length,'whitespace-only input shows all stories');
  assert.equal(h.counter.textContent,'5件の記事');
  assert.ok(!new URL('https://veripsa.com'+h.url).searchParams.has('q'));
}
{
  const query='?'+new URLSearchParams({q:'ＡＩ　音楽',category:'社会',decade:'2040',src:'share'});
  const h=harness(keywordStories.length,query,keywordStories);
  assert.equal(h.input.value,'ＡＩ　音楽','multiple keywords are restored from a shared URL');
  assert.equal(h.visible,1);
  assert.equal(h.cards[0].hidden,false);
  h.resetFilters.emit('click');
  const writesBeforeBack=h.historyWrites.length;
  h.popstate('/archive/'+query+'#category-feeds');
  assert.equal(h.input.value,'ＡＩ　音楽','browser history restores the original search text');
  assert.equal(h.tabs[1].getAttribute('aria-pressed'),'true');
  assert.equal(h.decade.value,'2040');
  assert.equal(h.visible,1,'browser history restores the combined keyword and filter results');
  assert.equal(h.cards[0].hidden,false);
  assert.equal(h.historyWrites.length,writesBeforeBack);
  assert.ok(h.url.includes('src=share') && h.url.endsWith('#category-feeds'));
}
console.log('Archive pagination, keyword search, filters, and history passed.');
