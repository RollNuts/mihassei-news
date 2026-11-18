'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../assets/photo-stories.js'),'utf8');
const japanese={name:'Device Japanese',lang:'ja-JP',localService:true,default:true};
const fixture=(id='saya-one')=>({id,modelId:'nagino-saya',name:'凪野さや',
  text:'私の取材メモに、寄り道をひとつ足しました。じゃ、行ってきます。',
  publishedAt:'2020-11-15T00:40:00Z',dateLabel:'2020年11月15日',settingLabel:'舞台：2031年11月',
  image:'/photo/weekly/autumn-saya-001/01.jpg?v=7d6077a0f2ef',
  sourceUrl:'/photo/weekly/autumn-saya-001/#photo-01',alt:'秋の小道の凪野さや。',
  width:800,height:1200,voice:{rate:0.98,pitch:1.02}});

test('photo-motion errors expose the actual still-photo link without autoplay',()=>{
  const media=element(),fallback=element(),sourceElement=element();
  fallback.hidden=true;media.querySelectorAll=()=>[sourceElement];
  const shell={querySelector:selector=>selector==='video'?media:fallback};
  vm.runInNewContext(source,{document:{querySelectorAll:selector=>
    selector==='[data-story-motion]'?[shell]:[]},window:{},Date,Intl});
  assert.equal(fallback.hidden,true);
  sourceElement.listeners.error();
  assert.equal(media.hidden,true);
  assert.equal(fallback.hidden,false);
});

function element(){
  return {listeners:{},dataset:{},textContent:'',disabled:false,hidden:false,focused:0,
    paused:true,pauseCalls:0,loadCalls:0,playCalls:0,attributes:{},
    pause(){this.paused=true;this.pauseCalls++;},load(){this.loadCalls++;},
    play(){this.playCalls++;this.paused=false;},querySelectorAll(){return [];},
    removeAttribute(name){delete this[name];delete this.attributes[name];},
    setAttribute(name,value){this.attributes[name]=value;},
    addEventListener(name,fn){this.listeners[name]=fn;},focus(){this.focused++;}};
}
function page(options={}){
  const fields=Object.fromEntries(['name','image','video','motion-label','text','date','setting','source','audio','next','previous','close','status']
    .map(name=>[name,element()]));
  fields.audio.disabled=true;
  fields.video.hidden=true;fields['motion-label'].hidden=true;fields.previous.hidden=true;
  const dialog=element();dialog.open=false;
  dialog.querySelector=selector=>fields[selector.match(/^\[data-story-(.+)\]$/)?.[1]]||null;
  dialog.showModal=()=>{dialog.open=true;};
  dialog.close=()=>{dialog.open=false;dialog.listeners.close?.();};
  const data={textContent:JSON.stringify(options.stories||[fixture()])};
  const link=element();link.dataset.storyOpen=options.modelId||'nagino-saya';
  const root={querySelector:selector=>({'[data-photo-story-data]':data,'[data-story-dialog]':dialog})[selector],
    querySelectorAll:selector=>selector==='[data-story-open]'?[link]:[]};
  const spoken=[],timers=new Map(),windowEvents={};let nextTimer=0;
  const synth={voices:options.voices??[japanese],cancellations:0,listeners:{},
    getVoices(){if(options.listError)throw new Error('unavailable');return this.voices;},
    speak(utterance){if(options.speakError)throw new Error('blocked');spoken.push(utterance);},
    cancel(){this.cancellations++;},addEventListener(event,fn){this.listeners[event]=fn;}};
  const window={speechSynthesis:options.unsupported?undefined:synth,
    SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},
    setTimeout(fn){timers.set(++nextTimer,fn);return nextTimer;},clearTimeout(id){timers.delete(id);},
    addEventListener(event,fn){windowEvents[event]=fn;}};
  const timeline=element(),fallback=element();
  fallback.hidden=true;
  const timelineShell={querySelector:selector=>selector==='video'?timeline:fallback};
  vm.runInNewContext(source,{document:{querySelectorAll:selector=>
    selector==='[data-story-motion]'?(options.timeline?[timelineShell]:[]):[root]},window,Date,Intl});
  const open=()=>{let prevented=false;link.listeners.click?.({preventDefault(){prevented=true;}});return prevented;};
  return {fields,dialog,link,synth,spoken,timers,windowEvents,open,timeline};
}

