import type {
  ConcertResult,
  DuetResult,
  Health,
  LessonResult,
  Metric,
  Room,
  SceneDescription,
} from "../types";

import health from "./health.json";
import glossary from "./glossary.json";
import scenes from "./scenes.json";
import room from "./room.json";
import lesson from "./lesson.json";
import duet from "./duet.json";
import concert from "./concert.json";

/* Captured API responses.
 *
 * Every byte in these files came out of the running engine — they are the
 * exact payloads from `lesson:phrase`, `duet:thirds` and `concert:band`,
 * recorded rather than authored. That distinction matters for a demo: a
 * hand-written fixture can show any number the author wishes were true, and
 * these can only show what the analysis actually produced, down to the
 * caveats it raised about its own confidence.
 *
 * The casts are unavoidable. TypeScript widens a JSON import's string
 * literals to `string` and its nulls to `null`, so a union like
 * `good: "low" | "high"` cannot be inferred from the file; the shapes are
 * checked at the boundary instead, by the live API paths using the same
 * types.
 */

export const FIXTURES = {
  health: health as Health,
  glossary: glossary as unknown as Metric[],
  scenes: scenes as unknown as SceneDescription[],
  room: room as unknown as Room,
  lesson: lesson as unknown as LessonResult,
  duet: duet as unknown as DuetResult,
  concert: concert as unknown as ConcertResult,
};
