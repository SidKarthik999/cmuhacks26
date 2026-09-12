import { h } from "../lib/dom";
import { mount, token } from "../lib/canvas";
import { audioUrl, isOffline } from "../api";
import { drawContour, drawDeviation, midiRange } from "../draw/contour";
import { drawScoreBar } from "../draw/meter";
import { drawWindows } from "../draw/spread";
import { drawWave } from "../draw/waveform";
import { caveatList, headline, metricList } from "../components/metric";
import { player } from "../components/player";
import { section, takeStrip } from "./shared";
import type { LessonResult, ScoredNote } from "../types";

/* Teach mode's scorecard.
 *
 * The order of this page is an argument, not a layout. It opens with the one
 * weighted number and the sentence that justifies it; then the evidence that
 * the two takes were made comparable at all, because a note-by-note
 * comparison of an unaligned pair is meaningless; then pitch, then level,
 * then the per-note table where a student can see which note to go and fix.
 * Timing comes last on purpose — it is how the comparison was made, not what
 * was being judged.
 */

export function teachPanel(result: LessonResult): { el: HTMLElement; dispose: () => void } {
  const disposers: (() => void)[] = [];
  const { score, timing, teacher, student } = result;
  const offline = isOffline();

  const url = (id: string): string | null => (offline ? null : audioUrl(id));

  const scoreCanvas = h("canvas", { class: "scorebar" });
  disposers.push(
    mount(
      scoreCanvas,
      (s) =>
        drawScoreBar(s, {
          overall: score.overall,
          pitch: score.pitch_score,
          volume: score.volume_score,
          weighting: score.weighting,
        }),
      { height: 78 },
    ),
  );

  const range = midiRange([teacher.contour, student.contour]);
  const contourCanvas = h("canvas", {});
  disposers.push(
    mount(
      contourCanvas,
      (s) =>
        drawContour(s, student.contour, {
          reference: teacher.contour,
          referenceColor: token("--cold-100"),
          color: token("--warm"),
          notes: teacher.notes,
          range,
          durationMs: Math.max(teacher.duration_ms, student.duration_ms),
        }),
      { height: 260 },
    ),
  );

  const centsCanvas = h("canvas", {});
  disposers.push(
    mount(
      centsCanvas,
      (s) =>
        drawDeviation(
          s,
          score.cents_series.map((p) => ({ t: p.t_ms, v: p.cents })),
          { span: 50, unit: "cents", tolerance: 20, durationMs: teacher.duration_ms },
        ),
      { height: 132 },
    ),
  );

  const levelCanvas = h("canvas", {});
  disposers.push(
    mount(
      levelCanvas,
      (s) =>
        drawDeviation(
          s,
          score.level_series.map((p) => ({ t: p.t_ms, v: p.db })),
          {
            span: 9,
            unit: "dB",
            tolerance: 3,
            color: token("--cold-300"),
            durationMs: teacher.duration_ms,
          },
        ),
      { height: 132 },
    ),
  );

  const alignedCanvas = h("canvas", {});
  disposers.push(
    mount(
      alignedCanvas,
      (s) =>
        drawWave(s, result.student_aligned.wave, {
          ghost: teacher.wave,
          ghostColor: token("--fg-300"),
          color: token("--cold-100"),
          durationMs: teacher.duration_ms,
          marks: teacher.notes.map((n) => n.start_ms),
          ruler: true,
        }),
      { height: 116 },
    ),
  );

  const windowsCanvas = h("canvas", {});
  disposers.push(
    mount(windowsCanvas, (s) => drawWindows(s, timing.windows, { span: 40 }), {
      height: 136,
    }),
  );

  const teacherPlayer = player({
    wave: teacher.wave,
    durationMs: teacher.duration_ms,
    url: url(result.audio.teacher),
    title: "teacher's take, cleaned",
    color: token("--fg-100"),
    marks: teacher.notes.map((n) => n.start_ms),
  });
  const studentPlayer = player({
    wave: result.student_aligned.wave,
    durationMs: teacher.duration_ms,
    url: url(result.audio.student_aligned),
    title: "student, time-matched to the teacher",
    compare: {
      wave: student.wave,
      url: url(result.audio.student),
      title: "student as sung",
    },
  });
  disposers.push(teacherPlayer.dispose, studentPlayer.dispose);

  const el = h(
    "div",
    { class: "panel panel--teach" },

    section(
      "the score",
      "One number, weighted exactly 70 / 30",
      h(
        "div",
        { class: "panel__headline" },
        headline(score.overall.toFixed(1), "of 100", score.headline),
        h(
          "div",
          { class: "panel__headaside" },
          scoreCanvas,
          h("p", { class: "panel__fine" }, score.weighting.note),
        ),
      ),
      metricList(score.metrics, ["overall_score", "pitch_score", "volume_score"], {
        scale: false,
      }),
      caveatList(score.caveats),
    ),

    section(
      "the two takes",
      "Made comparable before being compared",
      h("div", { class: "takes" }, takeStrip(teacher), takeStrip(student)),
      h("div", { class: "players" }, teacherPlayer.el, studentPlayer.el),
      h(
        "p",
        { class: "prose" },
        "The student sang ",
        h("b", { class: "num" }, `${timing.entry_lag_ms.toFixed(0)} ms`),
        " after the teacher at ",
        h("b", { class: "num" }, `${timing.tempo_ratio.toFixed(3)}×`),
        " their tempo. Both takes are cleaned separately, then a single " +
          "monotone time map is fitted so that the student's notes land on " +
          "the teacher's. Everything below is measured through that map — the " +
          "audio itself is never resampled for scoring.",
      ),
      h("figure", { class: "figure" }, alignedCanvas, h(
        "figcaption",
        {},
        "Student after time-matching (bright) over the teacher's envelope " +
          "(grey). Dashed lines are the teacher's note starts.",
      )),
    ),

    section(
      "pitch",
      `${score.pitch_score.toFixed(1)} of 100 · seventy per cent of the score`,
      h("figure", { class: "figure" }, contourCanvas, h(
        "figcaption",
        {},
        "Teacher in blue, student in amber, on a semitone axis. Both are " +
          "shown as sung, so the student's line starts later — the boxes are " +
          "the notes written out from the teacher's take, and the note-by-note " +
          "comparison further down is measured through the time map rather " +
          "than off this picture. Gaps are unvoiced frames, breaths and " +
          "consonants, left as gaps rather than interpolated across.",
      )),
      h("figure", { class: "figure" }, centsCanvas, h(
        "figcaption",
        {},
        "Signed error in cents, one hundredth of a semitone each. The band " +
          "is ±20 cents, about where a trained ear starts to object.",
      )),
      metricList(score.metrics, [
        "mean_abs_cents",
        "median_signed_cents",
        "in_tune_percent",
        "cents_spread",
      ]),
    ),

    section(
      "volume",
      `${score.volume_score.toFixed(1)} of 100 · the remaining thirty per cent`,
      h("figure", { class: "figure" }, levelCanvas, h(
        "figcaption",
        {},
        "Level difference over the phrase. A flat line away from zero is a " +
          "microphone set louder or quieter; a line that moves is the student " +
          "shaping the phrase differently from the teacher.",
      )),
      metricList(score.metrics, [
        "level_offset_db",
        "dynamics_error_db",
        "dynamic_range_db",
        "envelope_correlation",
      ]),
    ),

    section(
      "note by note",
      `${score.notes.length} notes written from the teacher's take`,
      noteTable(score.notes),
      h(
        "p",
        { class: "prose" },
        "Notes are segmented from the teacher's own pitch track and cut at " +
          "amplitude re-articulations, so a repeated note reads as two notes " +
          "rather than one long one. A dash means the student had no voiced " +
          "pitch over that note at all.",
      ),
    ),

    section(
      "how the takes were matched",
      timing.verdict,
      h("figure", { class: "figure" }, windowsCanvas, h(
        "figcaption",
        {},
        "Timing error left over after matching, in windows across the take. " +
          "Faded bars are windows with too few attacks to be sure about.",
      )),
      metricList(score.metrics, [
        "onset_offset_ms",
        "tempo_ratio",
        "worst_window_ms",
        "residual_offset_ms",
        "drift_spread_ms",
        "confidence",
      ]),
      h("p", { class: "panel__fine" }, timing.note),
    ),

    section(
      "cleaning",
      "Each voice de-noised on its own, before anything is measured",
      h(
        "div",
        { class: "clean" },
        cleanCard("teacher", teacher),
        cleanCard("student", student),
      ),
    ),
  );

  return {
    el,
    dispose() {
      for (const d of disposers) d();
    },
  };
}