const motionFixture=(id='saya-motion')=>({...fixture(id),motion:{
  url:'/photo/story-clips/'+id+'.mp4?v=a3408b617dab',width:640,height:360,durationSeconds:12}});

test('a story with a validated existing motion shows a muted controlled video, never autoplay',()=>{
  const story=motionFixture(),p=page({stories:[story]});
  assert.equal(p.fields.video.src,undefined,'closed dialog fetches no clip');
  p.open();
  assert.equal(p.fields.video.hidden,false);
  assert.equal(p.fields.image.hidden,true);
  assert.equal(p.fields.video.src,story.motion.url);
  assert.equal(p.fields.video.poster,story.image);
  assert.equal(p.fields.video.muted,true);
  assert.equal(p.fields['motion-label'].hidden,false);
  assert.equal(p.fields.video.playCalls,0);
  assert.equal(p.spoken.length,0);
});

test('motion URL, original story identity and dimensions cannot be substituted',()=>{
  for(const change of [{url:'https://elsewhere.example/clip.mp4'},
    {url:'/photo/story-clips/another-story.mp4?v=a3408b617dab'},
    {url:'/photo/story-clips/saya-motion.mp4?v=not-a-hash'},
    {width:1920},{height:1080},{durationSeconds:999}]){
    const story=motionFixture();story.motion={...story.motion,...change};
    const p=page({stories:[story]});
    assert.equal(p.open(),false);
    assert.equal(p.fields.video.src,undefined);
  }
});

test('next, previous, close, Escape and page exit stop the active motion',()=>{
  for(const action of ['next','previous','close','escape','pagehide']){
    const p=page({stories:[motionFixture(),motionFixture('saya-motion-two')]});p.open();
    if(action==='previous')p.fields.next.listeners.click();
    const before=p.fields.video.pauseCalls;
    p.fields.video.paused=false;
    if(action==='next')p.fields.next.listeners.click();
    if(action==='previous')p.fields.previous.listeners.click();
    if(action==='close')p.fields.close.listeners.click();
    if(action==='escape')p.dialog.listeners.cancel();
    if(action==='pagehide')p.windowEvents.pagehide();
    assert.equal(p.fields.video.paused,true,action);
    assert.ok(p.fields.video.pauseCalls>before,action);
    assert.equal(p.fields.video.playCalls,0,action+' never auto-starts a replacement');
    if(action==='close'||action==='escape')assert.equal(p.fields.video.src,undefined);
  }
});

test('motion errors reveal the same reviewed image and image-only posts stay unchanged',()=>{
  const p=page({stories:[motionFixture(),fixture('saya-still')]});p.open();
  p.fields.video.listeners.error();
  assert.equal(p.fields.video.hidden,true);
  assert.equal(p.fields.image.hidden,false);
  assert.equal(p.fields.image.src,motionFixture().image);
  assert.equal(p.fields['motion-label'].hidden,true);
  assert.match(p.fields.status.textContent,/写真を表示/);
  p.fields.next.listeners.click();
  assert.equal(p.fields.image.hidden,false);
  assert.equal(p.fields.video.hidden,true);
  assert.equal(p.fields.video.src,undefined);
  assert.equal(p.fields.text.textContent,fixture('saya-still').text);
});

test('dialog and timeline motion never overlap, and completion leaves other clips paused',()=>{
  const p=page({stories:[motionFixture()],timeline:true});p.open();
  p.timeline.paused=false;
  p.fields.video.listeners.play();
  assert.equal(p.timeline.paused,true);
  p.fields.video.paused=false;
  p.timeline.listeners.play();
  assert.equal(p.fields.video.paused,true);
  p.fields.video.paused=false;
  p.timeline.listeners.ended();
  assert.equal(p.fields.video.paused,true);
});

