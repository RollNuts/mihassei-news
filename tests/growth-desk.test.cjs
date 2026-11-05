'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../assets/growth-desk.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function node(dataset={}){
  const listeners=new Map();
  return {
    dataset,hidden:false,textContent:'',value:'x',scope:'',href:'',children:[],
    append(...items){this.children.push(...items);},
    replaceChildren(...items){this.children=[...items];},
    addEventListener(name,callback){listeners.set(name,callback);},
    fire(name){listeners.get(name)?.();}
  };
}

async function run(payload){
  const stories=[
    {slug:'story-a',title:'記事A',category:'社会',published:'2026-11-05'},
    {slug:'story-b',title:'記事B',category:'暮らし',published:'2026-11-04'}
  ];
  const root=node({growthStories:JSON.stringify(stories)});
  const elements={
    '[data-growth-status]':node(),
    '[data-growth-summary]':node(),
    '[data-growth-details]':node(),
    '[data-growth-categories]':node(),
    '[data-growth-articles]':node(),
    '[data-growth-sort]':node(),
    '[data-growth-advice]':node(),
    '[data-growth-pv-coverage]':node(),
    '[data-growth-visitors]':node(),
    '[data-growth-channel-x]':node(),
    '[data-growth-channel-search]':node(),
    '[data-growth-page-views]':node(),
    '[data-growth-week-page-views]':node(),
    '[data-growth-week-page-views-note]':node()
  };
  elements['[data-growth-summary]'].hidden=true;
  elements['[data-growth-details]'].hidden=true;
  root.querySelector=selector=>elements[selector]||null;
  const document={
    querySelector:selector=>selector==='[data-growth-desk]'?root:null,
    createElement:()=>node(),
    createTextNode:text=>({textContent:text})
  };
  const context={
    document,Intl,JSON,encodeURIComponent,
    fetch:async()=>({ok:true,json:async()=>JSON.parse(JSON.stringify(payload))}),
    console
  };
  vm.runInNewContext(source,vm.createContext(context),{timeout:1500});
  await settle();
  return {root,elements};
}

function measured(){
  return {
    unit:'anonymous-browser',visitors:9,
    channels:{x:4,search:2,external:1,unattributed:3},
    pageViews:42,
    last7Days:{
      startDateJst:'2026-10-30',endDateJst:'2026-11-05',
      pageViews:15,measuredDays:3,complete:false
    },
    articles:{
      'story-a':{
        arrivals:{x:1,search:1},readers:3,continuedReaders:2,likes:1,shareClicks:0,
        pageViews:20,last7DaysPageViews:12
      },
      'story-b':{
        arrivals:{x:3,search:0},readers:4,continuedReaders:1,likes:0,shareClicks:1,
        pageViews:null,last7DaysPageViews:null
      }
    }
  };
}

function articleTitle(row){
  return row.children[0].children[0].textContent;
}

test('growth desk keeps browser IDs and PV as separate measured units',async()=>{
  const page=await run(measured());
  const e=page.elements;
  assert.equal(e['[data-growth-visitors]'].textContent,'9');
  assert.equal(e['[data-growth-page-views]'].textContent,'42');
  assert.equal(e['[data-growth-week-page-views]'].textContent,'15');
  assert.match(e['[data-growth-week-page-views-note]'].textContent,/3\/7日計測/);
  assert.match(e['[data-growth-pv-coverage]'].textContent,/未計測の記事は0ではなく「—」/);
  assert.equal(e['[data-growth-summary]'].hidden,false);
  assert.equal(e['[data-growth-details]'].hidden,false);

  const rows=e['[data-growth-articles]'].children;
  assert.equal(articleTitle(rows[0]),'記事B','default X sort remains unchanged');
  assert.equal(rows[0].children[3].textContent,'—');
  assert.equal(rows[0].children[4].textContent,'—');

  e['[data-growth-sort]'].value='weekPv';
  e['[data-growth-sort]'].fire('change');
  const sorted=e['[data-growth-articles]'].children;
  assert.equal(articleTitle(sorted[0]),'記事A');
  assert.equal(sorted[0].children[3].textContent,'12');
  assert.equal(sorted[0].children[4].textContent,'20');
  assert.equal(articleTitle(sorted[1]),'記事B','unmeasured PV sorts after measured PV');
  assert.match(e['[data-growth-advice]'].textContent,/続報候補/);
  assert.match(e['[data-growth-advice]'].textContent,/新しい現実の事実/);
});

test('legacy API without PV stays unmeasured instead of becoming zero',async()=>{
  const payload=measured();
  delete payload.pageViews;
  delete payload.last7Days;
  for(const metric of Object.values(payload.articles)){
    delete metric.pageViews;
    delete metric.last7DaysPageViews;
  }
  const page=await run(payload);
  const e=page.elements;
  assert.equal(e['[data-growth-page-views]'].textContent,'—');
  assert.equal(e['[data-growth-week-page-views]'].textContent,'—');
  assert.match(e['[data-growth-pv-coverage]'].textContent,/現在未取得/);
  for(const row of e['[data-growth-articles]'].children){
    assert.equal(row.children[3].textContent,'—');
    assert.equal(row.children[4].textContent,'—');
  }
  assert.doesNotMatch(e['[data-growth-status]'].textContent,/表示できません/);
});

test('impossible PV relationships fail closed instead of displaying numbers',async()=>{
  const payload=measured();
  payload.articles['story-a'].last7DaysPageViews=21;
  const page=await run(payload);
  const e=page.elements;
  assert.equal(e['[data-growth-summary]'].hidden,true);
  assert.equal(e['[data-growth-details]'].hidden,true);
  assert.match(e['[data-growth-status]'].textContent,/集計を表示できません/);
});
