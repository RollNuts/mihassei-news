'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const upstream='https://api.github.com/repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml';
const workflowPath='.github/workflows/free-cpu-scenes.yml';
const source=fs.readFileSync(path.join(__dirname,'../src/worker.js'),'utf8')
  .replace('import { DurableObject } from "cloudflare:workers";','class DurableObject {}')
  .replace('import { imageControlMcp } from "./image_control_mcp.mjs";',
    fs.readFileSync(path.join(__dirname,'../src/image_control_mcp.mjs'),'utf8').replace('export async function','async function'))
  .replace('export { ReaderComments } from "./comments.js";','')
  .replace('export class NewsroomStats','class NewsroomStats')
  .replace('export default {','const serviceHandler = {');

function fixture(fetchImpl=async()=>Response.json({path:workflowPath,state:'active'})) {
  const requests=[],timers=new Set();
  let now=Date.parse('2026-11-16T07:00:00Z');
  class Clock extends Date {
    constructor(...args){super(...(args.length ? args : [now]));}
    static now(){return now;}
  }
  const context=vm.createContext({Response,Request,URL,Uint8Array,TextDecoder,
    AbortController,Date:Clock,
    fetch:async(url,options)=>{
      // Workers Request accepts only follow/manual, unlike Node's fetch.
      // Keep this runtime contract in the stub so an unsupported option cannot
      // appear to pass all endpoint tests while failing before a real request.
      if(!['follow','manual'].includes(options.redirect))
        throw new TypeError('Workers supports only follow/manual redirect modes');
      requests.push({url,options});
      return fetchImpl(url,options);
    },
    setTimeout(fn,ms){const timer={fn,ms};timers.add(timer);return timer;},
    clearTimeout(timer){timers.delete(timer);}
  });
  vm.runInContext(source,context);
  const service=vm.runInContext('serviceHandler',context);
  return {
    requests,timers,
    advance(ms){now+=ms;},
    timeout(){assert.equal(timers.size,1);const [timer]=timers;assert.equal(timer.ms,3000);timer.fn();},
    get(uri='/api/image-control?nonce=Fresh1',options={},env={}) {
      return service.fetch(new Request('https://veripsa.com'+uri,options),env);
    }
  };
}
function privateHeaders(response) {
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('x-robots-tag'),'noindex');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  assert.equal(response.headers.get('access-control-allow-origin'),null);
}
async function unavailable(f,reason) {
  const response=await f.get();
  assert.equal(response.status,503);
  assert.equal(await response.text(),'{"error":"image control unavailable"}');
  if(reason!==undefined)assert.equal(response.headers.get('x-image-control-reason'),reason);
  for(const header of ['authorization','x-private','x-ratelimit-remaining','x-ratelimit-reset','retry-after'])
    assert.equal(response.headers.get(header),null);
  privateHeaders(response);
  assert.equal(f.requests.length,1);
  assert.equal(f.timers.size,0);
}

test('public control GET exposes only the checked workflow state and echoed nonce',async()=>{
  for(const state of ['active','deleted','disabled_fork','disabled_inactivity','disabled_manually']) {
    const f=fixture(async()=>Response.json({path:workflowPath,state,name:'not exposed',id:123}));
    const response=await f.get();
    assert.equal(response.status,200);
    assert.deepEqual(await response.json(),{version:1,repository:'RollNuts/mihassei-news',
      path:workflowPath,state,nonce:'Fresh1',checked_at:'2026-11-16T07:00:00.000Z'});
    privateHeaders(response);
    assert.equal(response.headers.get('x-image-control-reason'),null);
    assert.equal(f.requests.length,1);
    const request=f.requests[0];
    assert.equal(request.url,upstream);
    assert.equal(request.options.method,'GET');
    assert.equal(request.options.redirect,'manual');
    assert.equal(request.options.cache,'no-store');
    assert.equal(request.options.headers['cache-control'],'no-cache');
    assert.match(request.options.headers['user-agent'],/mihassei/);
    assert.equal(request.options.headers.authorization,undefined);
    assert.ok(request.options.signal instanceof AbortSignal);
    assert.equal(f.timers.size,0);
  }
});

test('each nonce performs a fresh read, including a stop after an active response',async()=>{
  let state='active',rateLimited=false;
  const f=fixture(async()=>rateLimited ? new Response('',{status:429}) : Response.json({path:workflowPath,state}));
  const first=await f.get('/api/image-control?nonce=A');
  assert.equal((await first.json()).state,'active');
  state='disabled_manually';
  f.advance(1000);
  const second=await f.get('/api/image-control?nonce='+'z'.repeat(64));
  assert.deepEqual(await second.json(),{version:1,repository:'RollNuts/mihassei-news',
    path:workflowPath,state,nonce:'z'.repeat(64),checked_at:'2026-11-16T07:00:01.000Z'});
  rateLimited=true;
  const third=await f.get('/api/image-control?nonce=C');
  assert.equal(third.status,503);
  assert.deepEqual(await third.json(),{error:'image control unavailable'});
  assert.equal(f.requests.length,3);
});

test('invalid, duplicate and unbounded nonce values never contact GitHub',async()=>{
  for(const query of ['', '?nonce=', '?nonce='+ 'a'.repeat(65), '?nonce=a-b',
    '?nonce=%0A', '?nonce=%EF%BC%91', '?nonce=a&nonce=b', '?nonce=a&url=https://evil.example/']) {
    const f=fixture();
    const response=await f.get('/api/image-control'+query);
    assert.equal(response.status,400,query);
    assert.deepEqual(await response.json(),{error:'invalid nonce'});
    privateHeaders(response);
    assert.equal(f.requests.length,0);
    assert.equal(f.timers.size,0);
  }
});

