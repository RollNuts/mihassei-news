'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {pathToFileURL}=require('node:url');

const fileModule=pathToFileURL(path.join(__dirname,'../src/image_candidate_mcp.mjs')).href;
const ready=import(fileModule);
const repo='https://api.github.com/repos/RollNuts/mihassei-news';
const source='https://files.oaiusercontent.com/file-abcDEF123?se=short-lived&sig=synthetic';
const file={download_url:source,file_id:'file-abcDEF123',mime_type:'image/jpeg',file_name:'verified.jpg'};
const bytes=fs.readFileSync(path.join(__dirname,'../assets/generated/fermentation-rna-inspection-two-flavors.jpg'));
const sha=x=>createHash('sha256').update(x).digest('hex');
const encoded=value=>Buffer.from(JSON.stringify(value)).toString('base64');
const fromBytes=b=>Buffer.from(b).toString('base64');
const bodyFor=(tool,args)=>({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:tool,arguments:args}});
const send=async(body,fetchImpl,env={},headers={})=>{
  const {imageCandidateMcp}=await ready;
  return imageCandidateMcp(new Request('https://veripsa.com/api/image-candidate/mcp',{
    method:'POST',headers:{'content-type':'application/json',
      accept:'application/json, text/event-stream',
      'mcp-protocol-version':'2025-11-25',...headers},
    body:JSON.stringify(body)
  }),new URL('https://veripsa.com/api/image-candidate/mcp'),env,fetchImpl);
};
const data=async res=>{
  assert.equal(res.status,200);
  assert.equal(res.headers.get('cache-control'),'no-store');
  assert.equal(res.headers.get('x-robots-tag'),'noindex');
  assert.equal(res.headers.get('access-control-allow-origin'),null);
  return res.json();
};
function fileFetcher() {
  const calls=[];
  return {calls,fn:async(url,opts)=>{
    calls.push({url,opts});
    return new Response(bytes,{headers:{'content-type':'image/jpeg',
      'content-length':String(bytes.length)}});
  }};
}
function syntheticClaim() {
  const slug='fermentation-rna-inspection-two-flavors';
  const article='a'.repeat(64);
  const nonce='aB0123456789abcdefghijklmnopqrstuvwxyz';
  return {
    slug,article_sha256:article,
    branch:'native-image-candidate/'+slug+'/'+article.slice(0,16)+'-012345abcdef',
    expected_head_sha:'b'.repeat(40),claim_commit_sha:'9'.repeat(40),
    path:'maintenance/native-image-candidates/'+slug+'/candidate.jpg',
    jpeg_sha256:sha(bytes),jpeg_bytes:bytes.length,file_id_sha256:sha(file.file_id),
    nonce_sha256:sha(nonce),nonce,
    expires_at:new Date(Date.now()+5*60*1000).toISOString()
  };
}
const env={IMAGE_CANDIDATE_WRITE_ENABLED:'true',
  IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN:'synthetic-github-write-token-00001',
  IMAGE_CANDIDATE_CALLER_KEY:'synthetic-caller-token-00001'};
function githubFixture(claim,override={}) {
  const calls=[];
  let head=override.head||claim.claim_commit_sha;
  let stored=override.stored||null;
  const record={version:1,repository:'RollNuts/mihassei-news',
    ...Object.fromEntries(Object.entries(claim).filter(([k])=>k!=='nonce'&&k!=='claim_commit_sha'))};
  const fetchImpl=async(url,opts)=>{
    calls.push({url,method:opts.method,redirect:opts.redirect,
      authorized:Boolean(opts.headers.authorization),
      force:opts.body&&JSON.parse(opts.body).force});
    if(url===source)return new Response(bytes,{headers:{'content-type':'image/jpeg'}});
    if(!url.startsWith(repo))throw Error('Attempted external network');
    const p=url.slice(repo.length);
    if(p==='')return Response.json({full_name:'RollNuts/mihassei-news',
      private:override.private||false,visibility:override.private?'private':'public'});
    if(p==='/actions/workflows/free-cpu-scenes.yml')
      return Response.json({path:'.github/workflows/free-cpu-scenes.yml',
        state:override.off?'disabled_manually':'active'});
    if(p.startsWith('/contents/assets/generated/manifest.json?ref=main'))
      return Response.json({encoding:'base64',content:encoded({version:1,images:override.images||
        {old:{bytes:80000}}})});
    if(p.startsWith('/git/ref/heads/')){
      if(opts.method==='PATCH'){
        assert.equal(JSON.parse(opts.body).force,false);
        if(head!==claim.claim_commit_sha)return new Response('',{status:422});
        head='e'.repeat(40);
      }
      return Response.json({object:{sha:head}});
    }
    if(p.startsWith('/contents/maintenance/image-upload-claims/')){
      return Response.json({encoding:'base64',content:encoded(record)});
    }
    if(p.startsWith('/contents/'+claim.path)){
      if(stored || p.endsWith('ref='+'e'.repeat(40)))
        return Response.json({encoding:'base64',content:fromBytes(stored||bytes)});
      return new Response('missing',{status:404});
    }
    if(p==='/git/commits/'+claim.claim_commit_sha)return Response.json({tree:{sha:'c'.repeat(40)},
      parents:[{sha:claim.expected_head_sha}]});
    if(p==='/git/blobs') {
      assert.equal(opts.method,'POST');
      assert.equal(sha(Buffer.from(JSON.parse(opts.body).content,'base64')),sha(bytes));
      return Response.json({sha:'d'.repeat(40)});
    }
    if(p==='/git/trees')return Response.json({sha:'f'.repeat(40)});
    if(p==='/git/commits')return Response.json({sha:'e'.repeat(40)});
    throw Error('Unexpected Github request '+p);
  };
  return {calls,fetchImpl,get head(){return head;}};
}