test('opening a photo story and voice-list changes never autoplay',()=>{
  const p=page();
  assert.equal(p.spoken.length,0);
  assert.equal(p.open(),true);
  assert.equal(p.dialog.open,true);
  assert.equal(p.fields.image.src,fixture().image);
  assert.equal(p.fields.source.href,fixture().sourceUrl);
  assert.equal(p.fields.text.textContent,fixture().text);
  assert.equal(p.fields.audio.disabled,false);
  p.synth.listeners.voiceschanged();
  assert.equal(p.spoken.length,0);
  assert.match(p.fields.status.textContent,/端末により異なります/);
});

test('the complete current catalog starts and each model dialog keeps original multiline posts',()=>{
  const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../content/photo_issues.json'),'utf8'));
  const raw=JSON.parse(fs.readFileSync(path.join(__dirname,'../content/photo_stories.json'),'utf8')).stories;
  for(const model of catalog.models) {
    const file=path.join(__dirname,'../content/photo_stories',model.id+'.json');
    raw.push(...JSON.parse(fs.readFileSync(file,'utf8')).stories);
  }
  const posts=raw.map(post=>{
    const model=catalog.models.find(model=>model.id===post.model_id);
    const issue=catalog.issues.find(issue=>issue.slug===post.source.issue_slug);
    const image=issue.images[post.source.cut_id],shot=issue.shots.find(shot=>shot.id===post.source.cut_id);
    const base='/photo/weekly/'+issue.slug+'/';
    return {...fixture(post.id),modelId:model.id,name:model.name,text:post.text,publishedAt:post.published_at,
      image:base+post.source.cut_id+'.jpg?v='+post.source.image_sha256.slice(0,12),
      sourceUrl:base+'#photo-'+post.source.cut_id,alt:shot.alt,width:image.width,height:image.height};
  }).sort((a,b)=>b.publishedAt.localeCompare(a.publishedAt));
  assert.equal(new Set(posts.map(post=>post.modelId)).size,7);
  assert.ok(posts.some(post=>post.text.includes('\n')),'fixture must include actual paragraph breaks');
  for(const modelId of new Set(posts.map(post=>post.modelId))){
    const p=page({stories:posts,modelId});
    assert.equal(p.open(),true,modelId+' opens after whole-gallery validation');
    assert.equal(p.dialog.open,true);
    assert.equal(p.fields.text.textContent,posts.find(post=>post.modelId===modelId).text);
    assert.equal(p.spoken.length,0,'opening never starts speech');
  }
});

test('LF is allowed only in post text; identity, links and other controls stay rejected',()=>{
  for(const key of ['id','modelId','name','alt','dateLabel','settingLabel','publishedAt','image','sourceUrl']){
    const post=fixture();post[key]+='\n';const p=page({stories:[post]});
    assert.equal(p.open(),false,key+' must reject LF');
    assert.equal(p.dialog.open,false);
  }
  for(const control of ['\r','\t','\x01','\x7f']){
    const post=fixture();post.text='寄り道'+control+'しました。';const p=page({stories:[post]});
    assert.equal(p.open(),false,'text must reject control '+JSON.stringify(control));
  }
});

test('audio action uses only an explicit local Japanese voice and reports actual playback events',()=>{
  const remote={name:'Remote Japanese',lang:'ja-JP',localService:false,default:true};
  const english={name:'Local English',lang:'en-US',localService:true,default:true};
  const p=page({voices:[remote,english,japanese]});p.open();
  p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,1);
  const utterance=p.spoken[0];
  assert.equal(utterance.voice,japanese);
  assert.equal(utterance.lang,'ja-JP');
  assert.equal(utterance.rate,0.98);assert.equal(utterance.pitch,1.02);
  assert.equal(utterance.text,fixture().text);
  assert.match(p.fields.status.textContent,/準備/);
  assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
  utterance.onstart();assert.match(p.fields.status.textContent,/再生中/);
  utterance.onend();assert.match(p.fields.status.textContent,/再生しました/);
  assert.equal(p.fields.audio.textContent,'音声を聴く');
  assert.equal(p.timers.size,0);
});

