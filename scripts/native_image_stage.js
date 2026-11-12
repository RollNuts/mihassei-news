/* Capture → candidate commit/branch using explicit, caller-supplied GitHub tools.
 * Pure JavaScript. Image generation and visual acceptance are NEVER performed here.
 * Load with new Function(source + '\nreturn NativeImageStage;')().
 */
var NativeImageStage = (function () {
  "use strict";
  const SHA=/^[0-9a-f]{64}$/, GIT=/^[0-9a-f]{40}$/, SLUG=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const PROTECTED=["content/articles.json","assets/generated/manifest.json","content/visual_reviews.json"];
  const PART_SIZE=512*1024;
  function create({runtime,github,repository}) {
    if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository))throw Error("Invalid repository");
    for(const name of ["fetch_file","create_blob","create_tree","create_commit","create_branch"])if(typeof github[name]!=="function")throw Error("Missing GitHub callback: "+name);
    for(const name of ["convert","sha256","fromBase64","toBase64","utf8"])if(typeof runtime[name]!=="function")throw Error("Missing byte runtime: "+name);
    const hashText=s=>runtime.sha256(runtime.utf8(s)), transientInputs=new Map();
    function payload(r) {
      const p=r?.structuredContent??r;
      if(!p||r?.isError||p.error_code||p.error){const e=Error(p?.error||"GitHub operation failed");e.provider=p;throw e;}
      return p;
    }
    async function call(name,args){return payload(await github[name]({repository_full_name:repository,...args}));}
    function gitSha(p,kind){const candidates=[p.sha,p[kind+"_sha"],p[kind]?.sha].filter(v=>GIT.test(v||""));if(!candidates.length||new Set(candidates).size!==1)throw Error("Invalid "+kind+" SHA response");return candidates[0];}
    async function read(path,ref,encoding="utf-8"){
      const p=await call("fetch_file",{path,ref,encoding});
      if(p.encoding!==encoding||typeof p.content!=="string")throw Error("Unexpected file encoding: "+path);
      return p;
    }
    function absentRef(e,ref){const p=e.provider,m=p?.error_data?.message;return p?.error_code==="NOT_FOUND"&&(m==="No commit found for the ref "+ref||m==="No commit found for ref "+ref);}
    function fingerprint(article){const fields={};for(const k of ["category","paragraphs","scene_prompt","slug","summary","title"])fields[k]=Object.prototype.hasOwnProperty.call(article,k)?article[k]:"";return hashText(JSON.stringify(fields));}
    function identity(x){return hashText(JSON.stringify([repository,x.slug,x.articleSha256,x.logicalRunId,x.prompt]));}
    function checkPlan(p){
      if(!p||p.repository!==repository||!SLUG.test(p.slug)||!SHA.test(p.articleSha256)||!GIT.test(p.baseCommitSha)||!GIT.test(p.baseTreeSha)||typeof p.prompt!=="string"||!p.prompt.trim()||typeof p.logicalRunId!=="string"||!p.logicalRunId.trim()||p.identity!==identity(p))throw Error("Invalid candidate plan");
      const branch="native-image-candidate/"+p.slug+"/"+p.articleSha256.slice(0,16)+"-"+hashText(p.logicalRunId).slice(0,12);
      if(p.branch!==branch||p.directory!=="maintenance/native-image-candidates/"+p.slug)throw Error("Invalid candidate destination");
      for(const path of PROTECTED)if(!SHA.test(p.protectedHashes?.[path]||""))throw Error("Missing protected-file baseline");
    }
    async function prepare(input){
      const p={repository,slug:input.slug,articleSha256:input.articleSha256,baseCommitSha:input.baseCommitSha,baseTreeSha:input.baseTreeSha,prompt:input.prompt,logicalRunId:input.logicalRunId};
      if(!SLUG.test(p.slug||"")||!SHA.test(p.articleSha256||"")||!GIT.test(p.baseCommitSha||"")||!GIT.test(p.baseTreeSha||"")||typeof p.prompt!=="string"||!p.prompt.trim()||typeof p.logicalRunId!=="string"||!p.logicalRunId.trim())throw Error("Invalid prepare input");
      const loaded=await Promise.allSettled(PROTECTED.map(path=>read(path,p.baseCommitSha)));
      p.protectedHashes={};let articles;
      for(let i=0;i<loaded.length;i++){const r=loaded[i];if(r.status!=="fulfilled")throw r.reason;p.protectedHashes[PROTECTED[i]]=hashText(r.value.content);if(i===0)articles=JSON.parse(r.value.content);}
      if(!Array.isArray(articles))throw Error("Invalid article list");
      const matches=articles.filter(a=>a.slug===p.slug);
      if(matches.length!==1||fingerprint(matches[0])!==p.articleSha256)throw Error("Target article fingerprint mismatch");
      p.identity=identity(p);p.branch="native-image-candidate/"+p.slug+"/"+p.articleSha256.slice(0,16)+"-"+hashText(p.logicalRunId).slice(0,12);p.directory="maintenance/native-image-candidates/"+p.slug;
      return p;
    }
    async function verifyProtected(ref,hashes){
      const loaded=await Promise.allSettled(PROTECTED.map(path=>read(path,ref)));
      for(let i=0;i<loaded.length;i++){const r=loaded[i];if(r.status!=="fulfilled")throw r.reason;if(hashText(r.value.content)!==hashes[PROTECTED[i]])throw Error("Protected file changed: "+PROTECTED[i]);}
    }
    function equalMetadata(a,b){return JSON.stringify(a)===JSON.stringify(b);}
    async function verifyCandidate(plan,ref,expectedSourceHash,includeBytes=false){
      const raw=await read(plan.directory+"/receipt.json",ref),r=JSON.parse(raw.content);
      if(r.version!==1||r.kind!=="native_image_candidate"||r.status!=="awaiting_visual_review"||r.identity!==plan.identity||r.repository!==repository||r.slug!==plan.slug||r.article_sha256!==plan.articleSha256||r.logical_run_id!==plan.logicalRunId||r.generation?.prompt!==plan.prompt||r.generation.prompt_sha256!==hashText(plan.prompt))throw Error("Candidate identity conflict; refusing branch mutation");
      if(!SHA.test(r.source?.sha256||"")||!SHA.test(r.jpeg?.sha256||"")||r.jpeg.path!==plan.directory+"/candidate.jpg"||r.jpeg.width!==1200||r.jpeg.height!==675||r.jpeg.quality!==84||!Number.isSafeInteger(r.source.bytes)||r.source.bytes<33||r.source.bytes>32*1024*1024||!Number.isSafeInteger(r.jpeg.bytes)||r.jpeg.bytes<16000||r.jpeg.bytes>700*1024)throw Error("Invalid candidate receipt");
      if(expectedSourceHash&&r.source.sha256!==expectedSourceHash)throw Error("A different generated image already occupies this candidate identity");
      if(!Array.isArray(r.source.parts)||!r.source.parts.length||r.source.parts.length>64)throw Error("Invalid source parts");
      for(let i=0;i<r.source.parts.length;i++){const part=r.source.parts[i];if(part.path!==plan.directory+"/source.png.part"+String(i).padStart(3,"0")||!SHA.test(part.sha256||"")||!Number.isSafeInteger(part.bytes)||part.bytes<1||part.bytes>PART_SIZE)throw Error("Invalid source part record");}
      for(const path of PROTECTED)if(!SHA.test(r.baseline?.protected_hashes?.[path]||""))throw Error("Invalid candidate baseline");
      const requests=[read(r.jpeg.path,ref,"base64"),...r.source.parts.map(p=>read(p.path,ref,"base64"))];
      const loaded=await Promise.allSettled(requests),parts=[];let jpeg;
      for(let i=0;i<loaded.length;i++){const item=loaded[i];if(item.status!=="fulfilled")throw item.reason;const b=runtime.fromBase64(item.value.content),record=i===0?r.jpeg:r.source.parts[i-1];if(b.length!==record.bytes||runtime.sha256(b)!==record.sha256)throw Error("Candidate byte readback mismatch: "+record.path);if(i===0)jpeg=b;else parts.push(b);}
      const source=new Uint8Array(r.source.bytes);let at=0;for(const b of parts){if(at+b.length>source.length)throw Error("Source part length overflow");source.set(b,at);at+=b.length;}
      if(at!==source.length||runtime.sha256(source)!==r.source.sha256)throw Error("Reconstructed source PNG SHA mismatch");
      // Re-run the pinned conversion and metadata parser; a stored declaration alone
      // cannot establish either the JPEG correspondence or the C2PA instance ID.
      const converted=runtime.convert(source);
      if(converted.sha256!==r.jpeg.sha256||runtime.sha256(jpeg)!==converted.sha256||converted.source_png_sha256!==r.source.sha256||!equalMetadata(serialReceipt(converted.receipt),r.source.c2pa))throw Error("Saved PNG/JPEG/provenance do not correspond");
      await verifyProtected(ref,r.baseline.protected_hashes);
      const saved={status:"saved",branch:plan.branch,ref,receipt:r,receipt_sha256:hashText(raw.content),jpegPath:r.jpeg.path,sourceParts:r.source.parts.map(p=>p.path),reused:true};
      if(includeBytes){saved.sourcePngDataUrl="data:image/png;base64,"+runtime.toBase64(source);saved.jpegDataUrl="data:image/jpeg;base64,"+runtime.toBase64(jpeg);}
      return saved;
    }
    async function probe(plan){checkPlan(plan);try{return await verifyCandidate(plan,plan.branch);}catch(e){if(absentRef(e,plan.branch))return {status:"absent",branch:plan.branch};throw e;}}
    async function load(plan){checkPlan(plan);return await verifyCandidate(plan,plan.branch,undefined,true);}
    function serialReceipt(receipt){
      if(receipt===null)return null;
      if(!receipt||typeof receipt.generation_id!=="string"||!receipt.caBX_bytes)throw Error("Invalid C2PA extraction result");
      const {caBX_bytes,...rest}=receipt;
      // Typed byte arrays in nested metadata become ordinary exact byte arrays.
      return JSON.parse(JSON.stringify({...rest,caBX_sha256:runtime.sha256(caBX_bytes),caBX_base64:runtime.toBase64(caBX_bytes)},(_k,v)=>v instanceof Uint8Array?Array.from(v):v));
    }
    function capture(input){
      const result=input?.imageGenResult;
      if(!result||typeof result!=="object"||typeof result.image_url!=="string"||!result.image_url.startsWith("data:image/png;base64,"))throw Error("Expected actual ImageGen PNG data URL; this helper never invokes image generation");
      if(typeof input.observedAt!=="string"||!/(Z|[+-]\d\d:\d\d)$/.test(input.observedAt)||!Number.isFinite(Date.parse(input.observedAt)))throw Error("An actual timezone-qualified observation time is required");
      if(result.output_hint!==undefined&&typeof result.output_hint!=="string")throw Error("Invalid actual ImageGen output_hint");
      const source=runtime.fromBase64(result.image_url),converted=runtime.convert(source),sourceSha=runtime.sha256(source);
      if(converted.source_png_sha256!==sourceSha||converted.sha256!==runtime.sha256(converted.bytes)||converted.sha256!==runtime.sha256(runtime.fromBase64(converted.base64))||converted.width!==1200||converted.height!==675||converted.quality!==84||converted.bytes.length<16000||converted.bytes.length>700*1024)throw Error("Conversion result integrity mismatch");
      if(input.expectedSourcePngSha256!==undefined&&input.expectedSourcePngSha256!==sourceSha)throw Error("Expected source PNG SHA mismatch");
      if(input.expectedJpegSha256!==undefined&&input.expectedJpegSha256!==converted.sha256)throw Error("Expected JPEG SHA mismatch");
      if(input.recovery!==undefined&&(!input.recovery||typeof input.recovery.evidenceRef!=="string"||!input.recovery.evidenceRef.trim()||typeof input.recovery.promptSource!=="string"||!input.recovery.promptSource.trim()||!SHA.test(input.recovery.sourceArticleSha256||"")))throw Error("Recovered generation requires its actual source article and prompt evidence");
      return {source,converted,sourceSha};
    }
    function checkpoint(state){
      return {version:1,identity:state.identity,phase:state.phase,sourceSha:state.sourceSha||null,blobs:{...state.blobs},paths:state.paths?state.paths.slice():[],filesComplete:state.filesComplete===true,treeSha:state.treeSha||null,commitSha:state.commitSha||null,requires_original_bytes:!(state.filesComplete||state.treeSha||state.commitSha)};
    }
    function attachFailure(error,state){const e=error instanceof Error?error:Error(String(error));e.checkpoint=checkpoint(state);e.stage=state.phase;e.message="Native candidate not confirmed saved ("+state.phase+"): "+e.message;return e;}
    function checkReadyFiles(plan,state){
      const paths=state.paths,n=paths?.length;
      if(!n||n<3||n>66||paths[n-2]!==plan.directory+"/candidate.jpg"||paths[n-1]!==plan.directory+"/receipt.json")throw Error("Invalid completed-file checkpoint");
      for(let i=0;i<n;i++){if(i<n-2&&paths[i]!==plan.directory+"/source.png.part"+String(i).padStart(3,"0"))throw Error("Invalid checkpoint source path");if(!GIT.test(state.blobs?.[paths[i]]||""))throw Error("Invalid checkpoint blob SHA");}
    }
    async function commitFiles(plan,state){
      checkReadyFiles(plan,state);
      state.phase="create_tree";
      if(!state.treeSha)state.treeSha=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,tree_elements:state.paths.map(path=>({path,mode:"100644",type:"blob",sha:state.blobs[path]}))}),"tree");
      state.phase="create_commit";
      if(!state.commitSha)state.commitSha=gitSha(await call("create_commit",{parent_sha:plan.baseCommitSha,tree_sha:state.treeSha,message:"Stage native image candidate: "+plan.slug}),"commit");
      return await finish(plan,state);
    }
    async function finish(plan,state){
      state.phase="verify_commit";await verifyCandidate(plan,state.commitSha,state.sourceSha);
      state.phase="check_branch";const existing=await probe(plan);
      if(existing.status==="saved"){if(existing.receipt.source.sha256!==state.sourceSha)throw Error("Candidate branch conflict");return existing;}
      state.phase="create_branch";
      try{await call("create_branch",{branch_name:plan.branch,sha:state.commitSha});}
      catch(e){
        // A lost response can follow a successful create. Read only; never update
        // or delete an existing branch to make an upload appear successful.
        const maybe=await probe(plan);if(maybe.status==="saved"&&maybe.receipt.source.sha256===state.sourceSha)return maybe;throw e;
      }
      state.phase="verify_branch";const saved=await verifyCandidate(plan,plan.branch,state.sourceSha);
      state.phase="saved";return {...saved,commitSha:state.commitSha,reused:false};
    }
    async function stage(plan,input){
      checkPlan(plan);
      const state=input?.checkpoint?{...input.checkpoint,blobs:{...input.checkpoint.blobs}}:{version:1,identity:plan.identity,phase:"capture",blobs:{}};
      try{
        if(state.version!==1||state.identity!==plan.identity)throw Error("Checkpoint identity mismatch");
        if(state.commitSha||state.filesComplete||state.treeSha)return await resume(plan,state);
        state.phase="capture";const {source,converted,sourceSha}=capture(input);
        if(state.sourceSha&&state.sourceSha!==sourceSha)throw Error("Captured source differs from checkpoint");state.sourceSha=sourceSha;
        const {checkpoint:_ignored,...retained}=input;transientInputs.set(plan.identity,retained);
        state.phase="probe";const existing=await probe(plan);
        if(existing.status==="saved"){if(existing.receipt.source.sha256!==sourceSha)throw Error("Candidate branch already holds different image bytes");return existing;}
        const parts=[];for(let i=0;i<source.length;i+=PART_SIZE){const bytes=source.slice(i,i+PART_SIZE);parts.push({path:plan.directory+"/source.png.part"+String(parts.length).padStart(3,"0"),bytes:bytes.length,sha256:runtime.sha256(bytes),base64:runtime.toBase64(bytes)});}
        const jpeg={path:plan.directory+"/candidate.jpg",bytes:converted.bytes.length,sha256:converted.sha256,width:1200,height:675,quality:84};
        const files=[...parts.map(p=>({path:p.path,content:p.base64,encoding:"base64"})),{path:jpeg.path,content:converted.base64,encoding:"base64"}];
        state.paths=[...files.map(f=>f.path),plan.directory+"/receipt.json"];
        state.phase="upload_blobs";
        for(const file of files){if(!state.blobs[file.path])state.blobs[file.path]=gitSha(await call("create_blob",{content:file.content,encoding:file.encoding}),"blob");}
        const receipt={version:1,kind:"native_image_candidate",status:"awaiting_visual_review",identity:plan.identity,repository,slug:plan.slug,article_sha256:plan.articleSha256,logical_run_id:plan.logicalRunId,baseline:{commit_sha:plan.baseCommitSha,tree_sha:plan.baseTreeSha,protected_hashes:plan.protectedHashes},generation:{capture_kind:input.recovery?"recovered_native_output":"native_tool_result",prompt:plan.prompt,prompt_sha256:hashText(plan.prompt),observed_at:input.observedAt,actual_output_hint:input.imageGenResult.output_hint??null,recovery:input.recovery??null},source:{sha256:sourceSha,bytes:source.length,width:converted.sourceWidth,height:converted.sourceHeight,parts:parts.map(({base64,...p})=>({...p,blob_sha:state.blobs[p.path]})),c2pa:serialReceipt(converted.receipt)},jpeg:{...jpeg,blob_sha:state.blobs[jpeg.path]},transform:{helper:"scripts/native_image_bytes.js",resize:"center-cover-bilinear",alpha_background:"white",width:1200,height:675,jpeg_quality:84}};
        const receiptPath=plan.directory+"/receipt.json",receiptText=JSON.stringify(receipt,null,2)+"\n";
        if(runtime.utf8(receiptText).length>=PART_SIZE)throw Error("Candidate receipt is too large for reliable readback");
        if(!state.blobs[receiptPath])state.blobs[receiptPath]=gitSha(await call("create_blob",{content:receiptText,encoding:"utf-8"}),"blob");
        state.filesComplete=true;return await commitFiles(plan,state);
      }catch(e){if(e.checkpoint)throw e;throw attachFailure(e,state);}
    }
    async function resume(plan,checkpoint){
      checkPlan(plan);
      if(!checkpoint||checkpoint.version!==1||checkpoint.identity!==plan.identity)throw Error("Checkpoint identity mismatch");
      const state={...checkpoint,blobs:{...checkpoint.blobs}};
      try{
        if(state.commitSha){if(!GIT.test(state.commitSha)||!SHA.test(state.sourceSha||""))throw Error("Invalid commit checkpoint");return await finish(plan,state);}
        if(state.treeSha&&!GIT.test(state.treeSha))throw Error("Invalid tree checkpoint");
        if(state.filesComplete||state.treeSha){if(!SHA.test(state.sourceSha||""))throw Error("Missing source SHA in checkpoint");return await commitFiles(plan,state);}
        const retained=transientInputs.get(plan.identity);
        if(!retained)throw Error("Incomplete blob set: pass the original captured result back to stage with this checkpoint; do not regenerate");
        return await stage(plan,{...retained,checkpoint:state});
      }catch(e){if(e.checkpoint)throw e;throw attachFailure(e,state);}
    }
    return {prepare,probe,stage,resume,load};
  }
  return {create,PART_SIZE};
})();
if(typeof module!=="undefined"&&module.exports)module.exports=NativeImageStage;
