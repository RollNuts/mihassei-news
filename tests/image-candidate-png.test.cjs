'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {deflateSync,inflateSync}=require('node:zlib');
const {pathToFileURL}=require('node:url');

const ready=import(pathToFileURL(path.join(__dirname,'../src/image_candidate_mcp.mjs')).href);
const endpoint='https://veripsa.com/api/image-candidate/mcp';
const source='https://files.oaiusercontent.com/downloads/PRIVATE_PATH?sig=PRIVATE_SIGNATURE';
const file={download_url:source,file_id:'asset:PRIVATE_FILE_ID + 日本語',
  mime_type:'image/png',file_name:'PRIVATE_FILENAME.png'};
const maxPng=8*1024*1024;
const sha=value=>createHash('sha256').update(value).digest('hex');
const signature=Buffer.from([137,80,78,71,13,10,26,10]);
function crc32(bytes){
  let crc=0xffffffff;
  for(const byte of bytes){
    crc^=byte;
    for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);
  }
  return (crc^0xffffffff)>>>0;
}
function chunk(type,data=Buffer.alloc(0)){
  const name=Buffer.from(type),length=Buffer.alloc(4),crc=Buffer.alloc(4);
  length.writeUInt32BE(data.length);crc.writeUInt32BE(crc32(Buffer.concat([name,data])));
  return Buffer.concat([length,name,data,crc]);
}
function ihdr(width,height){
  const bytes=Buffer.alloc(13);
  bytes.writeUInt32BE(width);bytes.writeUInt32BE(height,4);
  bytes[8]=8;bytes[9]=6; // Eight-bit RGBA; standard compression, filtering and no interlace.
  return bytes;
}
const assemble=(...chunks)=>Buffer.concat([signature,...chunks]);
const end=chunk('IEND');
function syntheticPng(width=3,height=2){
  const pixels=Buffer.alloc((width*4+1)*height);
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const at=y*(width*4+1)+1+x*4;
    pixels.set([17+x*13,47+y*19,79+x+y,255],at);
  }
  const compressed=deflateSync(pixels),split=Math.floor(compressed.length/2);
  const idats=[compressed.subarray(0,split),compressed.subarray(split)];
  return {pixels,compressed,bytes:assemble(chunk('IHDR',ihdr(width,height)),
    chunk('tEXt',Buffer.from('Comment\0Deterministic PNG preservation fixture')),
    ...idats.map(data=>chunk('IDAT',data)),end)};
}
const fixture=syntheticPng(),bytes=fixture.bytes;
const bodyFor=args=>({jsonrpc:'2.0',id:1,method:'tools/call',
  params:{name:'read_candidate_png',arguments:args}});
const env={IMAGE_CANDIDATE_WRITE_ENABLED:'true',
  IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN:'PRIVATE_ENV_TOKEN',GITHUB_TOKEN:'PRIVATE_OTHER_TOKEN'};