function noteTable(notes: ScoredNote[]): HTMLElement {
  return h(
    "div",
    { class: "table__wrap" },
    h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "#"),
          h("th", {}, "note"),
          h("th", {}, "start"),
          h("th", {}, "length"),
          h("th", {}, "teacher"),
          h("th", {}, "student"),
          h("th", {}, "cents"),
          h("th", {}, "level"),
          h("th", {}, "verdict"),
        ),
      ),
      h(
        "tbody",
        {},
        ...notes.map((n) =>
          h(
            "tr",
            { class: n.covered ? "" : "table__row--gap" },
            h("td", { class: "num" }, String(n.index + 1)),
            h("td", { class: "table__note" }, n.note),
            h("td", { class: "num" }, `${(n.start_ms / 1000).toFixed(2)}s`),
            h("td", { class: "num" }, `${n.duration_ms.toFixed(0)}`),
            h("td", { class: "num" }, `${n.teacher_hz.toFixed(1)}`),
            h("td", { class: "num" }, n.student_hz === null ? "—" : n.student_hz.toFixed(1)),
            h(
              "td",
              { class: `num ${centsClass(n.cents)}` },
              n.cents === null ? "—" : `${n.cents > 0 ? "+" : ""}${n.cents.toFixed(0)}`,
            ),
            h(
              "td",
              { class: "num" },
              n.level_db === null ? "—" : `${n.level_db > 0 ? "+" : ""}${n.level_db.toFixed(1)}`,
            ),
            h("td", { class: "table__verdict" }, n.verdict),
          ),
        ),
      ),
    ),
  );
}

