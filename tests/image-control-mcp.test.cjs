'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const upstream='https://api.github.com/repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml';
const workflowPath='.github/workflows/free-cpu-scenes.yml';
const endpoint='https://veripsa.com/api/image-control/mcp';
const serverReadToken='test_only_server_read_token_0123456789'; // Synthetic fixture, not a credential.
const adapter=fs.readFileSync(path.join(__dirname,'../src/image_control_mcp.mjs'),'utf8')
  .replace('export async function','async function');
const source=fs.readFileSync(path.join(__dirname,'../src/worker.js'),'utf8')
  .replace('import { DurableObject } from "cloudflare:workers";','class DurableObject {}')
  .replace('import { imageControlMcp } from "./image_control_mcp.mjs";',adapter)
  .replace('export { ReaderComments } from "./comments.js";','')
  .replace('export class NewsroomStats','class NewsroomStats')
  .replace('export default {','const serviceHandler = {');
const call=(nonce='Fresh1')=>({jsonrpc:'2.0',id:1,method:'tools/call',
  params:{name:'read_image_control',arguments:{nonce}}});
function fixture(fetchImpl=async()=>Response.json({path:workflowPath,state:'active'})) {
  const requests=[],timers=new Set();
  let now=Date.parse('2026-11-16T07:00:00Z');
  class Clock extends Date {
    constructor(...args){super(...(args.length ? args : [now]));}
    static now(){return now;}
  }
  const context=vm.createContext({Response,Request,URL,Uint8Array,TextDecoder,AbortController,Date:Clock,
    fetch:async(url,options)=>{
      assert.equal(options.redirect,'manual');
      requests.push({url,options});
      return fetchImpl(url,options);
    },
    setTimeout(fn,ms){const timer={fn,ms};timers.add(timer);return timer;},
    clearTimeout(timer){timers.delete(timer);}
  });
  vm.runInContext(source,context);
  const service=vm.runInContext('serviceHandler',context);
  return {requests,timers,context,
    advance(ms){now+=ms;},
    async send(message,{method='POST',headers={},raw,uri=endpoint,env={}}={}) {
      const allHeaders=new Headers({'content-type':'application/json',
        accept:'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'});
      for(const [key,value] of Object.entries(headers))value===null ? allHeaders.delete(key) : allHeaders.set(key,value);
      const options={method,headers:allHeaders};
      if(!['GET','HEAD'].includes(method)) {
        options.body=raw===undefined ? JSON.stringify(message) : raw;
        if(raw instanceof ReadableStream)options.duplex='half';
      }
      return service.fetch(new Request(uri,options),env);
    }
  };
}
function privateHeaders(response) {
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('x-robots-tag'),'noindex');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  assert.equal(response.headers.get('access-control-allow-origin'),null);
  assert.equal(response.headers.get('mcp-session-id'),null);
}
async function json(response,status=200) {
  assert.equal(response.status,status);
  privateHeaders(response);
  return response.json();
}

test('stateless handshake negotiates only implemented revisions and never fetches workflow metadata',async()=>{
  const f=fixture();
  for(const version of ['2025-06-18','2025-11-25','2025-03-26','2026-07-28']) {
    const data=await json(await f.send({jsonrpc:'2.0',id:'init',method:'initialize',params:{
      protocolVersion:version,capabilities:{},clientInfo:{name:'test-client',version:'1'}}},
    {headers:{'mcp-protocol-version':null}}));
    assert.deepEqual(data,{jsonrpc:'2.0',id:'init',result:{
      protocolVersion:['2025-06-18','2025-11-25'].includes(version) ? version : '2025-11-25',
      capabilities:{tools:{}},serverInfo:{name:'mihassei-image-control',version:'1.0.0'}}});
  }
  const initialized=await f.send({jsonrpc:'2.0',method:'notifications/initialized'});
  assert.equal(initialized.status,202);
  assert.equal(await initialized.text(),'');
  privateHeaders(initialized);
  assert.deepEqual(await json(await f.send({jsonrpc:'2.0',id:0,method:'ping'})),
    {jsonrpc:'2.0',id:0,result:{}});
  assert.equal(f.requests.length,0);
  assert.equal(f.timers.size,0);
});