test('the published fileParams schema lists four fields; only two are required',async()=>{
  const f=fileFetcher();
  const response=await send({jsonrpc:'2.0',id:2,method:'tools/list'},f.fn);
  const x=await data(response);
  assert.equal(x.result.tools.length,2);
  assert.equal(f.calls.length,0);
  for(const tool of x.result.tools){
    assert.deepEqual(tool._meta['openai/fileParams'],['file']);
    const s=tool.inputSchema.properties.file;
    assert.deepEqual(Object.keys(s.properties).sort(),['download_url','file_id','file_name','mime_type']);
    assert.deepEqual(s.required,['download_url','file_id']);
    assert.equal(s.additionalProperties,false);
  }
  assert.equal(x.result.tools[0].annotations.readOnlyHint,true);
  assert.equal(x.result.tools[1].annotations.readOnlyHint,false);
});

test('real, already-approved 1200x675 JPEG passes file delivery probe without GitHub',async()=>{
  assert.ok(bytes.length>1000&&bytes.length<700*1024,'a real small JPEG is needed');
  const f=fileFetcher();
  const start=performance.now();
  const actual=await data(await send(bodyFor('probe_candidate_jpeg',{file}),f.fn));
  const elapsedMs=performance.now()-start;
  assert.deepEqual(actual.result.structuredContent,{status:'verified',width:1200,height:675,
    bytes:bytes.length,sha256:sha(bytes),file_id_sha256:sha(file.file_id)});
  assert.deepEqual(f.calls.map(x=>x.url),[source]);
  assert.equal(f.calls[0].opts.redirect,'manual');
  assert.match(actual.result.content[0].text,/verified/);
  assert.doesNotMatch(JSON.stringify(actual),/oaiusercontent|sig=|file-abcDEF123/);
  console.log('Real JPEG probe mock bytes:',bytes.length,'local elapsed ms:',elapsedMs.toFixed(1),
    '(NOT a deployed Worker CPU measurement)');
});

test('arbitrary URLs, redirects, missing MIME, bad dimensions, oversize fail closed',async()=>{
  for(const url of ['https://evil.example/a.jpg','https://files.oaiusercontent.com.evil.example/file-a',
    'http://files.oaiusercontent.com/file-a','https://user:password@files.oaiusercontent.com/file-a',
    'https://files.oaiusercontent.com:8443/file-a','https://files.oaiusercontent.com/file-a#frag']){
    const f=fileFetcher(),x=await data(await send(bodyFor('probe_candidate_jpeg',
      {file:{...file,download_url:url}}),f.fn));
    assert.equal(x.result.isError,true);
    assert.equal(f.calls.length,0);
  }
  for(const badResponse of [
    new Response(null,{status:302,headers:{location:'https://evil.example'}}),
    new Response('not jpeg',{headers:{'content-type':'image/jpeg'}}),
    new Response(bytes,{headers:{'content-type':'text/html'}}),
    new Response(Buffer.alloc(700*1024+1,1),{headers:{'content-type':'image/jpeg'}}),
    new Response(bytes,{headers:{'content-type':'image/jpeg',
      'content-length':String(700*1024+1)}})
  ]){
    const x=await data(await send(bodyFor('probe_candidate_jpeg',{file}),()=>badResponse));
    assert.equal(x.result.isError,true);
  }
  const corrupted=Buffer.from(bytes);const offset=corrupted.indexOf(Buffer.from([0xff,0xc0]));
  if(offset>=0){corrupted[offset+5]=0;corrupted[offset+6]=1;
    const res=await data(await send(bodyFor('probe_candidate_jpeg',{file}),
      async()=>new Response(corrupted,{headers:{'content-type':'image/jpeg'}})));
    assert.equal(res.result.isError,true);}
});

