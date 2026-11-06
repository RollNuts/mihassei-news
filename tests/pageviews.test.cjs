'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {DatabaseSync}=require('node:sqlite');
const {randomUUID,webcrypto}=require('node:crypto');

const source=fs.readFileSync(path.join(__dirname,'../src/worker.js'),'utf8')
  .replace('import { DurableObject } from "cloudflare:workers";',
    'class DurableObject { constructor(ctx){this.ctx=ctx;} }')
  .replace('export { ReaderComments } from "./comments.js";','')
  .replace('export class NewsroomStats','class NewsroomStats')
  .replace('export default {','const serviceHandler = {');

function fixture(start='2026-11-04T00:30:00Z',cryptoImplementation=webcrypto){
  let now=Date.parse(start);
  class Clock extends Date {static now(){return now;}}
  const context=vm.createContext({
    crypto:cryptoImplementation,TextEncoder,Response,Request,URL,Uint8Array,Date:Clock
  });
  vm.runInContext(source,context);
  const Stats=vm.runInContext('NewsroomStats',context);
  const db=new DatabaseSync(':memory:');
  const sql={exec(query,...args){
    const statement=db.prepare(query);
    if(/^\s*SELECT\b/i.test(query)){
      const rows=statement.all(...args);
      return {toArray:()=>rows,one:()=>rows[0]||null};
    }
    statement.run(...args);
    return {toArray:()=>[],one:()=>null};
  }};
  const stats=new Stats({storage:{sql,transactionSync(fn){
    db.exec('BEGIN IMMEDIATE');
    try{const value=fn();db.exec('COMMIT');return value;}
    catch(error){db.exec('ROLLBACK');throw error;}
  }}},{});
  const post=async(payload,headers={})=>{
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement',{
      method:'POST',headers:{'content-type':'application/json',...headers},
      body:JSON.stringify(payload)
    }));
    return {status:response.status,data:await response.json()};
  };
  const get=async(articles='story-a,story-b',day,hour)=>{
    const params=new URLSearchParams({articles});
    if(day!==undefined)params.set('day',day);
    if(hour!==undefined)params.set('hour',hour);
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?'+params));
    return {status:response.status,data:await response.json()};
  };
  return {db,post,get,setTime(value){now=Date.parse(value);}};
}

function view(clientId,article='story-a',viewId=randomUUID()){
  return {action:'visit',clientId,article,viewId};
}

function sessionView(clientId,article='story-a',sessionId=randomUUID(),sessionChannel='unattributed',viewId=randomUUID()){
  return {action:'visit',clientId,article,viewId,sessionId,sessionChannel};
}

test('PV begins with the first display, never with GETs or legacy distinct readers',async t=>{
  const f=fixture();t.after(()=>f.db.close());
  const browser=randomUUID();
  let snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,null);
  assert.equal(snapshot.pageViewTracking.startedAtUtc,null);
  assert.equal(snapshot.today.pageViews,null);
  assert.equal(snapshot.today.pageViewsCoverage,'unmeasured');
  assert.equal(snapshot.last7Days.pageViews,null);
  assert.equal(snapshot.last7Days.measuredDays,0);
  assert.equal(snapshot.last7Days.complete,false);
  assert.equal(snapshot.articles['story-a'].pageViews,null);
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,null);
  for(let i=0;i<3;i++)await f.post({action:'visit',clientId:browser,article:'story-a'});
  snapshot=(await f.get()).data;
  assert.equal(snapshot.visitors,1);
  assert.equal(snapshot.articles['story-a'].readers,1);
  assert.equal(snapshot.pageViews,null,'old browser IDs cannot be relabelled as PV');
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,null,
    'legacy browser visits cannot initialize a PV recommendation');
  const first=await f.post(view(browser));
  assert.equal(first.status,200);
  assert.equal(first.data.pageViews,1);
  assert.equal(first.data.articlePageViews,1);
  assert.equal(first.data.todayArticlePageViews,1);
  assert.equal(first.data.visitors,1,'legacy deduplication is unchanged');
  assert.equal(first.data.pageViewTracking.startedAtUtc,'2026-11-04T00:30:00.000Z');
  assert.equal(first.data.pageViewTracking.timezone,'Asia/Tokyo');
  assert.equal(first.data.pageViewTracking.unit,'page-view');
  assert.equal(first.data.today.pageViewsCoverage,'in-progress');
  assert.equal(first.data.yesterday.pageViews,null);
  assert.equal(first.data.yesterday.pageViewsCoverage,'unmeasured');
  assert.equal(first.data.last7Days.measuredDays,1);
  snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,1);
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,1);
  assert.equal(snapshot.articles['story-b'].pageViews,0,'measured zero stays distinguishable from missing history');
  assert.equal(snapshot.articles['story-b'].last7DaysPageViews,0);
});

