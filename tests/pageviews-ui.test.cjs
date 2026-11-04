'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../assets/engagement.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
let uuidSequence=0;
const uuid=()=>`00000000-0000-4000-8000-${(++uuidSequence).toString(16).padStart(12,'0')}`;

function measuredSnapshot(){
  return {
    unit:'anonymous-browser',visitors:900,pageViews:24,
    pageViewTracking:{unit:'page-view',startedAtUtc:'2026-11-04T00:00:00.000Z',timezone:'Asia/Tokyo'},
    today:{dateJst:'2026-11-04',visitors:6,articleReads:4,pageViews:24,pageViewsCoverage:'in-progress'},
    yesterday:{dateJst:'2026-11-03',visitors:30,articleReads:20,pageViews:null,pageViewsCoverage:'unmeasured'},
    last7Days:{startDateJst:'2026-10-29',endDateJst:'2026-11-04',pageViews:24,measuredDays:1,complete:false}
  };
}

function element(dataset={}){
  const events=new Map();
  return {
    dataset,textContent:'—',hidden:false,checked:false,attributes:{},
    classList:{add(){},remove(){},toggle(){}},
    setAttribute(name,value){this.attributes[name]=value;},
    querySelector(){return null;},querySelectorAll(){return [];},
    addEventListener(name,callback){events.set(name,callback);},
    fire(name,event={}){return events.get(name)?.(event);}
  };
}

async function runPage(options={}){
  const url=new URL(options.url||'https://veripsa.com/');
  const storage=options.storage||new Map();
  const listeners={document:new Map(),window:new Map()};
  const add=(scope,name,callback)=>{
    const bucket=listeners[scope].get(name)||[];
    bucket.push(callback);listeners[scope].set(name,bucket);
  };
  const fire=(scope,name,event={})=>{
    for(const callback of listeners[scope].get(name)||[])callback(event);
  };
  const widget=element(),note=element(),today=element(),yesterday=element(),week=element(),period=element();
  widget.hidden=true;widget.querySelector=selector=>selector==='small'?note:null;
  const preference=element(),status=element();
  const badges=(options.badges||[]).map((item,index)=>{
    const slug=typeof item==='string'?item:item.slug;
    const node=element({articlePv:slug}),count=element();
    node.cardHidden=Boolean(item.hidden);
    node.count=count;
    node.querySelector=selector=>selector==='[data-article-pv-count]'?count:null;
    node.closest=selector=>selector==='[hidden]' && node.cardHidden ? {} : null;
    return node;
  });
  const selectors=new Map([
    ['[data-observer-widget]',widget],['[data-pv-widget]',widget],
    ['[data-site-pv-today]',today],['[data-site-pv-yesterday]',yesterday],
    ['[data-site-pv-week]',week],['[data-site-pv-period-label]',period],
    ['[data-pv-tracking-note]',note],
    ['[data-analytics-status]',options.preference?status:null],
    ['.archive-list',options.archive?{}:null]
  ]);
  const document={
    referrer:'',visibilityState:options.hidden?'hidden':'visible',prerendering:Boolean(options.prerendering),
    querySelector:selector=>selectors.get(selector)||null,
    querySelectorAll:selector=>selector==='[data-article-pv]'?badges:
      selector==='[data-analytics-opt-out]'&&options.preference?[preference]:[],
    addEventListener:(name,callback)=>add('document',name,callback)
  };
  const timers=[],intervals=[],mutations=[],requests=[],seenViewIds=new Set();
  let clock=Date.UTC(2026,10,4,1),postFailures=options.postFailures||0,getFails=false,releaseGet=null;
  const getGate=options.delayFirstGet?new Promise(resolve=>{releaseGet=resolve;}):null;
  let delayed=false;
  let snapshot=options.snapshot||measuredSnapshot();
  class FakeDate extends Date{
    constructor(...args){super(...(args.length?args:[clock]));}
    static now(){return clock;}
  }
  const window={
    addEventListener:(name,callback)=>add('window',name,callback),
    setInterval(callback,delay){intervals.push({callback,delay});return intervals.length;},
    setTimeout(callback,delay){timers.push({callback,delay});return timers.length;},
    MutationObserver:class{constructor(callback){mutations.push(callback);}observe(){}}
  };
  const context={
    window,document,navigator:{webdriver:Boolean(options.webdriver)},Date:FakeDate,Intl,URL,URLSearchParams,console,
    location:{pathname:url.pathname,search:url.search,origin:url.origin},
    crypto:{randomUUID:uuid},
    localStorage:{
      getItem(key){if(options.storageFails)throw Error('storage disabled');return storage.get(key)||null;},
      setItem(key,value){if(options.storageFails)throw Error('storage disabled');storage.set(key,value);}
    },
    fetch:async(endpoint,request={})=>{
      requests.push({endpoint,method:request.method||'GET',body:request.body?JSON.parse(request.body):null});
      if(request.method==='POST'){
        const payload=JSON.parse(request.body);
        if(payload.action==='visit')seenViewIds.add(payload.viewId);
        if(postFailures-->0)throw Error('response lost after server accepted the view');
        return {ok:true,json:async()=>({})};
      }
      if(getFails)return {ok:false,json:async()=>({})};
      const slugs=(new URL('https://veripsa.com'+endpoint).searchParams.get('articles')||'').split(',').filter(Boolean);
      if(getGate && !delayed){delayed=true;await getGate;}
      const articles=Object.fromEntries(slugs.map(slug=>[slug,{
        likes:0,readers:80,todayReaders:60,
        ...(options.legacyArticles?{}:{pageViews:options.articleCounts?.[slug]??7,todayPageViews:2})
      }]));
      return {ok:true,json:async()=>({...JSON.parse(JSON.stringify(snapshot)),articles})};
    }
  };
  vm.runInNewContext(source,vm.createContext(context),{timeout:1500});
  await settle();
  return {
    document,widget,note,today,yesterday,week,period,badges,preference,status,storage,
    requests,timers,intervals,mutations,seenViewIds,fire,releaseGet,
    advance(ms){clock+=ms;},setSnapshot(value){snapshot=value;},failGets(){getFails=true;},
    visits:()=>requests.filter(request=>request.body?.action==='visit'),
    gets:()=>requests.filter(request=>request.method==='GET')
  };
}

