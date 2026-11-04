/* The homepage must never imply new articles were published today
   if a scheduled issue is delayed. All dates use Japan time. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src=fs.readFileSync(path.join(__dirname,'..','assets','engagement.js'),'utf8');
const RealDate=Date;

function runEdition(start,latestDay='2026-11-02',count=10) {
  let current=start;
  let tick=null;
  const edition={dataset:{lastEdition:latestDay,editionCount:String(count),lastLabel:'11月2日 21:21'}};
  const title={textContent:''},updated={textContent:''};
  const clock={textContent:'',setAttribute(){}};
  const map={
    '[data-edition-status]':edition,
    '[data-edition-title]':title,
    '[data-edition-updated]':updated,
  };
  const document={
    referrer:'',
    querySelector:selector=>map[selector]||null,
    querySelectorAll:selector=>selector==='[data-future-clock]'?[clock]:[],
    addEventListener:()=>{},
  };
  class FakeDate extends RealDate {
    constructor(...args) { super(...(args.length?args:[current])); }
  }
  const window={
    matchMedia:()=>({matches:true}),
    setInterval(fn, interval){assert.equal(interval,1000);tick=fn;return 1;}
  };
  const ctx={window,document,URL,URLSearchParams,Intl,Date:FakeDate,
    location:{pathname:'/',search:'',origin:'https://veripsa.com'},
    localStorage:{getItem:()=>null,setItem:()=>{}},
    crypto:{randomUUID:()=>'b756630e-c15f-4e2a-a71c-e3388ad46ad4'},
    navigator:{},Blob,
    fetch:async()=>{throw new Error('network disabled in test');}
  };
  vm.runInNewContext(src,ctx,{timeout:1000});
  return {title,updated,clock,tick:()=>tick(),
    advance(value){current=value;}};
}

test('real same-day issue shows actual count, not a fictional timestamp',()=>{
  const ui=runEdition('2026-11-02T14:10:00Z');
  assert.equal(ui.title.textContent,'本日の記事');
  assert.match(ui.updated.textContent,/本日 10本/);
  assert.match(ui.updated.textContent,/11月2日 21:21/);
  assert.ok(ui.clock.textContent.startsWith('2031.11.02'));
});

test('after JST midnight, prior articles are labelled latest, never today',()=>{
  const ui=runEdition('2026-11-02T14:40:00Z');
  assert.equal(ui.title.textContent,'本日の記事');
  ui.advance('2026-11-02T15:01:00Z'); // 00:01 JST Nov 3
  ui.tick();
  assert.equal(ui.title.textContent,'最新記事');
  assert.equal(ui.updated.textContent,'更新：11月2日 21:21');
  assert.ok(ui.clock.textContent.startsWith('2031.11.03'));
});

test('a previously published issue is not presented as new on first load',()=>{
  const ui=runEdition('2026-11-04T02:00:00Z');
  assert.equal(ui.title.textContent,'最新記事');
  assert.doesNotMatch(ui.updated.textContent,/本日 \d+本/);
});
