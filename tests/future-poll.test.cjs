'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {DatabaseSync}=require('node:sqlite');
const {randomUUID,webcrypto}=require('node:crypto');

let src=fs.readFileSync(path.join(__dirname,'../src/worker.js'),'utf8');
src=src.replace('import { DurableObject } from "cloudflare:workers";',
  'class DurableObject { constructor(ctx){this.ctx=ctx;} }');
src=src.replace('export { ReaderComments } from "./comments.js";','');
src=src.replace('export class NewsroomStats','class NewsroomStats');
src=src.replace('export default {','const serviceHandler = {');
const context=vm.createContext({crypto:webcrypto,TextEncoder,Response,Request,URL,Uint8Array,console});
vm.runInContext(src,context);
const Stats=vm.runInContext('NewsroomStats',context);

function fixture(){
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
  const ctx={storage:{
    sql,
    transactionSync(fn){
      db.exec('BEGIN IMMEDIATE');
      try{const v=fn();db.exec('COMMIT');return v;}
      catch(e){db.exec('ROLLBACK');throw e;}
    }
  }};
  const stats=new Stats(ctx,{});
  const api=async(action,clientId,article='future-story',choice,channel,fromArticle)=>{
    const body={action,clientId,article};
    if(choice!==undefined)body.choice=choice;
    if(channel!==undefined)body.channel=channel;
    if(fromArticle!==undefined)body.fromArticle=fromArticle;
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement',{
      method:'POST',
      headers:{'content-type':'application/json'},
      body:JSON.stringify(body)
    }));
    return {status:response.status,result:await response.json()};
  };
  const get=async(articles='future-story',day)=>{
    const params=new URLSearchParams({articles});
    if(day!==undefined)params.set('day',day);
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?'+params));
    return {status:response.status,result:await response.json()};
  };
  return {db,api,get};
}

test('each browser may cast one immutable vote per article, and retries are idempotent',async()=>{
  const {db,api,get}=fixture();
  const a=randomUUID(),b=randomUUID();
  const first=await api('vote',a,'future-story','plausible');
  assert.equal(first.status,200);
  assert.equal(first.result.vote,'plausible');
  assert.equal(first.result.alreadyVoted,false);
  assert.equal(first.result.votes.plausible,1);
  assert.equal(first.result.votes.unlikely,0);
  const retry=await api('vote',a,'future-story','plausible');
  assert.equal(retry.result.alreadyVoted,true);
  assert.equal(retry.result.votes.plausible,1);
  const switchAttempt=await api('vote',a,'future-story','unlikely');
  assert.equal(switchAttempt.result.alreadyVoted,true);
  assert.equal(switchAttempt.result.vote,'plausible');
  assert.equal(switchAttempt.result.votes.unlikely,0);
  const second=await api('vote',b,'future-story','unlikely');
  assert.equal(second.result.alreadyVoted,false);
  assert.equal(second.result.votes.unlikely,1);
  const metrics=await get('future-story,other-story');
  assert.equal(metrics.result.articles['future-story'].votes.plausible,1);
  assert.equal(metrics.result.articles['future-story'].votes.unlikely,1);
  assert.equal(metrics.result.articles['other-story'].votes.plausible,0);
  assert.equal(metrics.result.articles['other-story'].votes.unlikely,0);
  const other=await api('vote',a,'other-story','unlikely');
  assert.equal(other.result.votes.unlikely,1);
  assert.equal((await get('future-story')).result.articles['future-story'].votes.unlikely,1);
  const saved=db.prepare("SELECT visitor FROM events WHERE kind='prediction:plausible'").get();
  assert.notEqual(saved.visitor,a);
  assert.match(saved.visitor,/^[a-f0-9]{64}$/);
  db.close();
});

test('vote validation rejects invalid options and requires an article',async()=>{
  const {db,api,get}=fixture();
  const id=randomUUID();
  assert.equal((await api('vote',id,'future-story','probably')).status,400);
  assert.equal((await api('vote',id,'future-story',undefined)).status,400);
  assert.equal((await api('vote',id,'','plausible')).status,400);
  assert.equal((await api('vote',id,'../broken','plausible')).status,400);
  assert.equal((await api('vote','not-a-uuid','future-story','plausible')).status,400);
  const counts=(await get()).result.articles['future-story'].votes;
  assert.equal(counts.plausible,0);assert.equal(counts.unlikely,0);
  db.close();
});

