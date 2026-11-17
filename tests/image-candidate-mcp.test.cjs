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
const realArticles=fs.readFileSync(path.join(__dirname,'../content/articles.json'));
const articles=JSON.parse(realArticles);
const target=articles.find(a=>a.slug==='fermentation-rna-inspection-two-flavors');
const fingerprint=a=>sha(JSON.stringify(Object.fromEntries(
  ['category','paragraphs','scene_prompt','slug','summary','title'].map(k=>[k,Object.hasOwn(a,k)?a[k]:'']))));
const rawRepo='https://raw.githubusercontent.com/RollNuts/mihassei-news/';
const freshSource='https://sdmntpreastus3.oaiusercontent.com/upload-fresh.jpg?sig=synthetic';
const generatedDir=path.join(__dirname,'../assets/generated');
const archiveEntries=fs.readdirSync(generatedDir).map(name=>({path:'assets/generated/'+name,
  type:'blob',mode:'100644',size:fs.statSync(path.join(generatedDir,name)).size}));
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
  const nonce=Buffer.alloc(32,17).toString('base64url'),hash=sha(nonce);
  const article=fingerprint(target),slug=target.slug;
  return {version:2,slug,article_sha256:article,
    branch:'native-image-candidate/'+slug+'/'+article.slice(0,16)+'-'+hash.slice(0,12),
    expected_head_sha:'b'.repeat(40),claim_commit_sha:'9'.repeat(40),
    path:'maintenance/native-image-candidates/'+slug+'/candidate.jpg',
    jpeg_sha256:sha(bytes),jpeg_bytes:bytes.length,nonce_sha256:hash,nonce,
    expires_at:new Date(Date.now()+5*60*1000).toISOString()};
}
const env={IMAGE_CANDIDATE_WRITE_ENABLED:'true',
  IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN:'synthetic-github-write-token-00001'};