test('new page loads count PV while retries, widget reads and reactions do not',async t=>{
  const f=fixture();t.after(()=>f.db.close());
  const browser=randomUUID();
  const home=view(browser,'');
  await f.post(home);
  const article=view(browser);
  const first=(await f.post(article)).data;
  assert.equal(first.pageViews,2,'home and article are separate page displays');
  assert.equal(first.articlePageViews,1);
  for(let i=0;i<3;i++)await f.post({...article,viewId:article.viewId.toUpperCase()});
  const reload=(await f.post(view(browser))).data;
  assert.equal(reload.pageViews,3,'reloading an article is another PV');
  assert.equal(reload.articlePageViews,2);
  assert.equal(reload.readers,1,'one browser remains one distinct reader');
  assert.equal(reload.today.articleReads,1);
  await f.post(view(browser,'story-b'));
  for(const action of ['like','share','vote']){
    const payload={action,clientId:browser,article:'story-a'};
    if(action==='vote')payload.choice='plausible';
    assert.equal((await f.post(payload)).status,200);
  }
  const snapshots=await Promise.all([f.get(),f.get(),f.get()]);
  for(const {data} of snapshots){
    assert.equal(data.pageViews,4);
    assert.equal(data.today.pageViews,4);
    assert.equal(data.last7Days.pageViews,4);
    assert.equal(data.articles['story-a'].pageViews,2);
    assert.equal(data.articles['story-a'].todayPageViews,2);
    assert.equal(data.articles['story-a'].last7DaysPageViews,2,
      'repeat displays from the same browser count for recommendations');
    assert.equal(data.articles['story-b'].pageViews,1);
    assert.equal(data.articles['story-b'].last7DaysPageViews,1);
    assert.equal(data.visitors,1);
  }
  const events=f.db.prepare('SELECT * FROM pageview_events').all();
  assert.equal(events.length,4);
  assert.ok(events.every(event=>/^[a-f0-9]{64}$/.test(event.id)));
  assert.ok(events.every(event=>event.id!==browser && event.id!==article.viewId));
  assert.deepEqual(Object.keys(events[0]).sort(),['day','id']);
});

test('malformed display IDs and display IDs attached to reactions cannot mutate counts',async t=>{
  const f=fixture();t.after(()=>f.db.close());
  const browser=randomUUID();
  for(const viewId of ['',null,1,{},'not-a-uuid','f'.repeat(36)]){
    assert.equal((await f.post(view(browser,'story-a',viewId))).status,400);
  }
  for(const action of ['like','share','vote']){
    const payload={...view(browser),action};
    if(action==='vote')payload.choice='plausible';
    assert.equal((await f.post(payload)).status,400);
  }
  const snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,null);
  assert.equal(snapshot.visitors,0);
  assert.equal(snapshot.articles['story-a'].likes,0);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM pageview_events').get().n,0);
});

