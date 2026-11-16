/* Fictional photo posts: user-initiated, device-local Japanese speech only. */
(()=>{
  "use strict";
  const motionVideos=new Set();
  let playback=null;
  function pauseMotion(except=null){
    for(const video of motionVideos){
      if(video!==except)try{video.pause();}catch(_){}
    }
  }
  function registerMotion(video){
    motionVideos.add(video);
    video.addEventListener("play",()=>{stopSpeech();pauseMotion(video);});
    video.addEventListener("ended",()=>pauseMotion());
  }
  for(const shell of document.querySelectorAll("[data-story-motion]")){
    const video=shell.querySelector("video"),fallback=shell.querySelector("[data-story-motion-fallback]");
    if(!video || !fallback)continue;
    registerMotion(video);
    const fail=()=>{try{video.pause();}catch(_){}video.hidden=true;fallback.hidden=false;};
    video.addEventListener("error",fail);
    for(const source of video.querySelectorAll("source"))source.addEventListener("error",fail);
  }
  const roots=[...document.querySelectorAll("[data-photo-stories]")];
  if(!roots.length){window.addEventListener?.("pagehide",()=>pauseMotion());return;}
  const synth=window.speechSynthesis;
  const supported=!!synth && typeof synth.getVoices==="function" &&
    typeof synth.speak==="function" && typeof synth.cancel==="function" &&
    typeof window.SpeechSynthesisUtterance==="function";
  const slug=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const imagePattern=/^\/photo\/weekly\/([a-z0-9]+(?:-[a-z0-9]+)*)\/(0[1-9]|10)\.jpg\?v=[a-f0-9]{12}$/;
  const motionPattern=/^\/photo\/story-clips\/([a-z0-9]+(?:-[a-z0-9]+)*)\.mp4\?v=[a-f0-9]{12}$/;
  const plain=(value,max,multiline=false)=>typeof value==="string" && value.trim().length>0 &&
    value.length<=max && !(multiline ? /[<>\x00-\x09\x0b-\x1f\x7f]/ : /[<>\x00-\x1f\x7f]/).test(value);
  const valid=story=>{
    if(!story || !plain(story.id,100) || !slug.test(story.id) ||
       !plain(story.modelId,100) || !slug.test(story.modelId) || !plain(story.name,50) ||
       !plain(story.text,180,true) || !plain(story.alt,300) || !plain(story.dateLabel,40) ||
       !plain(story.settingLabel,40) || !plain(story.publishedAt,60) ||
       !Number.isFinite(Date.parse(story.publishedAt)) || Date.parse(story.publishedAt)>Date.now() ||
       !Number.isInteger(story.width) || !Number.isInteger(story.height) ||
       Math.min(story.width,story.height)<=0 || Math.max(story.width,story.height)>10000 ||
       !story.voice || !Number.isFinite(story.voice.rate) || !Number.isFinite(story.voice.pitch) ||
       story.voice.rate<0.85 || story.voice.rate>1.1 || story.voice.pitch<0.9 || story.voice.pitch>1.1)return false;
    const match=imagePattern.exec(story.image);
    if(!match || story.sourceUrl!=="/photo/weekly/"+match[1]+"/#photo-"+match[2])return false;
    if(story.motion===undefined)return true;
    const motion=story.motion,clip=motionPattern.exec(motion?.url);
    return !!clip && clip[1]===story.id && motion.width===640 && motion.height===360 &&
      motion.durationSeconds===12;
  };
  function stopSpeech(message="音声を停止しました。"){
    const previous=playback;
    playback=null;
    if(!previous)return;
    window.clearTimeout(previous.timer);
    previous.button.textContent="音声を聴く";
    previous.status.textContent=message;
    // Invalidate handlers before cancellation; canceled utterances can emit errors.
    try{synth.cancel();}catch(_){previous.status.textContent="音声を停止できませんでした。";}
  }
  function localVoice(){
    if(!supported)return {reason:"このブラウザは音声読み上げに対応していません。"};
    try{
      const voices=Array.from(synth.getVoices()).filter(voice=>
        voice.localService===true && /^ja(?:[-_]|$)/i.test(voice.lang));
      voices.sort((a,b)=>Number(!!b.default)-Number(!!a.default) ||
        String(a.name).localeCompare(String(b.name),"ja"));
      return voices.length ? {voice:voices[0]} : {reason:"端末内の日本語音声が見つかりません。文章でお楽しみください。"};
    }catch(_){return {reason:"端末の音声一覧を取得できませんでした。"};}
  }
  const controllers=[];
  for(const root of roots){
    const data=root.querySelector("[data-photo-story-data]");
    const dialog=root.querySelector("[data-story-dialog]");
    if(!data || !dialog || typeof dialog.showModal!=="function")continue;
    let stories;
    try{
      stories=JSON.parse(data.textContent);
      if(!Array.isArray(stories) || !stories.length || stories.length>200 ||
         !stories.every(valid) || new Set(stories.map(story=>story.id)).size!==stories.length)continue;
    }catch(_){continue;}
    const select=attribute=>dialog.querySelector("[data-story-"+attribute+"]");
    const name=select("name"),image=select("image"),text=select("text"),date=select("date");
    const video=select("video"),motionLabel=select("motion-label");
    const setting=select("setting"),source=select("source"),button=select("audio");
    const next=select("next"),previous=select("previous"),close=select("close"),status=select("status");
    if(![name,image,text,date,setting,source,button,next,previous,close,status,video,motionLabel].every(Boolean))continue;
    let selected=[],position=0,opener=null,activeMotion=null;
    registerMotion(video);
    function clearMotion(){
      activeMotion=null;
      try{video.pause();}catch(_){}
      video.hidden=true;motionLabel.hidden=true;
      video.removeAttribute("src");video.removeAttribute("poster");
      video.load();
    }
    video.addEventListener("error",()=>{
      if(!activeMotion)return;
      clearMotion();image.hidden=false;
      status.textContent="動画を読み込めませんでした。写真を表示しています。";
    });
    function availability(){
      if(playback?.button===button)return;
      const choice=localVoice();
      button.disabled=!choice.voice;
      button.textContent="音声を聴く";
      status.textContent=choice.voice
        ? "端末の日本語音声で読み上げます。声は端末により異なります。" : choice.reason;
    }
    function showStory(){
      stopSpeech();pauseMotion();clearMotion();
      const story=selected[position];
      name.textContent=story.name;
      image.src=story.image;image.alt=story.alt;
      image.width=story.width;image.height=story.height;image.hidden=false;
      if(story.motion){
        activeMotion=story.motion;
        video.poster=story.image;video.src=story.motion.url;
        video.width=story.motion.width;video.height=story.motion.height;
        video.muted=true;video.hidden=false;image.hidden=true;motionLabel.hidden=false;
        video.setAttribute("aria-label",story.name+"の写真モーション");
        // Metadata/poster only. Playback always requires the reader's controls.
        video.load();
      }
      text.textContent=story.text;
      date.dateTime=story.publishedAt;date.textContent=story.dateLabel;
      setting.textContent=story.settingLabel;source.href=story.sourceUrl;
      next.hidden=position>=selected.length-1;
      previous.hidden=position===0;
      availability();
    }
    for(const link of root.querySelectorAll("[data-story-open]")){
      link.addEventListener("click",event=>{
        const group=stories.filter(story=>story.modelId===link.dataset.storyOpen);
        if(!group.length)return;
        event.preventDefault();
        for(const controller of controllers){
          if(controller.dialog!==dialog && controller.dialog.open)controller.dialog.close();
        }
        selected=group;position=0;opener=link;showStory();
        if(!dialog.open)dialog.showModal();
      });
    }
    button.addEventListener("click",()=>{
      if(!dialog.open || !selected.length)return;
      if(playback?.button===button){stopSpeech();return;}
      const choice=localVoice();
      if(!choice.voice){button.disabled=true;status.textContent=choice.reason;return;}
      stopSpeech();pauseMotion();
      const story=selected[position];
      try{
        const utterance=new window.SpeechSynthesisUtterance(story.text);
        utterance.voice=choice.voice;utterance.lang=choice.voice.lang;
        utterance.rate=story.voice.rate;utterance.pitch=story.voice.pitch;utterance.volume=1;
        const active={utterance,button,status,started:false,timer:null};
        playback=active;button.textContent="音声を止める";status.textContent="音声を準備しています…";
        utterance.onstart=()=>{
          if(playback!==active)return;
          window.clearTimeout(active.timer);active.started=true;status.textContent="音声を再生中です。";
        };
        utterance.onend=()=>{
          if(playback!==active)return;
          window.clearTimeout(active.timer);playback=null;button.textContent="音声を聴く";
          status.textContent=active.started ? "音声を再生しました。" : "音声を再生できませんでした。文章でお楽しみください。";
        };
        utterance.onerror=()=>{
          if(playback!==active)return;
          stopSpeech("音声を再生できませんでした。文章でお楽しみください。");
        };
        // Do not leave a blocked or silent queue looking like successful playback.
        active.timer=window.setTimeout(()=>{
          if(playback===active)stopSpeech("音声を開始できませんでした。もう一度お試しください。");
        },10000);
        synth.cancel();
        synth.speak(utterance);
      }catch(_){
        stopSpeech("音声を再生できませんでした。文章でお楽しみください。");
        button.textContent="音声を聴く";
        status.textContent="音声を再生できませんでした。文章でお楽しみください。";
      }
    });
    next.addEventListener("click",()=>{
      if(position<selected.length-1){
        position+=1;showStory();
        if(next.hidden)(button.disabled ? close : button).focus();
      }
    });
    previous.addEventListener("click",()=>{
      if(position>0){
        position-=1;showStory();
        if(previous.hidden)(button.disabled ? next : button).focus();
      }
    });
    close.addEventListener("click",()=>dialog.close());
    dialog.addEventListener("cancel",()=>{stopSpeech();pauseMotion();clearMotion();});
    dialog.addEventListener("close",()=>{stopSpeech();pauseMotion();clearMotion();opener?.focus();});
    controllers.push({dialog,availability});
  }
  if(supported && typeof synth.addEventListener==="function")
    synth.addEventListener("voiceschanged",()=>{
      for(const controller of controllers)if(controller.dialog.open)controller.availability();
    });
  window.addEventListener("pagehide",()=>{stopSpeech();pauseMotion();});
})();