function githubFixture(claim,options={}) {
  const calls=[],blobs=new Map();let head=options.head||claim.claim_commit_sha;
  let pendingTree=[],storedFiles=new Map(),workflowCalls=0,refReads=0,lostPatch=false;
  const original=options.originalClaim||claim;
  const record={repository:'RollNuts/mihassei-news',
    ...Object.fromEntries(Object.entries(original).filter(([k])=>!['nonce','claim_commit_sha'].includes(k)))};
  const receiptPath='maintenance/native-image-candidates/'+claim.slug+'/upload-receipt.json';
  const publicClaim=rawRepo+'refs/heads/'+claim.branch+'/maintenance/image-upload-claims/'+claim.slug+'/claim.json';
  const articlesUrl=rawRepo+claim.expected_head_sha+'/content/articles.json';
  const contents=b=>Response.json({encoding:'base64',content:Buffer.from(b).toString('base64')});
  const fetchImpl=async(url,opts)=>{
    const body=opts.body?JSON.parse(opts.body):undefined;
    calls.push({url,method:opts.method,redirect:opts.redirect,
      authorized:Boolean(opts.headers.authorization),body});
    if(url===publicClaim){
      if(options.noPublicClaim)return new Response('not found',{status:404});
      if(options.rawRedirect)return new Response('',{status:302,headers:{location:'https://evil.example'}});
      return options.rawOversize?new Response(Buffer.alloc(16*1024+1,32),{headers:{'content-type':'text/plain'}})
        :Response.json(options.publicRecord||record);
    }
    if(url===articlesUrl)return new Response(options.articleBytes||realArticles,
      {headers:{'content-type':'text/plain'}});
    if(url===source||url===freshSource)return new Response(options.imageBytes||bytes,
      {headers:{'content-type':'image/jpeg'}});
    if(!url.startsWith(repo))throw Error('Unexpected source');
    const p=url.slice(repo.length);
    if(options.failPath===p)return new Response('not authorized',{status:options.failStatus||429});
    if(p==='')return Response.json({full_name:'RollNuts/mihassei-news',
      private:Boolean(options.private),visibility:options.private?'private':'public'});
    if(p==='/actions/workflows/free-cpu-scenes.yml'){
      workflowCalls++;
      if(options.onWorkflow)options.onWorkflow(workflowCalls);
      return Response.json({path:'.github/workflows/free-cpu-scenes.yml',
        state:options.off||options.stopOnWorkflowCall===workflowCalls?'disabled_manually':'active'});
    }
    if(p==='/git/trees/main?recursive=1')return Response.json(options.archive||{
      sha:'1'.repeat(40),truncated:false,tree:archiveEntries});
    if(p.startsWith('/git/ref/heads/')){
      assert.equal(opts.method,'GET');refReads++;
      if(options.moveOnFreshRef&&refReads>1)head='f'.repeat(40);
      return Response.json({object:{sha:head}});
    }
    if(p.startsWith('/git/refs/heads/')){
      assert.equal(opts.method,'PATCH');assert.equal(body.force,false);
      if(options.patchConflict||head!==claim.claim_commit_sha)return new Response('',{status:422});
      head='e'.repeat(40);storedFiles=new Map(pendingTree.map(row=>[row.path,blobs.get(row.sha)]));
      if(options.losePatchOnce&&!lostPatch){lostPatch=true;throw Error('response lost');}
      return Response.json({object:{sha:head}});
    }
    if(p.startsWith('/contents/maintenance/image-upload-claims/'))
      return contents(JSON.stringify(options.immutableRecord||record));
    if(p.startsWith('/contents/'+claim.path+'?ref=')){
      if(p.endsWith('ref='+claim.claim_commit_sha))return options.existingJpeg?contents(options.existingJpeg):new Response('',{status:404});
      return contents(options.corruptReadback?Buffer.from('bad'):storedFiles.get(claim.path)||Buffer.from('missing'));
    }
    if(p.startsWith('/contents/'+receiptPath+'?ref=')){
      if(p.endsWith('ref='+claim.claim_commit_sha))return options.existingReceipt?contents(options.existingReceipt):new Response('',{status:404});
      let receipt=storedFiles.get(receiptPath);
      if(options.tamperReceipt){const v=JSON.parse(receipt);v.article_sha256='0'.repeat(64);receipt=Buffer.from(JSON.stringify(v));}
      return contents(receipt||Buffer.from('{}'));
    }
    if(p==='/git/commits/'+claim.claim_commit_sha)return Response.json({tree:{sha:'c'.repeat(40)},
      parents:[{sha:options.wrongParent?'8'.repeat(40):claim.expected_head_sha}]});
    if(p==='/git/commits/'+'e'.repeat(40))return Response.json({tree:{sha:'f'.repeat(40)},
      parents:[{sha:options.wrongSavedParent?'8'.repeat(40):claim.claim_commit_sha}]});
    if(p==='/git/commits/'+'f'.repeat(40))return Response.json({tree:{sha:'f'.repeat(40)},parents:[]});
    if(p==='/git/blobs'){
      const b=Buffer.from(body.content,'base64'),id=createHash('sha1').update(Buffer.concat([
        Buffer.from('blob '+b.length+'\0'),b])).digest('hex');
      blobs.set(id,b);return Response.json({sha:id});
    }
    if(p==='/git/trees'){
      assert.equal(body.base_tree,'c'.repeat(40));pendingTree=body.tree;
      assert.deepEqual(pendingTree.map(row=>row.path),[claim.path,receiptPath]);
      return Response.json({sha:'f'.repeat(40)});
    }
    if(p==='/git/commits'){
      assert.deepEqual(body.parents,[claim.claim_commit_sha]);return Response.json({sha:'e'.repeat(40)});
    }
    throw Error('Unexpected Github request');
  };
  return {calls,fetchImpl,record,publicClaim,articlesUrl,receiptPath,
    get head(){return head;},get storedFiles(){return storedFiles;},options};
}
const mutations=f=>f.calls.filter(x=>['POST','PATCH'].includes(x.method));
const safeError=x=>JSON.parse(x.result.content[0].text).error;

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
  const {upload_ticket:ticket,...metadata}=actual.result.structuredContent;
  assert.deepEqual(metadata,{status:'verified',width:1200,height:675,
    bytes:bytes.length,sha256:sha(bytes),file_id_sha256:sha(file.file_id)});
  assert.equal(ticket.version,2);assert.match(ticket.nonce,/^[A-Za-z0-9_-]{43}$/);
  assert.equal(ticket.nonce_sha256,sha(ticket.nonce));
  assert.equal(ticket.branch_suffix,ticket.nonce_sha256.slice(0,12));
  assert.ok(Date.parse(ticket.expires_at)>Date.now()+14*60*1000);
  assert.ok(Date.parse(ticket.expires_at)<=Date.now()+15*60*1000);
  assert.deepEqual(f.calls.map(x=>x.url),[source]);
  assert.equal(f.calls[0].opts.redirect,'manual');
  assert.match(actual.result.content[0].text,/verified/);
  assert.doesNotMatch(JSON.stringify(actual),/oaiusercontent|sig=|file-abcDEF123/);
  console.log('Real JPEG probe mock bytes:',bytes.length,'local elapsed ms:',elapsedMs.toFixed(1),
    '(NOT a deployed Worker CPU measurement)');
});