test('JST midnight preserves retry idempotency and marks incomplete history honestly',async t=>{
  const f=fixture('2026-11-03T14:59:59Z');t.after(()=>f.db.close());
  const browser=randomUUID(),event=view(browser);
  await f.post(event);
  f.setTime('2026-11-03T15:00:01Z');
  const retry=(await f.post(event)).data;
  assert.equal(retry.pageViews,1);
  assert.equal(retry.today.dateJst,'2026-11-04');
  assert.equal(retry.today.pageViews,0);
  assert.equal(retry.today.visitors,0,'a retransmission is not tomorrow\'s visit');
  assert.equal(retry.today.articleReads,0);
  assert.equal(retry.today.pageViewsCoverage,'in-progress');
  assert.equal(retry.yesterday.pageViews,1);
  assert.equal(retry.yesterday.pageViewsCoverage,'partial');
  assert.equal(retry.last7Days.pageViews,1);
  assert.equal(retry.last7Days.measuredDays,2);
  assert.equal(retry.last7Days.complete,false);
  const before=(await f.get('story-a','2026-11-02')).data.day;
  assert.equal(before.pageViews,null);
  assert.equal(before.pageViewsCoverage,'unmeasured');
  const start=(await f.get('story-a','2026-11-03')).data.day;
  assert.equal(start.pageViews,1);
  assert.equal(start.pageViewsCoverage,'partial');
  const next=(await f.post(view(browser))).data;
  assert.equal(next.pageViews,2);
  assert.equal(next.articlePageViews,2);
  assert.equal(next.todayArticlePageViews,1);
  assert.equal(next.today.pageViews,1);
  assert.equal(next.today.visitors,1);
  assert.equal(next.visitors,1);
  f.setTime('2026-11-04T15:00:00Z');
  const ended=(await f.get('story-a','2026-11-04')).data.day;
  assert.equal(ended.pageViews,1);
  assert.equal(ended.pageViewsCoverage,'complete');
});

test('out-of-order digest completion across midnight retains the earliest counted arrival',async t=>{
  const earlyBrowser=randomUUID();
  let releaseEarly,signalPaused;
  const held=new Promise(resolve=>{releaseEarly=resolve;});
  const paused=new Promise(resolve=>{signalPaused=resolve;});
  const controlledCrypto={subtle:{async digest(algorithm,data){
    if(Buffer.from(data).toString('utf8')===earlyBrowser){
      signalPaused();
      await held;
    }
    return webcrypto.subtle.digest(algorithm,data);
  }}};
  const f=fixture('2026-11-03T14:59:59.999Z',controlledCrypto);
  t.after(()=>f.db.close());
  const early=f.post(view(earlyBrowser));
  await paused;
  try{
    f.setTime('2026-11-03T15:00:00.001Z');
    const later=await f.post(view(randomUUID()));
    assert.equal(later.status,200);
    assert.equal(later.data.pageViews,1);
    assert.equal(later.data.pageViewTracking.startedAtUtc,'2026-11-03T15:00:00.001Z');
  }finally{
    releaseEarly();
    await early;
  }
  const snapshot=(await f.get('story-a','2026-11-03')).data;
  assert.equal(snapshot.pageViews,2);
  assert.equal(snapshot.pageViewTracking.startedAtUtc,'2026-11-03T14:59:59.999Z');
  assert.equal(snapshot.day.pageViews,1,'the delayed display belongs to its actual arrival day');
  assert.equal(snapshot.day.pageViewsCoverage,'partial');
  assert.equal(snapshot.yesterday.pageViews,1);
  assert.equal(snapshot.today.pageViews,1);
  assert.equal(snapshot.today.pageViewsCoverage,'in-progress');
  assert.equal(snapshot.last7Days.pageViews,2);
  assert.equal(snapshot.last7Days.measuredDays,2);
});

test('seven-day PV excludes the older day and distinguishes partial start coverage',async t=>{
  const f=fixture('2026-11-03T03:00:00Z');t.after(()=>f.db.close());
  const browser=randomUUID();
  await f.post(view(browser));
  for(let date=4;date<=9;date++){
    f.setTime('2026-11-'+String(date).padStart(2,'0')+'T03:00:00Z');
    await f.post(view(browser));
  }
  let snapshot=(await f.get()).data;
  assert.equal(snapshot.last7Days.startDateJst,'2026-11-03');
  assert.equal(snapshot.last7Days.endDateJst,'2026-11-09');
  assert.equal(snapshot.last7Days.pageViews,7);
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,7);
  assert.equal(snapshot.last7Days.measuredDays,7);
  assert.equal(snapshot.last7Days.complete,false,'the first day began at noon');
  f.setTime('2026-11-10T03:00:00Z');
  await f.post(view(browser));
  snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,8);
  assert.equal(snapshot.last7Days.pageViews,7);
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,7);
  assert.equal(snapshot.last7Days.startDateJst,'2026-11-04');
  assert.equal(snapshot.last7Days.endDateJst,'2026-11-10');
  assert.equal(snapshot.last7Days.measuredDays,7);
  assert.equal(snapshot.last7Days.complete,true,'coverage starts before this window\'s first midnight');
  assert.equal(snapshot.today.pageViewsCoverage,'in-progress');
  assert.equal(snapshot.yesterday.pageViewsCoverage,'complete');
});