test('PV labels use display counts, retain missing history, and populate every article badge',async()=>{
  const page=await runPage({badges:['news-one','news-two','news-one'],articleCounts:{'news-one':105,'news-two':0}});
  assert.equal(page.today.textContent,'24');
  assert.equal(page.yesterday.textContent,'—');
  assert.equal(page.week.textContent,'24');
  assert.equal(page.period.textContent,'計測開始から');
  assert.match(page.note.textContent,/2026\/11\/4.*昨日は未計測/);
  assert.deepEqual(page.badges.map(badge=>badge.count.textContent),['105','0','105']);
  assert.match(page.badges[0].attributes['aria-label'],/105PV/);
  assert.equal(page.widget.hidden,false);
});

test('measured PV rendering does not depend on legacy browser counters',async()=>{
  const snapshot=measuredSnapshot();
  delete snapshot.unit;delete snapshot.visitors;
  for(const day of [snapshot.today,snapshot.yesterday]){
    delete day.visitors;delete day.articleReads;
  }
  const page=await runPage({snapshot,badges:['news']});
  assert.equal(page.today.textContent,'24');
  assert.equal(page.week.textContent,'24');
  assert.equal(page.badges[0].count.textContent,'7');
  assert.equal(page.widget.hidden,false);
});

test('old API numbers and failed requests cannot become plausible-looking PV',async()=>{
  const old=measuredSnapshot();
  delete old.pageViewTracking;delete old.pageViews;
  delete old.today.pageViews;delete old.yesterday.pageViews;delete old.last7Days;
  const legacy=await runPage({snapshot:old,badges:['news'],legacyArticles:true});
  assert.equal(legacy.today.textContent,'—');
  assert.equal(legacy.badges[0].count.textContent,'—');
  assert.match(legacy.note.textContent,/取得できません/);
  const page=await runPage();
  page.failGets();page.advance(180000);page.intervals[0].callback();await settle();
  assert.equal(page.today.textContent,'24');
  assert.match(page.note.textContent,/通信回復待ち/);
  assert.equal(page.visits().length,1);
});

test('initial tracking without observations remains unmeasured, and covered weeks get the correct label',async()=>{
  const empty=measuredSnapshot();
  empty.pageViewTracking.startedAtUtc=null;empty.pageViews=null;empty.today.pageViews=null;
  empty.last7Days.pageViews=null;empty.last7Days.measuredDays=0;
  const page=await runPage({snapshot:empty,webdriver:true});
  assert.equal(page.today.textContent,'—');
  assert.equal(page.week.textContent,'—');
  assert.match(page.note.textContent,/計測開始待ち/);
  const complete=measuredSnapshot();
  complete.pageViewTracking.startedAtUtc='2026-10-28T00:00:00Z';
  complete.yesterday.pageViews=50;complete.yesterday.pageViewsCoverage='complete';
  complete.last7Days.pageViews=500;complete.last7Days.complete=true;complete.last7Days.measuredDays=7;
  page.setSnapshot(complete);page.advance(180000);page.intervals[0].callback();await settle();
  assert.equal(page.period.textContent,'直近7日');assert.equal(page.week.textContent,'500');
});

