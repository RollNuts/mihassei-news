// Separate, stateless MCP for ChatGPT's documented fileParams input.
// No image generation/conversion and no access to the existing read_image_control.
// https://developers.openai.com/plugins/reference#define-file-inputs
const REPO="RollNuts/mihassei-news";
const API="https://api.github.com/repos/"+REPO;
const RAW="https://raw.githubusercontent.com/"+REPO+"/";
// fileParams uses regional CDN labels; keep that single-label family bounded.
// Do not allow arbitrary oaiusercontent.com subdomains or response-origin hops.
const FILE_HOST="files.oaiusercontent.com";
const REGIONAL_FILE_HOST=/^sdmntpr[a-z0-9]{1,40}\.oaiusercontent\.com$/;
const trustedFileHost=host=>host===FILE_HOST||REGIONAL_FILE_HOST.test(host);
const MAX_JPEG=700*1024;
const MAX_CLAIM_BYTES=16*1024;
const MAX_ARTICLES_BYTES=4*1024*1024;
const MAX_TREE_RESPONSE=2*1024*1024;
const MAX_CONTENTS_RESPONSE=2*1024*1024;
const MAX_GITHUB_RESPONSE=256*1024;
const ARCHIVE_MAX=1536*1024*1024;
const KEY=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SHA=/^[a-f0-9]{64}$/;
const GIT=/^[a-f0-9]{40}$/;
const VERSIONS=["2025-06-18","2025-11-25"];
const isObj=x=>x!==null&&typeof x==="object"&&!Array.isArray(x);
const validToken=x=>typeof x==="string"&&x.length>=20&&x.length<=255&&!/[\s\x00-\x1f\x7f]/.test(x);
const fileSchema={type:"object",properties:{
  download_url:{type:"string"},file_id:{type:"string"},mime_type:{type:"string"},file_name:{type:"string"}
},required:["download_url","file_id"],additionalProperties:false};
const PROBE={
  name:"probe_candidate_jpeg",
  title:"Check a prepared JPEG",
  description:"Read and validate one ChatGPT-provided JPEG without saving or generating anything. Returns dimensions, size and hashes. Rejected file references report only the rejection class and hostname, never the download URL.",
  inputSchema:{type:"object",properties:{file:fileSchema},required:["file"],additionalProperties:false},
  annotations:{readOnlyHint:true,destructiveHint:false,openWorldHint:false},
  _meta:{"openai/fileParams":["file"]}
};
const STAGE={
  name:"stage_candidate_jpeg",
  title:"Stage an approved JPEG candidate",
  description:"Disabled until the owner authorizes a repository-limited GitHub write secret and explicitly enables it. A version-2 owner-written claim and its one-time nonce authorize only exact JPEG bytes on the fixed candidate branch. No main write or visual approval.",
  inputSchema:{type:"object",properties:{
    file:fileSchema,
    claim:{type:"object",properties:{
      version:{type:"integer",enum:[2]},slug:{type:"string"},article_sha256:{type:"string"},branch:{type:"string"},
      expected_head_sha:{type:"string"},claim_commit_sha:{type:"string"},path:{type:"string"},
      jpeg_sha256:{type:"string"},jpeg_bytes:{type:"integer"},
      nonce_sha256:{type:"string"},
      nonce:{type:"string"},expires_at:{type:"string"}
    },required:["version","slug","article_sha256","branch","expected_head_sha","path","jpeg_sha256",
                "jpeg_bytes","nonce_sha256","nonce","expires_at","claim_commit_sha"],
      additionalProperties:false}
  },required:["file","claim"],additionalProperties:false},
  annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true,openWorldHint:false},
  _meta:{"openai/fileParams":["file"]}
};