test('article PV recommendations use exactly the current seven JST dates and GETs remain read-only',async t=>{
  const f=fixture('2026-10-28T14:59:59.999Z');t.after(()=>f.db.close());
  const browser=randomUUID();
  await f.post(view(browser,'story-a'));
  await f.post(view(browser,'story-old'));
  f.setTime('2026-10-28T15:00:00.000Z');
  await f.post(view(browser,'story-a'));
  await f.post(view(browser,'story-a'));
  f.setTime('2026-11-03T14:59:59.999Z');
  await f.post(view(browser,'story-b'));
  f.setTime('2026-11-03T15:00:00.000Z');
  await f.post(view(browser,'story-a'));
  const state=()=>['counters','events','pageview_events'].map(table=>
    f.db.prepare('SELECT * FROM '+table+' ORDER BY 1,2').all());
  const before=state();
  for(let i=0;i<3;i++){
    // The optional archive date affects `day`, not the current recommendation window.
    const snapshot=(await f.get('story-a,story-b,story-old,unread-story','2026-11-03')).data;
    assert.equal(snapshot.last7Days.startDateJst,'2026-10-29');
    assert.equal(snapshot.last7Days.endDateJst,'2026-11-04');
    assert.equal(snapshot.last7Days.pageViews,4);
    assert.equal(snapshot.articles['story-a'].pageViews,4);
    assert.equal(snapshot.articles['story-a'].last7DaysPageViews,3);
    assert.equal(snapshot.articles['story-b'].last7DaysPageViews,1);
    assert.equal(snapshot.articles['story-old'].pageViews,1);
    assert.equal(snapshot.articles['story-old'].last7DaysPageViews,0);
    assert.equal(snapshot.articles['unread-story'].last7DaysPageViews,0);
    assert.equal(snapshot.articles['story-a'].readers,1);
    assert.equal(snapshot.visitors,1);
  }
  assert.deepEqual(state(),before,'reading the ranking cannot change stored counts or events');
  f.setTime('2026-11-04T15:00:00.000Z');
  const next=(await f.get()).data;
  assert.equal(next.last7Days.startDateJst,'2026-10-30');
  assert.equal(next.last7Days.endDateJst,'2026-11-05');
  assert.equal(next.last7Days.pageViews,2);
  assert.equal(next.articles['story-a'].last7DaysPageViews,1,
    'the oldest day expires at JST midnight even without a new visit or prune');
  assert.equal(next.articles['story-b'].last7DaysPageViews,1);
  assert.deepEqual(state(),before,'midnight reads do not prune or fabricate PV events');
});

test('an exact-midnight start covers its first ended day without inventing earlier PV',async t=>{
  const f=fixture('2026-11-02T15:00:00Z');t.after(()=>f.db.close());
  await f.post(view(randomUUID()));
  f.setTime('2026-11-03T15:00:00Z');
  const snapshot=(await f.get()).data;
  assert.equal(snapshot.yesterday.dateJst,'2026-11-03');
  assert.equal(snapshot.yesterday.pageViewsCoverage,'complete');
  assert.equal(snapshot.yesterday.pageViews,1);
  assert.equal((await f.get('story-a','2026-11-02')).data.day.pageViews,null);
});

test('retention removes old event IDs and daily data but keeps lifetime PV and tracking start',async t=>{
  const f=fixture('2026-11-03T03:00:00Z');t.after(()=>f.db.close());
  const browser=randomUUID();
  await f.post(view(browser));
  f.setTime('2026-11-20T03:00:00Z');
  await f.post(view(browser));
  const snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,2);
  assert.equal(snapshot.articles['story-a'].pageViews,2);
  assert.equal(snapshot.pageViewTracking.startedAtUtc,'2026-11-03T03:00:00.000Z');
  assert.equal(snapshot.last7Days.pageViews,1);
  assert.equal(snapshot.articles['story-a'].last7DaysPageViews,1);
  assert.equal(snapshot.last7Days.complete,true);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM pageview_events').get().n,1);
  assert.equal(f.db.prepare("SELECT COUNT(*) AS n FROM counters WHERE key LIKE 'day:2026-11-03:%'").get().n,0);
  assert.equal((await f.get('story-a','2026-11-03')).status,400);
});

