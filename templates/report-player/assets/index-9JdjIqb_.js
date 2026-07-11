(function(){let e=document.createElement(`link`).relList;if(e&&e.supports&&e.supports(`modulepreload`))return;for(let e of document.querySelectorAll(`link[rel="modulepreload"]`))n(e);new MutationObserver(e=>{for(let t of e)if(t.type===`childList`)for(let e of t.addedNodes)e.tagName===`LINK`&&e.rel===`modulepreload`&&n(e)}).observe(document,{childList:!0,subtree:!0});function t(e){let t={};return e.integrity&&(t.integrity=e.integrity),e.referrerPolicy&&(t.referrerPolicy=e.referrerPolicy),e.crossOrigin===`use-credentials`?t.credentials=`include`:e.crossOrigin===`anonymous`?t.credentials=`omit`:t.credentials=`same-origin`,t}function n(e){if(e.ep)return;e.ep=!0;let n=t(e);fetch(e.href,n)}})();async function e(){let e=await fetch(`/api/runs`);if(!e.ok)throw Error(`Failed to load runs (${e.status})`);return e.json()}async function t(e){let t=await fetch(`/api/runs/${encodeURIComponent(e)}/cues`);if(!t.ok)throw Error(`Failed to load cues (${t.status})`);return t.json()}var n=document.querySelector(`#app`);if(!n)throw Error(`#app missing`);function r(){return new URLSearchParams(location.search).get(`run`)}function i(e){let t=new URL(location.href);e?t.searchParams.set(`run`,e):t.searchParams.delete(`run`),history.pushState({},``,t)}function a(e){let t=Math.max(0,e)/1e3;return`${Math.floor(t/60)}:${(t%60).toFixed(1).padStart(4,`0`)}`}function o(e,t){e.innerHTML=`
    <main class="page">
      <header class="header">
        <h1>lk-sim reports</h1>
        <p class="muted">Pick a run to play audio with time-synced transcript.</p>
      </header>
      <ul class="run-list" id="runs"></ul>
      <p class="muted ${t.length?`hidden`:``}" id="empty">
        No reports found under <code>.agent-sim/reports/</code>.
      </p>
    </main>
  `;let n=e.querySelector(`#runs`);if(n)for(let e of t){let t=document.createElement(`li`),r=document.createElement(`button`);r.type=`button`,r.className=`link`,r.textContent=e.run_id,r.addEventListener(`click`,()=>{i(e.run_id),u(e.run_id)});let a=document.createElement(`span`);a.className=`muted`,a.textContent=` — `+[e.status||`?`,e.turn_count==null?null:`${e.turn_count} turns`,e.duration_ms==null?null:`${(e.duration_ms/1e3).toFixed(1)}s`,e.has_audio?`audio`:`no audio`].filter(Boolean).join(` · `),t.append(r,a),n.appendChild(t)}}function s(e,t){e.innerHTML=`
    <main class="page player-page">
      <header class="header">
        <button type="button" class="back" id="back">← runs</button>
        <h1 id="title"></h1>
        <p id="subtitle" class="muted"></p>
      </header>
      <section class="audio-panel">
        <audio id="audio" controls preload="metadata"></audio>
        <p id="audio-missing" class="warn hidden">
          No <code>conversation.wav</code> for this run. Transcript still lists with timestamps.
        </p>
        <p class="hint muted">L = sim caller · R = agent (stereo). Click a line to seek.</p>
      </section>
      <section class="transcript-panel">
        <h2>Transcript</h2>
        <ol id="cues" class="cues"></ol>
      </section>
    </main>
  `,e.querySelector(`#back`)?.addEventListener(`click`,()=>{i(null),d()});let n=e.querySelector(`#title`);return n&&(n.textContent=t),{audio:e.querySelector(`#audio`),cuesEl:e.querySelector(`#cues`),subtitle:e.querySelector(`#subtitle`),missing:e.querySelector(`#audio-missing`)}}function c(e,t,n){e.innerHTML=``;let r=[];for(let i of t){let t=document.createElement(`li`);t.className=`cue`,t.dataset.start=String(i.start_ms),t.dataset.end=String(i.end_ms),t.innerHTML=`
      <div class="cue-meta">
        <span class="role ${i.role}"></span>
        <span class="time"></span>
      </div>
      <div class="cue-text"></div>
    `;let o=t.querySelector(`.role`),s=t.querySelector(`.time`),c=t.querySelector(`.cue-text`);o&&(o.textContent=i.role),s&&(s.textContent=`${a(i.start_ms)} – ${a(i.end_ms)}`),c&&(c.textContent=i.text),t.addEventListener(`click`,()=>{n.src&&(n.currentTime=(i.start_ms||0)/1e3,n.play().catch(()=>void 0))}),e.appendChild(t),r.push(t)}return r}function l(e,t){let n=(t.currentTime||0)*1e3,r=-1;for(let t=0;t<e.length;t++){let i=Number(e[t].dataset.start),a=Number(e[t].dataset.end);(n>=i&&n<a||n>=i)&&(r=t)}e.forEach((e,t)=>{let n=t===r;if(e.classList.toggle(`active`,n),n){let t=e.getBoundingClientRect();t.top>=80&&t.bottom<=window.innerHeight-40||e.scrollIntoView({block:`nearest`,behavior:`smooth`})}})}async function u(e){let r=s(n,e);try{let n=await t(e);n.scenario_id&&(r.subtitle.textContent=`scenario: ${n.scenario_id}`),n.audio?.file?r.audio.src=`/runs/${encodeURIComponent(e)}/${n.audio.file}`:r.missing.classList.remove(`hidden`);let i=c(r.cuesEl,n.cues||[],r.audio);i.length||(r.subtitle.textContent=(r.subtitle.textContent||``)+` · no transcript finals found`);let a=()=>l(i,r.audio);r.audio.addEventListener(`timeupdate`,a),r.audio.addEventListener(`seeked`,a),r.audio.addEventListener(`play`,()=>{let e=()=>{r.audio.paused||(a(),requestAnimationFrame(e))};requestAnimationFrame(e)})}catch(e){r.subtitle.className=`error`,r.subtitle.textContent=String(e)}}async function d(){try{o(n,await e())}catch(e){n.innerHTML=`<main class="page"><p class="error">${String(e)}</p></main>`}}async function f(){let e=r();e?await u(e):await d()}window.addEventListener(`popstate`,()=>{f()}),f();
//# sourceMappingURL=index-9JdjIqb_.js.map