async function send(body,fetchImpl,environment={},headers={}){
  const {imageCandidateMcp}=await ready;
  const response=await imageCandidateMcp(new Request(endpoint,{method:'POST',
    headers:{'content-type':'application/json',accept:'application/json, text/event-stream',
      'mcp-protocol-version':'2025-11-25',...headers},body:JSON.stringify(body)}),
  new URL(endpoint),environment,fetchImpl);
  assert.equal(response.status,200);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('x-robots-tag'),'noindex');
  assert.equal(response.headers.get('access-control-allow-origin'),null);
  return response.json();
}
function fileFetcher(makeResponse=()=>new Response(bytes,{headers:{'content-type':'image/png'}})){
  const calls=[];
  return {calls,fn:async(url,opts)=>{
    calls.push({url,opts});
    assert.doesNotMatch(url,/github\.com|githubusercontent\.com/,'PNG reads must not access GitHub');
    return makeResponse(url,opts);
  }};
}
function safeError(actual,expected){
  assert.equal(actual.result.isError,true);
  assert.equal(actual.result.structuredContent,undefined);
  assert.equal(actual.result.content.length,1);
  const text=actual.result.content[0].text;
  assert.ok(text.length<1024,'error diagnostics stay bounded');
  assert.doesNotMatch(text,/PRIVATE_|https?:|image_url|base64|data:image|日本語/);
  const detail=JSON.parse(text);
  assert.match(detail.error,/^[a-z][a-z0-9_]*$/);
  if(expected)assert.equal(detail.error,expected);
  return detail;
}
function readMetadata(actual,png=bytes,reference=file,width=3,height=2){
  assert.equal(actual.result.isError,false,JSON.stringify(actual.result.content));
  const {image_url,...metadata}=actual.result.structuredContent;
  assert.deepEqual(metadata,{status:'read',mime_type:'image/png',width,height,
    bytes:png.length,sha256:sha(png),file_id_sha256:sha(reference.file_id)});
  assert.equal(image_url,'data:image/png;base64,'+png.toString('base64'));
  assert.deepEqual(Buffer.from(image_url.split(',')[1],'base64'),png,'PNG bytes must remain unchanged');
  assert.deepEqual(actual.result.content.map(item=>item.type),['text']);
  const text=actual.result.content[0].text;
  assert.ok(text.length<1024,'content text contains only small metadata');
  assert.deepEqual(JSON.parse(text),metadata);
  assert.doesNotMatch(text,/PRIVATE_|https?:|image_url|base64|data:image|日本語/);
}

let originalFetch;
test.before(()=>{
  originalFetch=globalThis.fetch;
  globalThis.fetch=async()=>assert.fail('Offline PNG tests must never use live fetch');
});
test.after(()=>{globalThis.fetch=originalFetch;});

test('PNG reader advertises a read-only fileParams tool with only the existing file object',async()=>{
  const f=fileFetcher();
  const actual=await send({jsonrpc:'2.0',id:1,method:'tools/list'},f.fn);
  const png=actual.result.tools.find(tool=>tool.name==='read_candidate_png');
  const jpeg=actual.result.tools.find(tool=>tool.name==='probe_candidate_jpeg');
  assert.ok(png,'PNG read tool must be discoverable');
  assert.deepEqual(Object.keys(png.inputSchema.properties),['file']);
  assert.deepEqual(png.inputSchema.required,['file']);
  assert.equal(png.inputSchema.additionalProperties,false);
  assert.deepEqual(png.inputSchema.properties.file,jpeg.inputSchema.properties.file);
  assert.deepEqual(png._meta['openai/fileParams'],['file']);
  assert.equal(png.annotations.readOnlyHint,true);
  assert.equal(png.annotations.destructiveHint,false);
  assert.equal(png.annotations.openWorldHint,false);
  assert.equal(f.calls.length,0);
});

test('a valid deterministic PNG is returned byte for byte with metadata-only text',async()=>{
  assert.equal(end.readUInt32BE(end.length-4),0xae426082,'standard PNG IEND CRC');
  assert.deepEqual(inflateSync(fixture.compressed),fixture.pixels,'fixture has valid compressed pixels');
  const f=fileFetcher(()=>new Response(bytes,{headers:{'content-type':'image/png',
    'content-length':String(bytes.length)}}));
  readMetadata(await send(bodyFor({file}),f.fn));
  assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].url,source);
  assert.equal(f.calls[0].opts.method,'GET');
  assert.equal(f.calls[0].opts.redirect,'manual');
  assert.equal(f.calls[0].opts.cache,'no-store');
  assert.deepEqual(f.calls[0].opts.headers,{accept:'image/png'});
  assert.ok(f.calls[0].opts.signal instanceof AbortSignal);
});

const weeklyIssue='november-coast-city-rio-015';
const weeklyCommit='7abd73f7839688b447d05eedeb0a37d35b45dca0';
const weeklyFolder='maintenance/photo-issue-plans/'+weeklyIssue;
const weeklyArgs={issue:weeklyIssue,commit:weeklyCommit,path:weeklyFolder+'/originals/02.png',
  expected_sha256:'753b7c71191b32cecf5b459154511bcf31efbd54c0335a67c8269bca751bc95b'};
