import type { CuesPayload, RunSummary } from "./types";

export async function fetchRuns(): Promise<RunSummary[]> {
  const res = await fetch("/api/runs");
  if (!res.ok) throw new Error(`Failed to load runs (${res.status})`);
  return res.json() as Promise<RunSummary[]>;
}

export async function fetchCues(runId: string): Promise<CuesPayload> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/cues`);
  if (!res.ok) throw new Error(`Failed to load cues (${res.status})`);
  return res.json() as Promise<CuesPayload>;
}
