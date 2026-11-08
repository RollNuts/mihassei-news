/* Recommendations use measured PVs from the complete published candidate pool. */
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/engagement.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const candidate=(slug,title,category='社会')=>({slug,title,category});
const metrics=(week,today=1,readers=0)=>({
  last7DaysPageViews:week,todayPageViews:Math.min(week,today),pageViews:week,readers,likes:0
});

function header(){
  return {
    // Deliberately no unit/visitors/browser counts: PV is an independent metric.
    pageViews:100,
    pageViewTracking:{unit:'page-view',timezone:'Asia/Tokyo',startedAtUtc:'2026-10-28T00:00:00Z'},
    today:{dateJst:'2026-11-04',pageViews:25},
    yesterday:{dateJst:'2026-11-03',pageViews:30,pageViewsCoverage:'complete'},
    last7Days:{startDateJst:'2026-10-29',endDateJst:'2026-11-04',pageViews:100,measuredDays:7,complete:true}
  };
}

async function simulate(candidates,counts,options={}){
  const rankings=[],calls=[],intervals=[];
  const popular={dataset:{popularOptions:JSON.stringify(candidates)},hidden:true};
  const period={textContent:'過去7日間のPV順'},fallback={hidden:false};
  const widget={hidden:true,querySelector:()=>null};
  const list={replaceChildren(...items){rankings.splice(0,rankings.length,...items);}};
  function element(tag){
    return {
      tag,className:'',textContent:'',href:'',children:[],attributes:{},
      append(...nodes){this.children.push(...nodes);},
      setAttribute(name,value){this.attributes[name]=value;}
    };
  }
  const doc={
    referrer:'',visibilityState:'visible',
    querySelector(sel){
      return sel==='[data-popular-section]'?popular:
             sel==='[data-popular-list]'?list:
             sel==='[data-popular-period]'?period:
             sel==='[data-popular-fallback]'?fallback:
             ['[data-observer-widget]','[data-pv-widget]'].includes(sel)?widget:null;
    },
    querySelectorAll(){return [];},createElement:element,addEventListener(){}
  };
  let failed=Boolean(options.apiFails);
  const metricFor=slug=>typeof counts[slug]==='number'?metrics(counts[slug]):counts[slug];
  const ranking=()=>Object.keys(counts)
    .filter(slug=>/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug))
    .map(slug=>({slug,metric:metricFor(slug)}))
    .filter(item=>item.metric && Number.isSafeInteger(item.metric.last7DaysPageViews) &&
      item.metric.last7DaysPageViews>0 && Number.isSafeInteger(item.metric.todayPageViews) &&
      item.metric.todayPageViews>=0 && item.metric.todayPageViews<=item.metric.last7DaysPageViews)
    .map(item=>({article:item.slug,pageViews:item.metric.last7DaysPageViews,
                 todayPageViews:item.metric.todayPageViews}));
  const snapshotFor=url=>{
    const parsed=new URL('https://veripsa.com'+url);
    const slugs=(parsed.searchParams.get('articles')||'').split(',').filter(Boolean);
    const articles=Object.fromEntries(slugs.filter(slug=>Object.hasOwn(counts,slug)).map(slug=>[
      slug,metricFor(slug)
    ]));
    if(options.legacy)return {unit:'anonymous-browser',visitors:900,
      today:{dateJst:'2026-11-04',visitors:500,articleReads:800},articles};
    const summary=header();
    if(options.partial){
      summary.pageViewTracking.startedAtUtc='2026-11-04T00:00:00Z';
      summary.last7Days.complete=false;summary.last7Days.measuredDays=1;
    }
    if(parsed.searchParams.get('popular')==='1' && !options.noServerRanking)
      summary.topArticles={last7Days:ranking()};
    return {...summary,articles};
  };
  const ctx={
    window:{setInterval(callback){intervals.push(callback);},addEventListener(){}},
    document:doc,Date,Intl,URL,URLSearchParams,
    location:{pathname:options.pathname||'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,request={})=>{
      calls.push({url,method:request.method||'GET'});
      if(failed)return {ok:false,json:async()=>({})};
      return {ok:true,json:async()=>snapshotFor(url)};
    }
  };
  vm.runInNewContext(source,vm.createContext(ctx),{timeout:1500});
  await settle();
  return {popular,period,fallback,rankings,calls,intervals,fail(){failed=true;}};
}
const urls=result=>result.rankings.map(li=>li.children[1].children[1].href);

test('zero, unmeasured and missing PV never create a popularity claim or remove latest fallback',async()=>{
  const items=[candidate('a','記事A'),candidate('b','記事B')];
  for(const counts of [{a:0,b:0},{a:metrics(null),b:metrics(null)},{}]){
    const result=await simulate(items,counts);
    assert.equal(result.popular.hidden,true);
    assert.equal(result.fallback.hidden,false);
    assert.equal(result.rankings.length,0);
    assert.equal(result.calls.filter(x=>x.method==='GET').length,0);
    assert.equal(result.calls.filter(x=>x.method==='POST').length,1);
  }
  const legacy=await simulate(items,{a:{readers:500},b:{readers:300}},{legacy:true});
  assert.equal(legacy.popular.hidden,true,'legacy browser popularity is not a PV fallback');
  assert.equal(legacy.fallback.hidden,false);
});