test('known automated clients and prefetch requests do not initialize or inflate PV',async t=>{
  const f=fixture();t.after(()=>f.db.close());
  const browser=randomUUID();
  for(const headers of [
    {'user-agent':'Mozilla/5.0 (compatible; Googlebot/2.1)'},
    {'user-agent':'Mozilla/5.0 HeadlessChrome/123'},
    {'purpose':'prefetch'},
    {'sec-purpose':'prefetch;prerender'}
  ]){
    const response=await f.post(view(browser),headers);
    assert.equal(response.status,200);
    assert.equal(response.data.ignored,true);
    assert.equal(response.data.pageViews,null);
    assert.equal(response.data.visitors,0);
  }
  const human=await f.post(view(browser),{'user-agent':'Mozilla/5.0 (Android; CUBOT) Chrome/123'});
  assert.equal(human.data.pageViews,1);
  assert.equal(human.data.visitors,1);
});


test('hourly PV and top-article rankings expose a same-day spike without waiting for the daily snapshot',async t=>{
  const f=fixture('2026-11-06T09:15:00Z');t.after(()=>f.db.close());
  const browser=randomUUID();
  for(let i=0;i<3;i++)await f.post(view(browser,'story-a'));
  await f.post(view(browser,'story-b'));
  let snapshot=(await f.get('story-a,story-b','2026-11-06')).data;
  assert.equal(snapshot.version,2);
  assert.equal(snapshot.hourly.timezone,'Asia/Tokyo');
  assert.equal(snapshot.hourly.startedAtUtc,'2026-11-06T09:15:00.000Z');
  assert.equal(snapshot.hourly.today[18].hourJst,'2026-11-06T18:00');
  assert.equal(snapshot.hourly.today[18].pageViews,4);
  assert.equal(snapshot.hourly.today[18].pageViewsCoverage,'partial');
  assert.equal(snapshot.hourly.today[19].pageViews,null);
  assert.equal(snapshot.hourly.today[19].pageViewsCoverage,'future');
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.topArticles.currentHour)),[
    {article:'story-a',pageViews:3},{article:'story-b',pageViews:1}
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.topArticles.today)),[
    {article:'story-a',pageViews:3},{article:'story-b',pageViews:1}
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.topArticles.day)),[
    {article:'story-a',pageViews:3},{article:'story-b',pageViews:1}
  ]);
  assert.equal(snapshot.articles['story-a'].requestedDayPageViews,3);

  f.setTime('2026-11-06T10:05:00Z');
  for(let i=0;i<5;i++)await f.post(view(browser,'story-b'));
  snapshot=(await f.get('story-a,story-b')).data;
  assert.equal(snapshot.hourly.today[18].pageViews,4);
  assert.equal(snapshot.hourly.today[18].pageViewsCoverage,'partial');
  assert.equal(snapshot.hourly.today[19].pageViews,5);
  assert.equal(snapshot.hourly.today[19].pageViewsCoverage,'in-progress');
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.topArticles.currentHour)),[
    {article:'story-b',pageViews:5}
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.topArticles.today)),[
    {article:'story-b',pageViews:6},{article:'story-a',pageViews:3}
  ]);

  const selected=(await f.get('story-a,story-b',undefined,'2026-11-06T18')).data;
  assert.equal(selected.selectedHour.hourJst,'2026-11-06T18:00');
  assert.equal(selected.selectedHour.pageViews,4);
  assert.equal(selected.selectedHour.pageViewsCoverage,'partial');
  assert.equal(selected.selectedHour.pageViewSources,null);
  assert.equal(selected.selectedHour.pageViewSourcesCoverage,'unmeasured');
  assert.deepEqual(JSON.parse(JSON.stringify(selected.topArticles.selectedHour)),[
    {article:'story-a',pageViews:3},{article:'story-b',pageViews:1}
  ]);
});