test('existing visit, like and sharing counters remain unchanged',async()=>{
  const {db,api,get}=fixture();
  const id=randomUUID();
  assert.equal((await api('visit',id,'future-story',undefined,'x')).status,200);
  assert.equal((await api('like',id)).result.likes,1);
  assert.equal((await api('share',id)).result.shareClicks,1);
  assert.equal((await api('vote',id,'future-story','plausible')).result.votes.plausible,1);
  assert.equal((await api('like',id)).result.likes,0);
  const result=(await get()).result;
  assert.equal(result.articles['future-story'].readers,1);
  assert.equal(result.articles['future-story'].shareClicks,1);
  assert.equal(result.articles['future-story'].likes,0);
  assert.equal(result.articles['future-story'].votes.plausible,1);
  assert.equal(result.channels.x,1);
  assert.equal(result.visitors,1);
  db.close();
});

test('X/search entry counts are per-article browser IDs, with strict deduplication',async()=>{
  const {db,api,get}=fixture();
  const a=randomUUID(),b=randomUUID(),c=randomUUID();
  for(let i=0;i<3;i++)assert.equal((await api('visit',a,'story-a',undefined,'x')).status,200);
  assert.equal((await api('visit',b,'story-a',undefined,'search')).status,200);
  assert.equal((await api('visit',a,'story-b',undefined,'x')).status,200);
  // Same browser can re-enter the same article via another source.
  assert.equal((await api('visit',a,'story-a',undefined,'search')).status,200);
  // Unknown or internal visits never count as an X/search arrival.
  assert.equal((await api('visit',c,'story-b',undefined,'external')).status,200);
  assert.equal((await api('visit',c,'story-a')).status,200);
  // A landing on the homepage is a sitewide channel, not an article entry.
  assert.equal((await api('visit',b,'',undefined,'x')).status,200);
  const res=(await get('story-a,story-b,empty')).result;
  assert.equal(res.articles['story-a'].arrivals.x,1);
  assert.equal(res.articles['story-a'].arrivals.search,2);
  assert.equal(res.articles['story-b'].arrivals.x,1);
  assert.equal(res.articles['story-b'].arrivals.search,0);
  assert.equal(res.articles['empty'].arrivals.x,0);
  assert.equal(res.articles['empty'].arrivals.search,0);
  assert.equal(res.articles['story-a'].readers,3);
  assert.equal(res.channels.x,2,'sitewide X count must still dedupe browser IDs');
  assert.equal(res.channels.search,2,'sitewide search count remains independent');
  const stored=db.prepare("SELECT kind,article,visitor FROM events WHERE kind LIKE 'arrival:%'").all();
  assert.equal(stored.length,4);
  assert.ok(stored.every(x=>['arrival:x','arrival:search'].includes(x.kind)));
  assert.ok(stored.every(x=>x.article.startsWith('story-')));
  assert.ok(stored.every(x=>/^[a-f0-9]{64}$/.test(x.visitor)));
  assert.ok(!stored.some(x=>[a,b,c].includes(x.visitor)));
  db.close();
});

test('invalid arrival channel never writes a per-article counter',async()=>{
  const {db,api,get}=fixture();
  const id=randomUUID();
  const bad=await api('visit',id,'story-a',undefined,'x;https://example.org/private');
  assert.equal(bad.status,400);
  const summary=(await get('story-a')).result.articles['story-a'];
  assert.equal(summary.arrivals.x,0);
  assert.equal(summary.arrivals.search,0);
  db.close();
});

test('vote frontend never fakes figures and handles server rejection',()=>{
  const js=fs.readFileSync(path.join(__dirname,'../assets/engagement.js'),'utf8');
  assert.match(js,/selectedPoll=result\.vote/);
  assert.match(js,/renderPoll\(result\.votes\)/);
  assert.match(js,/pollError=/);
  assert.match(js,/data-poll-results/);
  assert.doesNotMatch(js,/\.innerHTML\s*=/);
});

test('article continuation counts source browsers, not clicks, destinations or raw URLs',async()=>{
  const {db,api,get}=fixture();
  const a=randomUUID(),b=randomUUID();
  const first=await api('visit',a,'story-b',undefined,undefined,'story-a');
  assert.equal(first.status,200);
  assert.equal((await get('story-a')).result.articles['story-a'].continuedReaders,1);
  // Same browser navigating into a third article must not inflate source A.
  assert.equal((await api('visit',a,'story-c',undefined,undefined,'story-a')).status,200);
  assert.equal((await get('story-a')).result.articles['story-a'].continuedReaders,1);
  assert.equal((await api('visit',b,'story-c',undefined,undefined,'story-a')).status,200);
  assert.equal((await get('story-a')).result.articles['story-a'].continuedReaders,2);
  assert.equal((await api('visit',a,'story-a',undefined,undefined,'story-b')).status,200);
  assert.equal((await get('story-b')).result.articles['story-b'].continuedReaders,1);
  assert.equal((await get('story-c')).result.articles['story-c'].continuedReaders,0);
  assert.equal((await get('story-a')).result.articles['story-a'].readers,1);
  assert.equal((await get('story-a')).result.visitors,2);
  const stored=db.prepare("SELECT visitor FROM events WHERE kind='continued' AND article='story-a'").all();
  assert.equal(stored.length,2);
  assert.ok(stored.every(x=>/^[a-f0-9]{64}$/.test(x.visitor)));
  assert.ok(!stored.some(x=>x.visitor===a||x.visitor===b));
  db.close();
});

