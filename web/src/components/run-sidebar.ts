/** Compact left nav for switching runs while the player is open. */

import type { RunSummary } from "../types";
import {
  fmtRelative,
  runInstant,
  shortRunId,
  statusTone,
} from "../lib/format";
import {
  loadSidebarFilter,
  loadSidebarOpenScenarios,
  loadSidebarViewMode,
  saveSidebarFilter,
  saveSidebarOpenScenarios,
  saveSidebarViewMode,
  type ListViewMode,
} from "../lib/ui-prefs";

function sortRunsNewest(runs: RunSummary[]): RunSummary[] {
  return [...runs].sort((a, b) => {
    const da = runInstant(a)?.getTime() ?? a.mtime_ms ?? 0;
    const db = runInstant(b)?.getTime() ?? b.mtime_ms ?? 0;
    if (db !== da) return db - da;
    return b.run_id.localeCompare(a.run_id);
  });
}

function groupByScenario(runs: RunSummary[]): Array<{
  scenarioId: string;
  runs: RunSummary[];
}> {
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

export type RunSidebarOpts = {
  runs: RunSummary[];
  activeRunId: string | null;
  onSelect: (runId: string) => void;
  onHome: () => void;
};

export function renderRunSidebar(
  mount: HTMLElement,
  opts: RunSidebarOpts,
): void {
  const sorted = sortRunsNewest(opts.runs);
  mount.innerHTML = `
    <aside class="run-sidebar" aria-label="Report runs">
      <div class="run-sidebar-head">
        <button type="button" class="run-sidebar-home" id="sb-home" title="All reports">
          lk-sim
        </button>
        <input
          type="search"
          class="run-sidebar-filter"
          id="sb-filter"
          placeholder="Filter…"
          autocomplete="off"
        />
      </div>
      <div class="run-sidebar-mode" id="sb-mode" role="tablist">
        <button type="button" class="run-sidebar-tab" data-mode="scenario" role="tab">By scenario</button>
        <button type="button" class="run-sidebar-tab" data-mode="recents" role="tab">Recents</button>
      </div>
      <nav class="run-sidebar-nav" id="sb-nav"></nav>
    </aside>
  `;

  const filter = mount.querySelector<HTMLInputElement>("#sb-filter");
  const nav = mount.querySelector<HTMLElement>("#sb-nav");
  const modeEl = mount.querySelector<HTMLElement>("#sb-mode");
  const homeBtn = mount.querySelector<HTMLButtonElement>("#sb-home");
  if (!filter || !nav || !modeEl || !homeBtn) return;

  let mode: ListViewMode = loadSidebarViewMode("scenario");
  filter.value = loadSidebarFilter();

  /** null = never customized → use default open heuristics */
  let openScenarios: Set<string> | null = loadSidebarOpenScenarios();

  homeBtn.addEventListener("click", () => opts.onHome());

  for (const tab of Array.from(
    modeEl.querySelectorAll<HTMLButtonElement>(".run-sidebar-tab"),
  )) {
    tab.addEventListener("click", () => {
      mode = (tab.dataset.mode as ListViewMode) || "scenario";
      saveSidebarViewMode(mode);
      paint();
    });
  }

  filter.addEventListener("input", () => {
    saveSidebarFilter(filter.value);
    paint();
  });

  function filtered(): RunSummary[] {
    const q = filter!.value.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((r) => {
      const hay = `${r.run_id} ${r.scenario_id ?? ""} ${r.status ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }

  function ensureOpenSet(groups: ReturnType<typeof groupByScenario>): Set<string> {
    if (openScenarios) return openScenarios;
    const next = new Set<string>();
    for (const g of groups) {
      const hasActive = g.runs.some((r) => r.run_id === opts.activeRunId);
      if (hasActive || g.runs.length <= 8) next.add(g.scenarioId);
    }
    openScenarios = next;
    return next;
  }

  function paint(): void {
    for (const tab of Array.from(
      modeEl!.querySelectorAll<HTMLButtonElement>(".run-sidebar-tab"),
    )) {
      tab.classList.toggle("is-active", tab.dataset.mode === mode);
    }
    nav!.innerHTML = "";
    const rows = filtered();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "run-sidebar-empty muted";
      empty.textContent = "No runs match.";
      nav!.appendChild(empty);
      return;
    }
    if (mode === "recents") {
      for (const r of rows) {
        nav!.appendChild(makeItem(r, true));
      }
      return;
    }
    const groups = groupByScenario(rows);
    const open = ensureOpenSet(groups);
    // Keep the active run's scenario expanded even if user collapsed others.
    for (const g of groups) {
      if (g.runs.some((r) => r.run_id === opts.activeRunId)) {
        open.add(g.scenarioId);
      }
    }
    for (const g of groups) {
      const details = document.createElement("details");
      details.className = "run-sidebar-group";
      details.open = open.has(g.scenarioId);
      details.addEventListener("toggle", () => {
        if (details.open) open.add(g.scenarioId);
        else open.delete(g.scenarioId);
        openScenarios = open;
        saveSidebarOpenScenarios(open);
      });
      const sum = document.createElement("summary");
      sum.className = "run-sidebar-scenario";
      sum.textContent = `${g.scenarioId} (${g.runs.length})`;
      details.appendChild(sum);
      const list = document.createElement("div");
      list.className = "run-sidebar-items";
      for (const r of g.runs) {
        list.appendChild(makeItem(r, false));
      }
      details.appendChild(list);
      nav!.appendChild(details);
    }
  }

  function makeItem(r: RunSummary, showScenario: boolean): HTMLButtonElement {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "run-sidebar-item";
    if (r.run_id === opts.activeRunId) btn.classList.add("is-active");
    const tone = statusTone(r.status);
    const when = runInstant(r);
    const title = showScenario
      ? (r.scenario_id && String(r.scenario_id).trim()) || shortRunId(r.run_id)
      : shortRunId(r.run_id);
    btn.innerHTML = `
      <span class="run-sidebar-item-top">
        <span class="status-dot tone-${tone}" title="${r.status ?? "unknown"}"></span>
        <span class="run-sidebar-item-title">${escapeHtml(title)}</span>
      </span>
      <span class="run-sidebar-item-meta muted">
        ${escapeHtml(fmtRelative(when))}
        ${r.turn_count != null ? ` · ${r.turn_count}t` : ""}
      </span>
    `;
    btn.title = r.run_id;
    btn.addEventListener("click", () => {
      if (r.run_id === opts.activeRunId) return;
      opts.onSelect(r.run_id);
    });
    return btn;
  }

  paint();
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
