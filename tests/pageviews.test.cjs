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
  const get=async(articles='story-a,story-b',day)=>{
    const params=new URLSearchParams({articles});
    if(day!==undefined)params.set('day',day);
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?'+params));
    return {status:response.status,data:await response.json()};
  };
  return {db,post,get,setTime(value){now=Date.parse(value);}};
}

function view(clientId,article='story-a',viewId=randomUUID()){
  return {action:'visit',clientId,article,viewId};
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
  for(let i=0;i<3;i++)await f.post({action:'visit',clientId:browser,article:'story-a'});
  snapshot=(await f.get()).data;
  assert.equal(snapshot.visitors,1);
  assert.equal(snapshot.articles['story-a'].readers,1);
  assert.equal(snapshot.pageViews,null,'old browser IDs cannot be relabelled as PV');
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
  assert.equal(snapshot.articles['story-b'].pageViews,0,'measured zero stays distinguishable from missing history');
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
    assert.equal(data.articles['story-b'].pageViews,1);
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
  assert.equal(snapshot.last7Days.measuredDays,7);
  assert.equal(snapshot.last7Days.complete,false,'the first day began at noon');
  f.setTime('2026-11-10T03:00:00Z');
  await f.post(view(browser));
  snapshot=(await f.get()).data;
  assert.equal(snapshot.pageViews,8);
  assert.equal(snapshot.last7Days.pageViews,7);
  assert.equal(snapshot.last7Days.startDateJst,'2026-11-04');
  assert.equal(snapshot.last7Days.endDateJst,'2026-11-10');
  assert.equal(snapshot.last7Days.measuredDays,7);
  assert.equal(snapshot.last7Days.complete,true,'coverage starts before this window\'s first midnight');
  assert.equal(snapshot.today.pageViewsCoverage,'in-progress');
  assert.equal(snapshot.yesterday.pageViewsCoverage,'complete');
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