test('untrusted source strings and invalid continuation events cannot write counters',async()=>{
  const {db,api,get}=fixture();
  const id=randomUUID();
  for(const [action,article,fromArticle] of [
    ['visit','story-b','../story-a'],['visit','story-b','story-b'],
    ['visit','','story-a'],['visit','story-b','n'.repeat(101)],
    ['like','story-b','story-a'],['vote','story-b','story-a']
  ]){
    const result=await api(action,id,article,action==='vote'?'plausible':undefined,undefined,fromArticle);
    assert.equal(result.status,400,action+': '+fromArticle);
  }
  assert.equal((await get('story-a')).result.articles['story-a'].continuedReaders,0);
  db.close();
});


test('daily browser and article counters stay honest, reset in JST and expire old dedupe data',async()=>{
  // The server clock (not user-supplied time) determines the JST calendar day.
  const runtimeDate=vm.runInContext('Date',context);
  const previousNow=runtimeDate.now;
  let now=Date.parse('2026-11-03T14:58:00Z'); // Nov 3, 23:58 JST
  runtimeDate.now=()=>now;
  const {db,api,get}=fixture();
  try{
    const a=randomUUID(),b=randomUUID();
    const empty=(await get('story-a')).result;
    assert.deepEqual(JSON.parse(JSON.stringify(empty.today)),{
      dateJst:'2026-11-03',visitors:0,articleReads:0,
      pageViews:null,pageViewsCoverage:'unmeasured',
      sessions:null,sessionsCoverage:'unmeasured'
    });

    await api('visit',a,''); // Home page only (no article slug)
    await api('visit',a,'story-a');
    await api('visit',a,'story-a'); // Reload must not inflate
    await api('visit',a,'story-b');
    await api('visit',b,'story-a');
    const oneDay=(await get('story-a,story-b')).result;
    assert.equal(oneDay.today.visitors,2);
    assert.equal(oneDay.today.articleReads,3,
      'one browser reading two distinct stories counts as two article reads');
    assert.equal(oneDay.articles['story-a'].todayReaders,2);
    assert.equal(oneDay.articles['story-b'].todayReaders,1);
    assert.equal(oneDay.visitors,2,'original cumulative remains unchanged');

    now=Date.parse('2026-11-03T15:02:00Z'); // Nov 4, 00:02 JST
    const midnight=(await get('story-a')).result;
    assert.equal(midnight.today.dateJst,'2026-11-04');
    assert.equal(midnight.today.visitors,0,'today resets without a visit');
    assert.equal(midnight.today.articleReads,0);
    assert.deepEqual(JSON.parse(JSON.stringify(midnight.yesterday)),{
      dateJst:'2026-11-03',visitors:2,articleReads:3,
      pageViews:null,pageViewsCoverage:'unmeasured',
      sessions:null,sessionsCoverage:'unmeasured'
    });
    assert.equal(midnight.articles['story-a'].todayReaders,0);
    const historical=await get('story-a','2026-11-03');
    assert.equal(historical.status,200);
    assert.deepEqual(JSON.parse(JSON.stringify(historical.result.day)),{
      dateJst:'2026-11-03',visitors:2,articleReads:3,
      pageViews:null,pageViewsCoverage:'unmeasured',
      sessions:null,sessionsCoverage:'unmeasured'
    });
    for(const invalid of ['2026-11-31','2026-11-05','2026-10-01','2026-11-03T00:00','NaN']){
      assert.equal((await get('story-a',invalid)).status,400,
        'invalid/out-of-retention days must fail instead of reporting zero: '+invalid);
    }
    await api('visit',a,'story-a');
    const next=(await get('story-a')).result;
    assert.equal(next.today.visitors,1,'same browser can count again next day');
    assert.equal(next.today.articleReads,1);
    assert.equal(next.visitors,2,'lifetime browser dedupe is unchanged');

    now=Date.parse('2026-11-20T00:00:00Z');
    await api('visit',b,'story-b'); // Prune daily rows older than 14 days
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM events WHERE kind LIKE 'day:2026-11-03:%'").get().n,0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM counters WHERE key LIKE 'day:2026-11-03:%'").get().n,0);
    assert.equal((await get('story-a')).result.visitors,2,
      'purging daily data must not erase lifetime totals');
    assert.equal((await get('story-a','2026-11-03')).status,400,
      'pruned days must not look like genuine zero-visit days');
  }finally{
    runtimeDate.now=previousNow;
    db.close();
  }
});