function reply(body,status=200,more={}) {
  return new Response(body===null?null:JSON.stringify(body),{status,headers:{
    "content-type":"application/json; charset=utf-8","cache-control":"no-store",
    "x-content-type-options":"nosniff","x-robots-tag":"noindex",...more
  }});
}
const rpcError=(id,code,message,status=200)=>reply({jsonrpc:"2.0",id,error:{code,message}},status);
const toolResult=(id,data,isError=false)=>reply({jsonrpc:"2.0",id,result:{
  content:[{type:"text",text:JSON.stringify(data)}],...(!isError?{structuredContent:data}:{}),isError
}});
const failure=(code)=>{const e=new Error(code);e.safeCode=code;return e;};
const FILE_REFERENCE_DIAGNOSTIC=Symbol("fileReferenceDiagnostic");
function allowedFile(file) {
  if(!isObj(file)||Object.keys(file).some(k=>!["download_url","file_id","mime_type","file_name"].includes(k))
     ||typeof file.file_id!=="string"||!/^file[-_][A-Za-z0-9_-]{5,128}$/.test(file.file_id)
     ||typeof file.download_url!=="string"||file.download_url.length>4096
     ||(file.mime_type!==undefined&&file.mime_type!=="image/jpeg")
     ||(file.file_name!==undefined&&(typeof file.file_name!=="string"||
        file.file_name.length>160||!file.file_name.toLowerCase().endsWith(".jpg")&&!file.file_name.toLowerCase().endsWith(".jpeg"))))
    throw failure("invalid_file_reference");
  let url;
  try{url=new URL(file.download_url);}catch(_){throw failure("invalid_file_reference");}
  const reason=url.protocol!=="https:"?"scheme":!trustedFileHost(url.hostname)?"hostname":
    url.port?"port":url.username||url.password?"credentials":url.hash?"fragment":
    url.pathname==="/"?"empty_path":url.pathname.includes("//")?"path_separator":null;
  if(reason){
    const error=failure("untrusted_file_host");
    // A probe may report only the bounded parsed hostname and rejection class.
    // Never retain the signed URL, path, query, file ID or URL credentials.
    error[FILE_REFERENCE_DIAGNOSTIC]={reason,hostname:url.hostname.slice(0,253)};
    throw error;
  }
  return url.href;
}
async function shaHex(bytes){
  const sum=await crypto.subtle.digest("SHA-256",bytes);
  return Array.from(new Uint8Array(sum),b=>b.toString(16).padStart(2,"0")).join("");
}
const encoder=new TextEncoder();
const shaText=x=>shaHex(encoder.encode(x));
async function limited(response,max){
  const length=response.headers.get("content-length");
  if(length!==null&&(!/^\d+$/.test(length)||Number(length)>max))throw failure("file_too_large");
  if(!response.body)throw failure("empty_response");
  const reader=response.body.getReader(),chunks=[];
  let size=0;
  try {
    while(true) {
      const {done,value}=await reader.read();
      if(done)break;
      size+=value.length;
      if(size>max)throw failure("file_too_large");
      chunks.push(value);
    }
  } finally {try{await reader.cancel();}catch(_){}}
  const joined=new Uint8Array(size);let offset=0;
  for(const part of chunks){joined.set(part,offset);offset+=part.length;}
  return joined;
}
function jpegSize(b){
  if(b.length<128||b[0]!==255||b[1]!==216||b.at(-2)!==255||b.at(-1)!==217)
    throw failure("invalid_jpeg");
  let i=2,size=null;
  while(i+3<b.length){
    if(b[i++]!==255)throw failure("invalid_jpeg");
    while(b[i]===255)i++;
    const marker=b[i++];
    if(marker===0xd9||marker===0xda)break;
    if(marker===0xd8||marker===0x01||(marker>=0xd0&&marker<=0xd7))continue;
    if(i+1>=b.length)throw failure("invalid_jpeg");
    const n=(b[i]<<8)|b[i+1];if(n<2||i+n>b.length)throw failure("invalid_jpeg");
    if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)){
      if(n<7)throw failure("invalid_jpeg");
      // SOF stores height, then width (after sample precision).
      size=[(b[i+5]<<8)|b[i+6],(b[i+3]<<8)|b[i+4]];
      break;
    }
    i+=n;
  }
  if(!size||size[0]!==1200||size[1]!==675)throw failure("wrong_jpeg_dimensions");
  return size;
}
async function readFile(file,fetchImpl){
  const url=allowedFile(file);
  const result=await fetchImpl(url,{method:"GET",redirect:"manual",cache:"no-store",
    headers:{"accept":"image/jpeg"},signal:AbortSignal.timeout(5000)});
  if(result.status!==200||result.url&&new URL(result.url).origin!==new URL(url).origin)
    throw failure("file_unavailable");
  const type=(result.headers.get("content-type")||"").split(";",1)[0].trim().toLowerCase();
  if(type!=="image/jpeg"&&type!=="application/octet-stream")throw failure("unexpected_file_type");
  const bytes=await limited(result,MAX_JPEG);
  jpegSize(bytes);
  return {bytes,sha256:await shaHex(bytes),file_id_sha256:await shaText(file.file_id)};
}
function encodedBase64(bytes){
  // Binary-to-base64 without node Buffer or external libraries, in 48 KiB blocks.
  const block=48*1024,parts=[];
  for(let i=0;i<bytes.length;i+=block)parts.push(btoa(String.fromCharCode(...bytes.subarray(i,i+block))));
  return parts.join("");
}
function decodedBase64(s,maxBytes=MAX_JPEG){
  // GitHub Content responses wrap base64; inventory and JPEG need distinct caps.
  const maxEncoded=Math.ceil(maxBytes/3)*4;
  const maxWrapped=maxEncoded+Math.ceil(maxEncoded/60)*2+128;
  if(typeof s!=="string"||s.length>maxWrapped)throw failure("invalid_github_response");
  const clean=s.replace(/\s/g,"");
  if(clean.length>maxEncoded||!/^[A-Za-z0-9+/]*={0,2}$/.test(clean))
    throw failure("invalid_github_response");
  let bytes;
  try{bytes=Uint8Array.from(atob(clean),x=>x.charCodeAt(0));}
  catch(_){throw failure("invalid_github_response");}
  if(bytes.length>maxBytes)throw failure("invalid_github_response");
  return bytes;
}
const CLAIM_FIELDS=["version","slug","article_sha256","branch","expected_head_sha",
  "claim_commit_sha","path","jpeg_sha256","jpeg_bytes","nonce_sha256","nonce","expires_at"];
