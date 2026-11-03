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

function harness(count) {
  const input=element();
  const decade=element();decade.value='すべて';
  const counter=element(),empty=element(),morePanel=element(),moreButton=element(),resetFilters=element();
  morePanel.hidden=true;
  const cards=Array.from({length:count},(_,i)=>element({
    category:i%3===0?'社会':'経済',
    decade:i%7===0?'2040':'2030',
    search:i%5===0?'ロボット':'未来ニュース',
  }));
  const tabs=['すべて','社会','経済'].map(category=>element({categoryFilter:category}));
  const nodes=new Map([
    ['[data-search-input]',input],['[data-decade-filter]',decade],
    ['[data-result-count]',counter],['[data-no-results]',empty],['[data-reset-filters]',resetFilters],
    ['[data-archive-more]',morePanel],['[data-archive-more-button]',moreButton]
  ]);
  let lastUrl='';
  const document={
    querySelector:(selector)=>nodes.get(selector)||null,
    querySelectorAll:(selector)=>selector==='[data-archive-card]'?cards:
      selector==='[data-category-filter]'?tabs:[],
  };
  const window={location:{href:'https://veripsa.com/archive/'},
    history:{replaceState:(_a,_b,url)=>{lastUrl=url;}}};
  const src=fs.readFileSync(path.join(__dirname,'..','assets','site.js'),'utf8');
  vm.runInNewContext(src,{document,window,URL,URLSearchParams,Set,String}, {timeout:1000});
  return {cards,input,decade,counter,empty,morePanel,moreButton,resetFilters,tabs,
    get url(){return lastUrl;},
    get visible(){return cards.filter(card=>!card.hidden).length;}
  };
}

{
  const h=harness(52);
  assert.equal(h.visible,18,'initial archive shows the latest 18');
  assert.equal(h.morePanel.hidden,false);
  assert.match(h.counter.textContent,/18 \/ 52/);
  h.moreButton.emit('click');
  assert.equal(h.visible,36,'show-more reveals the next 18');
  h.moreButton.emit('click');
  assert.equal(h.visible,52,'last page shows remainder');
  assert.equal(h.morePanel.hidden,true,'button hidden after final page');
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
  assert.equal(h.morePanel.hidden,true,'no unnecessary button for eight stories');
}
console.log('Archive pagination and filters passed: 52 and 8 article scenarios.');