test('temporary signed paths are not assumed to begin with file-',async()=>{
  const alternate={...file,download_url:'https://files.oaiusercontent.com/downloads/signed?id=123'};
  const f=fileFetcher();
  const result=await data(await send(bodyFor('probe_candidate_jpeg',{file:alternate}),f.fn));
  assert.equal(result.result.isError,false,JSON.stringify(result.result.content));
  assert.equal(f.calls[0].url,alternate.download_url);
});

test('observed regional fileParams hosts accept JPEGs only from the exact requested origin',async()=>{
  const hosts=['sdmntprwestus.oaiusercontent.com','sdmntpreastus3.oaiusercontent.com'];
  for(const host of hosts){
    const observed='https://'+host+'/delivered/image.jpg?sig=synthetic';
    const calls=[];
    const actual=await data(await send(bodyFor('probe_candidate_jpeg',
      {file:{...file,download_url:observed}}),async(url,opts)=>{
        calls.push({url,opts});
        const response=new Response(bytes,{headers:{'content-type':'image/jpeg'}});
        Object.defineProperty(response,'url',{value:observed});
        return response;
      }));
    assert.equal(actual.result.isError,false,JSON.stringify(actual.result.content));
    assert.equal(actual.result.structuredContent.sha256,sha(bytes));
    assert.equal(actual.result.structuredContent.bytes,bytes.length);
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,observed);
    assert.equal(calls[0].opts.redirect,'manual');
    assert.deepEqual(calls[0].opts.headers,{accept:'image/jpeg'},'never forward credentials to file hosts');
    assert.doesNotMatch(JSON.stringify(actual),/oaiusercontent|sig=|file-abcDEF123/);

    const other=hosts.find(value=>value!==host);
    for(const changedOrigin of [source,'https://'+other+'/elsewhere.jpg',
      'http://'+host+'/elsewhere.jpg','https://'+host+':8443/elsewhere.jpg']){
      const mismatch=await data(await send(bodyFor('probe_candidate_jpeg',
        {file:{...file,download_url:observed}}),async()=>{
          const response=new Response(bytes,{headers:{'content-type':'image/jpeg'}});
          Object.defineProperty(response,'url',{value:changedOrigin});
          return response;
        }));
      assert.equal(mismatch.result.isError,true);
      assert.deepEqual(JSON.parse(mismatch.result.content[0].text),{error:'file_unavailable'});
    }
  }
});

