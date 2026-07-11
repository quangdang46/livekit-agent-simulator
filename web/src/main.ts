import "./style.css";
import { fetchCues, fetchRuns } from "./api";
import type {
  AssertVerify,
  BehaviorSummary,
  Cue,
  CuesPayload,
  Marker,
  MarkerType,
  RunSummary,
  ScriptVerify,
} from "./types";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app missing");

const MARKER_LABELS: Record<string, string> = {
  barge_in: "Barge-in",
  script_cue: "Script cue",
  silence_wait: "User pause (script)",
  silence: "Silence detected",
  interruption: "Interruption",
  recovery: "Agent recovery",
};

const LEGEND_ORDER: MarkerType[] = [
  "barge_in",
  "silence_wait",
  "silence",
  "interruption",
  "recovery",
  "script_cue",
];

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

function markerTitle(type: string): string {
  return MARKER_LABELS[type] || type.replace(/_/g, " ");
}

function renderRunList(root: HTMLElement, runs: RunSummary[]): void {
  root.innerHTML = `
    <main class="page">
      <header class="header">
        <h1>lk-sim reports</h1>
        <p class="muted">Pick a run to play audio with time-synced transcript + behavior markers.</p>
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

type PlayerUI = {
  audio: HTMLAudioElement;
  cuesEl: HTMLOListElement;
  subtitle: HTMLElement;
  missing: HTMLElement;
  timeline: HTMLElement;
  playhead: HTMLElement;
  legend: HTMLElement;
  verify: HTMLElement;
  followBtn: HTMLButtonElement;
};

/** Role labels shown in the chat timeline (sim caller = user channel). */
function roleLabel(role: string): string {
  const r = role.toLowerCase();
  if (r === "agent") return "Agent";
  if (r === "user") return "Caller";
  return role;
}

function roleClass(role: string): string {
  const r = role.toLowerCase();
  if (r === "agent") return "role-agent";
  if (r === "user") return "role-user";
  return "role-other";
}

function renderPlayerShell(root: HTMLElement, runId: string): PlayerUI {
  root.innerHTML = `
    <main class="page player-page">
      <header class="header">
        <button type="button" class="back" id="back">← runs</button>
        <h1 id="title"></h1>
        <p id="subtitle" class="muted"></p>
        <div id="verify" class="verify-bar"></div>
      </header>
      <div class="player-dock" id="dock">
        <section class="audio-panel">
          <audio id="audio" controls preload="metadata"></audio>
          <p id="audio-missing" class="warn hidden">
            No <code>conversation.wav</code> for this run. Timeline still lists with timestamps.
          </p>
          <div id="timeline" class="timeline" title="Click to seek">
            <div id="playhead" class="timeline-playhead" style="left:0"></div>
          </div>
          <div class="dock-row">
            <div class="role-key" aria-label="Speaker legend">
              <span class="role-key-item"><span class="role-dot agent"></span> Agent (right channel)</span>
              <span class="role-key-item"><span class="role-dot user"></span> Caller / sim (left channel)</span>
            </div>
            <button type="button" class="follow-btn on" id="follow" title="When on, transcript keeps the current line in view. Scroll freely turns this off.">
              Follow live
            </button>
          </div>
          <div id="legend" class="legend"></div>
          <p class="hint muted">Stereo WAV · click a bubble or band to seek · scroll anytime (follow pauses until you re-enable)</p>
        </section>
      </div>
      <section class="transcript-panel">
        <div class="section-head">
          <h2 class="section-title">Conversation</h2>
          <span class="section-hint">Agent left · Caller right · Events centered</span>
        </div>
        <ol id="cues" class="cues"></ol>
      </section>
    </main>
  `;
  root.querySelector("#back")?.addEventListener("click", () => {
    playerListeners?.abort();
    playerListeners = null;
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
    timeline: root.querySelector("#timeline") as HTMLElement,
    playhead: root.querySelector("#playhead") as HTMLElement,
    legend: root.querySelector("#legend") as HTMLElement,
    verify: root.querySelector("#verify") as HTMLElement,
    followBtn: root.querySelector("#follow") as HTMLButtonElement,
  };
}

function fmtRecoveryMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function mountVerify(
  el: HTMLElement,
  script: ScriptVerify | null | undefined,
  assertV: AssertVerify | null | undefined,
  counts: Record<string, number> | undefined,
  behavior: BehaviorSummary | null | undefined,
): void {
  el.innerHTML = "";
  const chips: Array<{ text: string; cls: string }> = [];
  if (script && typeof script.pass === "boolean") {
    chips.push({
      text: `script ${script.pass ? "pass" : "fail"}`,
      cls: script.pass ? "chip pass" : "chip fail",
    });
    if (script.agent_finals_after_barge_in != null) {
      chips.push({
        text: `recovery finals: ${script.agent_finals_after_barge_in}`,
        cls: "chip",
      });
    }
    if (script.agent_finals_after_silence != null) {
      chips.push({
        text: `after silence: ${script.agent_finals_after_silence}`,
        cls: "chip",
      });
    }
  }
  let assertRecoveryShown = false;
  if (assertV && typeof assertV.pass === "boolean" && !assertV.skipped) {
    chips.push({
      text: `assert ${assertV.pass ? "pass" : "fail"}`,
      cls: assertV.pass ? "chip pass" : "chip fail",
    });
    for (const chk of assertV.checks || []) {
      if (chk.type === "recovery") {
        assertRecoveryShown = true;
        const ok = chk.pass !== false;
        const parts = ["recovery"];
        if (chk.recovery_ms != null) parts.push(fmtRecoveryMs(Number(chk.recovery_ms)));
        if (chk.agent_finals_after_barge_in != null) {
          parts.push(`${chk.agent_finals_after_barge_in} finals`);
        }
        chips.push({
          text: parts.join(" · "),
          cls: ok ? "chip pass" : "chip fail",
        });
      }
    }
  }
  if (behavior) {
    if (behavior.barges_fired) {
      chips.push({
        text: `barges ×${behavior.barges_fired}${
          behavior.barges_during_agent
            ? ` (${behavior.barges_during_agent} mid-agent)`
            : ""
        }`,
        cls: "chip",
      });
    }
    if (behavior.silences_held) {
      chips.push({
        text: `silence holds ×${behavior.silences_held}`,
        cls: "chip",
      });
    }
    if (!assertRecoveryShown) {
      if (behavior.recovery_ms != null && behavior.recovery_ms >= 0) {
        const passCls =
          behavior.recovery_assert_pass === true
            ? "chip pass"
            : behavior.recovery_assert_pass === false
              ? "chip fail"
              : "chip";
        chips.push({
          text: `recovery ${fmtRecoveryMs(behavior.recovery_ms)}`,
          cls: passCls,
        });
      } else if (
        behavior.barges_fired &&
        (behavior.agent_finals_after_barge ?? 0) === 0
      ) {
        chips.push({ text: "recovery: none", cls: "chip fail" });
      }
    }
  }
  if (counts) {
    for (const t of LEGEND_ORDER) {
      const n = counts[t];
      if (n) chips.push({ text: `${markerTitle(t)} ×${n}`, cls: "chip" });
    }
  }
  for (const c of chips) {
    const span = document.createElement("span");
    span.className = c.cls;
    span.textContent = c.text;
    el.appendChild(span);
  }
}

function mountLegend(el: HTMLElement, markers: Marker[]): void {
  el.innerHTML = "";
  const present = new Set(markers.map((m) => m.type));
  const types = LEGEND_ORDER.filter((t) => present.has(t));
  // Also show any unknown types
  for (const m of markers) {
    if (!types.includes(m.type)) types.push(m.type);
  }
  if (!types.length) {
    el.innerHTML = `<span class="muted">No barge-in / silence / interruption markers in this run.</span>`;
    return;
  }
  for (const t of types) {
    const item = document.createElement("span");
    item.className = "legend-item";
    item.innerHTML = `<span class="swatch ${t}"></span>`;
    const label = document.createElement("span");
    label.textContent = markerTitle(t);
    item.appendChild(label);
    el.appendChild(item);
  }
}

function mountTimeline(
  timeline: HTMLElement,
  playhead: HTMLElement,
  markers: Marker[],
  durationMs: number,
  audio: HTMLAudioElement,
): void {
  // Keep playhead; replace bands
  for (const node of Array.from(timeline.querySelectorAll(".timeline-band"))) {
    node.remove();
  }
  const dur = Math.max(durationMs, 1);
  for (const m of markers) {
    const band = document.createElement("button");
    band.type = "button";
    band.className = `timeline-band ${m.type}`;
    const left = (m.start_ms / dur) * 100;
    const width = Math.max(0.4, ((m.end_ms - m.start_ms) / dur) * 100);
    band.style.left = `${left}%`;
    band.style.width = `${width}%`;
    band.title = `${markerTitle(m.type)}: ${m.label}\n${fmtMs(m.start_ms)} – ${fmtMs(m.end_ms)}${m.detail ? "\n" + m.detail : ""}`;
    band.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!audio.src) return;
      audio.currentTime = (m.start_ms || 0) / 1000;
      void audio.play().catch(() => undefined);
    });
    timeline.appendChild(band);
  }
  // Ensure playhead stays on top
  timeline.appendChild(playhead);

  timeline.onclick = (ev) => {
    if (!audio.src || !durationMs) return;
    const rect = timeline.getBoundingClientRect();
    const x = (ev as MouseEvent).clientX - rect.left;
    const ratio = Math.min(1, Math.max(0, x / rect.width));
    audio.currentTime = (ratio * durationMs) / 1000;
    void audio.play().catch(() => undefined);
  };
}

type TimelineItem =
  | { kind: "cue"; start_ms: number; end_ms: number; cue: Cue }
  | { kind: "marker"; start_ms: number; end_ms: number; marker: Marker };

function buildTimelineItems(cues: Cue[], markers: Marker[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const c of cues) {
    items.push({ kind: "cue", start_ms: c.start_ms, end_ms: c.end_ms, cue: c });
  }
  for (const m of markers) {
    items.push({
      kind: "marker",
      start_ms: m.start_ms,
      end_ms: m.end_ms,
      marker: m,
    });
  }
  items.sort((a, b) => {
    if (a.start_ms !== b.start_ms) return a.start_ms - b.start_ms;
    // Markers slightly before transcript at same time so you see the event first
    if (a.kind !== b.kind) return a.kind === "marker" ? -1 : 1;
    return 0;
  });
  return items;
}

function mountTimelineList(
  ol: HTMLOListElement,
  items: TimelineItem[],
  audio: HTMLAudioElement,
  onUserSeek: () => void,
): HTMLElement[] {
  ol.innerHTML = "";
  const els: HTMLElement[] = [];
  for (const item of items) {
    const li = document.createElement("li");
    li.dataset.start = String(item.start_ms);
    li.dataset.end = String(item.end_ms);

    if (item.kind === "marker") {
      const m = item.marker;
      li.className = `cue marker ${m.type}`;
      li.innerHTML = `
        <div class="cue-meta">
          <span class="role marker-type ${m.type}"></span>
          <span class="time"></span>
          <span class="tag ${m.type}"></span>
        </div>
        <div class="cue-text"></div>
        <div class="cue-detail"></div>
      `;
      const role = li.querySelector(".role");
      const time = li.querySelector(".time");
      const tag = li.querySelector(".tag");
      const text = li.querySelector(".cue-text");
      const detail = li.querySelector(".cue-detail");
      if (role) role.textContent = markerTitle(m.type);
      if (time) time.textContent = `${fmtMs(m.start_ms)} – ${fmtMs(m.end_ms)}`;
      if (tag) tag.textContent = m.step_id || m.type;
      if (text) text.textContent = m.label + (m.say ? ` · “${m.say}”` : "");
      if (detail) {
        detail.textContent = m.detail || "";
        if (!m.detail) detail.classList.add("hidden");
      }
    } else {
      const c = item.cue;
      const r = (c.role || "other").toLowerCase();
      li.className = `cue ${roleClass(r)}`;
      li.dataset.role = r;
      li.innerHTML = `
        <div class="cue-meta">
          <span class="role ${r}"></span>
          <span class="time"></span>
          <span class="tags"></span>
        </div>
        <div class="cue-text"></div>
      `;
      const role = li.querySelector(".role");
      const time = li.querySelector(".time");
      const text = li.querySelector(".cue-text");
      const tags = li.querySelector(".tags");
      if (role) role.textContent = roleLabel(c.role);
      if (time) time.textContent = `${fmtMs(c.start_ms)} – ${fmtMs(c.end_ms)}`;
      if (text) text.textContent = c.text;
      if (tags && c.marker_tags?.length) {
        for (const t of c.marker_tags) {
          const span = document.createElement("span");
          span.className = `tag ${t}`;
          span.textContent = markerTitle(t);
          tags.appendChild(span);
        }
      }
    }

    li.addEventListener("click", () => {
      if (!audio.src) return;
      audio.currentTime = (item.start_ms || 0) / 1000;
      onUserSeek();
      void audio.play().catch(() => undefined);
    });
    ol.appendChild(li);
    els.push(li);
  }
  return els;
}

type FollowState = {
  enabled: boolean;
  /** Ignore scroll events we cause ourselves via scrollIntoView. */
  suppressScrollUntil: number;
  lastActive: number;
};

function setFollowUi(btn: HTMLButtonElement, on: boolean): void {
  btn.classList.toggle("on", on);
  btn.classList.toggle("off", !on);
  btn.textContent = on ? "Follow live" : "Follow paused";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
}

function syncActive(
  els: HTMLElement[],
  audio: HTMLAudioElement,
  playhead: HTMLElement,
  durationMs: number,
  follow: FollowState,
): void {
  const t = (audio.currentTime || 0) * 1000;
  let active = -1;
  for (let i = 0; i < els.length; i++) {
    const start = Number(els[i].dataset.start);
    const end = Number(els[i].dataset.end);
    if (t >= start && t < end) active = i;
    else if (t >= start) active = i;
  }
  els.forEach((el, i) => {
    el.classList.toggle("active", i === active);
  });

  // Only auto-scroll when follow is on, and only when the active line changes
  // (avoids fighting the user mid-line / during smooth scroll).
  if (follow.enabled && active >= 0 && active !== follow.lastActive) {
    const el = els[active];
    const dock = document.querySelector(".player-dock") as HTMLElement | null;
    const dockBottom = dock ? dock.getBoundingClientRect().bottom + 12 : 100;
    const rect = el.getBoundingClientRect();
    const inView =
      rect.top >= dockBottom && rect.bottom <= window.innerHeight - 24;
    if (!inView) {
      follow.suppressScrollUntil = performance.now() + 450;
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  follow.lastActive = active;

  if (durationMs > 0) {
    const pct = Math.min(100, Math.max(0, (t / durationMs) * 100));
    playhead.style.left = `${pct}%`;
  }
}

// Abort previous player listeners when navigating away / re-opening a run.
let playerListeners: AbortController | null = null;

async function showPlayer(runId: string): Promise<void> {
  playerListeners?.abort();
  playerListeners = new AbortController();
  const { signal } = playerListeners;

  const ui = renderPlayerShell(app!, runId);
  const follow: FollowState = {
    enabled: true,
    suppressScrollUntil: 0,
    lastActive: -1,
  };
  setFollowUi(ui.followBtn, true);
  ui.followBtn.addEventListener(
    "click",
    () => {
      follow.enabled = !follow.enabled;
      setFollowUi(ui.followBtn, follow.enabled);
      if (follow.enabled) {
        // Jump once to current line when re-enabling.
        follow.lastActive = -2;
      }
    },
    { signal },
  );

  // User scroll / wheel / touch → pause follow so they can read earlier turns.
  const pauseFollowFromUser = () => {
    if (performance.now() < follow.suppressScrollUntil) return;
    if (!follow.enabled) return;
    follow.enabled = false;
    setFollowUi(ui.followBtn, false);
  };
  window.addEventListener("wheel", pauseFollowFromUser, { passive: true, signal });
  window.addEventListener("touchmove", pauseFollowFromUser, {
    passive: true,
    signal,
  });
  window.addEventListener(
    "keydown",
    (ev) => {
      if (
        ev.key === "PageUp" ||
        ev.key === "PageDown" ||
        ev.key === "Home" ||
        ev.key === "End" ||
        ((ev.key === "ArrowUp" || ev.key === "ArrowDown") &&
          !(ev.target instanceof HTMLInputElement) &&
          !(ev.target instanceof HTMLTextAreaElement) &&
          !(ev.target instanceof HTMLSelectElement))
      ) {
        pauseFollowFromUser();
      }
    },
    { signal },
  );

  try {
    const data: CuesPayload = await fetchCues(runId);
    const markers = data.markers || [];
    const durationMs =
      data.audio?.duration_ms != null
        ? Number(data.audio.duration_ms)
        : Math.max(
            0,
            ...markers.map((m) => m.end_ms),
            ...(data.cues || []).map((c) => c.end_ms),
          ) || 1;

    if (data.scenario_id) {
      ui.subtitle.textContent = `scenario: ${data.scenario_id}`;
    }
    if (data.audio?.file) {
      ui.audio.src = `/runs/${encodeURIComponent(runId)}/${data.audio.file}`;
    } else {
      ui.missing.classList.remove("hidden");
    }

    const behavior =
      data.behavior_summary ||
      data.caller?.behavior_summary ||
      null;
    mountVerify(
      ui.verify,
      data.script_verify,
      data.assert_verify,
      data.marker_counts,
      behavior,
    );
    mountLegend(ui.legend, markers);
    mountTimeline(ui.timeline, ui.playhead, markers, durationMs, ui.audio);

    const onUserSeek = () => {
      // Seeking from bubble keeps follow so you land on that line.
      follow.enabled = true;
      setFollowUi(ui.followBtn, true);
      follow.lastActive = -2;
    };

    const items = buildTimelineItems(data.cues || [], markers);
    const els = mountTimelineList(ui.cuesEl, items, ui.audio, onUserSeek);
    if (!els.length) {
      ui.subtitle.textContent =
        (ui.subtitle.textContent || "") + " · no transcript/markers found";
    }

    const tick = () =>
      syncActive(els, ui.audio, ui.playhead, durationMs, follow);
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