test('only visible document displays count; focus, visibility returns and refresh polling are read-only',async()=>{
  const page=await runPage({hidden:true});
  assert.equal(page.requests.length,0);
  page.document.visibilityState='visible';page.fire('document','visibilitychange');await settle();
  assert.equal(page.visits().length,1);
  assert.notEqual(page.visits()[0].body.viewId,page.visits()[0].body.clientId);
  const id=page.visits()[0].body.viewId;
  page.document.visibilityState='hidden';page.advance(180000);page.intervals[0].callback();await settle();
  assert.equal(page.gets().length,1);
  page.document.visibilityState='visible';page.fire('document','visibilitychange');await settle();
  page.advance(50000);page.fire('window','focus');await settle();
  assert.equal(page.visits().length,1);
  assert.equal(page.visits()[0].body.viewId,id);
  page.fire('window','pageshow',{persisted:false});await settle();
  assert.equal(page.visits().length,1);
  page.fire('window','pageshow',{persisted:true});await settle();
  assert.equal(page.visits().length,2);
  assert.notEqual(page.visits()[1].body.viewId,id);
  const reload=await runPage({storage:page.storage});
  assert.equal(reload.visits()[0].body.clientId,page.visits()[0].body.clientId);
  assert.notEqual(reload.visits()[0].body.viewId,id);
});

test('a lost response retries the same page-view ID once instead of creating another display',async()=>{
  const page=await runPage({postFailures:1});
  assert.equal(page.visits().length,1);
  const retry=page.timers.find(timer=>timer.delay===1500);
  assert.ok(retry);retry.callback();await settle();
  assert.equal(page.visits().length,2);
  assert.equal(page.visits()[0].body.viewId,page.visits()[1].body.viewId);
  assert.equal(page.seenViewIds.size,1);
  assert.equal(page.timers.filter(timer=>timer.delay===1500).length,1);
});

test('URL, saved and automated-preview opt-outs suppress visits while retaining read-only PV display',async()=>{
  for(const options of [
    {url:'https://veripsa.com/?analytics=off'},
    {url:'https://veripsa.com/?analytics=off',storageFails:true,preference:true},
    {storage:new Map([['mihassei-analytics-disabled-v1','1']])},
    {webdriver:true}
  ]){
    const page=await runPage(options);
    assert.equal(page.visits().length,0);
    assert.equal(page.today.textContent,'24');
    assert.equal(page.gets().length,1);
    assert.ok(page.requests.every(request=>!request.endpoint.includes('analytics=')));
    if(options.storageFails)assert.match(page.status.textContent,/次のページには引き継がれません/);
  }
  const page=await runPage({url:'https://veripsa.com/privacy/?analytics=off',preference:true});
  assert.equal(page.preference.checked,true);
  page.preference.checked=false;page.preference.fire('change');await settle();
  assert.equal(page.visits().length,1);
  assert.equal(page.storage.get('mihassei-analytics-disabled-v1'),'0');
  page.preference.checked=true;page.preference.fire('change');
  page.fire('window','pageshow',{persisted:true});await settle();
  assert.equal(page.visits().length,1,'disabled BFCache displays remain excluded');
});

test('prerender activation waits for visibility and counts only one event',async()=>{
  const page=await runPage({prerendering:true});
  assert.equal(page.requests.length,0);
  page.document.prerendering=false;page.fire('document','prerenderingchange');
  page.fire('document','visibilitychange');await settle();
  assert.equal(page.visits().length,1);
});

test('large and dynamically revealed archives populate all visible PVs in batches of at most twenty',async()=>{
  const page=await runPage({archive:true,badges:[...Array.from({length:43},(_,i)=>'story-'+i),{slug:'later-story',hidden:true}]});
  assert.deepEqual(page.gets().map(request=>new URL('https://veripsa.com'+request.endpoint).searchParams.get('articles').split(',').length),[20,20,3]);
  assert.ok(page.badges.slice(0,43).every(badge=>badge.count.textContent==='7'));
  assert.equal(page.badges[43].count.textContent,'—');
  page.badges[43].cardHidden=false;page.mutations[0]();
  page.timers.find(timer=>timer.delay===200).callback();await settle();
  assert.equal(page.badges[43].count.textContent,'7');
  assert.equal(page.visits().length,1,'filtering an archive is not a page display');
});

test('cards revealed during an in-flight read get a queued read-only follow-up',async()=>{
  const page=await runPage({archive:true,delayFirstGet:true,badges:['first-story',{slug:'next-story',hidden:true}]});
  assert.equal(page.gets().length,1);
  page.badges[1].cardHidden=false;page.mutations[0]();
  page.timers.find(timer=>timer.delay===200).callback();
  page.releaseGet();await settle();await settle();
  assert.equal(page.gets().length,2);
  assert.deepEqual(page.badges.map(badge=>badge.count.textContent),['7','7']);
  assert.equal(page.visits().length,1);
});
