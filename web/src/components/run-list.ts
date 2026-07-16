import type { RunSummary } from "../types";
import {
  fmtDuration,
  fmtRelative,
  runInstant,
  shortRunId,
  statusTone,
} from "../lib/format";
import {
  loadHomeFilter,
  loadHomeViewMode,
  saveHomeFilter,
  saveHomeViewMode,
  type ListViewMode,
} from "../lib/ui-prefs";

type ViewMode = ListViewMode;

type ScenarioGroup = {
  scenarioId: string;
  runs: RunSummary[];
};

function sortRunsNewest(runs: RunSummary[]): RunSummary[] {
  return [...runs].sort((a, b) => {
    const da = runInstant(a)?.getTime() ?? a.mtime_ms ?? 0;
    const db = runInstant(b)?.getTime() ?? b.mtime_ms ?? 0;
    if (db !== da) return db - da;
    return b.run_id.localeCompare(a.run_id);
  });
}

function groupByScenario(runs: RunSummary[]): ScenarioGroup[] {
  const sorted = sortRunsNewest(runs);
  const order: string[] = [];
  const map = new Map<string, RunSummary[]>();
  for (const r of sorted) {
    const key = (r.scenario_id && String(r.scenario_id).trim()) || "unknown";
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(r);
  }
  // Scenario sections ordered by each group's newest run.
  order.sort((a, b) => {
    const ra = map.get(a)![0];
    const rb = map.get(b)![0];
    const da = runInstant(ra)?.getTime() ?? ra.mtime_ms ?? 0;
    const db = runInstant(rb)?.getTime() ?? rb.mtime_ms ?? 0;
    return db - da;
  });
  return order.map((scenarioId) => ({
    scenarioId,
    runs: map.get(scenarioId)!,
  }));
}

function runMetaBits(r: RunSummary): string[] {
  return [
    fmtDuration(r.duration_ms) ?? null,
    r.turn_count != null ? `${r.turn_count} turn${r.turn_count === 1 ? "" : "s"}` : null,
    r.tool_count != null && r.tool_count > 0 ? `${r.tool_count} tools` : null,
    r.has_audio ? "audio" : "no audio",
  ].filter(Boolean) as string[];
}

function statusLabel(status?: string | null): string {
  if (!status) return "unknown";
  return status;
}

function makeStatusPill(status?: string | null): HTMLSpanElement {
  const pill = document.createElement("span");
  pill.className = `status-pill tone-${statusTone(status)}`;
  pill.textContent = statusLabel(status);
  return pill;
}

function makeRunCard(
  r: RunSummary,
  opts: {
    featured?: boolean;
    isLatest?: boolean;
    showScenario?: boolean;
    onSelect: (runId: string) => void;
  },
): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = opts.featured ? "run-card run-card-featured" : "run-card";
  if (opts.isLatest) btn.classList.add("is-latest");

  const when = runInstant(r);
  const rel = fmtRelative(when);
  const scenario =
    (r.scenario_id && String(r.scenario_id).trim()) || "unknown scenario";

  const top = document.createElement("div");
  top.className = "run-card-top";
  top.appendChild(makeStatusPill(r.status));
  if (opts.isLatest) {
    const latest = document.createElement("span");
    latest.className = "latest-badge";
    latest.textContent = "Latest";
    top.appendChild(latest);
  }
  const time = document.createElement("span");
  time.className = "run-card-time";
  time.textContent = rel;
  if (when) time.title = when.toLocaleString();
  top.appendChild(time);

  const title = document.createElement("div");
  title.className = "run-card-title";
  title.textContent = opts.showScenario ? scenario : shortRunId(r.run_id);

  const meta = document.createElement("div");
  meta.className = "run-card-meta muted";
  const bits = runMetaBits(r);
  // Non-featured: scenario may already be title — still prepend when list mixes scenarios.
  if (opts.showScenario && !opts.featured) {
    bits.unshift(scenario);
  }
  meta.textContent = bits.join(" · ");

  const cta = document.createElement("span");
  cta.className = "run-card-cta";
  cta.textContent = opts.featured ? "Open player →" : "Open →";

  btn.title = r.run_id;
  if (opts.featured) {
    // Featured already has scenario as title — skip long run_id row.
    btn.append(top, title, meta, cta);
  } else {
    const idLine = document.createElement("div");
    idLine.className = "run-card-id muted";
    idLine.textContent = r.run_id;
    btn.append(top, title, idLine, meta, cta);
  }
  btn.addEventListener("click", () => opts.onSelect(r.run_id));
  return btn;
}

