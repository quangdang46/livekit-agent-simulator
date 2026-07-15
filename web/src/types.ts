export type RunSummary = {
  run_id: string;
  scenario_id?: string | null;
  status?: string;
  duration_ms?: number;
  turn_count?: number;
  tool_count?: number;
  has_audio?: boolean;
  started_utc?: string | null;
  mtime_ms?: number;
};

export type MarkerType =
  | "barge_in"
  | "script_cue"
  | "silence_wait"
  | "silence"
  | "interruption"
  | "recovery"
  | "backchannel"
  | "false_interrupt"
  | "dtmf"
  | "tool"
  | "tool_error"
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
  final_ms?: number;
  speech_origin?: SpeechOrigin;
  script_step_id?: string;
  script_say?: string;
  script_label?: string;
  inject_ms?: number;
  synthetic?: boolean;
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
  class?: string | null;
  duration_ms?: number;
  after_barge_ms?: number;
  tool_name?: string;
  is_error?: boolean;
  call_id?: string;
};

export type ToolSpan = {
  call_id?: string;
  name: string;
  start_ms: number;
  end_ms: number;
  duration_ms?: number | null;
  turn?: number;
  source?: string;
  arguments?: string;
  output?: string;
  is_error: boolean;
  error?: string | null;
  parent_event_id?: string;
};

export type SessionStateTransition = {
  at_ms: number;
  from?: string | null;
  to: string;
};

export type SessionSummary = {
  usage?: Record<string, unknown>;
  state_transitions?: SessionStateTransition[];
  errors?: Array<{ at_ms: number; message: string }>;
};

export type ToolSummary = {
  tool_count: number;
  tool_errors: number;
};

export type ChatHistoryItem = Record<string, unknown>;

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
  tool_events?: ToolSpan[];
  tool_summary?: ToolSummary;
  session_summary?: SessionSummary | null;
  chat_history?: ChatHistoryItem[] | null;
  observe_gaps?: string[];
};

export type TimelineItem =
  | { kind: "cue"; start_ms: number; end_ms: number; cue: Cue }
  | { kind: "marker"; start_ms: number; end_ms: number; marker: Marker }
  | { kind: "tool"; start_ms: number; end_ms: number; tool: ToolSpan };

export type PlayerUI = {
  audio: HTMLAudioElement;
  cuesEl: HTMLOListElement;
  subtitle: HTMLElement;
  missing: HTMLElement;
  timeline: HTMLElement;
  playhead: HTMLElement;
  legend: HTMLElement;
  verify: HTMLElement;
  followBtn: HTMLButtonElement;
  sessionFooter: HTMLElement;
};