const weeklyCall=args=>({jsonrpc:'2.0',id:1,method:'tools/call',
  params:{name:'read_weekly_candidate_png',arguments:args}});

test('weekly PNG reader declares four immutable source fields and its read-only byte output',async()=>{
  const response=await send({jsonrpc:'2.0',id:1,method:'tools/list'},()=>assert.fail('unexpected fetch'));
  const tool=response.result.tools.find(candidate=>candidate.name==='read_weekly_candidate_png');
  const fields=['commit','expected_sha256','issue','path'];
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(),fields);
  assert.deepEqual([...tool.inputSchema.required].sort(),fields);
  for(const field of fields)assert.equal(tool.inputSchema.properties[field].type,'string');
  assert.equal(tool.inputSchema.additionalProperties,false);
  assert.equal(tool._meta?.['openai/fileParams'],undefined);
  assert.equal(tool.annotations.readOnlyHint,true);
  assert.equal(tool.annotations.destructiveHint,false);
  assert.ok(tool.outputSchema.required.includes('image_url'));
  assert.equal(tool.outputSchema.properties.image_url.pattern,'^data:image/png;base64,');
});

test('weekly PNG reader preserves a 2.3 MB original and an actual supplemental photo',async()=>{
  // The published source files are byte-identical to the originals on this immutable candidate.
  const cases=[
    {args:weeklyArgs,cut:'02',size:2328106,width:1024,height:1536},
    {args:{...weeklyArgs,path:weeklyFolder+'/supplements/d0ff7c0e/03.png',
      expected_sha256:'535225a6b99705d4fa1dde4a49306cf0bfd16e996e92672f575a0d630954d1be'},
      cut:'05',size:2261909,width:1536,height:1024}
  ];
  for(const {args,cut,size,width,height} of cases){
    const original=fs.readFileSync(path.join(__dirname,'../maintenance/photo-issues',
      weeklyIssue,cut,'source-'+args.expected_sha256+'.png'));
    assert.equal(sha(original),args.expected_sha256);
    assert.equal(original.length,size);
    let requests=0;
    const response=await send(weeklyCall(args),(url,opts)=>{
      requests++;
      assert.equal(String(url),'https://raw.githubusercontent.com/RollNuts/mihassei-news/'+weeklyCommit+'/'+args.path);
      assert.equal(opts.method,'GET');
      assert.equal(opts.redirect,'manual');
      assert.equal(opts.cache,'no-store');
      assert.equal(opts.credentials,'omit');
      assert.ok(opts.signal instanceof AbortSignal);
      const headers=new Headers(opts.headers);
      assert.equal(headers.has('authorization'),false);
      assert.equal(headers.has('cookie'),false);
      return new Response(original,{headers:{'content-type':'image/png'}});
    },cut==='02'?{}:env,{authorization:'Bearer PRIVATE_REQUEST',cookie:'PRIVATE_COOKIE=1'});
    assert.equal(requests,1);
    assert.equal(response.result.isError,false,JSON.stringify(response.result.content));
    const {image_url,...meta}=response.result.structuredContent;
    assert.deepEqual(meta,{status:'read',mime_type:'image/png',sha256:args.expected_sha256,
      bytes:size,width,height,commit:weeklyCommit,path:args.path});
    assert.ok(image_url.startsWith('data:image/png;base64,'));
    assert.equal(image_url.length,22+4*Math.ceil(size/3));
    assert.ok(Buffer.from(image_url.slice(22),'base64').equals(original),'returned bytes differ from the candidate');
    assert.deepEqual(response.result.content.map(item=>item.type),['text']);
    assert.ok(response.result.content[0].text.length<1024);
    assert.deepEqual(JSON.parse(response.result.content[0].text),meta);
  }
});