const RECORD_FIELDS=["version","repository","slug","article_sha256","branch","expected_head_sha",
  "path","jpeg_sha256","jpeg_bytes","nonce_sha256","expires_at"];
function exactFields(value,fields){
  return isObj(value)&&Object.keys(value).length===fields.length&&fields.every(k=>Object.hasOwn(value,k));
}
function validateClaim(claim){
  if(!exactFields(claim,CLAIM_FIELDS)||claim.version!==2||!KEY.test(claim.slug||"")||
     !SHA.test(claim.article_sha256||"")||!SHA.test(claim.jpeg_sha256||"")||
     !SHA.test(claim.nonce_sha256||"")||
     (!GIT.test(claim.expected_head_sha||"")||!GIT.test(claim.claim_commit_sha||"")||
      claim.expected_head_sha===claim.claim_commit_sha)||!Number.isInteger(claim.jpeg_bytes)||
     claim.jpeg_bytes<128||claim.jpeg_bytes>MAX_JPEG||
     typeof claim.nonce!=="string"||!/^[A-Za-z0-9_-]{43}$/.test(claim.nonce)||
     typeof claim.expires_at!=="string"||!/[zZ]$/.test(claim.expires_at))
    throw failure("invalid_claim");
  const branch="native-image-candidate/"+claim.slug+"/"+
    claim.article_sha256.slice(0,16)+"-"+claim.nonce_sha256.slice(0,12);
  if(claim.branch!==branch||
     claim.path!=="maintenance/native-image-candidates/"+claim.slug+"/candidate.jpg")
    throw failure("invalid_claim_destination");
  const exp=Date.parse(claim.expires_at);
  if(!Number.isFinite(exp)||exp<Date.now()||exp>Date.now()+15*60*1000)throw failure("expired_claim");
}
async function uploadTicket(){
  const random=crypto.getRandomValues(new Uint8Array(32));
  const nonce=encodedBase64(random).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
  const hash=await shaText(nonce);
  // The nonce is returned only in this intentional, private tool response.
  // Callers publish its hash, never this preimage, in the repository claim.
  return {version:2,nonce,nonce_sha256:hash,branch_suffix:hash.slice(0,12),
    expires_at:new Date(Date.now()+15*60*1000).toISOString()};
}
function expectedRecord(claim){
  return Object.fromEntries(RECORD_FIELDS.map(k=>[k,k==="repository"?REPO:claim[k]]));
}
function requireRecord(record,claim){
  const expected=expectedRecord(claim);
  if(!exactFields(record,RECORD_FIELDS)||!RECORD_FIELDS.every(k=>record[k]===expected[k]))
    throw failure("claim_mismatch");
}
const claimPath=claim=>"maintenance/image-upload-claims/"+claim.slug+"/claim.json";
const receiptPath=claim=>"maintenance/native-image-candidates/"+claim.slug+"/upload-receipt.json";
async function rawJson(path,max,fetchImpl,code){
  // Every path is built here from a fixed repository and validated branch/SHA.
  // No caller URL, credential, nonce or authorization header is forwarded.
  const url=RAW+path;
  const res=await fetchImpl(url,{method:"GET",redirect:"manual",cache:"no-store",
    headers:{accept:"application/json"},signal:AbortSignal.timeout(5000)});
  if(res.status!==200||res.url&&res.url!==url)throw failure(code);
  const type=(res.headers.get("content-type")||"").split(";",1)[0].trim().toLowerCase();
  if(type!=="application/json"&&type!=="text/plain")throw failure(code);
  try{return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(await limited(res,max)));}
  catch(error){if(error.safeCode)throw error;throw failure(code);}
}
async function authorizeClaim(claim,fetchImpl){
  if(await shaText(claim.nonce)!==claim.nonce_sha256)throw failure("claim_mismatch");
  // A raw commit alone can refer to a fork-network object. This live owner-
  // written branch, not an arbitrary commit SHA, establishes the capability.
  const branch=claim.branch.split("/").map(encodeURIComponent).join("/");
  const record=await rawJson("refs/heads/"+branch+"/"+claimPath(claim),MAX_CLAIM_BYTES,
    fetchImpl,"claim_unavailable");
  requireRecord(record,claim);
}
async function verifyArticleSource(claim,fetchImpl){
  // Preserve reviewed working-commit drafts, including articles not yet on main.
  // This is capture, not current-main adoption or publication authorization.
  const articles=await rawJson(claim.expected_head_sha+"/content/articles.json",
    MAX_ARTICLES_BYTES,fetchImpl,"article_source_unavailable");
  if(!Array.isArray(articles))throw failure("article_source_unavailable");
  const matches=articles.filter(a=>isObj(a)&&a.slug===claim.slug);
  if(matches.length!==1)throw failure("article_mismatch");
  const article=matches[0],fields={};
  for(const key of ["category","paragraphs","scene_prompt","slug","summary","title"])
    fields[key]=Object.hasOwn(article,key)?article[key]:"";
  if(await shaText(JSON.stringify(fields))!==claim.article_sha256)throw failure("article_mismatch");
}
async function github(path,method,token,fetchImpl,body,allow404=false){
  const url=API+path;
  const res=await fetchImpl(url,{
    method,redirect:"manual",cache:"no-store",signal:AbortSignal.timeout(5000),
    headers:{"accept":"application/vnd.github+json","x-github-api-version":"2022-11-28",
      "user-agent":"veripsa-image-receiver",
      ...(token?{"authorization":"Bearer "+token}:{}),
      ...(body?{"content-type":"application/json"}:{})},
    ...(body?{body:JSON.stringify(body)}:{})
  });
  if(allow404&&res.status===404)return null;
  if(res.status<200||res.status>=300)throw failure(res.status===409||res.status===422?"candidate_conflict":"github_unavailable");
  const largeContents=method==="GET"&&path.startsWith("/contents/maintenance/native-image-candidates/");
  const cap=method==="GET"&&path==="/git/trees/main?recursive=1"?MAX_TREE_RESPONSE:
    largeContents?MAX_CONTENTS_RESPONSE:MAX_GITHUB_RESPONSE;
  const text=new TextDecoder("utf-8",{fatal:true}).decode(await limited(res,cap));
  try{return JSON.parse(text);}catch(_){throw failure("invalid_github_response");}
}
async function imageWorkflow(fetchImpl){
  const workflow=await github("/actions/workflows/free-cpu-scenes.yml","GET",null,fetchImpl);
  if(workflow.path!==".github/workflows/free-cpu-scenes.yml"||workflow.state!=="active")
    throw failure("image_workflow_not_active");
}
async function checkArchive(token,bytes,fetchImpl){
  const tree=await github("/git/trees/main?recursive=1","GET",token,fetchImpl);
  if(tree.truncated!==false||!Array.isArray(tree.tree)||!GIT.test(tree.sha||""))
    throw failure("invalid_archive_inventory");
  let total=0;const paths=new Set();
  for(const entry of tree.tree){
    if(!isObj(entry)||typeof entry.path!=="string")throw failure("invalid_archive_inventory");
    if(!/^assets\/generated\/.*\.jpe?g$/i.test(entry.path))continue;
    if(paths.has(entry.path)||entry.type!=="blob"||!["100644","100755"].includes(entry.mode)||
       !Number.isSafeInteger(entry.size)||entry.size<0)throw failure("invalid_archive_inventory");
    paths.add(entry.path);total+=entry.size;
    if(!Number.isSafeInteger(total))throw failure("invalid_archive_inventory");
  }
  if(total+bytes>ARCHIVE_MAX)throw failure("archive_limit");
}
async function contentBytes(path,ref,max,token,fetchImpl,allow404=false){
  const result=await github("/contents/"+path+"?ref="+ref,"GET",token,fetchImpl,undefined,allow404);
  if(result===null)return null;
  if(result.encoding!=="base64"||typeof result.content!=="string")throw failure("invalid_github_response");
  return decodedBase64(result.content,max);
}
function transportReceipt(claim,image){
  return {...expectedRecord(claim),kind:"claimed_jpeg_transport",status:"awaiting_visual_review",
    claim_commit_sha:claim.claim_commit_sha,width:1200,height:675,
    file_id_sha256:image.file_id_sha256,received_at:new Date().toISOString()};
}
async function verifiedCandidate(claim,image,ref,token,fetchImpl,status){
  const jpeg=await contentBytes(claim.path,ref,MAX_JPEG,token,fetchImpl);
  const raw=await contentBytes(receiptPath(claim),ref,MAX_CLAIM_BYTES,token,fetchImpl);
  let receipt;
  try{receipt=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(raw));}
  catch(_){throw failure("candidate_readback_failed");}
  const fields=[...RECORD_FIELDS,"kind","status","claim_commit_sha","width","height","file_id_sha256","received_at"];
  const expected=expectedRecord(claim),received=Date.parse(receipt?.received_at);
  if(!exactFields(receipt,fields)||!RECORD_FIELDS.every(k=>receipt[k]===expected[k])||
     receipt.kind!=="claimed_jpeg_transport"||receipt.status!=="awaiting_visual_review"||
     receipt.claim_commit_sha!==claim.claim_commit_sha||receipt.width!==1200||receipt.height!==675||
     !SHA.test(receipt.file_id_sha256||"")||!Number.isFinite(received)||received>Date.now()||
     received>Date.parse(claim.expires_at)||jpeg.length!==claim.jpeg_bytes||
     await shaHex(jpeg)!==claim.jpeg_sha256)throw failure("candidate_readback_failed");
  return {status,branch:claim.branch,path:claim.path,commit_sha:ref,
    jpeg_sha256:claim.jpeg_sha256,bytes:claim.jpeg_bytes,
    receipt_path:receiptPath(claim),receipt_sha256:await shaHex(raw),
    file_id_sha256:receipt.file_id_sha256,visual_review:"not_performed",published:false};
}
async function stageFile(req,env,file,claim,fetchImpl){
  // OFF by default. The owner must separately approve a new, repository-limited
  // write Secret and explicit enablement. AuthNone uses a per-upload capability,
  // never a caller-supplied static Bearer key or a transient file identity.
  if(env.IMAGE_CANDIDATE_WRITE_ENABLED!=="true"||
     !validToken(env.IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN))throw failure("write_disabled");
  validateClaim(claim);
  // This owner-branch proof MUST precede all file reads and token-bearing APIs.
  await authorizeClaim(claim,fetchImpl);
  const token=env.IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN;
  const repo=await github("","GET",token,fetchImpl);
  if(repo.full_name!==REPO||repo.private!==false||repo.visibility!=="public")throw failure("repository_not_public");
  await imageWorkflow(fetchImpl);
  const branchPath=claim.branch.split("/").map(encodeURIComponent).join("/");
  const getRef="/git/ref/heads/"+branchPath,updateRef="/git/refs/heads/"+branchPath;
  const branch=await github(getRef,"GET",token,fetchImpl);
  if(!GIT.test(branch.object?.sha||""))throw failure("invalid_github_response");
  const stored=await contentBytes(claimPath(claim),claim.claim_commit_sha,MAX_CLAIM_BYTES,token,fetchImpl);
  let record;
  try{record=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(stored));}
  catch(_){throw failure("invalid_claim");}
  requireRecord(record,claim);
  const parent=await github("/git/commits/"+claim.claim_commit_sha,"GET",token,fetchImpl);
  if(!GIT.test(parent.tree?.sha||"")||
     parent.parents?.length!==1||parent.parents[0]?.sha!==claim.expected_head_sha)
    throw failure("claim_parent_mismatch");
  await verifyArticleSource(claim,fetchImpl);
  const image=await readFile(file,fetchImpl);
  if(image.sha256!==claim.jpeg_sha256||image.bytes.length!==claim.jpeg_bytes)throw failure("claim_mismatch");
  if(branch.object.sha!==claim.claim_commit_sha){
    const saved=await github("/git/commits/"+branch.object.sha,"GET",token,fetchImpl);
    if(saved.parents?.length!==1||saved.parents[0]?.sha!==claim.claim_commit_sha)
      throw failure("candidate_conflict");
    return verifiedCandidate(claim,image,branch.object.sha,token,fetchImpl,"reused");
  }
  // Never attach a new claim to an older or partially stored JPEG/receipt.
  const existing=await contentBytes(claim.path,claim.claim_commit_sha,MAX_JPEG,token,fetchImpl,true);
  const oldReceipt=await contentBytes(receiptPath(claim),claim.claim_commit_sha,MAX_CLAIM_BYTES,token,fetchImpl,true);
  if(existing||oldReceipt)throw failure("candidate_conflict");
  await checkArchive(token,image.bytes.length,fetchImpl);
  await imageWorkflow(fetchImpl);
  validateClaim(claim);
  const receiptText=JSON.stringify(transportReceipt(claim,image),null,2)+"\n";
  const receiptBytes=encoder.encode(receiptText);
  const blob=await github("/git/blobs","POST",token,fetchImpl,{
    content:encodedBase64(image.bytes),encoding:"base64"});
  const receiptBlob=await github("/git/blobs","POST",token,fetchImpl,{
    content:encodedBase64(receiptBytes),encoding:"base64"});
  if(!GIT.test(blob.sha||"")||!GIT.test(receiptBlob.sha||""))throw failure("invalid_github_response");
  // JPEG and truthful transport receipt become visible in ONE commit/ref update.
  const tree=await github("/git/trees","POST",token,fetchImpl,{
    base_tree:parent.tree.sha,tree:[
      {path:claim.path,mode:"100644",type:"blob",sha:blob.sha},
      {path:receiptPath(claim),mode:"100644",type:"blob",sha:receiptBlob.sha}]});
  if(!GIT.test(tree.sha||""))throw failure("invalid_github_response");
  const commit=await github("/git/commits","POST",token,fetchImpl,{
    message:"Stage claimed JPEG candidate for "+claim.slug,
    tree:tree.sha,parents:[claim.claim_commit_sha]});
  if(!GIT.test(commit.sha||""))throw failure("invalid_github_response");
  await checkArchive(token,image.bytes.length,fetchImpl);
  const fresh=await github(getRef,"GET",token,fetchImpl);
  if(fresh.object?.sha!==claim.claim_commit_sha)throw failure("candidate_conflict");
  await imageWorkflow(fetchImpl);
  validateClaim(claim);
  const updated=await github(updateRef,"PATCH",token,fetchImpl,{sha:commit.sha,force:false});
  if(updated.object?.sha!==commit.sha)throw failure("candidate_conflict");
  return verifiedCandidate(claim,image,commit.sha,token,fetchImpl,"saved");
}
async function readJson(req,max=10000) {
  const content=await limited(req,max);
  try{return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(content));}
  catch(_){throw failure("malformed_request");}
}

