'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','assets','engagement.js'),'utf8');
function imageFixture(complete,naturalWidth){
  const attrs={src:'/generated/story.jpg',alt:'架空のAI生成画像','data-fallback':'/images/story.svg','data-fallback-alt':'創作記事のイラスト','data-generated-scene':'true'};
  const listeners={};
  const caption={textContent:'AI生成'};
  const removed=[];
  const image={
    complete,naturalWidth,
    getAttribute:n=>attrs[n],
    removeAttribute:n=>{delete attrs[n];},
    setAttribute:(n,v)=>{attrs[n]=v;},
    addEventListener:(n,fn)=>{listeners[n]=fn;},
    closest:n=>n==='figure'?{querySelector:()=>caption}:{classList:{remove:c=>removed.push(c)}},
    set src(v){attrs.src=v;},
  };
  vm.runInNewContext(source.split('// An animated')[0]+'})();',{
    document:{querySelectorAll:()=>[image]}
  });
  return {image,attrs,listeners,caption,removed};
}
test('already-failed cached photo switches immediately to SVG',()=>{
  const ui=imageFixture(true,0);
  assert.equal(ui.attrs.src,'/images/story.svg');
  assert.equal(ui.attrs['data-fallback'],undefined);
  assert.equal(ui.attrs['data-generated-scene'],undefined);
  assert.equal(ui.attrs['data-fallback-alt'],undefined);
  assert.equal(ui.attrs.alt,'創作記事のイラスト');
  assert.equal(ui.caption.textContent,'イメージ画像');
  assert.deepEqual(ui.removed,['story-image-link--generated']);
});
test('later image failure switches once without recursive errors',()=>{
  const ui=imageFixture(false,0);
  assert.equal(ui.attrs.src,'/generated/story.jpg');
  ui.listeners.error();
  ui.listeners.error();
  assert.equal(ui.attrs.src,'/images/story.svg');
  assert.equal(ui.removed.length,1);
});
test('loaded photograph keeps its source, caption and disclosure',()=>{
  const ui=imageFixture(true,1200);
  assert.equal(ui.attrs.src,'/generated/story.jpg');
  assert.equal(ui.attrs.alt,'架空のAI生成画像');
  assert.equal(ui.caption.textContent,'AI生成');
  assert.equal(ui.attrs['data-generated-scene'],'true');
});
