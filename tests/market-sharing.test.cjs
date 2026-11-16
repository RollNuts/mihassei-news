"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");

// Exercise the exact shared controls without starting unrelated analytics.
const source=fs.readFileSync("assets/engagement.js","utf8");
const start=source.indexOf('  document.querySelectorAll("[data-native-share]")');
assert.ok(start>0);
const code=source.slice(start,source.lastIndexOf("})();"));
const canonical="https://veripsa.com/serials/market-lab/";

function setup({share,canShare,clipboard}={}){
  const handlers={},prompts=[],copied=[],label={textContent:"リンクをコピー"};
  const native={hidden:true,dataset:{shareTitle:"未来相場観測室",shareUrl:canonical},
    addEventListener:(event,fn)=>{handlers.native=fn;}};
  const copy={dataset:{copyUrl:canonical},querySelector:()=>label,
    addEventListener:(event,fn)=>{handlers.copy=fn;}};
  const navigator={share,canShare,clipboard:clipboard||{writeText:async text=>copied.push(text)}};
  const window={prompt:(...args)=>prompts.push(args),setTimeout:()=>{}};
  const document={querySelectorAll:selector=>selector==="[data-native-share]"?[native]:[copy]};
  vm.runInNewContext(code,{document,navigator,window});
  return {native,handlers,prompts,copied,label};
}

test("native sharing is hidden on unsupported browsers and does nothing until clicked",async()=>{
  const missing=setup();
  assert.equal(missing.native.hidden,true);
  assert.equal(missing.handlers.native,undefined);
  const rejected=setup({share:async()=>{},canShare:()=>false});
  assert.equal(rejected.native.hidden,true);
  const calls=[];
  const supported=setup({share:async payload=>calls.push(payload)});
  assert.equal(supported.native.hidden,false);
  assert.equal(calls.length,0);
  await supported.handlers.native();
  assert.equal(calls.length,1);
  assert.equal(calls[0].title,"未来相場観測室");
  assert.equal(calls[0].url,canonical);
});

test("native cancellation stays quiet and real failure offers only the canonical link",async()=>{
  const cancelled=setup({share:async()=>{throw {name:"AbortError"};}});
  await cancelled.handlers.native();
  assert.deepEqual(cancelled.prompts,[]);
  const failed=setup({share:async()=>{throw {name:"NotAllowedError"};}});
  await failed.handlers.native();
  assert.deepEqual(failed.prompts,[["共有するURL",canonical]]);
});

test("existing copy action keeps the canonical URL and clipboard fallback",async()=>{
  const ui=setup();
  await ui.handlers.copy();
  assert.deepEqual(ui.copied,[canonical]);
  assert.equal(ui.label.textContent,"コピーしました ✓");
  const failed=setup({clipboard:{writeText:async()=>{throw new Error("unavailable");}}});
  await failed.handlers.copy();
  assert.deepEqual(failed.prompts,[["記事のURL",canonical]]);
});