export async function imageCandidateMcp(req,url,env,fetchImpl=fetch){
  const origin=req.headers.get("origin");
  if(origin&&origin!==url.origin&&origin!=="https://chatgpt.com")
    return rpcError(null,-32600,"Invalid origin",403);
  if(req.method!=="POST")return reply({error:"method not allowed"},405,{allow:"POST"});
  if(url.search)return rpcError(null,-32600,"Invalid request",400);
  const accept=req.headers.get("accept")||"";
  if(!accept.includes("application/json")||!accept.includes("text/event-stream"))
    return rpcError(null,-32600,"Both MCP Accept types are required",406);
  if(!/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?$/i.test(req.headers.get("content-type")||""))
    return rpcError(null,-32600,"JSON required",415);
  const version=req.headers.get("mcp-protocol-version");
  if(version&&!VERSIONS.includes(version))return rpcError(null,-32600,"Unsupported protocol version",400);
  let m;
  try{m=await readJson(req);}catch(_){return rpcError(null,-32700,"Invalid JSON",400);}
  if(!isObj(m)||m.jsonrpc!=="2.0"||typeof m.method!=="string"||
     m.params!==undefined&&!isObj(m.params)||"error" in m||"result" in m)
    return rpcError(null,-32600,"Invalid request",400);
  if(!Object.hasOwn(m,"id"))return reply(null,202);
  const id=m.id,p=m.params||{};
  if(!(typeof id==="string"||Number.isSafeInteger(id)))return rpcError(null,-32600,"Invalid ID",400);
  if(m.method==="initialize"){
    if(typeof p.protocolVersion!=="string"||!isObj(p.capabilities)||!isObj(p.clientInfo))
      return rpcError(id,-32602,"Invalid initialize parameters");
    return reply({jsonrpc:"2.0",id,result:{protocolVersion:VERSIONS.includes(p.protocolVersion)?p.protocolVersion:VERSIONS.at(-1),
      capabilities:{tools:{}},serverInfo:{name:"veripsa-candidate-file-receiver",version:"1.0.0"}}});
  }
  if(!version)return rpcError(id,-32600,"MCP protocol version required",400);
  if(m.method==="ping")return reply({jsonrpc:"2.0",id,result:{}});
  if(m.method==="tools/list")return reply({jsonrpc:"2.0",id,result:{tools:[PROBE,STAGE]}});
  if(m.method!=="tools/call")return rpcError(id,-32601,"Method not found");
  if(!isObj(p)||!isObj(p.arguments)||Object.keys(p).some(x=>!["name","arguments","_meta"].includes(x)))
    return rpcError(id,-32602,"Invalid call");
  if(p.name!==PROBE.name&&p.name!==STAGE.name)return rpcError(id,-32602,"Unknown tool");
  const args=p.arguments;
  if(!isObj(args.file)||Object.keys(args).some(x=>!["file","claim"].includes(x))||
    (p.name===PROBE.name&&Object.keys(args).length!==1)||
    (p.name===STAGE.name&&!isObj(args.claim)))
    return toolResult(id,{error:"invalid_arguments"},true);
  try {
    if(p.name===PROBE.name){
      const img=await readFile(args.file,fetchImpl);
      return toolResult(id,{status:"verified",width:1200,height:675,
        bytes:img.bytes.length,sha256:img.sha256,file_id_sha256:img.file_id_sha256,
        upload_ticket:await uploadTicket()});
    }
    return toolResult(id,await stageFile(req,env,args.file,args.claim,fetchImpl));
  }catch(e){
    // Never echo signed file URLs, nonce, credentials or upstream error bodies.
    // Diagnostic detail is restricted to this read-only probe; staging retains
    // its generic fail-closed response and all existing access checks.
    const result={error:e.safeCode||"unavailable"};
    if(p.name===PROBE.name&&e[FILE_REFERENCE_DIAGNOSTIC])
      result.file_reference_diagnostic=e[FILE_REFERENCE_DIAGNOSTIC];
    return toolResult(id,result,true);
  }
}