test('historical hour diagnostics retain the source mix after the spike hour ends',async t=>{
  const f=fixture('2026-11-06T09:15:00Z');t.after(()=>f.db.close());
  const browser=randomUUID();
  await f.post(sessionView(browser,'story-a',randomUUID(),'x'));
  await f.post(sessionView(browser,'story-a',randomUUID(),'x'));
  f.setTime('2026-11-06T10:05:00Z');
  await f.post(sessionView(browser,'story-b',randomUUID(),'search'));
  f.setTime('2026-11-06T11:05:00Z');

  const first=(await f.get('story-a,story-b',undefined,'2026-11-06T18')).data;
  assert.equal(first.selectedHour.pageViews,2);
  assert.equal(first.selectedHour.pageViewsCoverage,'partial');
  assert.equal(first.selectedHour.pageViewSourcesCoverage,'partial');
  assert.deepEqual(JSON.parse(JSON.stringify(first.selectedHour.pageViewSources)),{
    x:2,search:0,external:0,unattributed:0
  });
  assert.deepEqual(JSON.parse(JSON.stringify(first.topArticles.selectedHour)),[
    {article:'story-a',pageViews:2}
  ]);

  const second=(await f.get('story-a,story-b',undefined,'2026-11-06T19')).data;
  assert.equal(second.selectedHour.pageViews,1);
  assert.equal(second.selectedHour.pageViewsCoverage,'complete');
  assert.equal(second.selectedHour.pageViewSourcesCoverage,'complete');
  assert.deepEqual(JSON.parse(JSON.stringify(second.selectedHour.pageViewSources)),{
    x:0,search:1,external:0,unattributed:0
  });
  assert.deepEqual(JSON.parse(JSON.stringify(second.topArticles.selectedHour)),[
    {article:'story-b',pageViews:1}
  ]);

  assert.equal((await f.get('story-a',undefined,'2026-11-06T21')).status,400);
  assert.equal((await f.get('story-a',undefined,'bad-hour')).status,400);
});

test('30-minute sessions and session-acquisition page-view sources are counted separately from browsers',async t=>{
  const f=fixture('2026-11-06T09:15:00Z');t.after(()=>f.db.close());
  const browser=randomUUID(),session=randomUUID();
  const first=await f.post(sessionView(browser,'story-a',session,'x'));
  assert.equal(first.status,200);
  assert.equal(first.data.sessions,1);
  assert.equal(first.data.today.sessions,1);
  assert.equal(first.data.articleEntrances,1);
  assert.equal(first.data.pageViewSources.x,1);
  assert.equal(first.data.pageViewSourcesToday.x,1);
  assert.equal(first.data.pageViewSourcesCurrentHour.x,1);

  await f.post(sessionView(browser,'story-b',session,'x'));
  let snapshot=(await f.get('story-a,story-b')).data;
  assert.equal(snapshot.sessions,1,'two page views in one visit are one session');
  assert.equal(snapshot.visitors,1,'session counting never changes browser de-duplication');
  assert.equal(snapshot.articles['story-a'].entrances,1);
  assert.equal(snapshot.articles['story-b'].entrances,0);
  assert.equal(snapshot.pageViewSources.x,2);
  assert.equal(snapshot.articles['story-a'].pageViewSources.x,1);
  assert.equal(snapshot.articles['story-b'].pageViewSources.x,1);

  const secondSession=randomUUID();
  await f.post(sessionView(browser,'story-b',secondSession,'search'));
  snapshot=(await f.get('story-a,story-b')).data;
  assert.equal(snapshot.sessions,2);
  assert.equal(snapshot.today.sessions,2);
  assert.equal(snapshot.articles['story-b'].entrances,1);
  assert.equal(snapshot.pageViewSources.search,1);
  assert.equal(snapshot.pageViewSourceTracking.attribution,'session-acquisition-channel');
  assert.equal(snapshot.sessionTracking.inactivityTimeoutMinutes,30);

  const bad=await f.post({...view(browser),sessionId:randomUUID()});
  assert.equal(bad.status,400,'partial session metadata must fail closed');
});