test('regional hosts do not admit arbitrary subdomains, suffix spoofing or unsafe URL parts',async()=>{
  const rejected=[
    'https://arbitrary.oaiusercontent.com/image.jpg',
    'https://sdmntpr.oaiusercontent.com/image.jpg',
    'https://sdmntpr'+ 'a'.repeat(41)+'.oaiusercontent.com/image.jpg',
    'https://sdmntprwestus-1.oaiusercontent.com/image.jpg',
    'https://sdmntprwestus_1.oaiusercontent.com/image.jpg',
    'https://sdmntpreastus3.oaiusercontent.com./image.jpg',
    'https://nested.sdmntprwestus.oaiusercontent.com/image.jpg',
    'https://sdmntprwestus.oaiusercontent.com.evil.example/image.jpg',
    'https://sdmntprwestus-oaiusercontent.com/image.jpg',
    'http://sdmntprwestus.oaiusercontent.com/image.jpg',
    'https://sdmntprwestus.oaiusercontent.com:8443/image.jpg',
    'https://user:password@sdmntprwestus.oaiusercontent.com/image.jpg',
    'https://sdmntprwestus.oaiusercontent.com/image.jpg#fragment',
    'https://sdmntprwestus.oaiusercontent.com/',
    'https://sdmntprwestus.oaiusercontent.com/images//image.jpg'
  ];
  for(const download_url of rejected){
    const f=fileFetcher();
    const actual=await data(await send(bodyFor('probe_candidate_jpeg',
      {file:{...file,download_url}}),f.fn));
    assert.equal(actual.result.isError,true,download_url);
    assert.equal(JSON.parse(actual.result.content[0].text).error,'untrusted_file_host');
    assert.equal(f.calls.length,0,'untrusted URL must be rejected before any fetch');
  }
});

test('regional labels have explicit one-to-forty alphanumeric bounds',async()=>{
  for(const label of ['sdmntpra','sdmntpr'+'a'.repeat(40)]){
    const f=fileFetcher();
    const result=await data(await send(bodyFor('probe_candidate_jpeg',
      {file:{...file,download_url:'https://'+label+'.oaiusercontent.com/image.jpg'}}),f.fn));
    assert.equal(result.result.isError,false,JSON.stringify(result.result.content));
    assert.equal(f.calls.length,1);
  }
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

test('rejected file references reveal only a bounded hostname and exact rejection class',async()=>{
  const secret='DO-NOT-RETURN-this-signed-value';
  const cases=[
    ['https://uploads.example.net/private/'+secret+'?sig='+secret,'hostname','uploads.example.net'],
    ['http://files.oaiusercontent.com/private/'+secret+'?sig='+secret,'scheme','files.oaiusercontent.com'],
    ['https://files.oaiusercontent.com:8443/private/'+secret+'?sig='+secret,'port','files.oaiusercontent.com'],
    ['https://user:'+secret+'@files.oaiusercontent.com/private?sig='+secret,'credentials','files.oaiusercontent.com'],
    ['https://files.oaiusercontent.com/private?sig='+secret+'#'+secret,'fragment','files.oaiusercontent.com'],
    ['https://files.oaiusercontent.com/?sig='+secret,'empty_path','files.oaiusercontent.com'],
    ['https://files.oaiusercontent.com/private//'+secret+'?sig='+secret,'path_separator','files.oaiusercontent.com'],
    ['https://'+'a'.repeat(300)+'.example/private?sig='+secret,'hostname','a'.repeat(253)]
  ];
  for(const [download_url,reason,hostname] of cases){
    const f=fileFetcher();
    const x=await data(await send(bodyFor('probe_candidate_jpeg',
      {file:{...file,download_url}}),f.fn));
    assert.equal(x.result.isError,true);
    assert.deepEqual(JSON.parse(x.result.content[0].text),{
      error:'untrusted_file_host',file_reference_diagnostic:{reason,hostname}
    });
    assert.equal(f.calls.length,0,'untrusted sources must never be fetched');
    assert.doesNotMatch(JSON.stringify(x),/DO-NOT-RETURN|private|sig=|file-abcDEF123|https?:/);
  }
});

test('unrelated errors and malformed file values never echo file references',async()=>{
  const f=fileFetcher();
  const upstream=await data(await send(bodyFor('probe_candidate_jpeg',{file}),async()=>{
    throw new Error('private request failed: '+source);
  }));
  assert.deepEqual(JSON.parse(upstream.result.content[0].text),{error:'unavailable'});
  for(const bad of ['file-abcDEF123',null,{...file,file_id:'bad'}]){
    const x=await data(await send(bodyFor('probe_candidate_jpeg',{file:bad}),f.fn));
    assert.equal(x.result.isError,true);
    assert.equal(JSON.parse(x.result.content[0].text).file_reference_diagnostic,undefined);
  }
  assert.doesNotMatch(JSON.stringify(upstream),/private|sig=|file-abcDEF123/);
});

test('version-2 write remains OFF without both explicit enablement and the new write secret',async()=>{
  const claim=syntheticClaim();
  for(const settings of [{},{IMAGE_CANDIDATE_WRITE_ENABLED:'true'},
    {...env,IMAGE_CANDIDATE_WRITE_ENABLED:'false'},
    {...env,IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN:''},
    {IMAGE_CANDIDATE_CALLER_KEY:'old-static-caller-key-ignored'}]){
    const f=fileFetcher();const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fn,settings));
    assert.equal(safeError(x),'write_disabled');assert.equal(f.calls.length,0);
  }
});