test('weekly reader rejects moving refs, out-of-scope files and extra source options before fetching',async()=>{
  const rejected=[
    {...weeklyArgs,commit:'main'}, {...weeklyArgs,expected_sha256:''},
    {...weeklyArgs,issue:'another-issue'}, {...weeklyArgs,path:weeklyFolder+'/jpeg/02.jpg'},
    {...weeklyArgs,path:'assets/editorial-scenes/robot-job-hopping.png'},
    {...weeklyArgs,path:weeklyFolder+'/supplements/d0ff7c0e/inventory.json'},
    {...weeklyArgs,url:source}
  ];
  let requests=0;
  for(const args of rejected){
    safeError(await send(weeklyCall(args),()=>{requests++;return new Response(bytes);}), 'invalid_arguments');
  }
  assert.equal(requests,0);
});

test('weekly PNG read fails on hash mismatch, empty or oversized bytes, wrong MIME and redirect',async()=>{
  const args={...weeklyArgs,expected_sha256:sha(bytes)};
  const cases=[
    ['sha256_mismatch',{...args,expected_sha256:'0'.repeat(64)},()=>new Response(bytes,{headers:{'content-type':'image/png'}})],
    ['empty_response',args,()=>new Response(null,{headers:{'content-type':'image/png'}})],
    ['invalid_png',args,()=>new Response(new Uint8Array(),{headers:{'content-type':'image/png'}})],
    ['file_too_large',args,()=>new Response(bytes,{headers:{'content-type':'image/png','content-length':String(maxPng+1)}})],
    ['file_too_large',args,()=>new Response(new Uint8Array(maxPng+1),{headers:{'content-type':'image/png'}})],
    ['unexpected_file_type',args,()=>new Response(bytes,{headers:{'content-type':'text/plain'}})],
    ['file_unavailable',args,()=>new Response(null,{status:302,headers:{location:'https://example.com/elsewhere.png'}})]
  ];
  for(const [error,request,upstream] of cases){
    let requests=0;
    safeError(await send(weeklyCall(request),(_url,opts)=>{
      requests++;
      assert.equal(opts.redirect,'manual');
      return upstream();
    }),error);
    assert.equal(requests,1);
  }
});

test('actual PNG HTTP bytes determine type while optional file metadata stays advisory',async()=>{
  for(const supplied of [{download_url:source,file_id:file.file_id},
    {...file,mime_type:'image/jpeg',file_name:'PRIVATE_FILENAME.jpg'}]){
    const f=fileFetcher(()=>new Response(bytes,{headers:{'content-type':' Application/Octet-Stream ; charset=binary'}}));
    readMetadata(await send(bodyFor({file:supplied}),f.fn),bytes,supplied);
    assert.equal(f.calls.length,1);
  }
  const tiny=syntheticPng(1,1).bytes;
  const f=fileFetcher(()=>new Response(tiny,{headers:{'content-type':'image/png; charset=binary'}}));
  readMetadata(await send(bodyFor({file}),f.fn),tiny,file,1,1);
});

test('native typed-array base64 is used when available without changing original bytes',async()=>{
  const original=Object.getOwnPropertyDescriptor(Uint8Array.prototype,'toBase64');
  let calls=0;
  Object.defineProperty(Uint8Array.prototype,'toBase64',{configurable:true,value:function(){
    calls++;return Buffer.from(this).toString('base64');
  }});
  try{
    const f=fileFetcher();
    readMetadata(await send(bodyFor({file}),f.fn));
    assert.equal(calls,1);
  }finally{
    if(original)Object.defineProperty(Uint8Array.prototype,'toBase64',original);
    else delete Uint8Array.prototype.toBase64;
  }
});