test('tools/list exposes exactly one read-only fixed-input tool, with no credentials or generation tools',async()=>{
  const f=fixture();
  const data=await json(await f.send({jsonrpc:'2.0',id:2,method:'tools/list'}));
  assert.deepEqual(data,{jsonrpc:'2.0',id:2,result:{tools:[{
    name:'read_image_control',
    description:'Read the current public image workflow stop state. Supply a fresh nonce for every call; only active permits image operations.',
    inputSchema:{type:'object',properties:{nonce:{type:'string',pattern:'^[A-Za-z0-9]{1,64}$'}},
      required:['nonce'],additionalProperties:false},
    annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:true}
  }]}});
  assert.equal(f.requests.length,0);
});

test('tool calls reuse the exact current GET reader, including disabled states and fresh nonce/time',async()=>{
  let state='active';
  const f=fixture(async()=>Response.json({path:workflowPath,state,secret:'upstream-private-data'}));
  for(const next of ['active','disabled_manually','disabled_inactivity','disabled_fork','deleted']) {
    state=next;
    const nonce=state==='active' ? 'A' : 'z'.repeat(64);
    const data=await json(await f.send(call(nonce),{headers:{authorization:'Bearer caller-secret'},
      env:{GITHUB_TOKEN:'environment-secret'}}));
    const control={version:1,repository:'RollNuts/mihassei-news',path:workflowPath,state,
      nonce,checked_at:new Date(Date.parse('2026-11-16T07:00:00Z')+1000*(f.requests.length-1)).toISOString()};
    assert.deepEqual(data,{jsonrpc:'2.0',id:1,result:{content:[{type:'text',text:JSON.stringify(control)}],
      structuredContent:control,isError:false}});
    assert.equal(f.requests.at(-1).url,upstream);
    const options=f.requests.at(-1).options;
    assert.equal(options.method,'GET');
    assert.equal(options.cache,'no-store');
    assert.equal(options.headers.authorization,undefined);
    assert.equal(options.headers['cache-control'],'no-cache');
    assert.deepEqual(Object.keys(options.headers).sort(),['accept','cache-control','user-agent','x-github-api-version']);
    assert.doesNotMatch(JSON.stringify(data),/private-data|caller-secret|environment-secret/);
    assert.equal(f.timers.size,0);
    f.advance(1000);
  }
  assert.equal(f.requests.length,5);
});

test('invalid nonce, extra arguments, unknown tools and notifications never fetch',async()=>{
  const f=fixture();
  for(const nonce of ['', 'a-b', 'a'.repeat(65),123,null]) {
    const data=await json(await f.send(call(nonce)));
    assert.equal(data.result.isError,true);
    assert.equal(data.result.structuredContent,undefined);
  }
  const extra=call();extra.params.arguments.url='https://evil.example/';
  assert.equal((await json(await f.send(extra))).result.isError,true);
  for(const params of [{name:'generate_image',arguments:{nonce:'A'}},
    {name:'read_image_control'}, {name:'read_image_control',arguments:{nonce:'A'},url:upstream}]) {
    assert.equal((await json(await f.send({...call(),params}))).error.code,-32602);
  }
  const notification=call();delete notification.id;
  assert.equal((await f.send(notification)).status,202);
  assert.equal((await f.send({jsonrpc:'2.0',method:'notifications/cancelled',params:{requestId:1}})).status,202);
  assert.equal(f.requests.length,0);
});

test('MCP uses only the optional server credential and preserves fresh active and disabled metadata',async()=>{
  let state='active';
  const f=fixture(async()=>Response.json({path:workflowPath,state,private_value:serverReadToken}));
  for(const next of ['active','disabled_manually']) {
    state=next;
    const data=await json(await f.send(call('FreshAuthenticated'),{headers:{authorization:'Bearer caller_private_value'},
      env:{IMAGE_CONTROL_GITHUB_READ_TOKEN:serverReadToken}}));
    assert.equal(data.result.isError,false);
    assert.equal(data.result.structuredContent.state,state);
    assert.equal(data.result.structuredContent.nonce,'FreshAuthenticated');
    assert.equal(data.result.structuredContent.checked_at,
      new Date(Date.parse('2026-11-16T07:00:00Z')+1000*(f.requests.length-1)).toISOString());
    assert.doesNotMatch(JSON.stringify(data),/test_only_server|caller_private/);
    assert.equal(f.requests.at(-1).url,upstream);
    assert.equal(f.requests.at(-1).options.headers.authorization,'Bearer '+serverReadToken);
    assert.equal(f.requests.at(-1).options.redirect,'manual');
    f.advance(1000);
  }
  assert.equal(f.requests.length,2);
  assert.equal(f.timers.size,0);
});

