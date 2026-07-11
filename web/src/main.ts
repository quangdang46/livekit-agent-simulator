import "./style.css";
import { fetchCues, fetchRuns } from "./api";
import type { Cue, CuesPayload, RunSummary } from "./types";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app missing");

function runFromUrl(): string | null {
  return new URLSearchParams(location.search).get("run");
}

function setRunInUrl(runId: string | null): void {
  const url = new URL(location.href);
  if (runId) url.searchParams.set("run", runId);
  else url.searchParams.delete("run");
  history.pushState({}, "", url);
}

function fmtMs(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  const m = Math.floor(s / 60);
  const r = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${r}`;
}

function renderRunList(root: HTMLElement, runs: RunSummary[]): void {
  root.innerHTML = `
    <main class="page">
      <header class="header">
        <h1>lk-sim reports</h1>
        <p class="muted">Pick a run to play audio with time-synced transcript.</p>
      </header>
      <ul class="run-list" id="runs"></ul>
      <p class="muted ${runs.length ? "hidden" : ""}" id="empty">
        No reports found under <code>.agent-sim/reports/</code>.
      </p>
    </main>
  `;
  const ul = root.querySelector<HTMLUListElement>("#runs");
  if (!ul) return;
  for (const r of runs) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link";
    btn.textContent = r.run_id;
    btn.addEventListener("click", () => {
      setRunInUrl(r.run_id);
      void showPlayer(r.run_id);
    });
    const meta = document.createElement("span");
    meta.className = "muted";
    meta.textContent =
      " — " +
      [
        r.status || "?",
        r.turn_count != null ? `${r.turn_count} turns` : null,
        r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)}s` : null,
        r.has_audio ? "audio" : "no audio",
      ]
        .filter(Boolean)
        .join(" · ");
    li.append(btn, meta);
    ul.appendChild(li);
  }
}

function renderPlayerShell(root: HTMLElement, runId: string): {
  audio: HTMLAudioElement;
  cuesEl: HTMLOListElement;
  subtitle: HTMLElement;
  missing: HTMLElement;
} {
  root.innerHTML = `
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
  `;
  root.querySelector("#back")?.addEventListener("click", () => {
    setRunInUrl(null);
    void showList();
  });
  const title = root.querySelector("#title");
  if (title) title.textContent = runId;
  return {
    audio: root.querySelector("#audio") as HTMLAudioElement,
    cuesEl: root.querySelector("#cues") as HTMLOListElement,
    subtitle: root.querySelector("#subtitle") as HTMLElement,
    missing: root.querySelector("#audio-missing") as HTMLElement,
  };
}

function mountCues(
  ol: HTMLOListElement,
  cues: Cue[],
  audio: HTMLAudioElement,
): HTMLElement[] {
  ol.innerHTML = "";
  const els: HTMLElement[] = [];
  for (const c of cues) {
    const li = document.createElement("li");
    li.className = "cue";
    li.dataset.start = String(c.start_ms);
    li.dataset.end = String(c.end_ms);
    li.innerHTML = `
      <div class="cue-meta">
        <span class="role ${c.role}"></span>
        <span class="time"></span>
      </div>
      <div class="cue-text"></div>
    `;
    const role = li.querySelector(".role");
    const time = li.querySelector(".time");
    const text = li.querySelector(".cue-text");
    if (role) role.textContent = c.role;
    if (time) time.textContent = `${fmtMs(c.start_ms)} – ${fmtMs(c.end_ms)}`;
    if (text) text.textContent = c.text;
    li.addEventListener("click", () => {
      if (!audio.src) return;
      audio.currentTime = (c.start_ms || 0) / 1000;
      void audio.play().catch(() => undefined);
    });
    ol.appendChild(li);
    els.push(li);
  }
  return els;
}

function syncActive(els: HTMLElement[], audio: HTMLAudioElement): void {
  const t = (audio.currentTime || 0) * 1000;
  let active = -1;
  for (let i = 0; i < els.length; i++) {
    const start = Number(els[i].dataset.start);
    const end = Number(els[i].dataset.end);
    if (t >= start && t < end) active = i;
    else if (t >= start) active = i;
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

async function showPlayer(runId: string): Promise<void> {
  const ui = renderPlayerShell(app!, runId);
  try {
    const data: CuesPayload = await fetchCues(runId);
    if (data.scenario_id) {
      ui.subtitle.textContent = `scenario: ${data.scenario_id}`;
    }
    if (data.audio?.file) {
      ui.audio.src = `/runs/${encodeURIComponent(runId)}/${data.audio.file}`;
    } else {
      ui.missing.classList.remove("hidden");
    }
    const els = mountCues(ui.cuesEl, data.cues || [], ui.audio);
    if (!els.length) {
      ui.subtitle.textContent =
        (ui.subtitle.textContent || "") + " · no transcript finals found";
    }
    const tick = () => syncActive(els, ui.audio);
    ui.audio.addEventListener("timeupdate", tick);
    ui.audio.addEventListener("seeked", tick);
    ui.audio.addEventListener("play", () => {
      const loop = () => {
        if (ui.audio.paused) return;
        tick();
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });
  } catch (e) {
    ui.subtitle.className = "error";
    ui.subtitle.textContent = String(e);
  }
}

async function showList(): Promise<void> {
  try {
    const runs = await fetchRuns();
    renderRunList(app!, runs);
  } catch (e) {
    app!.innerHTML = `<main class="page"><p class="error">${String(e)}</p></main>`;
  }
}

async function boot(): Promise<void> {
  const run = runFromUrl();
  if (run) await showPlayer(run);
  else await showList();
}

window.addEventListener("popstate", () => {
  void boot();
});

void boot();