test('write path disabled before approval: zero file reads or GitHub calls',async()=>{
  const claim=syntheticClaim(),f=fileFetcher(),msg=bodyFor('stage_candidate_jpeg',{file,claim});
  const cases=[{}, {IMAGE_CANDIDATE_WRITE_ENABLED:'true'},
    {...env,IMAGE_CANDIDATE_WRITE_ENABLED:'false'},
    {...env,IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN:''},
    {...env,IMAGE_CANDIDATE_CALLER_KEY:null}];
  for(const e of cases) {
    const x=await data(await send(msg,f.fn,e));
    assert.equal(x.result.isError,true);
    assert.deepEqual(JSON.parse(x.result.content[0].text),{error:'write_disabled'});
    assert.equal(f.calls.length,0);
  }
  const auth=await data(await send(msg,f.fn,env));
  assert.equal(auth.result.isError,true);
  assert.deepEqual(JSON.parse(auth.result.content[0].text),{error:'unauthorized'});
  assert.equal(f.calls.length,0);
});

test('signed branch claim + separate caller key: one new immutable JPEG candidate only',async()=>{
  const claim=syntheticClaim(),fake=githubFixture(claim);
  const result=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),fake.fetchImpl,env,
    {authorization:'Bearer '+env.IMAGE_CANDIDATE_CALLER_KEY}));
  assert.equal(result.result.isError,false);
  assert.equal(result.result.structuredContent.status,'saved');
  assert.equal(result.result.structuredContent.path,claim.path);
  assert.equal(result.result.structuredContent.commit_sha,'e'.repeat(40));
  assert.doesNotMatch(JSON.stringify(result),/synthetic-github|synthetic-caller|oaiusercontent|sig=|aB0123/);
  assert.equal(fake.calls.filter(x=>x.method==='POST').length,3);
  assert.equal(fake.calls.filter(x=>x.method==='PATCH').length,1);
  assert.ok(fake.calls.every(x=>!x.url.includes('/git/ref/heads/main')));
  assert.equal(fake.calls.find(x=>x.url.endsWith('/actions/workflows/free-cpu-scenes.yml')).authorized,false);
  assert.ok(fake.calls.filter(x=>x.method==='POST'||x.method==='PATCH').every(x=>x.authorized));
  assert.equal(fake.calls.find(x=>x.method==='PATCH').force,false);
  assert.equal(fake.head,'e'.repeat(40));
  // A retry with the same exact source/claim must reuse its old candidate.
  const replay=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),
    fake.fetchImpl,env,{authorization:'Bearer '+env.IMAGE_CANDIDATE_CALLER_KEY}));
  assert.equal(replay.result.structuredContent.status,'reused');
  assert.equal(fake.calls.filter(x=>x.method==='PATCH').length,1);
});

test('concurrent head, inactive image workflow, private repo or limit never write',async()=>{
  const claim=syntheticClaim();
  for(const options of [{head:'f'.repeat(40)},{off:true},{private:true},
    {images:{someHugeImage:{bytes:1536*1024*1024}}}]) {
    const fake=githubFixture(claim,options);
    const result=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),
      fake.fetchImpl,env,{authorization:'Bearer '+env.IMAGE_CANDIDATE_CALLER_KEY}));
    assert.equal(result.result.isError,true);
    assert.equal(fake.calls.filter(x=>x.method==='POST'||x.method==='PATCH').length,0);
  }
});

test('wrong nonce/file hash/expired claim blocks before any GitHub call',async()=>{
  for(const transform of [c=>(c.nonce='x'.repeat(36)),c=>(c.file_id_sha256='0'.repeat(64)),
    c=>(c.jpeg_bytes+=1),c=>(c.expires_at='2020-01-01T00:00:00Z'),
    c=>(c.branch='main')]){
    const claim=syntheticClaim();transform(claim);
    const fake=githubFixture(claim);
    const actual=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),fake.fetchImpl,env,
      {authorization:'Bearer '+env.IMAGE_CANDIDATE_CALLER_KEY}));
    assert.equal(actual.result.isError,true);
    assert.equal(fake.calls.filter(x=>x.url.startsWith(repo)).length,0);
  }
});
