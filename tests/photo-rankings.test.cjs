'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {DatabaseSync}=require('node:sqlite');
const {randomUUID,webcrypto}=require('node:crypto');
const workerSource=fs.readFileSync(path.join(__dirname,'../src/worker.js'),'utf8')
  .replace('import { DurableObject } from "cloudflare:workers";',
    'class DurableObject { constructor(ctx){this.ctx=ctx;} }')
  .replace('export { ReaderComments } from "./comments.js";','')
  .replace('export class NewsroomStats','class NewsroomStats')
  .replace('export default {','const serviceHandler = {');
const uiSource=fs.readFileSync(path.join(__dirname,'../assets/photo-rankings.js'),'utf8');
const key=slug=>'photo-issue:'+slug;

function fixture(t){
  let now=Date.parse('2026-11-15T01:00:00Z');
  class Clock extends Date {static now(){return now;}}
  const context=vm.createContext({crypto:webcrypto,TextEncoder,Response,Request,URL,Uint8Array,Date:Clock});
  vm.runInContext(workerSource,context);
  const Stats=vm.runInContext('NewsroomStats',context),db=new DatabaseSync(':memory:');
  t.after(()=>db.close());
  const queries=[];
  const sql={exec(query,...args){
    queries.push({query,args});
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
  async function get(params='view=public&photoPopular=1'){
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?'+params));
    return {status:response.status,data:await response.json()};
  }
  async function visit(article,browser=randomUUID()){
    const response=await stats.fetch(new Request('https://veripsa.com/api/engagement?response=ack',{
      method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({action:'visit',article,clientId:browser,viewId:randomUUID()})
    }));
    assert.equal(response.status,200);
  }
  const put=(counter,value)=>db.prepare('INSERT OR REPLACE INTO counters(key,value) VALUES (?,?)').run(counter,value);
  return {db,stats,queries,get,visit,put,setTime(value){now=Date.parse(value);},
    handler:vm.runInContext('serviceHandler',context)};
}

test('photo ranking reads actual repeated displays, independent of news and browser counts',async t=>{
  const f=fixture(t),browser=randomUUID();
  await f.visit(key('issue-b'),browser);
  await f.visit(key('issue-b'),browser);
  await f.visit(key('issue-a'));
  for(let i=0;i<5;i++)await f.visit('story-a');
  f.put('likes:'+key('issue-a'),1000);
  f.put('readers:'+key('issue-a'),1000);
  f.put('predictions:plausible:'+key('issue-a'),1000);
  const changes=f.db.prepare('SELECT total_changes() AS n').get().n;
  const {status,data}=await f.get('view=public&photoPopular=1&popular=1');
  assert.equal(status,200);
  assert.deepEqual(data.topPhotoIssues.last7Days,[
    {article:key('issue-b'),pageViews:2,todayPageViews:2},
    {article:key('issue-a'),pageViews:1,todayPageViews:1}
  ]);
  assert.deepEqual(data.topArticles.last7Days,[{article:'story-a',pageViews:5,todayPageViews:5}]);
  assert.deepEqual(data.articles,{},'ranking does not request every issue metric');
  assert.equal(data.pageViewTracking.unit,'page-view');
  assert.equal(data.unit,'anonymous-browser','legacy unit remains intact');
  assert.equal(f.stats.count('readers:'+key('issue-b')),1,'one browser can make two PV');
  assert.equal(f.db.prepare('SELECT total_changes() AS n').get().n,changes,'GET performs no writes');
  for(const privateField of ['hourly','pageViewSources','pageViewKinds','sessionTracking','channels'])
    assert.equal(privateField in data,false);
});

test('photo ranking is opt-in and leaves existing public news responses unchanged',async t=>{
  const f=fixture(t);
  await f.visit(key('issue-a'));await f.visit('story-a');
  const before=(await f.get('view=public&popular=1')).data;
  let calls=0;
  const original=f.stats.topPhotoIssueRowsForWeek.bind(f.stats);
  f.stats.topPhotoIssueRowsForWeek=(...args)=>{calls++;return original(...args);};
  for(const params of ['view=public','view=public&photoPopular=0','view=public&photoPopular=true',
                       'articles=story-a','photoPopular=1']){
    const {data}=await f.get(params);
    assert.equal('topPhotoIssues' in data,false);
  }
  assert.equal(calls,0,'unrelated requests never run the photo aggregate');
  const after=(await f.get('view=public&popular=1&photoPopular=1')).data;
  delete after.topPhotoIssues;
  assert.deepEqual(after,before);
  assert.equal(calls,1);
});

test('photo ranking uses today plus the previous six JST dates, not lifetime or future counters',async t=>{
  const f=fixture(t);
  f.setTime('2026-11-08T15:00:00Z'); // Nov 9, JST: first included date.
  await f.visit(key('issue-a'));
  f.setTime('2026-11-15T01:00:00Z');
  await f.visit(key('issue-a'));await f.visit(key('issue-b'));
  for(const date of ['2026-11-08','2026-11-16'])
    f.put('day:'+date+':pageViews:'+key('outside'),500);
  f.put('pageViews:'+key('lifetime-only'),2000);
  f.put('day:2026-11-15:pageViews:photo-issues:wrong-prefix',3000);
  f.put('day:2026-11-15:pageViews:photo-issue:../bad',3000);
  const {data}=await f.get();
  assert.equal(data.last7Days.startDateJst,'2026-11-09');
  assert.equal(data.last7Days.endDateJst,'2026-11-15');
  assert.deepEqual(data.topPhotoIssues.last7Days,[
    {article:key('issue-a'),pageViews:2,todayPageViews:1},
    {article:key('issue-b'),pageViews:1,todayPageViews:1}
  ]);
});

test('photo aggregate is prefix-only and capped at twenty before unrelated news traffic',async t=>{
  const f=fixture(t);await f.visit('story-a');
  for(let i=0;i<30;i++){
    f.put('day:2026-11-15:pageViews:'+key('issue-'+String(i).padStart(2,'0')),i+1);
    f.put('day:2026-11-15:pageViews:news-'+i,5000);
  }
  const {data}=await f.get();
  const rows=data.topPhotoIssues.last7Days;
  assert.equal(rows.length,20);
  assert.equal(rows[0].article,key('issue-29'));
  assert.equal(rows.at(-1).article,key('issue-10'));
  assert.ok(rows.every(row=>row.article.startsWith('photo-issue:') && row.pageViews>0));
  const aggregate=f.queries.find(q=>q.query.includes('GROUP BY article') && q.args.at(-1)===20);
  assert.equal(aggregate.args.length,8);
  assert.ok(aggregate.args.slice(0,7).every(pattern=>pattern.endsWith(':pageViews:photo-issue:%')));
});

test('closed photo window returns exact prior seven JST dates and all observed issue keys',async t=>{
  const f=fixture(t);
  f.setTime('2026-11-07T15:00:00Z'); // Nov 8 at 00:00 JST, the first complete date.
  await f.visit(key('issue-a'));
  f.setTime('2026-11-14T00:00:00Z');await f.visit(key('issue-b'));
  f.setTime('2026-11-15T01:00:00Z');await f.visit(key('today-only'));
  f.put('day:2026-11-07:pageViews:'+key('too-old'),99);
  for(let i=0;i<25;i++)f.put('day:2026-11-14:pageViews:'+key('archive-'+i),i+1);
  f.put('day:2026-11-14:pageViews:news-story',10000);
  const before=f.db.prepare('SELECT total_changes() AS n').get().n;
  const {status,data}=await f.get('view=public&photoPopular=1&photoEnd=2026-11-14');
  assert.equal(status,200);
  assert.deepEqual(data.topPhotoIssues.window,{startDateJst:'2026-11-08',endDateJst:'2026-11-14',
    timezone:'Asia/Tokyo',unit:'page-view',measuredDays:7,complete:true});
  assert.equal(data.topPhotoIssues.version,1);
  assert.equal(data.topPhotoIssues.truncated,false);
  assert.equal(data.topPhotoIssues.issues.length,27,'editorial model totals are not limited to top twenty');
  assert.equal(data.topPhotoIssues.issues.some(row=>row.article===key('today-only')),false);
  assert.equal(data.topPhotoIssues.issues.some(row=>row.article===key('too-old')),false);
  assert.equal(data.topPhotoIssues.issues.some(row=>row.article==='news-story'),false);
  assert.equal('last7Days' in data.topPhotoIssues,false,'closed weeks cannot masquerade as rolling rankings');
  assert.equal(f.db.prepare('SELECT total_changes() AS n').get().n,before);
});

test('historical photo window rejects future, noncalendar, retained-range and non-opt-in requests',async t=>{
  const f=fixture(t);
  for(const end of ['2026-11-15','2026-11-16','2026-11-07','2026-02-30','2026-1-14','garbage'])
    assert.equal((await f.get('view=public&photoPopular=1&photoEnd='+end)).status,400,end);
  for(const params of ['photoEnd=2026-11-14','view=public&photoEnd=2026-11-14',
    'photoPopular=1&photoEnd=2026-11-14'])assert.equal((await f.get(params)).status,400);
  assert.equal((await f.get('view=public&photoPopular=1&photoEnd=2026-11-08')).status,200,
    'oldest complete retained seven-date window remains readable');
});

test('historical coverage never labels partial or absent tracking as a complete observed zero',async t=>{
  const f=fixture(t);
  const params='view=public&photoPopular=1&photoEnd=2026-11-14';
  let data=(await f.get(params)).data;
  assert.equal(data.topPhotoIssues.window.complete,false);
  assert.equal(data.topPhotoIssues.window.measuredDays,0);
  f.setTime('2026-11-08T00:01:00Z');await f.visit(key('issue-a'));
  f.setTime('2026-11-15T01:00:00Z');
  data=(await f.get(params)).data;
  assert.equal(data.topPhotoIssues.window.measuredDays,7);
  assert.equal(data.topPhotoIssues.window.complete,false,'a partial first date is not full seven-day coverage');
});

test('historical response flags its hard cap rather than making omitted issue keys look like zero',async t=>{
  const f=fixture(t);
  f.put('pageViews:startedAt',Date.parse('2026-11-07T15:00:00Z'));
  for(let i=0;i<1001;i++)f.put('day:2026-11-14:pageViews:'+key('issue-'+i),1);
  const data=(await f.get('view=public&photoPopular=1&photoEnd=2026-11-14')).data;
  assert.equal(data.topPhotoIssues.issues.length,1000);
  assert.equal(data.topPhotoIssues.truncated,true);
});

test('historical response marks omitted malformed counters instead of claiming a complete zero',async t=>{
  const f=fixture(t);
  f.put('pageViews:startedAt',Date.parse('2026-11-07T15:00:00Z'));
  f.put('day:2026-11-14:pageViews:photo-issue:../invalid',10);
  const data=(await f.get('view=public&photoPopular=1&photoEnd=2026-11-14')).data;
  assert.deepEqual(data.topPhotoIssues.issues,[]);
  assert.equal(data.topPhotoIssues.truncated,true);
});

test('zero, absent and unmeasured counters never create a photo rank',async t=>{
  const f=fixture(t);
  assert.deepEqual((await f.get()).data.topPhotoIssues.last7Days,[]);
  await f.visit('story-a');
  f.put('day:2026-11-15:pageViews:'+key('zero'),0);
  f.put('likes:'+key('likes-only'),300);
  assert.deepEqual((await f.get()).data.topPhotoIssues.last7Days,[]);
});

test('SQL failure returns the existing unavailable response without exposing internal details',async t=>{
  const f=fixture(t);await f.visit(key('issue-a'));
  f.stats.topPhotoIssueRowsForWeek=()=>{throw new Error('private database detail');};
  const response=await f.handler.fetch(new Request(
    'https://veripsa.com/api/engagement?view=public&photoPopular=1'
  ),{NEWSROOM_STATS:{idFromName:()=>0,get:()=>f.stats}});
  assert.equal(response.status,503);
  assert.deepEqual(await response.json(),{error:'engagement unavailable'});
});

const row=(slug,pageViews,todayPageViews=pageViews)=>({article:key(slug),pageViews,todayPageViews});
async function simulate(rows,options={}){
  const calls=[],items=[];
  const status={textContent:'閲覧ランキングを読み込み中です。'};
  for(const entry of options.candidates||['issue-a','issue-b','issue-c']){
    const slug=typeof entry==='string'?entry:entry.slug;
    const href=entry.href||'/photo/weekly/'+slug+'/';
    const link={textContent:entry.title||'モデル '+slug+'号',getAttribute:()=>href};
    const pv={textContent:''};
    const item={dataset:{rankingKey:key(slug)},value:0,link,pv,
      querySelector(selector){return selector==='a'?link:selector==='[data-ranking-pv]'?pv:null;}};
    items.push(item);
  }
  const list={hidden:true,children:items.slice(),querySelectorAll:()=>items,
    replaceChildren(...children){this.children=children;}};
  const section={querySelector(selector){
    return selector==='[data-ranking-status]'?status:selector==='[data-ranking-list]'?list:null;
  }};
  const payload=options.payload||{pageViewTracking:{unit:'page-view'},topPhotoIssues:{last7Days:rows}};
  const context=vm.createContext({URL,location:{origin:'https://veripsa.com'},
    document:{querySelector:()=>options.noTarget?null:section},
    fetch:async(url,init)=>{
      calls.push({url,init});
      if(options.networkError)throw new Error('offline');
      return {ok:!options.apiFails,json:async()=>{
        if(options.invalidJson)throw new Error('invalid JSON');
        return payload;
      }};
    }});
  vm.runInContext(uiSource,context,{timeout:1000});
  await new Promise(resolve=>setImmediate(resolve));
  return {calls,list,status,items};
}

test('photo frontend performs one public GET and displays only positive known published issues',async()=>{
  const result=await simulate([row('unpublished',90),row('issue-b',8),row('issue-a',2),
    row('issue-c',0),{article:'story-a',pageViews:200,todayPageViews:200}]);
  assert.equal(result.calls.length,1);
  assert.equal(result.calls[0].url,'/api/engagement?view=public&photoPopular=1');
  assert.equal(result.calls[0].init.method,'GET');
  assert.equal(new URL(result.calls[0].url,'https://veripsa.com').searchParams.get('articles'),null);
  assert.equal(result.list.hidden,false);
  assert.deepEqual(result.list.children.map(item=>item.dataset.rankingKey),[key('issue-b'),key('issue-a')]);
  assert.deepEqual(result.list.children.map(item=>item.pv.textContent),['8 PV','2 PV']);
  assert.match(result.status.textContent,/表示回数/);
});

test('equal PVs receive equal displayed ranks and retain authoritative server order',async()=>{
  const result=await simulate([row('issue-b',5),row('issue-a',5,1),row('issue-c',3)]);
  assert.deepEqual(result.list.children.map(item=>item.dataset.rankingKey),
    [key('issue-b'),key('issue-a'),key('issue-c')]);
  assert.deepEqual(result.list.children.map(item=>item.value),[1,1,3]);
});

test('a partial seven-date window states the actual measurement start in JST',async()=>{
  const result=await simulate([],{payload:{
    pageViewTracking:{unit:'page-view',startedAtUtc:'2026-11-14T15:01:00Z'},
    last7Days:{complete:false},topPhotoIssues:{last7Days:[row('issue-a',1)]}
  }});
  assert.equal(result.list.hidden,false);
  assert.match(result.status.textContent,/2026\/11\/15の計測開始から/);
});

test('photo frontend caps visible entries at ten and never adds an API-provided link or title',async()=>{
  const candidates=Array.from({length:25},(_,i)=>'issue-'+i);
  const result=await simulate(candidates.map((slug,i)=>({...row(slug,25-i),
    url:'https://evil.example/',title:'<img src=x onerror=alert(1)>'})),{candidates});
  assert.equal(result.list.children.length,10);
  assert.equal(result.list.children[0],result.items[0],'keeps the validated DOM node');
  assert.equal(result.list.children[0].link.textContent,'モデル issue-0号');
  assert.equal(result.list.children[0].link.getAttribute('href'),'/photo/weekly/issue-0/');
});

test('unsafe, inconsistent, duplicate and missing DOM candidates cannot become links',async()=>{
  const result=await simulate([row('issue-a',5),row('issue-a',5),row('issue-b',4),
    row('issue-c',3),row('../bad',2)],{candidates:[
      {slug:'issue-a',title:'<script>literal supplied title</script>'},'issue-a',
      {slug:'issue-b',href:'https://evil.example/photo/weekly/issue-b/'},
      {slug:'issue-c',href:'/photo/weekly/other/'},{slug:'../bad'}
    ]});
  assert.equal(result.list.children.length,1);
  assert.equal(result.list.children[0].link.textContent,'<script>literal supplied title</script>');
});

test('empty observations and all zero or invalid rows show a factual empty state',async()=>{
  for(const rows of [[],[row('issue-a',0),row('issue-b',null)],
    [row('unknown',50),row('issue-a',-1),row('issue-b',1.5),row('issue-c',3,4)]]){
    const result=await simulate(rows);
    assert.equal(result.list.hidden,true);
    assert.equal(result.list.children.length,0);
    assert.match(result.status.textContent,/まだありません/);
  }
});

test('request, JSON and non-authoritative failures display a retry message without a made-up order',async()=>{
  for(const options of [{apiFails:true},{networkError:true},{invalidJson:true},
    {payload:{unit:'anonymous-browser',topPhotoIssues:{last7Days:[row('issue-a',900)]}}},
    {payload:{pageViewTracking:{unit:'page-view'}}}]){
    const result=await simulate([row('issue-a',5)],options);
    assert.equal(result.list.hidden,true);
    assert.match(result.status.textContent,/再読み込み/);
    assert.equal(result.items.every(item=>item.value===0),true);
    assert.equal(result.calls.length,1);
  }
  const unsorted=await simulate([row('issue-a',1),row('issue-b',5)]);
  assert.equal(unsorted.list.hidden,true);
  assert.match(unsorted.status.textContent,/再読み込み/);
});

test('pages without the ranking target, or without published candidate issues, do not fetch',async()=>{
  assert.equal((await simulate([],{noTarget:true})).calls.length,0);
  const empty=await simulate([],{candidates:[]});
  assert.equal(empty.calls.length,0);
  assert.equal(empty.list.hidden,true);
  assert.match(empty.status.textContent,/まだありません/);
});
