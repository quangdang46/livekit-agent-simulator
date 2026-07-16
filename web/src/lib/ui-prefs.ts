/** Persist home / sidebar list UI prefs across tab switches and remounts. */

export type ListViewMode = "recents" | "scenario";

const HOME_MODE = "lk-sim.home.viewMode";
const HOME_FILTER = "lk-sim.home.filter";
const SIDEBAR_MODE = "lk-sim.sidebar.viewMode";
const SIDEBAR_FILTER = "lk-sim.sidebar.filter";
const SIDEBAR_OPEN = "lk-sim.sidebar.openScenarios";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode / quota */
  }
}

function parseMode(raw: string | null, fallback: ListViewMode): ListViewMode {
  if (raw === "recents" || raw === "scenario") return raw;
  return fallback;
}

export function loadHomeViewMode(fallback: ListViewMode = "recents"): ListViewMode {
  return parseMode(read(HOME_MODE), fallback);
}

export function saveHomeViewMode(mode: ListViewMode): void {
  write(HOME_MODE, mode);
}

export function loadHomeFilter(): string {
  return read(HOME_FILTER) ?? "";
}

export function saveHomeFilter(q: string): void {
  write(HOME_FILTER, q);
}

export function loadSidebarViewMode(
  fallback: ListViewMode = "scenario",
): ListViewMode {
  return parseMode(read(SIDEBAR_MODE), fallback);
}

export function saveSidebarViewMode(mode: ListViewMode): void {
  write(SIDEBAR_MODE, mode);
}

export function loadSidebarFilter(): string {
  return read(SIDEBAR_FILTER) ?? "";
}

export function saveSidebarFilter(q: string): void {
  write(SIDEBAR_FILTER, q);
}

export function loadSidebarOpenScenarios(): Set<string> | null {
  const raw = read(SIDEBAR_OPEN);
  if (raw == null) return null;
  try {
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return new Set();
    return new Set(arr.map((x) => String(x)));
  } catch {
    return new Set();
  }
}

export function saveSidebarOpenScenarios(ids: Iterable<string>): void {
  write(SIDEBAR_OPEN, JSON.stringify([...ids]));
}