function renderRecents(
  container: HTMLElement,
  runs: RunSummary[],
  latestId: string | null,
  onSelect: (runId: string) => void,
): void {
  const list = document.createElement("div");
  list.className = "run-grid";
  // Featured card already shows the newest run — skip duplicating it here.
  const rows = sortRunsNewest(runs).filter((r) => r.run_id !== latestId);
  for (const r of rows) {
    list.appendChild(
      makeRunCard(r, {
        isLatest: false,
        showScenario: true,
        onSelect,
      }),
    );
  }
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No older runs.";
    container.appendChild(empty);
    return;
  }
  container.appendChild(list);
}

function renderByScenario(
  container: HTMLElement,
  runs: RunSummary[],
  latestId: string | null,
  onSelect: (runId: string) => void,
): void {
  for (const g of groupByScenario(runs)) {
    const section = document.createElement("section");
    section.className = "scenario-group";

    const heading = document.createElement("div");
    heading.className = "scenario-head";
    const title = document.createElement("h2");
    title.className = "scenario-title";
    title.textContent = g.scenarioId;
    const count = document.createElement("span");
    count.className = "muted scenario-count";
    count.textContent = `${g.runs.length} run${g.runs.length === 1 ? "" : "s"}`;
    const fresh = document.createElement("span");
    fresh.className = "scenario-fresh muted";
    const newest = g.runs[0];
    fresh.textContent = newest
      ? `updated ${fmtRelative(runInstant(newest))}`
      : "";
    heading.append(title, count, fresh);

    const grid = document.createElement("div");
    grid.className = "run-grid";
    for (const r of g.runs) {
      grid.appendChild(
        makeRunCard(r, {
          isLatest: r.run_id === latestId,
          showScenario: false,
          onSelect,
        }),
      );
    }
    section.append(heading, grid);
    container.appendChild(section);
  }
}

export function renderRunList(
  root: HTMLElement,
  runs: RunSummary[],
  onSelect: (runId: string) => void,
): void {
  const sorted = sortRunsNewest(runs);
  const latest = sorted[0] ?? null;
  let mode: ViewMode = loadHomeViewMode("recents");

  root.innerHTML = `
    <main class="page home-page">
      <header class="header home-header">
        <div>
          <p class="eyebrow">lk-sim</p>
          <h1>Reports</h1>
          <p class="muted home-sub" id="home-sub"></p>
        </div>
        <div class="home-toolbar" id="home-toolbar"></div>
      </header>
      <section id="latest-slot" class="latest-slot"></section>
      <section id="list-slot" class="list-slot"></section>
      <p class="muted ${runs.length ? "hidden" : ""}" id="empty">
        No reports found under <code>.agent-sim/reports/</code>.
      </p>
    </main>
  `;

  const sub = root.querySelector<HTMLElement>("#home-sub");
  const toolbar = root.querySelector<HTMLElement>("#home-toolbar");
  const latestSlot = root.querySelector<HTMLElement>("#latest-slot");
  const listSlotEl = root.querySelector<HTMLElement>("#list-slot");
  if (!sub || !toolbar || !latestSlot || !listSlotEl) return;
  const listSlot: HTMLElement = listSlotEl;

  if (latest) {
    sub.textContent = `${runs.length} run${runs.length === 1 ? "" : "s"} · newest first`;
  } else {
    sub.textContent = "Run a scenario, then refresh this page.";
  }

  if (latest) {
    latestSlot.appendChild(
      makeRunCard(latest, {
        featured: true,
        isLatest: true,
        showScenario: true,
        onSelect,
      }),
    );
  }

  const tabs = document.createElement("div");
  tabs.className = "view-tabs";
  const mkTab = (id: ViewMode, label: string) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "view-tab";
    b.dataset.mode = id;
    b.textContent = label;
    b.addEventListener("click", () => {
      mode = id;
      saveHomeViewMode(mode);
      paint();
    });
    return b;
  };
  tabs.append(mkTab("recents", "Recents"), mkTab("scenario", "By scenario"));
  toolbar.appendChild(tabs);

  const filter = document.createElement("input");
  filter.type = "search";
  filter.className = "run-filter";
  filter.placeholder = "Filter scenario or run id…";
  filter.autocomplete = "off";
  filter.value = loadHomeFilter();
  filter.addEventListener("input", () => {
    saveHomeFilter(filter.value);
    paint();
  });
  toolbar.appendChild(filter);

  function filtered(): RunSummary[] {
    const q = filter.value.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((r) => {
      const hay = `${r.run_id} ${r.scenario_id ?? ""} ${r.status ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }

  function paint(): void {
    for (const b of Array.from(tabs.querySelectorAll<HTMLButtonElement>(".view-tab"))) {
      b.classList.toggle("is-active", b.dataset.mode === mode);
    }
    listSlot.innerHTML = "";
    const rows = filtered();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No runs match that filter.";
      listSlot.appendChild(empty);
      return;
    }
    const latestId = latest?.run_id ?? null;
    if (mode === "recents") renderRecents(listSlot, rows, latestId, onSelect);
    else renderByScenario(listSlot, rows, latestId, onSelect);
  }

  paint();
}
