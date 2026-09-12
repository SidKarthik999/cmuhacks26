/* Shapes returned by `swarlink/api.py`.
 *
 * Written by hand against the Python dataclasses rather than generated,
 * because the interesting part of every payload is the metric registry, and
 * a generator would flatten `meaning` and `reading` into `string` and lose
 * the reason they exist: no number in this product is allowed on screen
 * without the sentence that says what it means.
 */

export interface Health {
  ok: boolean;
  version: string;
  sample_rate: number;
  cleaning: boolean;
  cleaning_error: string | null;
  notes: boolean;
  notes_error: string | null;
  weighting: { pitch: number; volume: number };
  rooms: number;
}

export type Good = "low" | "high" | "zero";

export interface Metric {
  key: string;
  label: string;
  unit: string;
  value: number | null;
  display: string;
  meaning: string;
  reading: string;
  good: Good;
  anchor: string | null;
  anchors: { value: number; label: string }[];
}

export interface ContourPoint {
  t: number;
  hz: number | null;
  midi: number | null;
  db: number;
}

export interface NoteRow {
  note: string;
  midi: number;
  start_ms: number;
  duration_ms: number;
  hz: number;
  cents_off_equal?: number;
  confidence: number;
}

export interface CleaningReport {
  applied: boolean;
  reason?: string;
  module?: string;
  analysis_sr?: number;
  noise_floor_change_db?: number;
  signal_change_db?: number;
  peak_before?: number;
  peak_after?: number;
}

export interface TakeView {
  name: string;
  role: string;
  duration_ms: number;
  lufs: number;
  peak: number;
  wave: number[];
  contour: ContourPoint[];
  voiced_percent: number;
  notes: NoteRow[];
  cleaning: CleaningReport;
}

export interface ScoredNote {
  index: number;
  note: string;
  start_ms: number;
  duration_ms: number;
  teacher_hz: number;
  student_hz: number | null;
  cents: number | null;
  level_db: number | null;
  verdict: string;
  covered: boolean;
}

export interface Score {
  overall: number;
  pitch_score: number;
  volume_score: number;
  weighting: { pitch: number; volume: number; note: string };
  metrics: Metric[];
  notes: ScoredNote[];
  cents_series: { t_ms: number; cents: number | null }[];
  level_series: { t_ms: number; db: number | null }[];
  caveats: string[];
  headline: string;
}

export interface TimingWindow {
  t: number;
  residual_ms: number;
  confidence: number;
}

export interface LessonTiming {
  entry_lag_ms: number;
  tempo_ratio: number;
  tempo_percent: number;
  confidence: number;
  method: string;
  residual_offset_ms: number;
  worst_window_ms: number;
  verdict: string;
  windows: TimingWindow[];
  drift: { t_ms: number; drift_ms: number }[];
  warp: { t_ms: number; shift_ms: number }[];
  note: string;
}

export interface LessonResult {
  mode: "lesson";
  result_id: string;
  elapsed_ms: number;
  teacher: TakeView;
  student: TakeView;
  student_aligned: { wave: number[]; duration_ms: number; note: string };
  score: Score;
  timing: LessonTiming;
  glossary?: Metric[];
  scene?: SceneDescription;
  audio: { teacher: string; student: string; student_aligned: string };
}

export interface Harmony {
  cents: number;
  nearest_interval_cents: number;
  label: string;
  error_cents: number;
  in_tune: boolean;
  reliable: boolean;
  steady: boolean;
  spread_cents: number | null;
  coverage_percent: number;
  range_cents?: [number, number];
  reading?: string;
}

export interface DuetSync {
  entry_offset_ms: number;
  tempo_ratio: number;
  tempo_percent: number;
  scan_tempo_ratio: number;
  drift_ms: number;
  rubato_ms: number;
  tightness_before_ms: number;
  tightness_after_ms: number;
  improvement_ms: number;
  confidence: number;
  verdict: string;
  drift: { t_ms: number; drift_ms: number }[];
  warp: { t_ms: number; shift_ms: number }[];
  windows: TimingWindow[];
  caveats: string[];
  note: string;
}

export interface DuetSinger extends TakeView {
  match_gain_db: number;
  aligned_wave: number[];
}

export interface DuetResult {
  mode: "duet";
  result_id: string;
  elapsed_ms: number;
  singers: DuetSinger[];
  mix: { wave: number[]; duration_ms: number; lufs: number };
  naive_mix: { wave: number[]; duration_ms: number; note: string };
  sync: DuetSync;
  harmony: Harmony;
  glossary?: Metric[];
  scene?: SceneDescription;
  audio: { mix: string; naive_mix: string; singers: string[] };
}

export interface ConcertStem extends TakeView {
  is_lead: boolean;
  measured_delay_ms: number;
  delay_confidence: number;
  gain_db: number;
  aligned_wave: number[];
}

export interface ConcertTiming {
  nominal_delay_ms: number;
  measured_delays: {
    name: string;
    delay_ms: number;
    confidence: number;
    shift_applied_ms: number;
    residual_ms: number;
  }[];
  lead_advance_ms: number;
  advance_statistic: string;
  spread_before_ms: number;
  spread_after_ms: number;
  worst_pair_before_ms: number;
  worst_pair_after_ms: number;
  planned_spread_after_ms: number;
  verdict: string;
  verdict_before: string;
  caveats: string[];
  note: string;
  match_gain_db: number[];
}

export interface ConcertResult {
  mode: "concert";
  result_id: string;
  elapsed_ms: number;
  lead: string;
  audience: number;
  lead_advance_ms: number;
  stems: ConcertStem[];
  mix: { wave: number[]; duration_ms: number; lufs: number };
  naive_mix: { wave: number[]; note: string };
  timing: ConcertTiming;
  glossary?: Metric[];
  scene?: SceneDescription;
  audio: { mix: string; naive_mix: string; stems: string[] };
}

export interface RemixResult {
  result_id: string;
  elapsed_ms: number;
  gains_db: number[];
  audio: { mix: string };
  mix: { wave: number[]; lufs: number; peak: number };
}

export interface ScenePart {
  name: string;
  role: string;
  profile: string;
  duration_ms: number;
  peak: number;
  lufs: number;
  truth: Record<string, number | string>;
}

export interface SceneDescription {
  key: string;
  title: string;
  mode: "lesson" | "duet" | "concert";
  summary: string;
  sample_rate: number;
  truth: Record<string, unknown>;
  parts: ScenePart[];
}

export interface Participant {
  id: string;
  name: string;
  role: string;
  part: string | null;
  voice_profile: string | null;
  muted: boolean;
  gain_db: number;
  level_db: number;
  performs: boolean;
  joined_ms: number;
}

export interface LogLine {
  kind: "join" | "leave" | "mode" | "analysis" | "mix" | "chat" | "note";
  text: string;
  actor: string | null;
  at_ms: number;
  clock: string;
  data: Record<string, unknown>;
}

export interface Telemetry {
  participants: number;
  performers: number;
  audience: number;
  uplink_streams: number;
  downlink_streams: number;
  mix_streams: number;
  mesh_streams_avoided: number;
  audience_bitrate_kbps: number;
  audience_downlink_mbps: number;
  audience_buffer_ms: number;
  note: string;
}

export interface Room {
  id: string;
  name: string;
  mode: "teach" | "practice" | "perform";
  opened_ms: number;
  elapsed_ms: number;
  sample_rate: number;
  participants: Participant[];
  audience_count: number;
  roster: Participant[];
  log: LogLine[];
  telemetry: Telemetry;
  audio_ids: string[];
}
