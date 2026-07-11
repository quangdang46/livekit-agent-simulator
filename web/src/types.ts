export type RunSummary = {
  run_id: string;
  status?: string;
  duration_ms?: number;
  turn_count?: number;
  has_audio?: boolean;
};

export type Cue = {
  role: "agent" | "user" | string;
  start_ms: number;
  end_ms: number;
  text: string;
  turn?: number;
  source?: string;
};

export type CuesPayload = {
  run_id: string;
  scenario_id?: string;
  audio: {
    file: string | null;
    duration_ms?: number | null;
    t0_mono_ms?: number;
    channels?: { left?: string; right?: string };
  };
  cues: Cue[];
};
