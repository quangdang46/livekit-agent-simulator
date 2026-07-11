export type RunSummary = {
  run_id: string;
  status?: string;
  duration_ms?: number;
  turn_count?: number;
  has_audio?: boolean;
};

export type MarkerType =
  | "barge_in"
  | "script_cue"
  | "silence_wait"
  | "silence"
  | "interruption"
  | "recovery"
  | string;

/** Where user-channel speech came from (script barge vs natural persona). */
export type SpeechOrigin = "natural" | "script_barge" | "script_cue" | string;

export type Cue = {
  role: "agent" | "user" | string;
  start_ms: number;
  end_ms: number;
  text: string;
  turn?: number;
  source?: string;
  marker_tags?: MarkerType[];
  /** Audio-aligned end of utterance (transcript final). */
  final_ms?: number;
  /**
   * user role only: script_barge = Script barge_in inject heard on mic;
   * natural = Gemini persona / real caller intent.
   */
  speech_origin?: SpeechOrigin;
  script_step_id?: string;
  script_say?: string;
  script_label?: string;
};

export type Marker = {
  type: MarkerType;
  start_ms: number;
  end_ms: number;
  label: string;
  detail?: string;
  step_id?: string;
  say?: string;
  during_agent_speech?: boolean;
  barge_in?: boolean;
  duration_ms?: number;
  after_barge_ms?: number;
};

export type ScriptVerify = {
  pass?: boolean;
  script_steps?: number;
  cues_fired?: number;
  waits_fired?: number;
  agent_finals_after_barge_in?: number;
  agent_finals_after_silence?: number;
  interruptions?: number;
  checks?: Array<{
    step_id?: string;
    pass?: boolean;
    trigger?: string;
    action?: string;
    during_agent_speech?: boolean;
    check?: string;
    expected?: number;
    actual?: number;
  }>;
};

export type AssertVerify = {
  pass?: boolean;
  skipped?: boolean;
  checks?: Array<{
    check?: string;
    pass?: boolean;
    role?: string;
    type?: string;
    reason?: string | null;
    recovery_ms?: number | null;
    agent_finals_after_barge_in?: number;
    expected_min?: number;
  }>;
};

/** Aggregated barge / silence / recovery stats from the run. */
export type BehaviorSummary = {
  script_cues_fired?: number;
  waits_fired?: number;
  barges_fired?: number;
  barges_during_agent?: number;
  cues_during_agent?: number;
  silences_held?: number;
  silence_events?: number;
  interruptions?: number;
  agent_finals_after_barge?: number;
  agent_finals_after_silence?: number;
  recovery_ms?: number | null;
  recovery_assert_pass?: boolean;
  cue_assets?: string[];
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
  markers?: Marker[];
  marker_counts?: Record<string, number>;
  script_verify?: ScriptVerify | null;
  assert_verify?: AssertVerify | null;
  caller?: { behavior_summary?: BehaviorSummary | null } | null;
  behavior_summary?: BehaviorSummary | null;
};