test('control endpoint is read-only and accepts no preflight or write methods',async()=>{
  for(const method of ['HEAD','POST','PUT','DELETE','OPTIONS']) {
    const f=fixture();
    const response=await f.get(undefined,{method});
    assert.equal(response.status,405);
    assert.equal(response.headers.get('allow'),'GET');
    privateHeaders(response);
    assert.equal(f.requests.length,0);
  }
});

test('missing, malformed or unrelated workflow metadata cannot return active',async()=>{
  for(const payload of [{path:workflowPath}, {state:'active'},
    {path:'.github/workflows/other.yml',state:'active'},
    {path:workflowPath,state:''}, {path:workflowPath,state:'unknown'},
    {path:workflowPath,state:['active']}, null, 'active']) {
    await unavailable(fixture(async()=>Response.json(payload)),'invalid_response');
  }
  await unavailable(fixture(async()=>new Response('invalid json')),'invalid_response');
  await unavailable(fixture(async()=>{throw new Error('private network failure');}),'runtime_error');
});

test('upstream failures and rate limits return a minimal 503 without retrying',async()=>{
  for(const status of [301,302,303,307,308,403,404,429,500,503]) {
    await unavailable(fixture(async()=>new Response('private upstream detail',{
      status,headers:{location:'https://evil.example/', 'retry-after':'60','x-private':'private header'}
    })),status===403 ? 'github_http_403' : status===429 ? 'github_http_429'
      : status>=500 ? 'github_http_5xx' : 'github_http_other');
  }
});

test('only a 403 or 429 with exact zero remaining proves the GitHub quota reason',async()=>{
  for(const status of [403,429]) {
    for(const remaining of ['0','1','00','unknown'])
      await unavailable(fixture(async()=>new Response('private quota detail',{
        status,headers:{'x-ratelimit-remaining':remaining,'x-ratelimit-reset':'private reset'}
      })),remaining==='0' ? 'github_rate_limit' : 'github_http_'+status);
  }
  await unavailable(fixture(async()=>new Response('',{status:500,headers:{'x-ratelimit-remaining':'0'}})),
    'github_http_5xx');
});

test('workflow response body is bounded with or without content-length',async()=>{
  let advertisedBodyRead=false;
  await unavailable(fixture(async()=>({ok:true,headers:new Headers({'content-length':'16385'}),
    get body(){advertisedBodyRead=true;throw new Error('body must not be read');}})),'invalid_response');
  assert.equal(advertisedBodyRead,false);
  let cancelled=false;
  await unavailable(fixture(async()=>new Response(new ReadableStream({
    start(controller){controller.enqueue(new Uint8Array(16385));},
    cancel(){cancelled=true;}
  }))), 'invalid_response');
  assert.equal(cancelled,true);
  await unavailable(fixture(async()=>new Response(new ReadableStream({
    start(controller){controller.error(new Error('body interrupted'));}
  }))), 'invalid_response');
});

test('deadline bounds both connection and response body reads',async()=>{
  let connectionSignal;
  const connection=fixture(async(_,options)=>{
    connectionSignal=options.signal;
    return new Promise(()=>{});
  });
  const connecting=unavailable(connection,'timeout');
  connection.timeout();
  await connecting;
  assert.equal(connectionSignal.aborted,true);

  let bodySignal,reading;
  const bodyStarted=new Promise(resolve=>{reading=resolve;});
  const body=fixture(async(_,options)=>{
    bodySignal=options.signal;
    return new Response(new ReadableStream({pull(){reading();return new Promise(()=>{});}}));
  });
  const consuming=unavailable(body,'timeout');
  await bodyStarted;
  body.timeout();
  await consuming;
  assert.equal(bodySignal.aborted,true);
});

test('site assets and existing API routing and same-origin protections are unchanged',async()=>{
  const f=fixture();
  const calls=[];
  const binding=name=>({idFromName(id){return name+':'+id;},get(id){
    return {fetch(req){calls.push({name,id,request:req});return new Response(name);}};
  }});
  const env={ASSETS:{fetch(req){calls.push({name:'assets',request:req});return new Response('asset');}},
    NEWSROOM_STATS:binding('engagement'),READER_COMMENTS:binding('comments')};
  for(const uri of ['/', '/api/unrelated?nonce=A', '/api/image-control/?nonce=A']) {
    assert.equal(await (await f.get(uri,{},env)).text(),'asset');
    assert.equal(calls.at(-1).request.url,'https://veripsa.com'+uri);
  }
  assert.equal(await (await f.get('/api/engagement',{},env)).text(),'engagement');
  assert.equal(calls.at(-1).id,'engagement:mihassei-main-v1');
  for(const uri of ['/api/comments','/api/comments/summary','/api/comments/admin']) {
    assert.equal(await (await f.get(uri,{},env)).text(),'comments');
    assert.equal(calls.at(-1).id,'comments:mihassei-comments-main-v1');
  }
  const before=calls.length;
  const post=await f.get('/api/engagement',{method:'POST'},env);
  assert.equal(post.status,403);
  assert.deepEqual(await post.json(),{error:'same origin only'});
  assert.equal(calls.length,before);
  assert.equal(f.requests.length,0);
});