test('MCP rejects bad server bindings before network access without exposing their values',async()=>{
  for(const binding of [undefined,null,false,{},'', '   ', 'short', serverReadToken+'\n', 'x'.repeat(256)]) {
    const f=fixture();
    assert.deepEqual(await json(await f.send(call(),{env:{IMAGE_CONTROL_GITHUB_READ_TOKEN:binding}})),
      {jsonrpc:'2.0',id:1,result:{content:[{type:'text',
        text:'{"error":"image control unavailable","reason":"runtime_error"}'}],isError:true}});
    assert.equal(f.requests.length,0);
    assert.equal(f.timers.size,0);
  }
});

test('MCP authentication 401 is a bounded failure with no anonymous fallback or verified metadata',async()=>{
  const f=fixture(async()=>new Response(JSON.stringify({state:'active',nonce:'private',checked_at:'private',
    credential:serverReadToken}),{status:401,headers:{authorization:serverReadToken}}));
  assert.deepEqual(await json(await f.send(call(),{headers:{authorization:'Bearer caller_private_value'},
    env:{IMAGE_CONTROL_GITHUB_READ_TOKEN:serverReadToken}})),{jsonrpc:'2.0',id:1,result:{content:[{type:'text',
      text:'{"error":"image control unavailable","reason":"github_http_401"}'}],isError:true}});
  assert.equal(f.requests.length,1);
  assert.equal(f.requests[0].url,upstream);
  assert.equal(f.requests[0].options.headers.authorization,'Bearer '+serverReadToken);
  assert.equal(f.timers.size,0);
});

test('upstream failures preserve generic privacy and become tool errors without retries or active state',async()=>{
  for(const [reason,fetchImpl] of [
    ['github_http_401',async()=>new Response('private upstream response',{status:401})],
    ['github_http_403',async()=>new Response('private upstream response',{status:403,headers:{'x-private':'private header'}})],
    ['github_http_429',async()=>new Response('',{status:429})],
    ['github_rate_limit',async()=>new Response('',{status:403,headers:{'x-ratelimit-remaining':'0'}})],
    ['github_rate_limit',async()=>new Response('',{status:429,headers:{'x-ratelimit-remaining':'0'}})],
    ['github_http_5xx',async()=>new Response('private failure',{status:503})],
    ['github_http_other',async()=>new Response('',{status:302,headers:{location:'https://evil.example'}})],
    ['invalid_response',async()=>Response.json({path:'.github/workflows/other.yml',state:'active'})],
    ['invalid_response',async()=>new Response('private invalid json')],
    ['runtime_error',async()=>{throw new Error('private failure');}]]) {
    const f=fixture(fetchImpl);
    assert.deepEqual(await json(await f.send(call())),{jsonrpc:'2.0',id:1,result:{
      content:[{type:'text',text:JSON.stringify({error:'image control unavailable',reason})}],isError:true}});
    assert.equal(f.requests.length,1);
    assert.equal(f.timers.size,0);
  }
});

test('Origin, Accept, media type, versions and HTTP methods are checked before any read',async()=>{
  const f=fixture();
  for(const origin of ['https://evil.example','null','https://chatgpt.com.evil.example','https://chatgpt.com/'])
    await json(await f.send(call(),{headers:{origin}}),403);
  for(const origin of [null,'https://veripsa.com','https://chatgpt.com'])
    assert.equal((await f.send({jsonrpc:'2.0',id:1,method:'ping'},{headers:{origin}})).status,200);
  for(const accept of [null,'*/*','application/json','text/event-stream','application/json;q=0,text/event-stream'])
    await json(await f.send(call(),{headers:{accept}}),406);
  await json(await f.send(call(),{headers:{'content-type':'text/plain'}}),415);
  await json(await f.send(call(),{headers:{'content-type':'application/json; charset=latin1'}}),415);
  for(const version of ['2026-07-28','bad','2025-03-26',null])
    await json(await f.send(call(),{headers:{'mcp-protocol-version':version}}),400);
  for(const method of ['GET','HEAD','DELETE','OPTIONS','PUT']) {
    const result=await f.send(null,{method});
    assert.equal(result.status,405);assert.equal(result.headers.get('allow'),'POST');privateHeaders(result);
  }
  await json(await f.send(call(),{uri:endpoint+'?url=https://evil.example'}),400);
  assert.equal(f.requests.length,0);
});