test('trusted file hosts are read without request credentials, environment tokens or GitHub access',async()=>{
  const hosts=['files.oaiusercontent.com','sdmntprwestus.oaiusercontent.com',
    'sdmntpreastus3.oaiusercontent.com','sdmntpra.oaiusercontent.com',
    'sdmntpr'+'a'.repeat(40)+'.oaiusercontent.com',
    'oaisdmntpraustraliaeast.blob.core.windows.net',
    'oaisdmntprindiasocentral.blob.core.windows.net',
    'oaisdmntprkoreacentral.blob.core.windows.net',
    'oaisdmntprwestus.blob.core.windows.net',
    'oaisdmntpraustraliaeast2.blob.core.windows.net',
    'oaisdmntpra.blob.core.windows.net',
    'oaisdmntpr'+'a'.repeat(14)+'.blob.core.windows.net'];
  for(const host of hosts){
    const reference={...file,download_url:'https://'+host+'/PRIVATE_PATH?sig=PRIVATE_SIGNATURE'};
    const f=fileFetcher(()=>{
      const response=new Response(bytes,{headers:{'content-type':'image/png'}});
      Object.defineProperty(response,'url',{value:'https://'+host+'/same-origin-response.png'});
      return response;
    });
    readMetadata(await send(bodyFor({file:reference}),f.fn,env,
      {authorization:'Bearer PRIVATE_REQUEST_TOKEN',cookie:'session=PRIVATE_COOKIE'}),bytes,reference);
    assert.equal(f.calls.length,1);
    assert.equal(f.calls[0].url,reference.download_url);
    assert.equal(f.calls[0].opts.method,'GET');
    assert.equal(f.calls[0].opts.redirect,'manual');
    assert.deepEqual(f.calls[0].opts.headers,{accept:'image/png'});
    assert.equal(f.calls[0].opts.body,undefined);
    assert.doesNotMatch(JSON.stringify(f.calls[0].opts),/PRIVATE_|authorization|cookie/i);
  }
});

test('missing, extra or credential arguments are rejected before fetching',async()=>{
  for(const args of [{},{file:null},{file:[]},{file,claim:{}},{file,token:'PRIVATE_TOKEN'},
    {file,authorization:'PRIVATE_AUTH'},{file,image_url:'PRIVATE_INPUT'}]){
    const f=fileFetcher();
    safeError(await send(bodyFor(args),f.fn,env),'invalid_arguments');
    assert.equal(f.calls.length,0);
  }
});

test('invalid fileParams values are rejected without fetching or echoing private values',async()=>{
  const without=key=>Object.fromEntries(Object.entries(file).filter(([name])=>name!==key));
  const rejected=[without('download_url'),without('file_id'),{...file,file_id:42},
    {...file,file_id:''},{...file,file_id:'x'.repeat(1025)},
    {...file,file_id:'PRIVATE_FILE_ID\0value'},{...file,download_url:42},
    {...file,download_url:''},{...file,download_url:'PRIVATE_'.repeat(600)},
    {...file,download_url:'PRIVATE_INVALID_URL'},{...file,download_url:source+'\nPRIVATE_VALUE'},
    {...file,mime_type:42},{...file,mime_type:'x'.repeat(256)},
    {...file,file_name:42},{...file,file_name:'x'.repeat(513)},
    {...file,file_name:'PRIVATE_FILENAME\nvalue'},
    {...file,PRIVATE_UNKNOWN_FIELD:'PRIVATE_VALUE'}];
  for(const reference of rejected){
    const f=fileFetcher();
    safeError(await send(bodyFor({file:reference}),f.fn),'invalid_file_reference');
    assert.equal(f.calls.length,0);
  }
});