test('verified probe issues fresh random tickets, without saving or authorizing a repository claim',async()=>{
  const f=fileFetcher(),tickets=[];
  for(let i=0;i<2;i++)tickets.push((await data(await send(bodyFor('probe_candidate_jpeg',{file}),f.fn))).result.structuredContent.upload_ticket);
  assert.notEqual(tickets[0].nonce,tickets[1].nonce);
  assert.notEqual(tickets[0].nonce_sha256,tickets[1].nonce_sha256);
  assert.equal(f.calls.length,2);
  assert.ok(f.calls.every(x=>x.url===source&&x.opts.method==='GET'));
});

test('AuthNone stages only an exact owner-claimed JPEG and atomically stores its truthful transport receipt',async()=>{
  const claim=syntheticClaim(),f=githubFixture(claim);
  const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
  assert.equal(x.result.isError,false,JSON.stringify(x.result.content));
  const result=x.result.structuredContent;
  assert.equal(result.status,'saved');assert.equal(result.commit_sha,'e'.repeat(40));
  assert.equal(result.jpeg_sha256,sha(bytes));assert.equal(result.bytes,bytes.length);
  assert.equal(result.published,false);assert.equal(result.visual_review,'not_performed');
  assert.equal(f.calls[0].url,f.publicClaim);assert.equal(f.calls[0].authorized,false);
  assert.ok(f.calls.findIndex(c=>c.authorized)>0,'public branch capability precedes every authenticated API');
  assert.equal(f.head,'e'.repeat(40));assert.equal(f.storedFiles.size,2);
  const receipt=JSON.parse(f.storedFiles.get(f.receiptPath));
  assert.equal(receipt.version,2);assert.equal(receipt.status,'awaiting_visual_review');
  assert.equal(receipt.file_id_sha256,sha(file.file_id));assert.equal(receipt.claim_commit_sha,claim.claim_commit_sha);
  assert.equal(result.receipt_sha256,sha(f.storedFiles.get(f.receiptPath)));
  assert.equal(mutations(f).length,5,'two blobs, one tree, one commit, one ref update');
  assert.ok(mutations(f).every(c=>c.authorized));
  assert.ok(f.calls.filter(c=>c.url.startsWith(rawRepo)||c.url===source).every(c=>!c.authorized));
  assert.ok(f.calls.every(c=>c.redirect==='manual'));
  assert.doesNotMatch(JSON.stringify({result,receipt}),new RegExp(claim.nonce+'|synthetic-github|sig=|file-abcDEF123'));
  assert.ok(!Object.hasOwn(receipt,'nonce'));
});

test('missing or forged public branch claims never fetch the image or use privileged GitHub APIs',async()=>{
  const claim=syntheticClaim();
  for(const options of [{noPublicClaim:true},{rawRedirect:true},{rawOversize:true},
    {publicRecord:{}},{publicRecord:{...githubFixture(claim).record,repository:'attacker/fork'}},
    {publicRecord:{...githubFixture(claim).record,nonce_sha256:'0'.repeat(64)}},
    {publicRecord:{...githubFixture(claim).record,nonce:claim.nonce}}]){
    const f=githubFixture(claim,options);
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env,
      {authorization:'Bearer pretending-to-be-an-owner'}));
    assert.equal(x.result.isError,true);
    assert.equal(f.calls.length,1);assert.equal(f.calls[0].url,f.publicClaim);
    assert.equal(f.calls[0].authorized,false);assert.equal(mutations(f).length,0);
    assert.doesNotMatch(JSON.stringify(x),new RegExp(claim.nonce+'|sig=|synthetic-github'));
  }
});

