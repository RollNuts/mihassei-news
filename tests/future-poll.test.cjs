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
  const api=async(action,clientId,article='future-story',choice,channel)=>{
    const body={action,clientId,article};
    if(choice!==undefined)body.choice=choice;
    if(channel!==undefined)body.channel=channel;
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement',{
      method:'POST',
      headers:{'content-type':'application/json'},
      body:JSON.stringify(body)
    }));
    return {status:response.status,result:await response.json()};
  };
  const get=async(articles='future-story')=>{
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?articles='+articles));
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

test('vote frontend never fakes figures and handles server rejection',()=>{
  const js=fs.readFileSync(path.join(__dirname,'../assets/engagement.js'),'utf8');
  assert.match(js,/selectedPoll=result\.vote/);
  assert.match(js,/renderPoll\(result\.votes\)/);
  assert.match(js,/pollError=/);
  assert.match(js,/data-poll-results/);
  assert.doesNotMatch(js,/\.innerHTML\s*=/);
});