test('last-seven-day PV determines the actual top three, regardless of browser or lifetime counts',async()=>{
  const items=[candidate('a','見出しA'),candidate('b','見出しB','経済'),
               candidate('c','見出しC','技術'),candidate('d','見出しD','暮らし')];
  const result=await simulate(items,{
    a:{...metrics(6,5,600),pageViews:1000},b:metrics(11,2,1),
    c:metrics(7,7,2),d:{...metrics(3,3,900),pageViews:5000}
  });
  assert.equal(result.popular.hidden,false);
  assert.equal(result.fallback.hidden,true);
  assert.deepEqual(urls(result),['/articles/b/','/articles/c/','/articles/a/']);
  assert.deepEqual(result.rankings.map(li=>li.children[2].textContent),['11 PV','7 PV','6 PV']);
  assert.equal(result.rankings[0].children[0].textContent,'01');
  assert.equal(result.rankings[0].children[1].children[0].textContent,'経済');
  assert.equal(result.period.textContent,'過去7日間のPV順');
  assert.equal(result.calls.filter(x=>x.method==='GET').length,0,
    'the visit response already carries the authoritative ranking');
});

test('ties use today PV and then real-publication candidate order',async()=>{
  const items=[candidate('new','最新'),candidate('old','古い'),candidate('older','さらに古い')];
  const result=await simulate(items,{new:metrics(5,1),old:metrics(5,3),older:metrics(5,3)});
  assert.deepEqual(urls(result),['/articles/old/','/articles/older/','/articles/new/']);
});

test('one observed PV is displayed honestly and partial tracking states its actual start',async()=>{
  const result=await simulate([candidate('a','A'),candidate('b','B')],{a:1,b:0},{partial:true});
  assert.equal(result.popular.hidden,false);
  assert.deepEqual(urls(result),['/articles/a/']);
  assert.equal(result.rankings[0].children[2].textContent,'1 PV');
  assert.equal(result.period.textContent,'2026/11/4からのPV順');
});

test('all published candidates remain eligible beyond twenty without client-side metric batches',async()=>{
  const items=Array.from({length:60},(_,i)=>candidate('story-'+i,'掲載記事 '+i));
  const counts=Object.fromEntries(items.map((v,i)=>[v.slug,i+1]));
  const result=await simulate(items,counts);
  assert.equal(result.calls.filter(x=>x.method==='POST').length,1);
  assert.equal(result.calls.filter(x=>x.method==='GET').length,0,
    'homepage ranking is returned with the visit instead of fetching all sixty stories');
  assert.equal(result.popular.hidden,false);
  assert.equal(result.fallback.hidden,true);
  assert.deepEqual(urls(result),['/articles/story-59/','/articles/story-58/','/articles/story-57/']);
  const request=new URL('https://veripsa.com'+result.calls[0].url);
  assert.equal(request.searchParams.get('popular'),'1');
  assert.equal(request.searchParams.get('articles'),null,
    'popular candidates do not inflate the per-article metrics query');
});

test('invalid, duplicate and current-article candidates cannot create unsafe or self recommendations',async()=>{
  const result=await simulate([
    candidate('../invalid','unsafe'),candidate('a',''),candidate('current','読んでいる記事'),
    candidate('a','<img src=x onerror=alert(1)>'),candidate('a','重複'),candidate('b','B')
  ],{a:2,b:1,current:99},{pathname:'/articles/current/'});
  assert.deepEqual(urls(result),['/articles/a/','/articles/b/']);
  assert.equal(result.rankings[0].children[1].children[1].textContent,'<img src=x onerror=alert(1)>');
});

test('authoritative server ranking may omit zero or malformed candidate rows safely',async()=>{
  const items=[candidate('a','A'),candidate('b','B')];
  const result=await simulate(items,{a:50,b:0});
  assert.equal(result.popular.hidden,false);
  assert.deepEqual(urls(result),['/articles/a/']);
  assert.equal(result.rankings[0].children[2].textContent,'50 PV');

  const malformed=await simulate(items,{a:{...metrics(20),todayPageViews:21},b:0});
  assert.equal(malformed.popular.hidden,true,'invalid ranking rows are never promoted');
  assert.equal(malformed.fallback.hidden,false);
});

test('failed or non-authoritative initial metrics never expose a partial ranking',async()=>{
  const items=Array.from({length:21},(_,i)=>candidate('story-'+i,'記事'+i));
  const counts=Object.fromEntries(items.map((item,i)=>[item.slug,i+1]));
  for(const options of [{apiFails:true},{noServerRanking:true}]){
    const result=await simulate(items,counts,options);
    assert.equal(result.popular.hidden,true);
    assert.equal(result.fallback.hidden,false);
    assert.equal(result.rankings.length,0);
  }
});