function centsClass(cents: number | null): string {
  if (cents === null) return "";
  const m = Math.abs(cents);
  return m <= 15 ? "cell--good" : m <= 40 ? "cell--warn" : "cell--bad";
}

function cleanCard(
  who: string,
  take: { cleaning: LessonResult["teacher"]["cleaning"]; lufs: number; peak: number; voiced_percent: number },
): HTMLElement {
  const c = take.cleaning;
  return h(
    "div",
    { class: "clean__card" },
    h("span", { class: "label label--accent" }, who),
    c.applied
      ? h(
          "ul",
          { class: "clean__list" },
          h(
            "li",
            {},
            h("b", { class: "num" }, `${(c.noise_floor_change_db ?? 0).toFixed(2)} dB`),
            " change in the noise floor, measured on the quietest fifth of the take",
          ),
          h(
            "li",
            {},
            h("b", { class: "num" }, `${(c.signal_change_db ?? 0).toFixed(2)} dB`),
            " change in the signal, measured on the loudest fifth",
          ),
          h("li", {}, h("b", { class: "num" }, `${take.lufs.toFixed(1)} LUFS`), " integrated loudness"),
          h("li", {}, h("b", { class: "num" }, `${take.voiced_percent.toFixed(0)}%`), " of frames voiced"),
        )
      : h("p", { class: "panel__fine" }, c.reason ?? "not applied"),
    c.module && h("p", { class: "panel__fine" }, `module: ${c.module} at ${c.analysis_sr} Hz`),
  );
}
