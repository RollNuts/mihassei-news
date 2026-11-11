/* Pure-JS regression fixtures. Synthetic C2PA boxes below are unsigned test data,
 * never provider receipts. Run with a loaded NativeImageBytes API and pinned sources:
 * new Function(testSource + '\nreturn NativeImageBytesTests;')()(api, sources).
 */
var NativeImageBytesTests = function (api, sources) {
  "use strict";
  const results=[];
  function assert(ok,name){if(!ok)throw Error("FAIL: "+name);results.push(name);}
  function rejects(fn,name){let failed=false;try{fn();}catch(e){failed=true;}assert(failed,name);}
  const rt=api.initialize(sources);
  // pngjs/pngjs test/in/basn2c08.png, commit c565210c602527eb459f857eeb78183997482d5b.
  const png=api.fromBase64("iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAABGdBTUEAAYagMeiWXwAAAEhJREFUeJzt1cEJADAMAkCF7JH9t3ITO0Qr9KH4zuErtA0EO4AKFPgcoO3kfUx4QIECD0qHH8KEBxQo8KB0OCOpQIG7cHejwAGCsfleD0DPSwAAAABJRU5ErkJggg==");
  function concat(...parts){const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let at=0;for(const p of parts){out.set(p,at);at+=p.length;}return out;}
  function u32(n){return new Uint8Array([n>>>24,(n>>>16)&255,(n>>>8)&255,n&255]);}
  function crc(b){let x=0xffffffff;for(const v of b){x^=v;for(let i=0;i<8;i++)x=(x>>>1)^((x&1)?0xedb88320:0);}return (x^0xffffffff)>>>0;}
  function chunk(type,data){const body=concat(api.utf8(type),data);return concat(u32(data.length),body,u32(crc(body)));}
  function box(type,data){return concat(u32(data.length+8),api.utf8(type),data);}
  function superbox(label,...children){return box("jumb",concat(box("jumd",concat(new Uint8Array(16),new Uint8Array([3]),api.utf8(label),new Uint8Array([0]))),...children));}
  function head(major,n){if(n<24)return new Uint8Array([(major<<5)|n]);if(n<256)return new Uint8Array([(major<<5)|24,n]);if(n<65536)return new Uint8Array([(major<<5)|25,n>>>8,n&255]);return concat(new Uint8Array([(major<<5)|26]),u32(n));}
  function enc(v){if(typeof v==="string"){const b=api.utf8(v);return concat(head(3,b.length),b);}if(Array.isArray(v))return concat(head(4,v.length),...v.map(enc));const entries=Object.entries(v);return concat(head(5,entries.length),...entries.flatMap(([k,x])=>[enc(k),enc(x)]));}
  const oldID="xmp:iid:11111111-1111-4111-8111-111111111111",currentID="xmp:iid:22222222-2222-4222-8222-222222222222",decoyID="xmp:iid:33333333-3333-4333-8333-333333333333";
  const when="2026-11-11T06:30:04.644653836Z";
  const action={actions:[{action:"c2pa.created",when,softwareAgent:{name:"Fixture creator",version:"test"}}]};
  function manifest(label,claim){return superbox(label,superbox("c2pa.assertions",superbox("c2pa.ingredient.v3",box("cbor",enc({instanceID:decoyID}))),superbox("c2pa.actions.v2",box("cbor",enc(action)))),superbox("c2pa.claim",box("cbor",claim)));}
  function payload(claim){return superbox("c2pa",manifest("urn:c2pa:old",enc({instanceID:oldID})),manifest("urn:c2pa:current",claim));}
  function withMetadata(data){return concat(png.subarray(0,png.length-12),chunk("caBX",data),png.subarray(png.length-12));}
  // Repairing or replacing provenance is never part of runtime code; these builders
  // exist only to construct intentionally synthetic parser fixtures.
  assert(rt.sha256("")==="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","SHA-256 empty vector");
  const b=api.utf8("日本語😀");assert(api.toBase64(b)==="5pel5pys6Kqe8J+YgA==","UTF-8 and base64 known vector");
  assert(rt.sha256(api.fromBase64(api.toBase64(b)))===rt.sha256(b),"base64 roundtrip");
  rejects(()=>api.fromBase64("A===\n"),"malformed base64 rejected");
  const plain=rt.convert(png);
  assert(plain.width===1200&&plain.height===675&&plain.quality===84,"fixed delivery dimensions and quality");
  assert(plain.sha256==="fed04331a8d183970996abc287bcbde743a3e7acacac05d4a9c732bdc0598331","PNG-to-JPEG known byte digest");
  assert(plain.source_png_sha256==="c90e86090a625661b19960cafdde6e347d6e32d73837aaae533f66dd3f099506","original PNG digest");
  assert(plain.receipt===null,"no C2PA means no invented receipt");
  const evidence=api.extractC2PA(withMetadata(payload(enc({instanceID:currentID,claim_generator:"Unsigned synthetic fixture"}))));
  assert(evidence.generation_receipt===currentID&&evidence.generation_id===currentID.slice(8),"current direct claim wins over old and ingredient IDs");
  assert(evidence.source_c2pa.actions[0].value.actions[0].when===when,"nanosecond timestamp retained");
  assert(evidence.source_c2pa.signature_validation==="not_performed","no false signature-verification claim");
  rejects(()=>api.extractC2PA(withMetadata(payload(enc({claim_generator:"Missing ID fixture"})))),"missing current ID cannot use ingredient or manifest UUID");
  const duplicate=concat(new Uint8Array([0xa2]),enc("instanceID"),enc(currentID),enc("instanceID"),enc(decoyID));
  rejects(()=>api.extractC2PA(withMetadata(payload(duplicate))),"duplicate CBOR instanceID rejected");
  const broken=payload(enc({instanceID:currentID})).slice();broken.set([255,255,255,255],0);
  rejects(()=>api.extractC2PA(withMetadata(broken)),"JUMBF parent overflow rejected");
  const corrupt=png.slice();corrupt[40]^=1;rejects(()=>rt.convert(corrupt),"PNG CRC corruption rejected");
  rejects(()=>api.initialize({...sources,encode:sources.encode+"\n"}),"changed pinned dependency rejected");
  return {passed:results.length,checks:results,jpeg_bytes:plain.bytes.length,jpeg_sha256:plain.sha256};
};
if(typeof module!=="undefined"&&module.exports)module.exports=NativeImageBytesTests;