test('unsupported, empty local Japanese voice list, remote-only and list failure remain distinct',()=>{
  for(const options of [{unsupported:true},{voices:[]},
    {voices:[{name:'Remote',lang:'ja-JP',localService:false}]},
    {voices:[{name:'Unknown service',lang:'ja-JP',localService:'true'}]},
    {voices:[{name:'English',lang:'en-US',localService:true}]},{listError:true}]){
    const p=page(options);p.open();
    assert.equal(p.fields.audio.disabled,true);
    const expected=options.unsupported?/対応していません/:options.listError?/取得できません/:/見つかりません/;
    assert.match(p.fields.status.textContent,expected);
    assert.equal(p.spoken.length,0);
    assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
  }
  const later=page({voices:[]});later.open();
  later.synth.voices=[japanese];later.synth.listeners.voiceschanged();
  assert.equal(later.fields.audio.disabled,false);
  assert.equal(later.spoken.length,0);
});

test('stop, close, Escape, next story and page exit cancel audio with no overlap or automatic next playback',()=>{
  for(const action of ['stop','close','escape','next','pagehide']){
    const p=page({stories:[fixture(),fixture('saya-two')]});p.open();
    p.fields.audio.listeners.click();const utterance=p.spoken[0];utterance.onstart();
    const before=p.synth.cancellations;
    if(action==='stop')p.fields.audio.listeners.click();
    if(action==='close')p.fields.close.listeners.click();
    if(action==='escape'){p.dialog.listeners.cancel();p.dialog.close();}
    if(action==='next')p.fields.next.listeners.click();
    if(action==='pagehide')p.windowEvents.pagehide();
    assert.equal(p.synth.cancellations,before+1,action);
    assert.equal(p.spoken.length,1,action);
    const status=p.fields.status.textContent;
    utterance.onerror({error:'canceled'});utterance.onend();
    assert.equal(p.fields.status.textContent,status,'canceled events cannot report a successful play');
    assert.equal(p.timers.size,0);
    if(action==='close' || action==='escape')assert.equal(p.link.focused,1);
  }
  const p=page();p.open();p.fields.audio.listeners.click();
  p.fields.audio.listeners.click();p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,2);
  assert.equal(p.synth.cancellations,3);
});

test('engine errors, rejected starts and silent starts never appear as a successful play',()=>{
  for(const mode of ['error','throw','timeout','end-before-start']){
    const p=page({speakError:mode==='throw'});p.open();p.fields.audio.listeners.click();
    if(mode==='error')p.spoken[0].onerror({error:'synthesis-failed'});
    if(mode==='timeout')[...p.timers.values()][0]();
    if(mode==='end-before-start')p.spoken[0].onend();
    assert.match(p.fields.status.textContent,/できません/);
    assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
    assert.equal(p.fields.audio.textContent,'音声を聴く');
  }
});

test('voice loss after opening refuses playback instead of choosing a remote fallback',()=>{
  const p=page();p.open();p.synth.voices=[];p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,0);
  assert.equal(p.fields.audio.disabled,true);
  assert.match(p.fields.status.textContent,/見つかりません/);
});

test('unsafe or unpublished payloads leave the original photo link intact',()=>{
  for(const mutate of [story=>{story.image='https://remote.test/photo.jpg';},
    story=>{story.sourceUrl='javascript:alert(1)';},story=>{story.text='<img src=x onerror=alert(1)>';},
    story=>{story.voice.rate=2;},story=>{story.publishedAt='2999-01-01T00:00:00Z';},
    story=>{story.width='800';}]){
    const story=fixture();mutate(story);const p=page({stories:[story]});
    assert.equal(p.open(),false);
    assert.equal(p.dialog.open,false);
    assert.equal(p.spoken.length,0);
  }
});