test('malformed, batched, invalid UTF-8 and oversized bodies fail before GitHub',async()=>{
  const f=fixture();
  for(const raw of ['{','null','[]','[{}]',JSON.stringify({...call(),id:null}),
    JSON.stringify({...call(),jsonrpc:'1.0'}),JSON.stringify({jsonrpc:'2.0',id:1,result:{}}),
    new Uint8Array([0xff])]) {
    const response=await f.send(null,{raw});assert.equal(response.status,400);privateHeaders(response);
  }
  const advertised=await f.send(call(),{headers:{'content-length':'8193'}});
  assert.equal(advertised.status,413);
  let cancelled=false;
  const raw=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(8193));},cancel(){cancelled=true;}});
  assert.equal((await f.send(null,{raw})).status,413);
  assert.equal(cancelled,true);
  assert.equal(f.requests.length,0);
});

test('unsupported RPC methods and initialize parameters are minimal errors and do not fetch',async()=>{
  const f=fixture();
  assert.deepEqual(await json(await f.send({jsonrpc:'2.0',id:3,method:'resources/read',params:{uri:'secret'}})),
    {jsonrpc:'2.0',id:3,error:{code:-32601,message:'Method not found'}});
  assert.equal((await json(await f.send({jsonrpc:'2.0',id:4,method:'initialize',params:{}}))).error.code,-32602);
  assert.equal((await json(await f.send({jsonrpc:'2.0',id:5,method:'tools/list',params:{cursor:'secret'}}))).error.code,-32602);
  assert.equal(f.requests.length,0);
});

test('MCP timeout contains only the safe reason, without an active state or retry',async()=>{
  let started,signal;
  const ready=new Promise(resolve=>{started=resolve;});
  const f=fixture(async(_,options)=>{signal=options.signal;started();return new Promise(()=>{});});
  const pending=f.send(call('PrivateNonce'));
  await ready;
  assert.equal(f.timers.size,1);
  const [timer]=f.timers;assert.equal(timer.ms,3000);timer.fn();
  assert.deepEqual(await json(await pending),{jsonrpc:'2.0',id:1,result:{
    content:[{type:'text',text:'{"error":"image control unavailable","reason":"timeout"}'}],isError:true}});
  assert.equal(signal.aborted,true);
  assert.equal(f.requests.length,1);
  assert.equal(f.timers.size,0);
});

test('adapter validates the reason enum and never reads or echoes a failed reader body',async()=>{
  const f=fixture();
  const handle=vm.runInContext('imageControlMcp',f.context);
  for(const reason of [null,'private secret','github_http_403 private header','github_http_403']) {
    let read=false;
    const response=await handle(new Request(endpoint,{method:'POST',headers:{accept:'application/json,text/event-stream',
      'content-type':'application/json','mcp-protocol-version':'2025-11-25'},body:JSON.stringify(call('PrivateNonce'))}),
    new URL(endpoint),async()=>({ok:false,headers:new Headers(reason===null ? {} : {'x-image-control-reason':reason}),
      json(){read=true;throw new Error('private body');}}),async req=>new Uint8Array(await req.arrayBuffer()));
    assert.deepEqual((await json(response)).result,{content:[{type:'text',text:JSON.stringify({error:'image control unavailable',
      reason:reason==='github_http_403' ? reason : 'runtime_error'})}],isError:true});
    assert.equal(read,false);
  }
  const thrown=await handle(new Request(endpoint,{method:'POST',headers:{accept:'application/json,text/event-stream',
    'content-type':'application/json','mcp-protocol-version':'2025-11-25'},body:JSON.stringify(call())}),
  new URL(endpoint),async()=>{throw new Error('private secret');},async req=>new Uint8Array(await req.arrayBuffer()));
  assert.deepEqual((await json(thrown)).result,{content:[{type:'text',
    text:'{"error":"image control unavailable","reason":"runtime_error"}'}],isError:true});
});
