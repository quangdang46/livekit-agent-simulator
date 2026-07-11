/* lk-sim report player — sync transcript cues to audio.currentTime */

function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

function fmtMs(ms) {
  const s = Math.max(0, ms) / 1000;
  const m = Math.floor(s / 60);
  const r = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${r}`;
}

async function loadCues(runId) {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/cues`);
  if (!res.ok) throw new Error(`Failed to load cues (${res.status})`);
  return res.json();
}

function renderCues(cues, audio) {
  const ol = document.getElementById("cues");
  ol.innerHTML = "";
  const els = [];

  for (const c of cues) {
    const li = document.createElement("li");
    li.className = "cue";
    li.dataset.start = String(c.start_ms);
    li.dataset.end = String(c.end_ms);

    const meta = document.createElement("div");
    meta.className = "cue-meta";
    const role = document.createElement("span");
    role.className = `role ${c.role}`;
    role.textContent = c.role;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = `${fmtMs(c.start_ms)} – ${fmtMs(c.end_ms)}`;
    meta.appendChild(role);
    meta.appendChild(time);

    const text = document.createElement("div");
    text.className = "cue-text";
    text.textContent = c.text;

    li.appendChild(meta);
    li.appendChild(text);
    li.addEventListener("click", () => {
      if (!audio.src) return;
      audio.currentTime = (c.start_ms || 0) / 1000;
      audio.play().catch(() => {});
    });
    ol.appendChild(li);
    els.push(li);
  }
  return els;
}

function syncActive(els, audio) {
  const t = (audio.currentTime || 0) * 1000;
  let active = -1;
  for (let i = 0; i < els.length; i++) {
    const start = Number(els[i].dataset.start);
    const end = Number(els[i].dataset.end);
    if (t >= start && t < end) active = i;
    else if (t >= start) active = i; // fallback last started
  }
  els.forEach((el, i) => {
    const on = i === active;
    el.classList.toggle("active", on);
    if (on) {
      const rect = el.getBoundingClientRect();
      const view = rect.top >= 80 && rect.bottom <= window.innerHeight - 40;
      if (!view) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });
}

async function main() {
  const runId = qs("run");
  const title = document.getElementById("title");
  const subtitle = document.getElementById("subtitle");
  const audio = document.getElementById("audio");
  const missing = document.getElementById("audio-missing");

  if (!runId) {
    title.textContent = "No run selected";
    subtitle.textContent = "Open from the runs list.";
    return;
  }

  title.textContent = runId;
  const data = await loadCues(runId);
  if (data.scenario_id) {
    subtitle.textContent = `scenario: ${data.scenario_id}`;
  }

  const hasAudio = Boolean(data.audio && data.audio.file);
  if (hasAudio) {
    audio.src = `/runs/${encodeURIComponent(runId)}/${data.audio.file}`;
  } else {
    missing.classList.remove("hidden");
  }

  const els = renderCues(data.cues || [], audio);
  if (!els.length) {
    subtitle.textContent = (subtitle.textContent || "") + " · no transcript finals found";
  }

  const tick = () => syncActive(els, audio);
  audio.addEventListener("timeupdate", tick);
  audio.addEventListener("seeked", tick);
  audio.addEventListener("play", () => {
    const loop = () => {
      if (audio.paused) return;
      tick();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

main().catch((e) => {
  document.getElementById("title").textContent = "Error";
  document.getElementById("subtitle").textContent = String(e);
});