test('legacy claims, wrong nonce preimages, expired claims and alternate destinations fail before any network',async()=>{
  const claim=syntheticClaim();
  for(const change of [c=>{c.version=1;},c=>{c.nonce='z'.repeat(43);},
    c=>{c.expires_at='2000-01-01T00:00:00Z';},c=>{c.path='content/articles.json';},
    c=>{c.branch='main';},c=>{c.file_id_sha256=sha(file.file_id);},
    c=>{c.claim_commit_sha=c.expected_head_sha;}]){
    const c={...claim};change(c);const f=githubFixture(claim);
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim:c}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);assert.equal(f.calls.length,0);
  }
});

test('the real current article snapshot is read without base64 truncation and bound to the canonical fingerprint',async()=>{
  assert.ok(realArticles.length>500*1024&&realArticles.length<4*1024*1024);
  const claim=syntheticClaim(),f=githubFixture(claim);
  const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
  assert.equal(x.result.isError,false,JSON.stringify(x.result.content));
  assert.ok(f.calls.some(c=>c.url===f.articlesUrl&&!c.authorized));
  assert.ok(f.calls.some(c=>c.url.endsWith('/git/trees/main?recursive=1')));
  assert.ok(archiveEntries.some(e=>e.path.includes('/photo-issue-')),'gallery JPEGs exist outside the article manifest');
  for(const articleBytes of [Buffer.from(JSON.stringify(articles.map(a=>a.slug===claim.slug?{...a,title:'Changed body direction'}:a))),
    Buffer.from('[]'),Buffer.alloc(4*1024*1024+1,32)]){
    const changed=githubFixture(claim,{articleBytes});
    const fail=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),changed.fetchImpl,env));
    assert.equal(fail.result.isError,true);assert.equal(mutations(changed).length,0);
  }
});

test('private repo, inactive workflow, unsafe source parent, and immutable-claim mismatch cannot write',async()=>{
  const claim=syntheticClaim();
  for(const options of [{private:true},{off:true},{wrongParent:true},
    {immutableRecord:{...githubFixture(claim).record,nonce_sha256:'0'.repeat(64)}}]){
    const f=githubFixture(claim,options);
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);assert.equal(mutations(f).length,0);
  }
});

test('archive cap includes gallery JPEGs and rejects truncated, malformed or oversized inventories',async()=>{
  const claim=syntheticClaim();
  const base={sha:'1'.repeat(40),truncated:false,tree:archiveEntries};
  const sample=archiveEntries.find(row=>row.path.endsWith('.jpg'));
  const huge={path:'assets/generated/photo-issue-extra.jpg',type:'blob',mode:'100644',size:1536*1024*1024};
  for(const archive of [{...base,tree:[...archiveEntries,huge]}, {...base,truncated:true},
    {...base,tree:[{...huge,size:-1}]},{...base,tree:[{...huge,mode:'120000'}]},
    {...base,tree:[sample,sample]}, {...base,padding:'x'.repeat(2*1024*1024)}]){
    const f=githubFixture(claim,{archive});
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);
    assert.equal(mutations(f).length,0);
  }
});

test('stops and concurrent branch changes are checked before blob writes and before the atomic ref update',async()=>{
  const claim=syntheticClaim();
  for(const options of [{stopOnWorkflowCall:2},{stopOnWorkflowCall:3},{moveOnFreshRef:true},{patchConflict:true}]){
    const f=githubFixture(claim,options);
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);assert.equal(f.storedFiles.size,0);
    if(options.stopOnWorkflowCall===2)assert.equal(mutations(f).length,0);
    else assert.equal(f.calls.filter(c=>c.method==='PATCH').length,options.patchConflict?1:0);
  }
});