test('a later request failure keeps the last confirmed whole-archive ranking visible',async()=>{
  const result=await simulate([candidate('a','A'),candidate('b','B')],{a:10,b:5});
  assert.equal(result.popular.hidden,false);
  assert.deepEqual(urls(result),['/articles/a/','/articles/b/']);
  result.fail();result.intervals[0]();await settle();
  assert.equal(result.popular.hidden,false);
  assert.equal(result.fallback.hidden,true);
  assert.deepEqual(urls(result),['/articles/a/','/articles/b/']);
  assert.match(result.period.textContent,/更新待ち/);
  assert.equal(result.calls.filter(call=>call.method==='POST').length,1,'refreshes are GET-only');
});


test('real browser counters refresh while visible, resume on focus and preserve last good value',async()=>{
  const timers=[],listeners={},requests=[];
  let clock=Date.UTC(2026,10,3,2,30);
  class FakeDate extends Date {
    constructor(...args){super(...(args.length?args:[clock]));}
    static now(){return clock;}
  }
  const note={textContent:'※ブラウザ単位の参考値'};
  const widget={hidden:true,querySelector:selector=>selector==='small'?note:null};
  const number={textContent:'—'},reads={textContent:'—'};
  const yesterdayStat={hidden:true},yesterdayNumber={textContent:'—'};
  const responses=[25,26,27,'offline',28];
  const document={
    referrer:'',visibilityState:'visible',
    querySelector:selector=>selector==='[data-observer-widget]'?widget:
                            selector==='[data-observer-count]'?number:
                            selector==='[data-observer-yesterday-stat]'?yesterdayStat:
                            selector==='[data-observer-yesterday]'?yesterdayNumber:
                            selector==='[data-observer-reads]'?reads:null,
    querySelectorAll:()=>[],
    addEventListener(name,callback){listeners[name]=callback;}
  };
  const window={
    setInterval(callback,delay){timers.push({callback,delay});return timers.length;},
    addEventListener(name,callback){listeners['window:'+name]=callback;}
  };
  const ctx={
    window,document,Date:FakeDate,Intl,URL,URLSearchParams,
    location:{pathname:'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,options={})=>{
      if(options.method==='POST'){
        requests.push('POST');
        return {ok:true,json:async()=>({})};
      }
      requests.push('GET');
      const current=responses.shift();
      if(current==='offline')return {ok:false,json:async()=>({})};
      return {ok:true,json:async()=>({
        unit:'anonymous-browser',visitors:900,articles:{},
        today:{dateJst:'2026-11-03',visitors:current,articleReads:current+10},
        yesterday:{dateJst:'2026-11-02',visitors:17,articleReads:21}
      })};
    }
  };
  vm.runInNewContext(source,vm.createContext(ctx),{timeout:1500});
  const settle=()=>new Promise(resolve=>setImmediate(resolve));

  await settle();
  assert.deepEqual(requests,['POST','GET']);
  assert.equal(number.textContent,'25');
  assert.equal(reads.textContent,'35');
  assert.equal(yesterdayNumber.textContent,'17');
  assert.equal(yesterdayStat.hidden,false);
  assert.equal(widget.hidden,false);
  assert.match(note.textContent,/3分ごと更新/);
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,180000,'bounded free-tier polling');

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.deepEqual(requests,['POST','GET','GET']);
  assert.equal(number.textContent,'26');
  assert.equal(reads.textContent,'36');

  document.visibilityState='hidden';
  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(requests.filter(x=>x==='GET').length,2,'hidden tabs make no API requests');

  document.visibilityState='visible';
  listeners.visibilitychange();
  await settle();
  assert.equal(number.textContent,'27');
  listeners['window:focus']();
  await settle();
  assert.equal(requests.filter(x=>x==='GET').length,3,'focus is throttled');

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(number.textContent,'27','do not show a fake zero on API failure');
  assert.match(note.textContent,/通信回復待ち/);

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(number.textContent,'28');
  assert.equal(reads.textContent,'38');
  assert.match(note.textContent,/確認/);
  assert.equal(requests.filter(x=>x==='POST').length,1,
    'read-only refresh never registers additional visits');
});

test('a failed first metrics request never displays the placeholder as a visitor total',async()=>{
  const hint={textContent:''};
  const widget={hidden:true,querySelector:selector=>selector==='small'?hint:null};
  const number={textContent:'—'};
  const document={
    referrer:'',visibilityState:'visible',
    querySelector:selector=>selector==='[data-observer-widget]'?widget:
                            selector==='[data-observer-count]'?number:null,
    querySelectorAll:()=>[],
    addEventListener(){}
  };
  const context={
    window:{setInterval(){},addEventListener(){}},document,Date,Intl,URL,URLSearchParams,
    location:{pathname:'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,options={})=>options.method==='POST'
      ? {ok:true,json:async()=>({})}
      : {ok:false,json:async()=>({})}
  };
  vm.runInNewContext(source,vm.createContext(context),{timeout:1500});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(widget.hidden,true,'no verified count means no counter on screen');
  assert.equal(number.textContent,'—','error must not invent a count');
});
