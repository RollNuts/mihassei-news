// Separate, stateless MCP for ChatGPT's documented fileParams input.
// No image generation/conversion and no access to the existing read_image_control.
// https://developers.openai.com/plugins/reference#define-file-inputs
const REPO="RollNuts/mihassei-news";
const API="https://api.github.com/repos/"+REPO;
const FILE_HOST="files.oaiusercontent.com";
const MAX_JPEG=700*1024;
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
  description:"Read and validate one ChatGPT-provided JPEG without saving or generating anything. Returns only dimensions, size and hashes; never returns a download URL.",
  inputSchema:{type:"object",properties:{file:fileSchema},required:["file"],additionalProperties:false},
  annotations:{readOnlyHint:true,destructiveHint:false,openWorldHint:false},
  _meta:{"openai/fileParams":["file"]}
};
const STAGE={
  name:"stage_candidate_jpeg",
  title:"Stage an approved JPEG candidate",
  description:"Disabled unless the owner separately authorizes caller authentication and a GitHub Contents-write secret. Only stages an exact claimed image on its own candidate branch, never on main. No visual approval.",
  inputSchema:{type:"object",properties:{
    file:fileSchema,
    claim:{type:"object",properties:{
      slug:{type:"string"},article_sha256:{type:"string"},branch:{type:"string"},
      expected_head_sha:{type:"string"},claim_commit_sha:{type:"string"},path:{type:"string"},
      jpeg_sha256:{type:"string"},jpeg_bytes:{type:"integer"},
      file_id_sha256:{type:"string"},nonce_sha256:{type:"string"},
      nonce:{type:"string"},expires_at:{type:"string"}
    },required:["slug","article_sha256","branch","expected_head_sha","path","jpeg_sha256",
                "jpeg_bytes","file_id_sha256","nonce_sha256","nonce","expires_at","claim_commit_sha"],
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
  if(url.protocol!=="https:"||url.hostname!==FILE_HOST||url.port||url.username||url.password||
     url.hash||!url.pathname.startsWith("/file-")||url.pathname.includes("//"))
    throw failure("untrusted_file_host");
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
      size=[(b[i+3]<<8)|b[i+4],(b[i+5]<<8)|b[i+6]];
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
  if(result.status!==200||result.url&&new URL(result.url).host!==FILE_HOST)
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
function decodedBase64(s){
  if(typeof s!=="string"||s.length>Math.ceil(MAX_JPEG/3)*4+200)throw failure("invalid_github_response");
  const clean=s.replace(/\s/g,"");
  if(!/^[A-Za-z0-9+/]*={0,2}$/.test(clean))throw failure("invalid_github_response");
  return Uint8Array.from(atob(clean),x=>x.charCodeAt(0));
}
async function authorizedCaller(req,env){
  const serverKey=env.IMAGE_CANDIDATE_CALLER_KEY;
  const authorization=req.headers.get("authorization")||"";
  if(!validToken(serverKey)||!authorization.startsWith("Bearer ")||!validToken(authorization.slice(7)))
    return false;
  const a=await shaHex(encoder.encode(serverKey)),b=await shaHex(encoder.encode(authorization.slice(7)));
  let diff=0;for(let i=0;i<64;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);
  return diff===0;
}
function validateClaim(claim){
  if(!isObj(claim)||Object.keys(claim).length!==12||!KEY.test(claim.slug||"")||
     !SHA.test(claim.article_sha256||"")||!SHA.test(claim.jpeg_sha256||"")||
     !SHA.test(claim.file_id_sha256||"")||!SHA.test(claim.nonce_sha256||"")||
     (!GIT.test(claim.expected_head_sha||"")||!GIT.test(claim.claim_commit_sha||"")||
      claim.expected_head_sha===claim.claim_commit_sha)||!Number.isInteger(claim.jpeg_bytes)||
     claim.jpeg_bytes<128||claim.jpeg_bytes>MAX_JPEG||
     typeof claim.nonce!=="string"||!/^[A-Za-z0-9_-]{32,128}$/.test(claim.nonce)||
     typeof claim.expires_at!=="string"||!/[zZ]$/.test(claim.expires_at))
    throw failure("invalid_claim");
  const branchPattern=new RegExp("^native-image-candidate/"+claim.slug+"/"+
    claim.article_sha256.slice(0,16)+"-[a-f0-9]{12}$");
  if(!branchPattern.test(claim.branch||"")||
     claim.path!=="maintenance/native-image-candidates/"+claim.slug+"/candidate.jpg")
    throw failure("invalid_claim_destination");
  const exp=Date.parse(claim.expires_at);
  if(!Number.isFinite(exp)||exp<Date.now()||exp>Date.now()+15*60*1000)throw failure("expired_claim");
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
  const text=new TextDecoder("utf-8",{fatal:true}).decode(await limited(res,256*1024));
  try{return JSON.parse(text);}catch(_){throw failure("invalid_github_response");}
}
async function stageFile(req,env,file,claim,fetchImpl){
  // This kill-switch is intentionally OFF in source and requires TWO new
  // independent Worker Secrets plus explicit enablement after owner approval.
  if(env.IMAGE_CANDIDATE_WRITE_ENABLED!=="true"||
     !validToken(env.IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN)||
     !validToken(env.IMAGE_CANDIDATE_CALLER_KEY))
    throw failure("write_disabled");
  if(!await authorizedCaller(req,env))throw failure("unauthorized");
  validateClaim(claim);
  const image=await readFile(file,fetchImpl);
  if(image.sha256!==claim.jpeg_sha256||image.bytes.length!==claim.jpeg_bytes||
     image.file_id_sha256!==claim.file_id_sha256||
     await shaText(claim.nonce)!==claim.nonce_sha256)
    throw failure("claim_mismatch");
  const token=env.IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN;
  // All GitHub API paths, repo identity, workflow and candidate paths are fixed
  // by validated input. No caller-provided GitHub URL is ever followed.
  const repo=await github("", "GET",token,fetchImpl);
  if(repo.full_name!==REPO||repo.private!==false||repo.visibility!=="public")throw failure("repository_not_public");
  // Public read-only metadata needs no Actions permission on the write token.
  const workflow=await github("/actions/workflows/free-cpu-scenes.yml","GET",null,fetchImpl);
  if(workflow.path!==".github/workflows/free-cpu-scenes.yml"||workflow.state!=="active")
    throw failure("image_workflow_not_active");
  const manifest=await github("/contents/assets/generated/manifest.json?ref=main","GET",token,fetchImpl);
  if(manifest.encoding!=="base64"||typeof manifest.content!=="string")throw failure("invalid_manifest");
  let inventory;
  try{inventory=JSON.parse(new TextDecoder().decode(decodedBase64(manifest.content)));}catch(_){throw failure("invalid_manifest");}
  if(inventory.version!==1||!isObj(inventory.images))throw failure("invalid_manifest");
  let archive=0;
  for(const record of Object.values(inventory.images)){
    if(!Number.isSafeInteger(record.bytes)||record.bytes<0)throw failure("invalid_manifest");
    archive+=record.bytes;
  }
  if(archive+image.bytes.length>ARCHIVE_MAX)throw failure("archive_limit");

  const branchPath=claim.branch.split("/").map(encodeURIComponent).join("/");
  const ref="/git/ref/heads/"+branchPath;
  const branch=await github(ref,"GET",token,fetchImpl);
  if(branch.object?.sha!==claim.claim_commit_sha) {
    // Never write to a branch with changed head, even on a retry.
    const present=await github("/contents/"+claim.path+"?ref="+encodeURIComponent(claim.branch),
      "GET",token,fetchImpl,undefined,true);
    if(present&&present.encoding==="base64"&&
       await shaHex(decodedBase64(present.content))===image.sha256)
      return {status:"reused",branch:claim.branch,path:claim.path,
        jpeg_sha256:image.sha256,bytes:image.bytes.length};
    throw failure("candidate_conflict");
  }
  const claimPath="maintenance/image-upload-claims/"+claim.slug+"/claim.json";
  const stored=await github("/contents/"+claimPath+"?ref="+claim.claim_commit_sha,
    "GET",token,fetchImpl);
  if(stored.encoding!=="base64"||typeof stored.content!=="string")throw failure("invalid_claim");
  let record;
  try{record=JSON.parse(new TextDecoder().decode(decodedBase64(stored.content)));}catch(_){throw failure("invalid_claim");}
  const expected=Object.fromEntries(Object.entries(claim).filter(([k])=>k!=="nonce"&&k!=="claim_commit_sha"));
  if(record.version!==1||record.repository!==REPO||
     !Object.keys(expected).every(k=>record[k]===expected[k])||
     Object.hasOwn(record,"nonce"))throw failure("claim_mismatch");
  const existing=await github("/contents/"+claim.path+"?ref="+claim.claim_commit_sha,
    "GET",token,fetchImpl,undefined,true);
  if(existing) {
    if(existing.encoding==="base64"&&await shaHex(decodedBase64(existing.content))===image.sha256)
      return {status:"reused",branch:claim.branch,path:claim.path,
        jpeg_sha256:image.sha256,bytes:image.bytes.length};
    throw failure("candidate_conflict");
  }
  const parent=await github("/git/commits/"+claim.claim_commit_sha,"GET",token,fetchImpl);
  if(!GIT.test(parent.tree?.sha||"")||
     parent.parents?.length!==1||parent.parents[0]?.sha!==claim.expected_head_sha)
    throw failure("claim_parent_mismatch");
  const blob=await github("/git/blobs","POST",token,fetchImpl,{
    content:encodedBase64(image.bytes),encoding:"base64"});
  if(!GIT.test(blob.sha||""))throw failure("invalid_github_response");
  const tree=await github("/git/trees","POST",token,fetchImpl,{
    base_tree:parent.tree.sha,tree:[{path:claim.path,mode:"100644",type:"blob",sha:blob.sha}]});
  if(!GIT.test(tree.sha||""))throw failure("invalid_github_response");
  const commit=await github("/git/commits","POST",token,fetchImpl,{
    message:"Stage claimed JPEG candidate for "+claim.slug,
    tree:tree.sha,parents:[claim.claim_commit_sha]});
  if(!GIT.test(commit.sha||""))throw failure("invalid_github_response");
  const updated=await github(ref,"PATCH",token,fetchImpl,{sha:commit.sha,force:false});
  if(updated.object?.sha!==commit.sha)throw failure("candidate_conflict");
  const verify=await github("/contents/"+claim.path+"?ref="+commit.sha,"GET",token,fetchImpl);
  if(verify.encoding!=="base64"||await shaHex(decodedBase64(verify.content))!==image.sha256)
    throw failure("candidate_readback_failed");
  return {status:"saved",branch:claim.branch,path:claim.path,commit_sha:commit.sha,
    jpeg_sha256:image.sha256,bytes:image.bytes.length};
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
        bytes:img.bytes.length,sha256:img.sha256,file_id_sha256:img.file_id_sha256});
    }
    return toolResult(id,await stageFile(req,env,args.file,args.claim,fetchImpl));
  }catch(e){
    // Never echo signed file URLs, nonce, credentials or upstream error bodies.
    return toolResult(id,{error:e.safeCode||"unavailable"},true);
  }
}
