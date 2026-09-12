/* Notation glyphs, drawn as paths rather than set as text.
 *
 * The obvious way to put a treble clef on a page is to type U+1D11E and hope
 * the user has a font with it. They generally do not, and the ones who do get
 * a glyph from whatever fallback font won, at whatever weight that font
 * happens to be — which is how a music site ends up with a clef that does not
 * match anything else on the page. These are hand-built paths on a 24-unit
 * grid, they inherit `currentColor`, and they are the "small music
 * components" that mark this as a music tool rather than a dashboard.
 */

const wrap = (body: string, box = 24): string =>
  `<svg viewBox="0 0 ${box} ${box}" fill="none" stroke="currentColor"
     stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"
     aria-hidden="true" focusable="false">${body}</svg>`;

/** Treble clef. Not a faithful Bravura outline — a legible shorthand. */
export const trebleClef = wrap(`
  <path d="M12.6 21.4c1.9.3 3.3-.8 3.3-2.4 0-1.5-1.2-2.6-2.8-2.6-2.4 0-4.3 2-4.3 4.5"
        stroke-width="1" opacity=".55"/>
  <path d="M12.1 21.7C12.1 16 11.8 9.4 11.8 7.3c0-2.6 1-4.6 2.6-4.6 1.3 0 2.1 1.1 2.1 2.6
           0 2.6-2.3 4.6-5.4 6.3C8.4 13 6.6 14.6 6.6 16.7c0 2 1.6 3.5 3.7 3.5 2.2 0 3.8-1.6 3.8-3.6
           0-1.7-1.1-3-2.7-3"/>
`);

/** Quarter note. */
export const quarterNote = wrap(`
  <ellipse cx="8.6" cy="17.4" rx="3.9" ry="2.8" transform="rotate(-22 8.6 17.4)"
           fill="currentColor" stroke="none"/>
  <path d="M12.3 15.9V4.4"/>
`);

/** Beamed pair — used as the "two voices" mark on practice mode. */
export const beamedPair = wrap(`
  <ellipse cx="6.4" cy="17.8" rx="3.2" ry="2.3" transform="rotate(-22 6.4 17.8)"
           fill="currentColor" stroke="none"/>
  <ellipse cx="16.4" cy="15.4" rx="3.2" ry="2.3" transform="rotate(-22 16.4 15.4)"
           fill="currentColor" stroke="none"/>
  <path d="M9.4 16.6V5.6M19.4 14.2V3.2"/>
  <path d="M9.4 5.6 19.4 3.2v2.6L9.4 8.2z" fill="currentColor" stroke="none"/>
`);

/** Fermata — the hold. Used on the concert timing panel. */
export const fermata = wrap(`
  <path d="M3.5 15.5a8.5 8.5 0 0 1 17 0"/>
  <circle cx="12" cy="15.8" r="1.5" fill="currentColor" stroke="none"/>
`);

/** Metronome. */
export const metronome = wrap(`
  <path d="M8.6 20.5h6.8L13.2 5.2h-2.4z"/>
  <path d="M6.5 20.5h11"/>
  <path d="M12 13.4 19 7.1" stroke-width="1"/>
  <circle cx="19.4" cy="6.7" r="1.3" fill="currentColor" stroke="none"/>
`);

/** Tuning fork — the reference pitch. */
export const tuningFork = wrap(`
  <path d="M9 3.5v7.8a3 3 0 0 0 6 0V3.5"/>
  <path d="M12 14.3v6.2"/>
`);

/** A three-note stave fragment, for section marks. */
export const staveMark = wrap(`
  <path d="M2 7h20M2 12h20M2 17h20" opacity=".4" stroke-width="1"/>
  <circle cx="7" cy="12" r="2" fill="currentColor" stroke="none"/>
  <circle cx="13" cy="7" r="2" fill="currentColor" stroke="none"/>
  <circle cx="19" cy="17" r="2" fill="currentColor" stroke="none"/>
`);

export const waveMark = wrap(`
  <path d="M2 12c2.5-7 4-7 6.5 0s4 7 6.5 0 4-7 6.5 0"/>
`);

export const micMark = wrap(`
  <rect x="9" y="2.6" width="6" height="11" rx="3"/>
  <path d="M5.5 11.6a6.5 6.5 0 0 0 13 0M12 18.1v3.3M8.5 21.4h7"/>
`);

export const GLYPHS = {
  trebleClef,
  quarterNote,
  beamedPair,
  fermata,
  metronome,
  tuningFork,
  staveMark,
  waveMark,
  micMark,
} as const;

export type GlyphName = keyof typeof GLYPHS;