test('SSRF URLs and unrecognized sibling hosts are rejected before any fetch',async()=>{
  const rejected=['https://evil.example/PRIVATE_PATH?sig=PRIVATE_SIGNATURE',
    'https://127.0.0.1/PRIVATE_PATH','https://[::1]/PRIVATE_PATH',
    'https://arbitrary.oaiusercontent.com/PRIVATE_PATH',
    'https://sdmntpr.oaiusercontent.com/PRIVATE_PATH',
    'https://sdmntpr'+'a'.repeat(41)+'.oaiusercontent.com/PRIVATE_PATH',
    'https://sdmntprwestus-1.oaiusercontent.com/PRIVATE_PATH',
    'https://nested.sdmntprwestus.oaiusercontent.com/PRIVATE_PATH',
    'https://files.oaiusercontent.com.evil.example/PRIVATE_PATH',
    'https://sdmntprwestus.oaiusercontent.com./PRIVATE_PATH',
    'https://oaisdmntpr.blob.core.windows.net/PRIVATE_PATH',
    'https://oaisdmntpr'+'a'.repeat(15)+'.blob.core.windows.net/PRIVATE_PATH',
    'https://oaisdmntpr-koreacentral.blob.core.windows.net/PRIVATE_PATH',
    'https://oaisdmntprindiasocentral2.blob.core.windows.net/PRIVATE_PATH',
    'https://nested.oaisdmntprindiasocentral.blob.core.windows.net/PRIVATE_PATH',
    'https://oaisdmntprindiasocentral.blob.core.windows.net.evil.example/PRIVATE_PATH',
    'https://attacker.blob.core.windows.net/PRIVATE_PATH',
    'https://nested.oaisdmntpraustraliaeast.blob.core.windows.net/PRIVATE_PATH',
    'https://oaisdmntpraustraliaeast.blob.core.windows.net.evil.example/PRIVATE_PATH',
    'http://files.oaiusercontent.com/PRIVATE_PATH',
    'https://files.oaiusercontent.com:8443/PRIVATE_PATH',
    'https://PRIVATE_USER:PRIVATE_PASSWORD@files.oaiusercontent.com/PRIVATE_PATH',
    source+'#PRIVATE_FRAGMENT','https://files.oaiusercontent.com/',
    'https://files.oaiusercontent.com/images//PRIVATE_PATH'];
  for(const download_url of rejected){
    const f=fileFetcher();
    safeError(await send(bodyFor({file:{...file,download_url}}),f.fn),'untrusted_file_host');
    assert.equal(f.calls.length,0,'untrusted input must not be fetched');
  }
});

test('an unknown PNG host reports only the existing bounded diagnostic, without an extra JPEG probe',async()=>{
  const reference={...file,download_url:'https://oaisdmntprindiasocentral2.blob.core.windows.net/PRIVATE_PATH?sig=PRIVATE_SIGNATURE'};
  const f=fileFetcher(),actual=await send(bodyFor({file:reference}),f.fn);
  const detail=safeError(actual,'untrusted_file_host');
  assert.deepEqual(detail,{error:'untrusted_file_host',file_reference_diagnostic:{
    reason:'hostname',hostname:'oaisdmntprindiasocentral2.blob.core.windows.net'}});
  assert.equal(f.calls.length,0);
});

test('PNG regional relay still rejects a response from a different allowed Azure account',async()=>{
  const reference={...file,download_url:'https://oaisdmntprkoreacentral.blob.core.windows.net/PRIVATE_PATH?sig=PRIVATE_SIGNATURE'};
  const f=fileFetcher(()=>{
    const response=new Response(bytes,{headers:{'content-type':'image/png'}});
    Object.defineProperty(response,'url',{value:'https://oaisdmntpraustraliaeast.blob.core.windows.net/PRIVATE_PATH'});
    return response;
  });
  safeError(await send(bodyFor({file:reference}),f.fn),'file_unavailable');
  assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].opts.redirect,'manual');
});

test('redirects, upstream failures and cross-origin response URLs fail closed',async()=>{
  const factories=[
    ()=>new Response('PRIVATE_UPSTREAM',{status:302,headers:{location:source}}),
    ()=>new Response('PRIVATE_UPSTREAM',{status:403}),
    ...['https://evil.example/PRIVATE_PATH','https://sdmntprwestus.oaiusercontent.com/image.png',
      'http://files.oaiusercontent.com/image.png','https://files.oaiusercontent.com:8443/image.png']
      .map(url=>()=>{
        const response=new Response(bytes,{headers:{'content-type':'image/png'}});
        Object.defineProperty(response,'url',{value:url});return response;
      })];
  for(const factory of factories){
    const f=fileFetcher(factory);
    safeError(await send(bodyFor({file}),f.fn),'file_unavailable');
    assert.equal(f.calls.length,1,'no redirect or fallback request is allowed');
    assert.equal(f.calls[0].opts.redirect,'manual');
  }
  const f=fileFetcher(()=>{throw Error(source+' '+file.file_id+' '+file.file_name);});
  safeError(await send(bodyFor({file}),f.fn),'unavailable');
  assert.equal(f.calls.length,1);
});