test('claim expiry during a live guard read stops before blobs or before the atomic ref update',async()=>{
  const original=Date.now;let clock=original();
  try{
    Date.now=()=>clock;
    for(const expireOn of [2,3]){
      clock=original();const claim=syntheticClaim();
      const f=githubFixture(claim,{onWorkflow:n=>{if(n===expireOn)clock=Date.parse(claim.expires_at)+1;}});
      const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
      assert.equal(safeError(x),'expired_claim');assert.equal(f.storedFiles.size,0);
      assert.equal(f.calls.filter(c=>c.method==='PATCH').length,0);
      if(expireOn===2)assert.equal(mutations(f).length,0);
    }
  }finally{Date.now=original;}
});

test('a fresh transport file ID and URL reuse only the same approved bytes without rewriting the first receipt',async()=>{
  const claim=syntheticClaim(),f=githubFixture(claim);
  const first=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
  assert.equal(first.result.isError,false);
  const savedReceipt=Buffer.from(f.storedFiles.get(f.receiptPath)),writes=mutations(f).length;
  const reuploaded={...file,file_id:'file-freshTransport987',download_url:freshSource};
  const replay=await data(await send(bodyFor('stage_candidate_jpeg',{file:reuploaded,claim}),f.fetchImpl,env));
  assert.equal(replay.result.structuredContent.status,'reused');
  assert.equal(replay.result.structuredContent.file_id_sha256,sha(file.file_id),'retain original successful transport');
  assert.deepEqual(f.storedFiles.get(f.receiptPath),savedReceipt);assert.equal(mutations(f).length,writes);
  const changed=Buffer.from(bytes);changed[changed.length-3]^=1;f.options.imageBytes=changed;
  const mismatch=await data(await send(bodyFor('stage_candidate_jpeg',{file:reuploaded,claim}),f.fetchImpl,env));
  assert.equal(safeError(mismatch),'claim_mismatch');assert.equal(mutations(f).length,writes);
});

test('lost update response recovers the exact atomic JPEG+receipt commit without generation or new writes',async()=>{
  const claim=syntheticClaim(),f=githubFixture(claim,{losePatchOnce:true});
  const first=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
  assert.equal(first.result.isError,true);assert.equal(f.head,'e'.repeat(40));assert.equal(f.storedFiles.size,2);
  const writes=mutations(f).length;
  const retry=await data(await send(bodyFor('stage_candidate_jpeg',
    {file:{...file,file_id:'file-newIDAfterFailure',download_url:freshSource},claim}),f.fetchImpl,env));
  assert.equal(retry.result.structuredContent.status,'reused');assert.equal(mutations(f).length,writes);
});

test('older partial files, cross-claim replay, modified receipt and non-child branch heads never authorize reuse',async()=>{
  const claim=syntheticClaim();
  for(const options of [{existingJpeg:bytes},{existingReceipt:Buffer.from('{}')},
    {head:'f'.repeat(40)}]){
    const f=githubFixture(claim,options);
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);assert.equal(mutations(f).length,0);
  }
  const f=githubFixture(claim);
  assert.equal((await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env))).result.isError,false);
  const writes=mutations(f).length;
  const altered={...claim,expires_at:new Date(Date.now()+6*60*1000).toISOString()};
  assert.equal((await data(await send(bodyFor('stage_candidate_jpeg',{file,claim:altered}),f.fetchImpl,env))).result.isError,true);
  f.options.tamperReceipt=true;
  assert.equal((await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env))).result.isError,true);
  f.options.tamperReceipt=false;f.options.wrongSavedParent=true;
  assert.equal((await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env))).result.isError,true);
  assert.equal(mutations(f).length,writes);
});

test('GitHub authorization, quota and readback failures never report a stored candidate as verified',async()=>{
  const claim=syntheticClaim();
  for(const status of [401,403,429]){
    const f=githubFixture(claim,{failPath:'/git/ref/heads/'+claim.branch,failStatus:status});
    const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
    assert.equal(x.result.isError,true);assert.equal(mutations(f).length,0);
  }
  const f=githubFixture(claim,{corruptReadback:true});
  const x=await data(await send(bodyFor('stage_candidate_jpeg',{file,claim}),f.fetchImpl,env));
  assert.equal(safeError(x),'candidate_readback_failed');
});
