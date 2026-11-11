/* Native ImageGen delivery bytes; no network, filesystem, Node or paid API required.
 * Load: new Function(source + '\nreturn NativeImageBytes;')().
 * Fetch SOURCES through a trusted GitHub connector, then initialize(sourceMap).
 * C2PA extraction records observed asset identifiers; it does NOT verify signatures,
 * certificate trust or content bindings, and never invents a generation/job ID.
 */
var NativeImageBytes = (function () {
  "use strict";
  const SOURCES = Object.freeze({
    upng: Object.freeze({repository_full_name:"photopea/UPNG.js",path:"UPNG.js",ref:"88f504b6577b726975f593caa38ba85d327c7c1f",sha256:"39213412bc9945002588f5e12adfc555c63d314874bba1dbed5be802b1c86ed2"}),
    encode: Object.freeze({repository_full_name:"jpeg-js/jpeg-js",path:"lib/encoder.js",ref:"1031ccd7a63c641c16afb2430279d1598530a85c",sha256:"88469ea7656343c717cd74a4bfc824623ed25277b6685f5e03c5c194d8af48d5"}),
    sha: Object.freeze({repository_full_name:"emn178/js-sha256",path:"build/sha256.cjs",ref:"9a54fb31d4594762987e1b5d175265f6bac921de",sha256:"fb7860e00d7718e1c4b0d1df112727261f8cbee112963db1b46ad7b5874041db"})
  });
  const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  function fromBase64(value) {
    if (typeof value !== "string") throw Error("Expected base64 string");
    let s = value;
    if (s.startsWith("data:")) {
      const comma = s.indexOf(",");
      if (comma < 0 || !s.slice(0, comma).endsWith(";base64")) throw Error("Expected base64 data URL");
      s = s.slice(comma + 1);
    }
    s = s.replace(/\s/g, "");
    const pad = s.indexOf("=");
    if (s.length % 4 || /[^A-Za-z0-9+/=]/.test(s) || (pad >= 0 && !(pad === s.length - 1 || (pad === s.length - 2 && s.endsWith("=="))))) throw Error("Invalid base64");
    const padding = s.endsWith("==") ? 2 : s.endsWith("=") ? 1 : 0;
    const map = new Int16Array(128); map.fill(-1);
    for (let i = 0; i < 64; i++) map[alphabet.charCodeAt(i)] = i;
    const out = new Uint8Array(s.length / 4 * 3 - padding); let p = 0;
    for (let i = 0; i < s.length; i += 4) {
      const n = (map[s.charCodeAt(i)] << 18) | (map[s.charCodeAt(i+1)] << 12) | ((s[i+2] === "=" ? 0 : map[s.charCodeAt(i+2)]) << 6) | (s[i+3] === "=" ? 0 : map[s.charCodeAt(i+3)]);
      out[p++] = (n >>> 16) & 255;
      if (p < out.length) out[p++] = (n >>> 8) & 255;
      if (p < out.length) out[p++] = n & 255;
    }
    return out;
  }
  function toBase64(bytes) {
    const chunks = []; let s = "";
    for (let i = 0; i < bytes.length; i += 3) {
      const n = (bytes[i] << 16) | ((bytes[i+1] || 0) << 8) | (bytes[i+2] || 0);
      s += alphabet[(n >>> 18) & 63] + alphabet[(n >>> 12) & 63] + (i+1 < bytes.length ? alphabet[(n >>> 6) & 63] : "=") + (i+2 < bytes.length ? alphabet[n & 63] : "=");
      if (s.length >= 32768) { chunks.push(s); s = ""; }
    }
    chunks.push(s); return chunks.join("");
  }
  function utf8(text) {
    const out = [];
    for (const ch of text) {
      let c = ch.codePointAt(0); if (c >= 0xd800 && c <= 0xdfff) c = 0xfffd;
      if (c < 128) out.push(c);
      else if (c < 2048) out.push(192 | (c >>> 6), 128 | (c & 63));
      else if (c < 65536) out.push(224 | (c >>> 12), 128 | ((c >>> 6) & 63), 128 | (c & 63));
      else out.push(240 | (c >>> 18), 128 | ((c >>> 12) & 63), 128 | ((c >>> 6) & 63), 128 | (c & 63));
    }
    return new Uint8Array(out);
  }
  function decodeUTF8(b) {
    const out = [];
    for (let i = 0; i < b.length;) {
      const x = b[i++]; let n, cp, min;
      if (x < 128) { out.push(String.fromCharCode(x)); continue; }
      if (x >= 194 && x <= 223) { n=1; cp=x&31; min=128; }
      else if (x >= 224 && x <= 239) { n=2; cp=x&15; min=2048; }
      else if (x >= 240 && x <= 244) { n=3; cp=x&7; min=65536; }
      else throw Error("Invalid UTF-8 metadata");
      if (i+n > b.length) throw Error("Truncated UTF-8 metadata");
      while (n--) { const y=b[i++]; if ((y&192)!==128) throw Error("Invalid UTF-8 continuation"); cp=(cp<<6)|(y&63); }
      if (cp<min || cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff)) throw Error("Invalid UTF-8 code point");
      out.push(String.fromCodePoint(cp));
    }
    return out.join("");
  }
  function ascii(b, start, end) { let s=""; for(let i=start;i<end;i++)s+=String.fromCharCode(b[i]); return s; }
  function crc32(b) { let c=0xffffffff; for(const x of b){c^=x;for(let j=0;j<8;j++)c=(c>>>1)^((c&1)?0xedb88320:0);} return (c^0xffffffff)>>>0; }
  function pngChunks(bytes) {
    const signature=[137,80,78,71,13,10,26,10];
    if (bytes.length<33 || bytes.length>32*1024*1024 || signature.some((v,i)=>bytes[i]!==v)) throw Error("Invalid or oversized PNG");
    const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength), chunks=[]; let p=8, ended=false;
    while(p<bytes.length) {
      if(p+12>bytes.length)throw Error("Truncated PNG chunk");
      const n=v.getUint32(p), end=p+12+n;
      if(end>bytes.length)throw Error("PNG chunk exceeds input");
      const type=ascii(bytes,p+4,p+8);
      if(crc32(bytes.subarray(p+4,p+8+n))!==v.getUint32(p+8+n))throw Error("PNG CRC mismatch: "+type);
      chunks.push({type,data:bytes.subarray(p+8,p+8+n)}); p=end;
      if(type==="IEND"){if(n!==0 || p!==bytes.length)throw Error("Invalid PNG end");ended=true;break;}
    }
    if(!ended || chunks[0].type!=="IHDR" || chunks[0].data.length!==13)throw Error("Incomplete PNG");
    if(chunks.some(c=>c.type==="acTL"))throw Error("Animated PNG not supported");
    const d=new DataView(chunks[0].data.buffer,chunks[0].data.byteOffset,13), w=d.getUint32(0),h=d.getUint32(4);
    if(!w || !h || w*h>32000000)throw Error("Invalid or oversized PNG dimensions");
    return {chunks,width:w,height:h};
  }
  // Bounded, definite-length CBOR. C2PA claims use deterministic CBOR.
  function cbor(bytes) {
    const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let p=0,budget=100000;
    function take(n){if(n<0 || p+n>bytes.length)throw Error("Truncated CBOR");const s=p;p+=n;return s;}
    function read(depth){
      if(depth>48 || --budget<0)throw Error("CBOR limits exceeded");
      const lead=bytes[take(1)], major=lead>>>5, ai=lead&31;
      if(major===7){if(ai===20)return false;if(ai===21)return true;if(ai===22)return null;if(ai===26)return v.getFloat32(take(4));if(ai===27)return v.getFloat64(take(8));throw Error("Unsupported CBOR simple value");}
      let n;
      if(ai<24)n=ai;else if(ai===24)n=bytes[take(1)];else if(ai===25)n=v.getUint16(take(2));else if(ai===26)n=v.getUint32(take(4));else if(ai===27){const q=take(8);n=v.getUint32(q)*4294967296+v.getUint32(q+4);if(!Number.isSafeInteger(n))throw Error("Unsafe CBOR integer");}else throw Error("Indefinite CBOR unsupported");
      if(major===0)return n;if(major===1)return -1-n;
      if(major===2 || major===3){const s=take(n),b=bytes.subarray(s,s+n);return major===2?b.slice():decodeUTF8(b);}
      if(n>100000 && (major===4 || major===5))throw Error("CBOR collection too large");
      if(major===4){const a=[];for(let i=0;i<n;i++)a.push(read(depth+1));return a;}
      if(major===5){const o=Object.create(null);for(let i=0;i<n;i++){const k=read(depth+1);if(typeof k!=="string" || Object.prototype.hasOwnProperty.call(o,k))throw Error("Invalid CBOR map key");o[k]=read(depth+1);}return o;}
      if(major===6)return read(depth+1);
      throw Error("Unsupported CBOR type");
    }
    const out=read(0);if(p!==bytes.length)throw Error("Trailing CBOR bytes");return out;
  }
  function jumbfBoxes(bytes) {
    const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let budget=10000;
    function parse(start,end,depth){
      if(depth>16)throw Error("JUMBF depth exceeded");const out=[];
      for(let p=start;p<end;){
        if(--budget<0 || p+8>end)throw Error("Invalid JUMBF box");
        let size=v.getUint32(p),header=8;const type=ascii(bytes,p+4,p+8);
        if(size===1){if(p+16>end)throw Error("Truncated extended JUMBF box");size=v.getUint32(p+8)*4294967296+v.getUint32(p+12);header=16;}
        if(size===0)size=end-p;
        if(!Number.isSafeInteger(size)||size<header||p+size>end)throw Error("JUMBF box exceeds parent");
        const box={type,data:bytes.subarray(p+header,p+size)};
        if(type==="jumb"){
          box.children=parse(p+header,p+size,depth+1);
          const desc=box.children.find(x=>x.type==="jumd");
          if(desc && desc.data.length>17){if((desc.data[16]&3)!==3)throw Error("Unsupported JUMBF description flags");let q=17;while(q<desc.data.length && desc.data[q]!==0)q++;if(q===desc.data.length)throw Error("Unterminated JUMBF label");box.label=decodeUTF8(desc.data.subarray(17,q));}
        }
        out.push(box);p+=size;
      }
      return out;
    }
    return parse(0,bytes.length,0);
  }
  function extractC2PA(bytes) {
    const parsed=pngChunks(bytes), chunks=parsed.chunks.filter(c=>c.type==="caBX");
    if(!chunks.length)return null;
    if(chunks.length!==1)throw Error("Ambiguous multiple PNG C2PA chunks");
    const roots=jumbfBoxes(chunks[0].data), stores=roots.filter(b=>b.type==="jumb" && b.label==="c2pa");
    if(stores.length!==1)throw Error("Expected one C2PA manifest store");
    const manifests=stores[0].children.filter(b=>b.type==="jumb" && typeof b.label==="string" && b.label.startsWith("urn:"));
    if(!manifests.length)throw Error("No C2PA manifest");
    // The final manifest is the active candidate; no recursive ingredient lookup.
    const manifest=manifests[manifests.length-1];
    const claims=manifest.children.filter(b=>b.type==="jumb" && (b.label==="c2pa.claim" || b.label==="c2pa.claim.v2"));
    if(claims.length!==1)throw Error("Ambiguous current C2PA claim");
    const payloads=claims[0].children.filter(b=>b.type==="cbor");
    if(payloads.length!==1)throw Error("Expected one CBOR claim payload");
    const claim=cbor(payloads[0].data), instanceID=claim.instanceID;
    if(typeof instanceID!=="string" || !instanceID.startsWith("xmp:iid:") || !UUID.test(instanceID.slice(8)))throw Error("Current claim has no supported asset instance UUID");
    const actions=[];
    for(const assertionStore of manifest.children.filter(b=>b.type==="jumb" && b.label==="c2pa.assertions")) {
      for(const assertion of assertionStore.children.filter(b=>b.type==="jumb" && /^c2pa\.actions(?:\.v\d+)?(?:__\d+)?$/.test(b.label||""))) {
        for(const payload of assertion.children.filter(b=>b.type==="cbor"))actions.push({label:assertion.label,value:cbor(payload.data)});
      }
    }
    return {
      generation_id:instanceID.slice(8), generation_receipt:instanceID,
      generation_id_source:"Actual C2PA claim instanceID embedded in the original PNG; asset-instance identifier, not an exposed backend generation/job ID.",
      source_c2pa:{manifest_uri:manifest.label,instanceID,claim_generator:claim.claim_generator||null,claim_generator_info:claim.claim_generator_info||null,actions,signature_validation:"not_performed",content_binding_validation:"not_performed"},
      caBX_bytes:chunks[0].data.slice()
    };
  }
  function centerFitRGBA(src,sw,sh,dw=1200,dh=675) {
    if(src.length!==sw*sh*4)throw Error("Invalid RGBA");
    const dst=new Uint8Array(dw*dh*4),scale=Math.max(dw/sw,dh/sh),ox=(sw-dw/scale)/2,oy=(sh-dh/scale)/2;
    for(let y=0;y<dh;y++){
      const sy=Math.max(0,Math.min(sh-1,oy+(y+0.5)/scale-0.5)),y0=Math.floor(sy),y1=Math.min(y0+1,sh-1),fy=sy-y0;
      for(let x=0;x<dw;x++){
        const sx=Math.max(0,Math.min(sw-1,ox+(x+0.5)/scale-0.5)),x0=Math.floor(sx),x1=Math.min(x0+1,sw-1),fx=sx-x0;
        const p00=(y0*sw+x0)*4,p10=(y0*sw+x1)*4,p01=(y1*sw+x0)*4,p11=(y1*sw+x1)*4;
        const a=(1-fx)*(1-fy),b=fx*(1-fy),c=(1-fx)*fy,d=fx*fy,a00=src[p00+3]/255,a10=src[p10+3]/255,a01=src[p01+3]/255,a11=src[p11+3]/255;
        const alpha=a*a00+b*a10+c*a01+d*a11,p=(y*dw+x)*4;
        for(let k=0;k<3;k++)dst[p+k]=Math.round(a*src[p00+k]*a00+b*src[p10+k]*a10+c*src[p01+k]*a01+d*src[p11+k]*a11+255*(1-alpha));
        dst[p+3]=255;
      }
    }
    return dst;
  }
  async function fetchSources(fetchFile) {
    const keys=Object.keys(SOURCES),result=await Promise.allSettled(keys.map(key=>{const {sha256,...args}=SOURCES[key];return fetchFile(args);})),sources={};
    for(let i=0;i<keys.length;i++){
      const r=result[i];if(r.status!=="fulfilled")throw Error("Source fetch failed: "+keys[i]);
      const s=r.value.structuredContent;if(r.value.isError||s?.encoding!=="utf-8"||typeof s.content!=="string")throw Error("Invalid source response: "+keys[i]);sources[keys[i]]=s.content;
    }
    return sources;
  }
  function initialize(sources) {
    for(const key of Object.keys(SOURCES))if(typeof sources[key]!=="string")throw Error("Missing dependency source: "+key);
    const mod={exports:{}};new Function("module",sources.sha)(mod);const sha256=mod.exports;
    if(typeof sha256!=="function"||sha256("abc")!=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")throw Error("SHA-256 self-test failed");
    for(const key of Object.keys(SOURCES))if(sha256(sources[key])!==SOURCES[key].sha256)throw Error("Pinned source SHA mismatch: "+key);
    const UPNG=new Function(sources.upng+"\nreturn UPNG;")(),encode=new Function("module","window",sources.encode+"\nreturn encode;")(undefined,undefined);
    function convert(input) {
      const bytes=typeof input==="string"?fromBase64(input):new Uint8Array(input),info=pngChunks(bytes),receipt=extractC2PA(bytes);
      const png=UPNG.decode(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)),frames=UPNG.toRGBA8(png);
      if(frames.length!==1)throw Error("Animated PNG not supported");
      const rgba=centerFitRGBA(new Uint8Array(frames[0]),png.width,png.height),jpeg=encode({data:rgba,width:1200,height:675},84).data;
      return {bytes:jpeg,base64:toBase64(jpeg),sha256:sha256(jpeg),source_png_sha256:sha256(bytes),sourceWidth:info.width,sourceHeight:info.height,width:1200,height:675,quality:84,receipt};
    }
    return {convert,sha256,extractC2PA,fromBase64,toBase64,utf8};
  }
  return {SOURCES,fetchSources,initialize,fromBase64,toBase64,utf8,extractC2PA};
})();
if (typeof module !== "undefined" && module.exports) module.exports = NativeImageBytes;