test('missing or non-PNG HTTP MIME types are rejected even when bytes are valid PNG',async()=>{
  for(const type of [null,'image/jpeg','image/svg+xml','text/plain','application/json']){
    const f=fileFetcher(()=>new Response(bytes,{headers:type?{'content-type':type}:{}}));
    safeError(await send(bodyFor({file}),f.fn),'unexpected_file_type');
    assert.equal(f.calls.length,1);
  }
});

test('JPEG, SVG, bad PNG magic and malformed chunk structures are rejected',async()=>{
  const badMagic=Buffer.from(bytes);badMagic[0]=0;
  const header=chunk('IHDR',ihdr(3,2)),idat=chunk('IDAT',fixture.compressed);
  const jpeg=fs.readFileSync(path.join(__dirname,'../assets/generated/fermentation-rna-inspection-two-flavors.jpg'));
  const malformed=[jpeg,Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>'),badMagic,
    signature,bytes.subarray(0,bytes.length-1),assemble(header,idat),
    assemble(chunk('tEXt',Buffer.from('x')),header,idat,end),
    assemble(chunk('IHDR',Buffer.alloc(12)),idat,end),
    assemble(chunk('IHDR',Buffer.alloc(14)),idat,end),
    assemble(header,header,idat,end),assemble(header,end),
    assemble(header,idat,chunk('IEND',Buffer.from([0]))),
    Buffer.concat([bytes,Buffer.from([0])]),
    assemble(header,Buffer.from([0xff,0xff,0xff,0xff,73,68,65,84]),end)];
  for(const png of malformed){
    const f=fileFetcher(()=>new Response(png,{headers:{'content-type':'image/png'}}));
    safeError(await send(bodyFor({file}),f.fn),'invalid_png');
  }
});

test('zero, excessive side lengths and more than 32 million PNG pixels are rejected',async()=>{
  for(const [width,height] of [[0,2],[2,0],[8193,1],[1,8193],[8192,4000],[0xffffffff,1]]){
    const png=assemble(chunk('IHDR',ihdr(width,height)),chunk('IDAT',fixture.compressed),end);
    const f=fileFetcher(()=>new Response(png,{headers:{'content-type':'image/png'}}));
    safeError(await send(bodyFor({file}),f.fn),'wrong_png_dimensions');
  }
});

test('declared sizes above 8 MiB or invalid content lengths are rejected before reading',async()=>{
  for(const length of [String(maxPng+1),'-1','PRIVATE_LENGTH']){
    let reads=0;
    const f=fileFetcher(()=>new Response(new ReadableStream({pull(controller){
      reads++;controller.enqueue(bytes);controller.close();
    }},{highWaterMark:0}),{headers:{'content-type':'image/png','content-length':length}}));
    safeError(await send(bodyFor({file}),f.fn),'file_too_large');
    assert.equal(reads,0,'declared oversize must not consume the response body');
  }
});

test('streaming size limit stops at 8 MiB plus one byte and cancels without returning payload',async()=>{
  let pulls=0,cancels=0;
  const f=fileFetcher(()=>new Response(new ReadableStream({pull(controller){
    pulls++;
    if(pulls>5)throw Error('reader exceeded the bounded streaming fixture');
    controller.enqueue(new Uint8Array(pulls<=4?2*1024*1024:1));
  },cancel(){cancels++;}},{highWaterMark:0}),{headers:{'content-type':'image/png'}}));
  safeError(await send(bodyFor({file}),f.fn),'file_too_large');
  assert.equal(pulls,5);
  assert.equal(cancels,1,'bounded streaming must cancel the remaining source');
  assert.equal(f.calls.length,1);
});

test('stream errors do not leak the upstream URL, file ID or filename',async()=>{
  const f=fileFetcher(()=>new Response(new ReadableStream({pull(controller){
    controller.error(Error(source+' '+file.file_id+' '+file.file_name));
  }}),{headers:{'content-type':'image/png'}}));
  safeError(await send(bodyFor({file}),f.fn),'unavailable');
  assert.equal(f.calls.length,1);
});
