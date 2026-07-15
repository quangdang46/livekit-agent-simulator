import type { MarkerType } from "../types";

export const MARKER_LABELS: Record<string, string> = {
  barge_in: "Barge-in",
  script_cue: "Script cue",
  silence_wait: "User pause (script)",
  silence: "Silence detected",
  interruption: "Interruption",
  recovery: "Agent recovery",
  backchannel: "Backchannel",
  false_interrupt: "False interrupt",
  dtmf: "DTMF",
  tool: "Tool call",
  tool_error: "Tool error",
};

export const LEGEND_ORDER: MarkerType[] = [
  "barge_in",
  "backchannel",
  "false_interrupt",
  "dtmf",
  "silence_wait",
  "silence",
  "interruption",
  "recovery",
  "script_cue",
  "tool",
  "tool_error",
];

export function markerTitle(type: string): string {
  return MARKER_LABELS[type] || type.replace(/_/g, " ");
}
