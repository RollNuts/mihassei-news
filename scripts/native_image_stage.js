/* Verify transport → reserve one generation → save its original candidate.
 * Pure JavaScript with explicit callbacks; visual acceptance is NEVER performed here.
 * Load with new Function(source + '\nreturn NativeImageStage;')().
 */
var NativeImageStage = (function () {
  "use strict";
  const SHA=/^[0-9a-f]{64}$/, GIT=/^[0-9a-f]{40}$/, SLUG=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const PROTECTED=["content/articles.json","assets/generated/manifest.json","content/visual_reviews.json"];
  const PART_SIZE=512*1024, COMPACT_SOURCE_LIMIT=4*1024*1024;
  const PREFLIGHT_BRANCH="native-image-preflight/transport";
  const PREFLIGHT_DIR="maintenance/native-image-preflight";
  const CLAIM_BRANCH_PREFIX="native-image-generation-claim/";
  const CLAIM_DIR="maintenance/native-image-generation-claims";
  const CLOUD_WORKFLOW="free-cpu-scenes.yml", JPEG_CAP=1536*1024*1024;
  const FUTURE_CLOCK_TOLERANCE_MS=1000;
  function create({runtime,github,repository,readCloudWorkflow,manifestPatch}) {
    if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository))throw Error("Invalid repository");
    for(const name of ["fetch_file","create_blob","create_tree","create_commit","create_branch"])if(typeof github[name]!=="function")throw Error("Missing GitHub callback: "+name);
    for(const name of ["convert","sha256","fromBase64","toBase64","utf8"])if(typeof runtime[name]!=="function")throw Error("Missing byte runtime: "+name);
    if(readCloudWorkflow!==undefined&&typeof readCloudWorkflow!=="function")throw Error("Invalid cloud workflow callback");
    const hashText=s=>runtime.sha256(runtime.utf8(s)), transientInputs=new Map();
    const activeGenerations=new Set(), generationAttempts=new Map();
    let generatedSnapshot, workflowNonce=0, stepUsage=null;
    function countStep(mutation=false){
      if(!stepUsage)return;
      if(stepUsage.calls>=15||mutation&&stepUsage.mutations>=2)throw Error("Bounded image step callback budget exceeded");
      stepUsage.calls++;if(mutation)stepUsage.mutations++;
    }
    async function invoke(name,args){
      countStep(name!=="fetch"&&name!=="fetch_file");
      try{return await github[name](args);}
      catch(error){
        // This connector can throw its typed missing-ref result as a primitive
        // string. Normalize only the observed Contents error, never a file 404,
        // permission failure, network error or a message about another ref.
        const prefix="NOT_FOUND: Error code: NOT_FOUND; Error: GitHub API error 404: ";
        if(name==="fetch_file"&&typeof args.ref==="string"&&typeof error==="string"&&error.startsWith(prefix)){
          try{
            const record=JSON.parse(error.slice(prefix.length));
            if(record&&Object.keys(record).length===3
              &&record.message==="No commit found for the ref "+args.ref
              &&record.documentation_url==="https://docs.github.com/v3/repos/contents/"&&record.status==="404")
              return {error_code:"NOT_FOUND",error_data:{message:record.message}};
          }catch(_invalid){/* Preserve the original unrecognized failure. */}
        }
        throw error;
      }
    }
    function payload(r) {
      const p=r?.structuredContent??r?.result??r;
      // Preserve concrete outer errors; a generic outer isError flag must not
      // hide the inner NOT_FOUND record used by exact missing-ref checks.
      const failed=r?.error_code||r?.error?r:p?.error_code||p?.error?p:r?.isError||p?.isError?p:null;
      if(!p||failed){const provider=failed||p,e=Error(provider?.error||"GitHub operation failed");e.provider=provider;throw e;}
      return p;
    }
    async function fetchJSON(path){
      if(typeof github.fetch!=="function")throw Error("Missing cloud guard GitHub callback: fetch");
      const raw=payload(await invoke("fetch",{url:"https://api.github.com/repos/"+repository+path}));
      return typeof raw.content==="string"?JSON.parse(raw.content):raw;
    }
    async function cloudStatus(){
      try {
        // Visibility and the stop authority are live on every check. Only the
        // byte total of an immutable main commit may be reused between writes.
        const repo=await fetchJSON("");
        if(typeof repo.full_name!=="string"||repo.full_name.toLowerCase()!==repository.toLowerCase()
          ||repo.private!==false||repo.visibility!=="public")throw Error("Repository is not confirmed public");
        const commitSha=await branchHead("main");
        if(generatedSnapshot?.commitSha!==commitSha){
          const commit=await fetchJSON("/git/commits/"+commitSha);
          if(commit.sha!==commitSha||!GIT.test(commit.tree?.sha||""))throw Error("Invalid current main commit response");
          const tree=await fetchJSON("/git/trees/"+commit.tree.sha+"?recursive=1");
          if(tree.sha!==commit.tree.sha||tree.truncated!==false||!Array.isArray(tree.tree))throw Error("Incomplete generated-JPEG tree");
          let jpegBytes=0;
          for(const entry of tree.tree){
            if(typeof entry.path!=="string")throw Error("Invalid Git tree path");
            if(!/^assets\/generated\/.*\.jpe?g$/i.test(entry.path))continue;
            if(entry.type!=="blob"||!Number.isSafeInteger(entry.size)||entry.size<0)throw Error("Unknown generated-JPEG size");
            jpegBytes+=entry.size;
            if(!Number.isSafeInteger(jpegBytes))throw Error("Invalid generated-JPEG total");
          }
          generatedSnapshot={commitSha,jpegBytes};
        }
        if(generatedSnapshot.jpegBytes>JPEG_CAP)throw Error("Generated JPEG total exceeds 1.5 GiB");
        // Keep this last: a stop-all disables this existing workflow, including
        // while byte conversion, claim creation or candidate recovery is busy.
        let workflow;
        if(readCloudWorkflow){
          const nonce=hashText(JSON.stringify([repository,Date.now(),++workflowNonce,Math.random(),Math.random()]));
          countStep();const raw=payload(await readCloudWorkflow({nonce}));
          workflow=typeof raw.content==="string"?JSON.parse(raw.content):raw;
          const checked=Date.parse(workflow.checked_at),age=Date.now()-checked;
          if(workflow.version!==1||workflow.repository!==repository||workflow.nonce!==nonce
            ||typeof workflow.checked_at!=="string"||!/(Z|[+-]\d\d:\d\d)$/.test(workflow.checked_at)
            // The proxy and caller may differ by a fraction of a second.
            ||!Number.isFinite(checked)||age < -FUTURE_CLOCK_TOLERANCE_MS||age>10000)throw Error("Unverified or stale cloud workflow response");
        }else workflow=await fetchJSON("/actions/workflows/"+CLOUD_WORKFLOW);
        if(workflow.path!==".github/workflows/"+CLOUD_WORKFLOW||workflow.state!=="active")
          throw Error("Cloud image workflow is not confirmed active");
        return {state:"active",public:true,main_commit_sha:commitSha,jpeg_bytes:generatedSnapshot.jpegBytes,jpeg_cap_bytes:JPEG_CAP};
      } catch(error){
        const e=error instanceof Error?error:Error(String(error));
        e.message="Cloud image operations stopped: "+e.message;throw e;
      }
    }
    async function call(name,args){
      if(name!=="fetch_file")await cloudStatus();
      return payload(await invoke(name,{repository_full_name:repository,...args}));
    }
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
    function prepareInput(input){
      const p={repository,slug:input.slug,articleSha256:input.articleSha256,baseCommitSha:input.baseCommitSha,baseTreeSha:input.baseTreeSha,prompt:input.prompt,logicalRunId:input.logicalRunId};
      if(!SLUG.test(p.slug||"")||!SHA.test(p.articleSha256||"")||!GIT.test(p.baseCommitSha||"")||!GIT.test(p.baseTreeSha||"")||typeof p.prompt!=="string"||!p.prompt.trim()||typeof p.logicalRunId!=="string"||!p.logicalRunId.trim())throw Error("Invalid prepare input");
      return p;
    }
    async function protectedSnapshot(ref,readFile=read){
      const loaded=await Promise.allSettled(PROTECTED.map(path=>readFile(path,ref))),hashes={},raw={};
      for(let i=0;i<loaded.length;i++){const r=loaded[i];if(r.status!=="fulfilled")throw r.reason;raw[PROTECTED[i]]=r.value.content;hashes[PROTECTED[i]]=hashText(r.value.content);}
      const articles=JSON.parse(raw[PROTECTED[0]]);
      if(!Array.isArray(articles))throw Error("Invalid article list");
      return {hashes,raw,articles};
    }
    function prepareSnapshot(p,snapshot){
      p.protectedHashes=snapshot.hashes;
      const matches=snapshot.articles.filter(a=>a.slug===p.slug);
      if(matches.length!==1||fingerprint(matches[0])!==p.articleSha256)throw Error("Target article fingerprint mismatch");
      p.identity=identity(p);p.branch="native-image-candidate/"+p.slug+"/"+p.articleSha256.slice(0,16)+"-"+hashText(p.logicalRunId).slice(0,12);p.directory="maintenance/native-image-candidates/"+p.slug;
      checkPlan(p);
      return p;
    }
    async function prepare(input){
      const p=prepareInput(input);
      return prepareSnapshot(p,await protectedSnapshot(p.baseCommitSha));
    }
    async function prepareArticle(input){
      if(!input||typeof input!=="object"||Array.isArray(input)||Object.keys(input).length!==2
        ||Object.keys(input).some(key=>key!=="slug"&&key!=="baseCommitSha")
        ||!SLUG.test(input.slug||"")||!GIT.test(input.baseCommitSha||""))throw Error("prepareArticle requires only slug and immutable baseCommitSha");
      const commit=await fetchJSON("/git/commits/"+input.baseCommitSha);
      if(commit.sha!==input.baseCommitSha||!GIT.test(commit.tree?.sha||""))throw Error("Article commit/tree response mismatch");
      const snapshot=await protectedSnapshot(input.baseCommitSha),matches=snapshot.articles.filter(a=>a.slug===input.slug);
      if(matches.length!==1)throw Error("Target article must exist exactly once at the supplied commit");
      const article=matches[0];
      if(article.visual_strategy!=null&&article.visual_strategy!=="cpu-photo")throw Error("Article is not eligible for the normal missing-photo path");
      if(typeof article.scene_prompt!=="string"||!article.scene_prompt.trim())throw Error("Article needs its committed scene_prompt");
      const articleSha256=fingerprint(article),manifest=JSON.parse(snapshot.raw[PROTECTED[1]]),reviews=JSON.parse(snapshot.raw[PROTECTED[2]]);
      if(!manifest||typeof manifest!=="object"||Array.isArray(manifest)||!manifest.images||typeof manifest.images!=="object"||Array.isArray(manifest.images)
        ||!reviews||typeof reviews!=="object"||Array.isArray(reviews)||!reviews.reviews||typeof reviews.reviews!=="object"||Array.isArray(reviews.reviews))throw Error("Invalid current photo/review records");
      const image=manifest.images[input.slug],review=reviews.reviews[input.slug];
      if(image?.article_sha256===articleSha256||review?.article_sha256===articleSha256)throw Error("Current article already has photo/review records; inspect or recover them instead of starting a normal generation");
      return prepareSnapshot(prepareInput({slug:input.slug,baseCommitSha:input.baseCommitSha,baseTreeSha:commit.tree.sha,
        articleSha256,prompt:article.scene_prompt,logicalRunId:"news-photo-v1:"+articleSha256}),snapshot);
    }
    async function verifyProtected(ref,hashes,readFile=read){
      const loaded=await Promise.allSettled(PROTECTED.map(path=>readFile(path,ref)));
      for(let i=0;i<loaded.length;i++){const r=loaded[i];if(r.status!=="fulfilled")throw r.reason;if(hashText(r.value.content)!==hashes[PROTECTED[i]])throw Error("Protected file changed: "+PROTECTED[i]);}
    }
    function equalMetadata(a,b){return JSON.stringify(a)===JSON.stringify(b);}
    async function verifyCandidate(plan,ref,expectedSourceHash,includeBytes=false,publication=false,readFile=read){
      const raw=await readFile(plan.directory+"/receipt.json",ref),r=JSON.parse(raw.content);
      if(r.version!==1||r.kind!=="native_image_candidate"||r.status!=="awaiting_visual_review"||r.identity!==plan.identity||r.repository!==repository||r.slug!==plan.slug||r.article_sha256!==plan.articleSha256||r.logical_run_id!==plan.logicalRunId||r.generation?.prompt!==plan.prompt||r.generation.prompt_sha256!==hashText(plan.prompt))throw Error("Candidate identity conflict; refusing branch mutation");
      if(!SHA.test(r.source?.sha256||"")||!SHA.test(r.jpeg?.sha256||"")||r.jpeg.path!==plan.directory+"/candidate.jpg"||r.jpeg.width!==1200||r.jpeg.height!==675||r.jpeg.quality!==84||!Number.isSafeInteger(r.source.bytes)||r.source.bytes<33||r.source.bytes>32*1024*1024||!Number.isSafeInteger(r.jpeg.bytes)||r.jpeg.bytes<16000||r.jpeg.bytes>700*1024)throw Error("Invalid candidate receipt");
      if(expectedSourceHash&&r.source.sha256!==expectedSourceHash)throw Error("A different generated image already occupies this candidate identity");
      if(!Array.isArray(r.source.parts)||!r.source.parts.length||r.source.parts.length>64)throw Error("Invalid source parts");
      for(let i=0;i<r.source.parts.length;i++){const part=r.source.parts[i];if(part.path!==plan.directory+"/source.png.part"+String(i).padStart(3,"0")||!SHA.test(part.sha256||"")||!Number.isSafeInteger(part.bytes)||part.bytes<1||part.bytes>PART_SIZE)throw Error("Invalid source part record");}
      for(const path of PROTECTED)if(!SHA.test(r.baseline?.protected_hashes?.[path]||""))throw Error("Invalid candidate baseline");
      const requests=[readFile(r.jpeg.path,ref,"base64"),...r.source.parts.map(p=>readFile(p.path,ref,"base64"))];
      const loaded=await Promise.allSettled(requests),parts=[];let jpeg;
      for(let i=0;i<loaded.length;i++){const item=loaded[i];if(item.status!=="fulfilled")throw item.reason;const b=runtime.fromBase64(item.value.content),record=i===0?r.jpeg:r.source.parts[i-1];if(b.length!==record.bytes||runtime.sha256(b)!==record.sha256)throw Error("Candidate byte readback mismatch: "+record.path);if(publication&&(!GIT.test(record.blob_sha||"")||item.value.sha!==record.blob_sha))throw Error("Candidate Git blob readback mismatch: "+record.path);if(i===0)jpeg=b;else parts.push(b);}
      const source=new Uint8Array(r.source.bytes);let at=0;for(const b of parts){if(at+b.length>source.length)throw Error("Source part length overflow");source.set(b,at);at+=b.length;}
      if(at!==source.length||runtime.sha256(source)!==r.source.sha256)throw Error("Reconstructed source PNG SHA mismatch");
      // Re-run the pinned conversion and metadata parser; a stored declaration alone
      // cannot establish either the JPEG correspondence or the C2PA instance ID.
      const converted=runtime.convert(source);
      if(converted.sha256!==r.jpeg.sha256||runtime.sha256(jpeg)!==converted.sha256||converted.source_png_sha256!==r.source.sha256||!equalMetadata(serialReceipt(converted.receipt),r.source.c2pa))throw Error("Saved PNG/JPEG/provenance do not correspond");
      if(publication&&(r.source.width!==converted.sourceWidth||r.source.height!==converted.sourceHeight
        ||r.baseline.commit_sha!==plan.baseCommitSha||r.baseline.tree_sha!==plan.baseTreeSha
        ||PROTECTED.some(path=>r.baseline.protected_hashes[path]!==plan.protectedHashes[path])))
        throw Error("Candidate source dimensions or baseline differ from the original plan");
      await verifyProtected(ref,r.baseline.protected_hashes,readFile);
      const saved={status:"saved",branch:plan.branch,ref,receipt:r,receipt_sha256:hashText(raw.content),jpegPath:r.jpeg.path,sourceParts:r.source.parts.map(p=>p.path),reused:true};
      if(includeBytes){saved.sourcePngDataUrl="data:image/png;base64,"+runtime.toBase64(source);saved.jpegDataUrl="data:image/jpeg;base64,"+runtime.toBase64(jpeg);}
      return saved;
    }
    async function probe(plan){checkPlan(plan);try{return await verifyCandidate(plan,plan.branch);}catch(e){if(absentRef(e,plan.branch))return {status:"absent",branch:plan.branch};throw e;}}
    async function load(plan,ref=plan.branch){checkPlan(plan);if(ref!==plan.branch&&!GIT.test(ref||""))throw Error("Invalid candidate read ref");return await verifyCandidate(plan,ref,undefined,true);}
    async function preparePublication(input){
      // Read-only preparation for a new article photo. The candidate commit is
      // provenance, never the publication tree or a merge parent. Acceptance
      // must already have been recorded by the person who displayed the images.
      const {plan:requestedPlan,candidateCommitSha,pullRequestNumber,expectedHeadSha,articleSha256,reviewSource}=input||{};
      checkPlan(requestedPlan);
      const plan={...requestedPlan,protectedHashes:{...requestedPlan.protectedHashes}};
      if(typeof candidateCommitSha!=="string"||typeof expectedHeadSha!=="string"
        ||!GIT.test(candidateCommitSha)||!GIT.test(expectedHeadSha)
        ||candidateCommitSha===expectedHeadSha||!Number.isSafeInteger(pullRequestNumber)||pullRequestNumber<1
        ||articleSha256!==plan.articleSha256||typeof reviewSource!=="string")
        throw Error("Publication preparation requires fixed candidate/PR SHAs, the current article SHA and an actual review source");
      if(typeof manifestPatch?.appendImage!=="function"||typeof manifestPatch?.appendReview!=="function")
        throw Error("Publication preparation requires GeneratedManifestPatch appendImage and appendReview");
      const cache=new Map();
      function readFixed(path,ref,encoding="utf-8"){
        if(!GIT.test(ref||""))throw Error("Publication data reads require immutable commits");
        const key=JSON.stringify([ref,path,encoding]);
        if(!cache.has(key))cache.set(key,read(path,ref,encoding));
        return cache.get(key);
      }
      async function currentPR(){
        const pr=await fetchJSON("/pulls/"+pullRequestNumber);
        if(pr.number!==pullRequestNumber||pr.state!=="open"||pr.merged===true
          ||pr.head?.sha!==expectedHeadSha||pr.head?.repo?.full_name!==repository
          ||pr.base?.repo?.full_name!==repository||pr.base?.ref!=="main"
          ||typeof pr.head.ref!=="string"||!pr.head.ref||pr.head.ref==="main"
          ||pr.head.ref.startsWith("native-image-"))throw Error("Working publication PR changed or is not an open same-repository PR to main");
        return pr;
      }
      const pr=await currentPR(),commit=await fetchJSON("/git/commits/"+expectedHeadSha);
      if(commit.sha!==expectedHeadSha||!GIT.test(commit.tree?.sha||""))throw Error("Working PR commit/tree response mismatch");
      const tree=await fetchJSON("/git/trees/"+commit.tree.sha+"?recursive=1");
      if(tree.sha!==commit.tree.sha||tree.truncated!==false||!Array.isArray(tree.tree)
        ||tree.tree.some(entry=>typeof entry.path!=="string"))throw Error("Incomplete working PR tree");
      if(PROTECTED.some(path=>tree.tree.filter(entry=>entry.path===path&&entry.type==="blob"&&entry.mode==="100644").length!==1))
        throw Error("Working publication inputs must be ordinary Git files");
      const publishedPath="assets/generated/"+plan.slug+".jpg";
      if(tree.tree.some(entry=>entry.path===plan.directory||entry.path.startsWith(plan.directory+"/")))
        throw Error("Working PR contains duplicated candidate sources; retain them only at the fixed candidate commit");
      if(tree.tree.some(entry=>entry.path===publishedPath))throw Error("Publication JPEG already exists; replacement is not supported");
      const current=await protectedSnapshot(expectedHeadSha,readFixed),matches=current.articles.filter(article=>article.slug===plan.slug);
      if(matches.length!==1||fingerprint(matches[0])!==articleSha256||matches[0].scene_prompt!==plan.prompt
        ||(Object.prototype.hasOwnProperty.call(matches[0],"visual_strategy")&&matches[0].visual_strategy!=="cpu-photo"))
        throw Error("Working article or scene changed; publication requires the current reviewed article");
      // These appenders validate duplicate keys and preserve every pre-existing
      // character, including integers beyond JavaScript Number's safe range.
      const reviewsSource=manifestPatch.appendReview({reviewsSource:current.raw[PROTECTED[2]],slug:plan.slug,reviewSource});
      const review=JSON.parse(reviewSource);
      const has=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
      const manifest=JSON.parse(current.raw[PROTECTED[1]]);
      if(!manifest||typeof manifest!=="object"||Array.isArray(manifest)
        ||!manifest.images||!manifest.rejected||has(manifest.images,plan.slug)||has(manifest.rejected,plan.slug))
        throw Error("Existing image or rejection history requires the explicit recovery procedure");
      if(!review||typeof review!=="object"||Array.isArray(review)||review.decision!=="accepted"
        ||review.article_sha256!==articleSha256||!SHA.test(review.image_sha256||"")
        ||typeof review.note!=="string"||!review.note.trim()||has(review,"reuse"))
        throw Error("An actual accepted current-article/current-image visual review is required");
      function stamp(value){
        const match=typeof value==="string"&&/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(value);
        if(!match)return NaN;
        const [year,month,day,hour,minute,second]=match.slice(1,7).map(Number);
        const days=[31,(year%4===0&&(year%100!==0||year%400===0))?29:28,31,30,31,30,31,31,30,31,30,31];
        if(year<1||month<1||month>12||day<1||day>days[month-1]||hour>23||minute>59||second>59
          ||(match[7]!=="Z"&&(Number(match[7].slice(1,3))>23||Number(match[7].slice(4,6))>59)))return NaN;
        return Date.parse(value);
      }
      if(!Number.isFinite(stamp(review.reviewed_at))||stamp(review.reviewed_at)>Date.now()+FUTURE_CLOCK_TOLERANCE_MS)
        throw Error("Visual review needs an actual timezone-qualified review time");
      const saved=await verifyCandidate(plan,candidateCommitSha,undefined,false,true,readFixed),r=saved.receipt,g=r.generation,c2pa=r.source.c2pa;
      const transform={helper:"scripts/native_image_bytes.js",resize:"center-cover-bilinear",alpha_background:"white",width:1200,height:675,jpeg_quality:84};
      if(g.capture_kind!=="native_tool_result"||g.recovery!==null||!Number.isFinite(stamp(g.observed_at))
        ||stamp(review.reviewed_at)<stamp(g.observed_at)||review.image_sha256!==r.jpeg.sha256
        ||!equalMetadata(r.transform,transform)||!c2pa
        ||!/^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.test(c2pa.generation_id||"")
        ||c2pa.generation_receipt!=="xmp:iid:"+c2pa.generation_id
        ||c2pa.source_c2pa?.instanceID!==c2pa.generation_receipt
        ||typeof c2pa.generation_id_source!=="string"||!c2pa.generation_id_source.trim()
        ||!SHA.test(c2pa.caBX_sha256||""))throw Error("Incomplete or mismatched native candidate provenance/review");
      await verifyReserved(plan,{generationClaim:g.generation_claim,transportPreflight:g.transport_preflight,observedAt:g.observed_at},readFixed);
      const record={article_sha256:articleSha256,bytes:r.jpeg.bytes,generated_at:g.observed_at,
        generated_at_source:"First observation of the actual native tool result in the fixed candidate receipt; not a backend completion time.",
        generation_id:c2pa.generation_id,generation_id_source:c2pa.generation_id_source,generation_receipt:c2pa.generation_receipt,
        generation_prompt:g.prompt,generation_prompt_source:"Exact generation prompt preserved in the fixed candidate receipt.",
        model:"ChatGPT ImageGen",origin:"chatgpt-imagegen",prompt_version:3,sha256:r.jpeg.sha256,
        source_c2pa:c2pa.source_c2pa,source_png_sha256:r.source.sha256,transform:r.transform,
        source_capture:{kind:g.capture_kind,method:"Original PNG parts, JPEG, receipt and generation reservation read back from fixed commits.",
          logical_run_id:r.logical_run_id,actual_output_hint:g.actual_output_hint,observed_at:g.observed_at,
          bytes:r.source.bytes,width:r.source.width,height:r.source.height,caBX_sha256:c2pa.caBX_sha256,
          candidate_branch:plan.branch,candidate_commit:candidateCommitSha,receipt_path:plan.directory+"/receipt.json",
          receipt_sha256:saved.receipt_sha256,original_png_parts:r.source.parts,
          transport_preflight:g.transport_preflight,generation_claim:g.generation_claim}};
      const manifestSource=manifestPatch.appendImage({manifestSource:current.raw[PROTECTED[1]],slug:plan.slug,imageSource:JSON.stringify(record,null,2)});
      const latest=await currentPR();
      if(latest.head.ref!==pr.head.ref)throw Error("Working publication branch changed during preparation");
      return {status:"prepared_not_published",repository,pullRequestNumber,branch:pr.head.ref,
        expectedHeadSha,baseTreeSha:commit.tree.sha,articleSha256,
        candidate:{commitSha:candidateCommitSha,receiptPath:plan.directory+"/receipt.json",receiptSha256:saved.receipt_sha256,
          sourcePngSha256:r.source.sha256,jpegSha256:r.jpeg.sha256,jpegBlobSha:r.jpeg.blob_sha},
        files:[{path:publishedPath,mode:"100644",type:"blob",sha:r.jpeg.blob_sha},
          {path:PROTECTED[1],mode:"100644",type:"blob",content:manifestSource},
          {path:PROTECTED[2],mode:"100644",type:"blob",content:reviewsSource}]};
    }
    function serialReceipt(receipt){
      if(receipt===null)return null;
      if(!receipt||typeof receipt.generation_id!=="string"||!receipt.caBX_bytes)throw Error("Invalid C2PA extraction result");
      const {caBX_bytes,...rest}=receipt;
      // Typed byte arrays in nested metadata become ordinary exact byte arrays.
      return JSON.parse(JSON.stringify({...rest,caBX_sha256:runtime.sha256(caBX_bytes),caBX_base64:runtime.toBase64(caBX_bytes)},(_k,v)=>v instanceof Uint8Array?Array.from(v):v));
    }
    function capture(input){
      const result=input?.imageGenResult;
      if(!result||typeof result!=="object"||typeof result.image_url!=="string"||!result.image_url.startsWith("data:image/png;base64,"))throw Error("Expected actual PNG bytes returned by the configured transfer callback");
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
        const receipt={version:1,kind:"native_image_candidate",status:"awaiting_visual_review",identity:plan.identity,repository,slug:plan.slug,article_sha256:plan.articleSha256,logical_run_id:plan.logicalRunId,baseline:{commit_sha:plan.baseCommitSha,tree_sha:plan.baseTreeSha,protected_hashes:plan.protectedHashes},generation:{capture_kind:input.recovery?"recovered_native_output":"native_tool_result",prompt:plan.prompt,prompt_sha256:hashText(plan.prompt),observed_at:input.observedAt,actual_output_hint:input.imageGenResult.output_hint??null,recovery:input.recovery??null,transport_preflight:input.transportPreflight??null,generation_claim:input.generationClaim??null},source:{sha256:sourceSha,bytes:source.length,width:converted.sourceWidth,height:converted.sourceHeight,parts:parts.map(({base64,...p})=>({...p,blob_sha:state.blobs[p.path]})),c2pa:serialReceipt(converted.receipt)},jpeg:{...jpeg,blob_sha:state.blobs[jpeg.path]},transform:{helper:"scripts/native_image_bytes.js",resize:"center-cover-bilinear",alpha_background:"white",width:1200,height:675,jpeg_quality:84}};
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
    async function branchHead(branch) {
      const ref=await fetchJSON("/git/ref/heads/"+branch);
      if(ref.ref!=="refs/heads/"+branch||!GIT.test(ref.object?.sha||""))throw Error("Invalid branch response: "+branch);
      return ref.object.sha;
    }
    async function transportPreflight(plan,options) {
      let phase="preflight_cloud_status";
      try {
        await cloudStatus();
        phase="preflight_read";
        // Use the very same callback for this existing image and the eventual
        // tool result. A local path or file ID alone is not a byte transfer.
        const transferred=await options.readBytes(options.probeSource);
        const observedAt=new Date().toISOString();
        const {source,converted,sourceSha}=capture({imageGenResult:transferred,observedAt,
          expectedSourcePngSha256:options.expectedProbeSha256});
        if(source.length>PART_SIZE)throw Error("Use an existing probe PNG no larger than 512 KiB");
        phase="preflight_branch_lookup";
        let previousSha=null;
        try {await read(PREFLIGHT_DIR+"/receipt.json",PREFLIGHT_BRANCH);}
        catch(error) {if(!absentRef(error,PREFLIGHT_BRANCH))throw error;previousSha=false;}
        if(previousSha!==false) {
          previousSha=await branchHead(PREFLIGHT_BRANCH);
          const previous=JSON.parse((await read(PREFLIGHT_DIR+"/receipt.json",previousSha)).content);
          if(previous.version!==1||previous.kind!=="native_image_transport_preflight"||previous.repository!==repository)
            throw Error("The preflight branch is not an owned transport probe");
        }
        const receipt={version:1,kind:"native_image_transport_preflight",status:"probe_only_not_an_article_candidate",
          repository,candidate_identity:plan.identity,logical_run_id:plan.logicalRunId,observed_at:observedAt,
          route:options.route,probe_source_ref:options.probeSourceRef,
          source:{sha256:sourceSha,bytes:source.length},jpeg:{sha256:converted.sha256,bytes:converted.bytes.length},
          baseline:{commit_sha:plan.baseCommitSha,protected_hashes:plan.protectedHashes}};
        const receiptText=JSON.stringify(receipt,null,2)+"\n";
        const files=[{path:PREFLIGHT_DIR+"/probe.png",content:runtime.toBase64(source),encoding:"base64"},
          {path:PREFLIGHT_DIR+"/probe.jpg",content:converted.base64,encoding:"base64"},
          {path:PREFLIGHT_DIR+"/receipt.json",content:receiptText,encoding:"utf-8"}];
        phase="preflight_save";
        const entries=[];
        for(const file of files)entries.push({path:file.path,mode:"100644",type:"blob",
          sha:gitSha(await call("create_blob",{content:file.content,encoding:file.encoding}),"blob")});
        const treeSha=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,tree_elements:entries}),"tree");
        const commitSha=gitSha(await call("create_commit",{parent_sha:previousSha||plan.baseCommitSha,tree_sha:treeSha,
          message:"Check native image transport: "+plan.logicalRunId}),"commit");
        async function verify(ref) {
          const results=await Promise.allSettled(files.map(file=>read(file.path,ref,file.encoding)));
          for(let i=0;i<results.length;i++) {
            const item=results[i];if(item.status!=="fulfilled")throw item.reason;
            const bytes=i<2?runtime.fromBase64(item.value.content):runtime.utf8(item.value.content);
            const expected=i===0?sourceSha:i===1?converted.sha256:hashText(receiptText);
            if(runtime.sha256(bytes)!==expected)throw Error("Preflight readback SHA mismatch: "+files[i].path);
          }
          await verifyProtected(ref,plan.protectedHashes);
        }
        phase="preflight_commit_readback";await verify(commitSha);
        phase="preflight_branch_save";
        try {
          if(previousSha)await call("update_ref",{branch_name:PREFLIGHT_BRANCH,sha:commitSha,expected_sha:previousSha,force:false});
          else await call("create_branch",{branch_name:PREFLIGHT_BRANCH,sha:commitSha});
        } catch(error) {
          // Recover only a lost successful response, never overwrite a competing run.
          if(await branchHead(PREFLIGHT_BRANCH)!==commitSha)throw error;
        }
        phase="preflight_branch_readback";
        if(await branchHead(PREFLIGHT_BRANCH)!==commitSha)throw Error("Concurrent transport check changed the preflight branch");
        await verify(PREFLIGHT_BRANCH);
        return {kind:"native_image_transport_preflight",status:"passed",branch:PREFLIGHT_BRANCH,commit_sha:commitSha,
          receipt_path:PREFLIGHT_DIR+"/receipt.json",receipt_sha256:hashText(receiptText),observed_at:observedAt,
          route:options.route,source_sha256:sourceSha,jpeg_sha256:converted.sha256};
      } catch(error) {
        const e=error instanceof Error?error:Error(String(error));e.stage=phase;
        e.message="Image generation not started ("+phase+"): "+e.message;throw e;
      }
    }
    async function checkTransport(plan,options) {
      checkPlan(plan);
      if(typeof options?.readBytes!=="function"||!SHA.test(options.expectedProbeSha256||"")
        ||typeof options.route!=="string"||!options.route.trim()||options.route.length>500
        ||typeof options.probeSourceRef!=="string"||!options.probeSourceRef.trim()||options.probeSourceRef.length>1000)
        throw Error("Provide the actual byte-transfer callback and existing probe PNG reference/SHA");
      for(const name of ["fetch","update_ref"])if(typeof github[name]!=="function")throw Error("Missing preflight GitHub callback: "+name);
      return await transportPreflight(plan,options);
    }
    async function reserveGeneration(plan,proof,claim) {
      try {
        const raw=await read(claim.receipt_path,claim.branch),r=JSON.parse(raw.content);
        if(r.version!==1||r.kind!==claim.kind||r.status!==claim.status||r.repository!==repository
          ||r.identity!==plan.identity||r.slug!==plan.slug||r.article_sha256!==plan.articleSha256
          ||r.logical_run_id!==plan.logicalRunId||r.prompt_sha256!==hashText(plan.prompt))
          throw Error("Generation claim identity conflict; refusing another generation");
        claim.receipt_sha256=hashText(raw.content);
        return false;
      } catch(error) {if(!absentRef(error,claim.branch))throw error;}
      const receipt={version:1,kind:claim.kind,status:claim.status,repository,identity:plan.identity,
        slug:plan.slug,article_sha256:plan.articleSha256,logical_run_id:plan.logicalRunId,
        prompt_sha256:hashText(plan.prompt),reserved_at:new Date().toISOString(),
        baseline:{commit_sha:plan.baseCommitSha,tree_sha:plan.baseTreeSha,protected_hashes:plan.protectedHashes},
        transport_preflight:proof};
      const content=JSON.stringify(receipt,null,2)+"\n";
      claim.receipt_sha256=hashText(content);
      const blobSha=gitSha(await call("create_blob",{content,encoding:"utf-8"}),"blob");
      const treeSha=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,
        tree_elements:[{path:claim.receipt_path,mode:"100644",type:"blob",sha:blobSha}]}),"tree");
      claim.commit_sha=gitSha(await call("create_commit",{parent_sha:plan.baseCommitSha,tree_sha:treeSha,
        message:"Reserve native image generation: "+plan.slug}),"commit");
      async function verify(ref) {
        if(hashText((await read(claim.receipt_path,ref)).content)!==claim.receipt_sha256)
          throw Error("Generation claim readback mismatch");
        await verifyProtected(ref,plan.protectedHashes);
      }
      await verify(claim.commit_sha);
      // Atomic create-only reference ownership spans workers and fresh runtimes.
      // A lost/invalid response never proves ownership, even if our ref exists.
      const created=await call("create_branch",{branch_name:claim.branch,sha:claim.commit_sha});
      if(created.branch!==claim.branch)
        throw Error("Generation claim creation did not confirm ownership; recover without generating");
      if(await branchHead(claim.branch)!==claim.commit_sha)throw Error("Generation claim branch SHA mismatch");
      await verify(claim.branch);
      return true;
    }
    async function reservePrepared(plan,options,requireGenerator) {
      let phase="candidate_lookup",proof,generationClaim;
      try {
        const existing=await probe(plan);
        if(existing.status==="saved")return {...existing,plan,transportPreflight:null};
        if(generationAttempts.has(plan.identity))throw Error("This execution already requested this image; recover its output instead of generating again");
        if(requireGenerator&&typeof options?.generate!=="function")throw Error("Provide the actual generator callback");
        // A reservation always performs the real byte read/save/readback here.
        phase="preflight";proof=await checkTransport(plan,options);
        phase="candidate_lookup_after_preflight";const completed=await probe(plan);
        if(completed.status==="saved")return {...completed,plan,transportPreflight:proof};
        phase="generation_claim";
        generationClaim={kind:"native_image_generation_claim",status:"reserved_generation_unconfirmed",
          branch:CLAIM_BRANCH_PREFIX+plan.identity,receipt_path:CLAIM_DIR+"/"+plan.identity+".json"};
        const acquired=await reserveGeneration(plan,proof,generationClaim);
        // A candidate can finish during reservation, including a legacy recovery.
        // Even a losing worker may recover it; neither worker replaces the claim.
        phase="candidate_lookup_after_claim";const afterClaim=await probe(plan);
        if(afterClaim.status==="saved")return {...afterClaim,plan,transportPreflight:proof,generationClaim};
        if(!acquired)throw Error("Generation is already reserved at "+generationClaim.branch+"; recover the original output instead of generating again");
        phase="generation_cloud_status";await cloudStatus();
        return {status:"reserved",plan,transportPreflight:proof,generationClaim};
      } catch(error) {
        const e=error instanceof Error?error:Error(String(error));if(!e.stage)e.stage=phase;
        if(proof)e.transportPreflight=proof;
        if(generationClaim)e.generationClaim=generationClaim;
        throw e;
      }
    }
    async function reserveExternalGeneration(plan,options) {
      checkPlan(plan);
      if(activeGenerations.has(plan.identity))throw Error("Generation is already running for this candidate");
      activeGenerations.add(plan.identity);
      try{return await reservePrepared(plan,options,false);}
      finally{activeGenerations.delete(plan.identity);}
    }
    async function verifyReserved(plan,input,readFile=read) {
      const claim=input?.generationClaim,proof=input?.transportPreflight;
      const proofFields=["kind","status","branch","commit_sha","receipt_path","receipt_sha256",
        "observed_at","route","source_sha256","jpeg_sha256"];
      if(!claim||claim.kind!=="native_image_generation_claim"||claim.status!=="reserved_generation_unconfirmed"
        ||claim.branch!==CLAIM_BRANCH_PREFIX+plan.identity||claim.receipt_path!==CLAIM_DIR+"/"+plan.identity+".json"
        ||!GIT.test(claim.commit_sha||"")||!SHA.test(claim.receipt_sha256||""))throw Error("Invalid generation reservation");
      if(!proof||proof.kind!=="native_image_transport_preflight"||proof.status!=="passed"
        ||proof.branch!==PREFLIGHT_BRANCH||proof.receipt_path!==PREFLIGHT_DIR+"/receipt.json"
        ||Object.keys(proof).length!==proofFields.length||proofFields.some(key=>typeof proof[key]!=="string")
        ||!GIT.test(proof.commit_sha||"")||!SHA.test(proof.receipt_sha256||""))throw Error("Invalid reservation preflight");
      if(await branchHead(claim.branch)!==claim.commit_sha)throw Error("Generation reservation branch changed");
      const rawClaim=await readFile(claim.receipt_path,claim.commit_sha),record=JSON.parse(rawClaim.content);
      if(hashText(rawClaim.content)!==claim.receipt_sha256||record.version!==1||record.kind!==claim.kind||record.status!==claim.status
        ||record.repository!==repository||record.identity!==plan.identity||record.slug!==plan.slug
        ||record.article_sha256!==plan.articleSha256||record.logical_run_id!==plan.logicalRunId||record.prompt_sha256!==hashText(plan.prompt)
        ||record.baseline?.commit_sha!==plan.baseCommitSha||record.baseline.tree_sha!==plan.baseTreeSha
        ||PROTECTED.some(path=>record.baseline.protected_hashes?.[path]!==plan.protectedHashes[path])
        ||proofFields.some(key=>record.transport_preflight?.[key]!==proof[key]))throw Error("Generation reservation binding mismatch");
      const rawProof=await readFile(proof.receipt_path,proof.commit_sha),receipt=JSON.parse(rawProof.content);
      if(hashText(rawProof.content)!==proof.receipt_sha256||receipt.version!==1||receipt.kind!==proof.kind
        ||receipt.status!=="probe_only_not_an_article_candidate"||receipt.repository!==repository
        ||receipt.candidate_identity!==plan.identity||receipt.logical_run_id!==plan.logicalRunId
        ||receipt.observed_at!==proof.observed_at||receipt.route!==proof.route
        ||receipt.source?.sha256!==proof.source_sha256||receipt.jpeg?.sha256!==proof.jpeg_sha256
        ||receipt.baseline?.commit_sha!==plan.baseCommitSha
        ||PROTECTED.some(path=>receipt.baseline.protected_hashes?.[path]!==plan.protectedHashes[path]))throw Error("Reservation preflight binding mismatch");
      if(!Number.isFinite(Date.parse(record.reserved_at))||!Number.isFinite(Date.parse(input.observedAt))
        ||Date.parse(input.observedAt)<Date.parse(record.reserved_at))throw Error("Generated output precedes reservation");
      await verifyProtected(plan.baseCommitSha,plan.protectedHashes,readFile);
    }
    async function stageReserved(plan,input) {
      checkPlan(plan);
      try {
        await verifyReserved(plan,input);
        // stage checks the live stop state before every mutation. Its existing
        // saved-candidate path remains read-only and usable while stopped.
        return await stage(plan,input);
      }catch(error){
        const e=error instanceof Error?error:Error(String(error));if(!e.stage)e.stage="reservation_validation";
        if(input?.generationClaim)e.generationClaim=input.generationClaim;
        if(input?.transportPreflight)e.transportPreflight=input.transportPreflight;
        if(input?.imageGenResult)e.imageGenResult=input.imageGenResult;
        if(input?.observedAt)e.observedAt=input.observedAt;
        throw e;
      }
    }
    async function generateAndStage(plan,options) {
      checkPlan(plan);
      if(activeGenerations.has(plan.identity))throw Error("Generation is already running for this candidate");
      activeGenerations.add(plan.identity);
      let phase="reservation",attempt,proof,generationClaim;
      try {
        const reserved=await reservePrepared(plan,options,true);
        proof=reserved.transportPreflight;generationClaim=reserved.generationClaim;
        if(reserved.status==="saved"){
          const {plan:_plan,...saved}=reserved;
          return {...saved,imageGenResult:null};
        }
        phase="generation";attempt={transportPreflight:proof,generationClaim};generationAttempts.set(plan.identity,attempt);
        attempt.imageGenResult=await options.generate({prompt:plan.prompt,transparent_background:false});
        attempt.observedAt=new Date().toISOString();
        phase="generated_byte_transfer";const transferred=await options.readBytes(attempt.imageGenResult);
        attempt.transferredImage=transferred;
        const saved=await stage(plan,{imageGenResult:transferred,observedAt:attempt.observedAt,transportPreflight:proof,generationClaim});
        return {...saved,imageGenResult:attempt.imageGenResult,transportPreflight:proof,generationClaim};
      } catch(error) {
        const e=error instanceof Error?error:Error(String(error));if(!e.stage)e.stage=phase;
        // Carry real output/checkpoint to the caller without printing it. The
        // caller must retain references on failure and must not retry generation.
        if(proof)e.transportPreflight=proof;
        if(generationClaim)e.generationClaim=generationClaim;
        if(attempt){if(e.checkpoint)attempt.checkpoint=e.checkpoint;Object.assign(e,attempt);}
        throw e;
      } finally {activeGenerations.delete(plan.identity);}
    }
    // Bounded cells never run an entire transport/save macro. Version 2 carries
    // references/metadata only; checksums detect edits, not authorship.
    const canonical=x=>JSON.stringify(x,(_key,value)=>value&&typeof value==="object"&&!Array.isArray(value)
      ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,value[key]])):value);
    function sealStep(state){
      const body={...state};delete body.checksum;
      if(body.version===2){
        if(body.input)body.input={...body.input,imageGenResult:body.input.imageGenResult?.output_hint!==undefined
          ?{output_hint:body.input.imageGenResult.output_hint}:{}};
        const metadata=file=>{const copy={...file};if(copy.encoding==="base64")delete copy.content;return copy;};
        if(body.files)body.files=body.files.map(metadata);
        if(body.parts)body.parts=body.parts.map(metadata);
        if(body.jpeg)body.jpeg=metadata(body.jpeg);
        delete body.c2pa;delete body.receiptText;
        if(body.saved){body.saved={...body.saved};delete body.saved.receiptText;delete body.saved.source;delete body.saved.jpeg;}
      }
      // Do not serialize multi-MiB bytes even transiently when sealing v2.
      const plain=JSON.parse(JSON.stringify(body));
      return {...plain,checksum:hashText(canonical(plain))};
    }
    function sourceRef(value){
      if(typeof value!=="string"||!value.trim()||value.length>1000||/[\x00-\x1f\x7f]/.test(value)
        ||value.startsWith("data:")||/^https?:/i.test(value))throw Error("Retain the actual original file reference, not image bytes or a signed URL");
      return value;
    }
    async function compactCapture(state,reader,preflight=false){
      if(typeof reader!=="function")throw Error("The same original PNG byte reader is required for this step");
      const ref=sourceRef(preflight?state.probeSource:state.input.originalSourceRef);
      countStep();const image=await reader(ref);
      const input=preflight?{observedAt:state.proof.observed_at,expectedSourcePngSha256:state.proof.source_sha256,
        expectedJpegSha256:state.proof.jpeg_sha256}:{...state.input,expectedSourcePngSha256:state.sourceSha,
        expectedJpegSha256:state.jpeg?.hash};
      const captured=capture({...input,imageGenResult:image});
      if(captured.source.length>(preflight?PART_SIZE:COMPACT_SOURCE_LIMIT))throw Error("Compact image source exceeds its supported byte limit");
      return captured;
    }
    function stepState(plan,kind,checkpoint,input){
      checkPlan(plan);
      if(!checkpoint)return {version:input.compact===true?2:1,kind,identity:plan.identity,plan,phase:"lookup",steps:0,...input};
      const {checksum,...body}=checkpoint;
      if(checksum!==hashText(canonical(body))||![1,2].includes(body.version)||body.kind!==kind||body.identity!==plan.identity
        ||canonical(body.plan)!==canonical(plan)||!Number.isInteger(body.steps)||body.steps>=256)
        throw Error("Invalid or exhausted bounded image checkpoint");
      return JSON.parse(JSON.stringify(body));
    }
    async function boundedStep(plan,kind,checkpoint,input,run){
      if(stepUsage)throw Error("Another bounded image step is running");
      const state=stepState(plan,kind,checkpoint,input),before=canonical(state);
      stepUsage={calls:0,mutations:0};
      try{
        const result=await run(state);
        if(result)return {...result,tool_calls:stepUsage.calls,mutations:stepUsage.mutations};
        if(canonical(state)===before)throw Error("Bounded image step made no progress");
        state.steps++;
        return {status:"pending",phase:state.phase,checkpoint:sealStep(state),
          tool_calls:stepUsage.calls,mutations:stepUsage.mutations};
      }catch(error){
        const e=error instanceof Error?error:Error(String(error));
        e.checkpoint=sealStep(state);e.stage="bounded_"+state.phase;
        if(state.input){e.observedAt=state.input.observedAt;e.imageGenResult=state.input.imageGenResult;}
        if(state.claim)e.generationClaim=state.claim;
        if(state.proof)e.transportPreflight=state.proof;
        throw e;
      }finally{stepUsage=null;}
    }
    async function verifySlice(plan,state,ref,files){
      const all=[...files,...PROTECTED.map(path=>({path,hash:plan.protectedHashes[path],encoding:"utf-8"}))];
      const end=Math.min((state.verifyIndex||0)+2,all.length);
      for(let i=state.verifyIndex||0;i<end;i++){
        const file=all[i],raw=await read(file.path,ref,file.encoding||"utf-8");
        const bytes=file.encoding==="base64"?runtime.fromBase64(raw.content):runtime.utf8(raw.content);
        if(runtime.sha256(bytes)!==file.hash)throw Error("Bounded file readback mismatch: "+file.path);
        state.verifyIndex=i+1;
      }
      return state.verifyIndex===all.length;
    }
    async function lookupSaved(plan,state,expectedSourceSha){
      try{
        const raw=await read(plan.directory+"/receipt.json",plan.branch);
        const r=JSON.parse(raw.content);
        if(state.version===2&&(r.source?.bytes>COMPACT_SOURCE_LIMIT||r.source?.parts?.length>8))
          throw Error("Compact saved readback supports source PNGs up to 4 MiB");
        state.saved={ref:plan.branch,commitSha:await branchHead(plan.branch),receiptText:raw.content,
          receiptSha:hashText(raw.content),source:[],index:0,protectedIndex:0,expectedSourceSha:expectedSourceSha||null};
        state.phase="saved_readback";return true;
      }catch(error){if(absentRef(error,plan.branch))return false;throw error;}
    }
    async function savedSlice(plan,state){
      const saved=state.saved;
      if(state.version===2){
        // Full bytes live only in this cell. At most 8 parts + JPEG + receipt +
        // 3 protected files + final ref (+ resumed claim) fit 15 callbacks.
        const verified=await verifyCandidate(plan,saved.commitSha,saved.expectedSourceSha||undefined);
        if(verified.receipt.source.bytes>COMPACT_SOURCE_LIMIT||verified.receipt.source.parts.length>8
          ||verified.receipt_sha256!==saved.receiptSha
          ||verified.receipt.baseline.commit_sha!==plan.baseCommitSha
          ||PROTECTED.some(path=>verified.receipt.baseline.protected_hashes[path]!==plan.protectedHashes[path])
          ||await branchHead(plan.branch)!==saved.commitSha)throw Error("Compact candidate readback changed");
        return {...verified,ref:saved.ref,commitSha:saved.commitSha};
      }
      const r=JSON.parse(saved.receiptText);
      if(r.version!==1||r.kind!=="native_image_candidate"||r.status!=="awaiting_visual_review"
        ||r.identity!==plan.identity||r.repository!==repository||r.slug!==plan.slug||r.article_sha256!==plan.articleSha256
        ||r.logical_run_id!==plan.logicalRunId||r.generation?.prompt!==plan.prompt||r.generation.prompt_sha256!==hashText(plan.prompt)
        ||!SHA.test(r.source?.sha256||"")||!SHA.test(r.jpeg?.sha256||"")||!Array.isArray(r.source.parts)
        ||!r.source.parts.length||r.source.parts.length>64||!Number.isSafeInteger(r.source.bytes)
        ||r.source.bytes<33||r.source.bytes>32*1024*1024||r.jpeg.path!==plan.directory+"/candidate.jpg"
        ||r.jpeg.width!==1200||r.jpeg.height!==675||r.jpeg.quality!==84
        ||!Number.isSafeInteger(r.jpeg.bytes)||r.jpeg.bytes<16000||r.jpeg.bytes>700*1024
        ||r.baseline?.commit_sha!==plan.baseCommitSha
        ||PROTECTED.some(path=>r.baseline.protected_hashes?.[path]!==plan.protectedHashes[path])
        ||saved.expectedSourceSha&&r.source.sha256!==saved.expectedSourceSha)throw Error("Bounded candidate identity conflict");
      const files=[r.jpeg,...r.source.parts];
      if(saved.index<files.length){
        for(let i=saved.index,end=Math.min(saved.index+2,files.length);i<end;i++){
          const file=files[i];
          if(i&&file.path!==plan.directory+"/source.png.part"+String(i-1).padStart(3,"0")
            ||!SHA.test(file.sha256||"")||!Number.isSafeInteger(file.bytes)||file.bytes<1
            ||file.bytes>(i?PART_SIZE:700*1024))throw Error("Invalid bounded source part");
          const raw=await read(file.path,saved.ref,"base64"),bytes=runtime.fromBase64(raw.content);
          if(bytes.length!==file.bytes||runtime.sha256(bytes)!==file.sha256)throw Error("Bounded candidate byte mismatch");
          if(i)saved.source[i-1]=runtime.toBase64(bytes);else saved.jpeg=runtime.toBase64(bytes);
          saved.index=i+1;
        }
        return null;
      }
      if(saved.protectedIndex<PROTECTED.length){
        for(let i=saved.protectedIndex,end=Math.min(saved.protectedIndex+2,PROTECTED.length);i<end;i++){
          const path=PROTECTED[i];
          if(hashText((await read(path,saved.ref)).content)!==plan.protectedHashes[path])throw Error("Protected file changed: "+path);
          saved.protectedIndex=i+1;
        }
        return null;
      }
      if((await read(plan.directory+"/receipt.json",saved.ref)).content!==saved.receiptText
        ||await branchHead(plan.branch)!==saved.commitSha)throw Error("Bounded candidate ref changed");
      const source=new Uint8Array(r.source.bytes);let at=0;
      for(const value of saved.source){const part=runtime.fromBase64(value);if(at+part.length>source.length)throw Error("Source length overflow");source.set(part,at);at+=part.length;}
      const jpeg=runtime.fromBase64(saved.jpeg),converted=runtime.convert(source);
      if(at!==source.length||runtime.sha256(source)!==r.source.sha256||converted.source_png_sha256!==r.source.sha256
        ||converted.sha256!==r.jpeg.sha256||runtime.sha256(jpeg)!==converted.sha256
        ||!equalMetadata(serialReceipt(converted.receipt),r.source.c2pa))throw Error("Bounded PNG/JPEG/provenance mismatch");
      return {status:"saved",branch:plan.branch,ref:saved.ref,receipt:r,receipt_sha256:hashText(saved.receiptText),
        jpegPath:r.jpeg.path,sourceParts:r.source.parts.map(part=>part.path),reused:true,
        sourcePngDataUrl:"data:image/png;base64,"+runtime.toBase64(source),jpegDataUrl:"data:image/jpeg;base64,"+saved.jpeg};
    }
    async function reserveExternalGenerationStep(plan,options,checkpoint){
      return boundedStep(plan,"native_reservation_steps",checkpoint,{compact:options?.compact===true},async state=>{
        if(state.phase==="saved_readback")return await savedSlice(plan,state);
        if(state.phase==="lookup"){
          if(!await lookupSaved(plan,state))state.phase="capture";
        }else if(state.phase==="capture"){
          if(typeof options?.readBytes!=="function"||!SHA.test(options.expectedProbeSha256||"")
            ||typeof options.route!=="string"||!options.route.trim()||options.route.length>500
            ||typeof options.probeSourceRef!=="string"||!options.probeSourceRef.trim()||options.probeSourceRef.length>1000)
            throw Error("Provide the actual byte callback and existing probe reference/SHA");
          if(state.version===2)state.probeSource=sourceRef(options.probeSource);
          await cloudStatus();countStep();const image=await options.readBytes(options.probeSource);
          const observedAt=new Date().toISOString(),captured=capture({imageGenResult:image,observedAt,expectedSourcePngSha256:options.expectedProbeSha256});
          if(captured.source.length>PART_SIZE)throw Error("Use a probe PNG no larger than 512 KiB");
          state.previousSha=false;
          try{
            await read(PREFLIGHT_DIR+"/receipt.json",PREFLIGHT_BRANCH);
            state.previousSha=await branchHead(PREFLIGHT_BRANCH);
            const previous=JSON.parse((await read(PREFLIGHT_DIR+"/receipt.json",state.previousSha)).content);
            if(previous.version!==1||previous.kind!=="native_image_transport_preflight"||previous.repository!==repository)throw Error("Preflight branch is not an owned probe");
          }catch(error){if(!absentRef(error,PREFLIGHT_BRANCH))throw error;}
          const receipt={version:1,kind:"native_image_transport_preflight",status:"probe_only_not_an_article_candidate",repository,
            candidate_identity:plan.identity,logical_run_id:plan.logicalRunId,observed_at:observedAt,route:options.route,
            probe_source_ref:options.probeSourceRef,source:{sha256:captured.sourceSha,bytes:captured.source.length},
            jpeg:{sha256:captured.converted.sha256,bytes:captured.converted.bytes.length},
            baseline:{commit_sha:plan.baseCommitSha,protected_hashes:plan.protectedHashes}};
          const text=JSON.stringify(receipt,null,2)+"\n";
          state.files=[{path:PREFLIGHT_DIR+"/probe.png",content:runtime.toBase64(captured.source),encoding:"base64",hash:captured.sourceSha},
            {path:PREFLIGHT_DIR+"/probe.jpg",content:captured.converted.base64,encoding:"base64",hash:captured.converted.sha256},
            {path:PREFLIGHT_DIR+"/receipt.json",content:text,encoding:"utf-8",hash:hashText(text)}];
          state.blobs={};state.index=0;
          state.proof={kind:receipt.kind,status:"passed",branch:PREFLIGHT_BRANCH,receipt_path:PREFLIGHT_DIR+"/receipt.json",
            receipt_sha256:hashText(text),observed_at:observedAt,route:options.route,
            source_sha256:captured.sourceSha,jpeg_sha256:captured.converted.sha256};
          state.phase="preflight_blobs";
        }else if(state.phase==="preflight_blobs"){
          if(state.version===2&&state.index<2){
            if(options.probeSource!==undefined&&options.probeSource!==state.probeSource)throw Error("Probe source reference changed on retry");
            const captured=await compactCapture(state,options.readBytes,true);
            state.files[0].content=runtime.toBase64(captured.source);state.files[1].content=captured.converted.base64;
          }
          for(let n=0;n<2&&state.index<state.files.length;n++,state.index++){
            const file=state.files[state.index];state.blobs[file.path]=gitSha(await call("create_blob",{content:file.content,encoding:file.encoding}),"blob");
          }
          if(state.index===state.files.length)state.phase="preflight_commit";
        }else if(state.phase==="preflight_commit"){
          if(!state.treeSha)state.treeSha=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,
            tree_elements:state.files.map(file=>({path:file.path,mode:"100644",type:"blob",sha:state.blobs[file.path]}))}),"tree");
          if(!state.commitSha)state.commitSha=gitSha(await call("create_commit",{parent_sha:state.previousSha||plan.baseCommitSha,
            tree_sha:state.treeSha,message:"Check native image transport: "+plan.logicalRunId}),"commit");
          state.proof.commit_sha=state.commitSha;state.verifyIndex=0;state.phase="preflight_verify_commit";
        }else if(state.phase==="preflight_verify_commit"){
          if(await verifySlice(plan,state,state.commitSha,state.files))state.phase="preflight_publish";
        }else if(state.phase==="preflight_publish"){
          try{
            if(state.previousSha)await call("update_ref",{branch_name:PREFLIGHT_BRANCH,sha:state.commitSha,expected_sha:state.previousSha,force:false});
            else await call("create_branch",{branch_name:PREFLIGHT_BRANCH,sha:state.commitSha});
          }catch(error){if(await branchHead(PREFLIGHT_BRANCH)!==state.commitSha)throw error;}
          if(await branchHead(PREFLIGHT_BRANCH)!==state.commitSha)throw Error("Concurrent preflight branch change");
          state.verifyIndex=0;state.phase="preflight_verify_branch";
        }else if(state.phase==="preflight_verify_branch"){
          if(await verifySlice(plan,state,PREFLIGHT_BRANCH,state.files)){
            if(await branchHead(PREFLIGHT_BRANCH)!==state.commitSha)throw Error("Preflight branch changed during readback");
            state.phase="claim_prepare";
          }
        }else if(state.phase==="claim_prepare"){
          if(await lookupSaved(plan,state))return;
          state.claim={kind:"native_image_generation_claim",status:"reserved_generation_unconfirmed",
            branch:CLAIM_BRANCH_PREFIX+plan.identity,receipt_path:CLAIM_DIR+"/"+plan.identity+".json"};
          try{await read(state.claim.receipt_path,state.claim.branch);throw Error("Generation already reserved; recover only");}
          catch(error){if(!absentRef(error,state.claim.branch))throw error;}
          const receipt={version:1,...state.claim,repository,identity:plan.identity,slug:plan.slug,article_sha256:plan.articleSha256,
            logical_run_id:plan.logicalRunId,prompt_sha256:hashText(plan.prompt),reserved_at:new Date().toISOString(),
            baseline:{commit_sha:plan.baseCommitSha,tree_sha:plan.baseTreeSha,protected_hashes:plan.protectedHashes},transport_preflight:state.proof};
          state.claimText=JSON.stringify(receipt,null,2)+"\n";state.claim.receipt_sha256=hashText(state.claimText);
          const blob=gitSha(await call("create_blob",{content:state.claimText,encoding:"utf-8"}),"blob");
          state.claimTree=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,
            tree_elements:[{path:state.claim.receipt_path,mode:"100644",type:"blob",sha:blob}]}),"tree");
          state.phase="claim_commit";
        }else if(state.phase==="claim_commit"){
          if(!state.claim.commit_sha)state.claim.commit_sha=gitSha(await call("create_commit",{parent_sha:plan.baseCommitSha,
            tree_sha:state.claimTree,message:"Reserve native image generation: "+plan.slug}),"commit");
          if((await read(state.claim.receipt_path,state.claim.commit_sha)).content!==state.claimText)throw Error("Claim commit readback mismatch");
          await verifyProtected(state.claim.commit_sha,plan.protectedHashes);state.phase="claim_final";
        }else if(state.phase==="claim_final"){
          // This entire ownership grant is one cold <=15-call cell. Never resume
          // an uncertain create response into permission to generate.
          const created=await call("create_branch",{branch_name:state.claim.branch,sha:state.claim.commit_sha});
          if(created.branch!==state.claim.branch||await branchHead(state.claim.branch)!==state.claim.commit_sha)
            throw Error("Claim ownership unconfirmed; recover only");
          if((await read(state.claim.receipt_path,state.claim.branch)).content!==state.claimText)throw Error("Claim branch readback mismatch");
          await verifyProtected(state.claim.branch,plan.protectedHashes);
          if(await lookupSaved(plan,state))return;
          await cloudStatus();
          return {status:"reserved",plan,transportPreflight:state.proof,generationClaim:state.claim};
        }else throw Error("Unknown bounded reservation phase");
      });
    }
    async function stageReservedStep(plan,input,checkpoint){
      const {readBytes,...retained}=input||{};
      if(retained.compact===true){
        if(retained.originalSourceRef!==undefined)sourceRef(retained.originalSourceRef);
        if(retained.imageGenResult?.output_hint!==undefined)sourceRef(retained.imageGenResult.output_hint);
      }
      return boundedStep(plan,"native_stage_steps",checkpoint,{input:retained,compact:retained.compact===true},async state=>{
        if(checkpoint&&input){
          for(const key of ["observedAt","transportPreflight","generationClaim","originalSourceRef"])
            if(input[key]!==undefined&&canonical(input[key])!==canonical(state.input[key]))throw Error("Reserved source metadata changed on retry");
          if(state.version===1&&input.imageGenResult?.image_url&&input.imageGenResult.image_url!==state.input.imageGenResult?.image_url)
            throw Error("Reserved original PNG changed on retry");
        }
        if(!["lookup","reservation_verify"].includes(state.phase)
          &&await branchHead(state.input.generationClaim.branch)!==state.input.generationClaim.commit_sha)
          throw Error("Generation reservation branch changed between steps");
        if(state.phase==="saved_readback")return await savedSlice(plan,state);
        if(state.phase==="lookup"){
          if(state.version===2){
            sourceRef(state.input.originalSourceRef);
            if(!state.input.imageGenResult)state.input.imageGenResult={output_hint:state.input.originalSourceRef};
          }else if(!state.input?.imageGenResult)throw Error("Original reserved PNG is required");
          state.phase="reservation_verify";
        }else if(state.phase==="reservation_verify"){
          // Reuse the strict claim/proof validator without doing a monolithic save.
          await verifyReserved(plan,state.input);
          const captured=state.version===2?await compactCapture(state,readBytes):capture(state.input);
          state.sourceSha=captured.sourceSha;state.sourceBytes=captured.source.length;
          state.parts=[];for(let i=0;i<captured.source.length;i+=PART_SIZE){const part=captured.source.slice(i,i+PART_SIZE);
            state.parts.push({path:plan.directory+"/source.png.part"+String(state.parts.length).padStart(3,"0"),
              content:runtime.toBase64(part),encoding:"base64",hash:runtime.sha256(part),bytes:part.length});}
          state.jpeg={path:plan.directory+"/candidate.jpg",content:captured.converted.base64,encoding:"base64",
            hash:captured.converted.sha256,bytes:captured.converted.bytes.length};
          state.c2pa=serialReceipt(captured.converted.receipt);state.sourceWidth=captured.converted.sourceWidth;state.sourceHeight=captured.converted.sourceHeight;
          state.blobs={};state.index=0;state.phase="candidate_lookup";
        }else if(state.phase==="candidate_lookup"){
          if(!await lookupSaved(plan,state,state.sourceSha))state.phase="upload";
        }else if(state.phase==="upload"){
          if(state.version===2){
            const captured=await compactCapture(state,readBytes);
            for(let i=0;i<state.parts.length;i++){
              const part=captured.source.slice(i*PART_SIZE,(i+1)*PART_SIZE);
              if(runtime.sha256(part)!==state.parts[i].hash)throw Error("Original PNG part changed on retry");
              state.parts[i].content=runtime.toBase64(part);
            }
            state.jpeg.content=captured.converted.base64;
          }
          const files=[...state.parts,state.jpeg];
          for(let n=0;n<2&&state.index<files.length;n++,state.index++){
            const file=files[state.index];state.blobs[file.path]=gitSha(await call("create_blob",{content:file.content,encoding:"base64"}),"blob");
          }
          if(state.index===files.length)state.phase="receipt";
        }else if(state.phase==="receipt"){
          const captured=state.version===2?await compactCapture(state,readBytes):null;
          const source=captured?.source||runtime.fromBase64(state.input.imageGenResult.image_url),jpeg=state.jpeg;
          const receipt={version:1,kind:"native_image_candidate",status:"awaiting_visual_review",identity:plan.identity,repository,slug:plan.slug,
            article_sha256:plan.articleSha256,logical_run_id:plan.logicalRunId,
            baseline:{commit_sha:plan.baseCommitSha,tree_sha:plan.baseTreeSha,protected_hashes:plan.protectedHashes},
            generation:{capture_kind:"native_tool_result",prompt:plan.prompt,prompt_sha256:hashText(plan.prompt),observed_at:state.input.observedAt,
              actual_output_hint:state.input.imageGenResult.output_hint??null,recovery:null,
              transport_preflight:state.input.transportPreflight,generation_claim:state.input.generationClaim},
            source:{sha256:state.sourceSha,bytes:source.length,width:state.sourceWidth,height:state.sourceHeight,
              parts:state.parts.map(part=>({path:part.path,bytes:part.bytes,sha256:part.hash,blob_sha:state.blobs[part.path]})),
              c2pa:captured?serialReceipt(captured.converted.receipt):state.c2pa},
            jpeg:{path:jpeg.path,bytes:jpeg.bytes,sha256:jpeg.hash,width:1200,height:675,quality:84,blob_sha:state.blobs[jpeg.path]},
            transform:{helper:"scripts/native_image_bytes.js",resize:"center-cover-bilinear",alpha_background:"white",width:1200,height:675,jpeg_quality:84}};
          state.receiptText=JSON.stringify(receipt,null,2)+"\n";state.receiptHash=hashText(state.receiptText);
          if(runtime.utf8(state.receiptText).length>=PART_SIZE)throw Error("Candidate receipt is too large");
          const path=plan.directory+"/receipt.json";
          state.blobs[path]=gitSha(await call("create_blob",{content:state.receiptText,encoding:"utf-8"}),"blob");state.phase="commit";
        }else if(state.phase==="commit"){
          if(!state.treeSha)state.treeSha=gitSha(await call("create_tree",{base_tree_sha:plan.baseTreeSha,
            tree_elements:[...state.parts,state.jpeg,{path:plan.directory+"/receipt.json"}].map(file=>({path:file.path,mode:"100644",type:"blob",sha:state.blobs[file.path]}))}),"tree");
          if(!state.commitSha)state.commitSha=gitSha(await call("create_commit",{parent_sha:plan.baseCommitSha,tree_sha:state.treeSha,message:"Stage native image candidate: "+plan.slug}),"commit");
          state.verifyIndex=0;state.phase="verify_commit";
        }else if(state.phase==="verify_commit"){
          const files=[...state.parts,state.jpeg,{path:plan.directory+"/receipt.json",hash:state.receiptHash||hashText(state.receiptText),encoding:"utf-8"}];
          if(await verifySlice(plan,state,state.commitSha,files))state.phase="publish";
        }else if(state.phase==="publish"){
          if(await lookupSaved(plan,state,state.sourceSha))return;
          try{await call("create_branch",{branch_name:plan.branch,sha:state.commitSha});}
          catch(error){if(!await lookupSaved(plan,state,state.sourceSha))throw error;return;}
          if(!await lookupSaved(plan,state,state.sourceSha))throw Error("Candidate branch was not saved");
        }else throw Error("Unknown bounded reserved-stage phase");
      });
    }
    return {prepare,prepareArticle,probe,stage,resume,load,preparePublication,checkTransport,reserveExternalGeneration,stageReserved,generateAndStage,
      reserveExternalGenerationStep,stageReservedStep,cloudStatus};
  }
  return {create,PART_SIZE};
})();
if(typeof module!=="undefined"&&module.exports)module.exports=NativeImageStage;
