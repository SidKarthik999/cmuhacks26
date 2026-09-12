# Swarlink

Swarlink is a browser-based remote music studio for precision lessons, asynchronous rehearsals, and audience-synchronized performances.

## What works

- **Learn / Phrase Mirror:** records teacher and student audio, removes measured low-frequency room noise with an adaptive expander, tracks pitch in musical cents, resolves BPM octave aliases, aligns the active phrases, and draws the exact contours used by the score.
- **Blend / Duet Builder:** captures two independent clean stems, detects and removes entry silence, uses waveform-similarity overlap-add (WSOLA) to match phrase duration without ordinary resampling's pitch shift, loudness-matches both voices, and renders one overlapping WAV.
- **Stage / Audience Mixroom:** creates shareable six-character concert rooms, coordinates performers and audience devices through persistent D1 signaling, establishes an all-participant WebRTC camera mesh, carries isolated performer microphone stems only to the conductor, schedules one audible future downbeat, measures peer delay, enhances and holds each stem separately, and returns an intentionally delayed audience program. The calibrated five-stem renderer remains available for a deterministic judge demo and downloadable stereo WAV.

Audio analysis and demo rendering run locally in the browser. The calibrated demo path requires no device permissions.

Live rooms use peer-to-peer WebRTC media with a D1-backed signaling plane. Camera tracks form a deterministic mesh so every admitted device sees every other device; audio follows role-specific paths to prevent the video-call monitor from contaminating the concert master. They are designed for a hackathon-scale room (one conductor, a handful of performers, and a small audience). A production-scale public concert would replace the peer mesh with an SFU/TURN layer while preserving the same shared-clock and audience-buffer model.

## The synchronization model

Swarlink does not claim to violate network causality. Remote musicians cannot hear a voice before the voice is produced. The Stage workflow instead uses a future shared performance clock:

1. Every contribution is timestamped against the same clock.
2. Clean performer stems enter a bounded jitter buffer.
3. Faster stems wait for the slowest admitted stem.
4. The audience receives a delayed but synchronized program mix.

The demonstrator makes that last step audible. It trims each input to detected musical content, resamples it to the 48 kHz program rate, uses waveform-similarity overlap-add (WSOLA) to time-map it to the median phrase length without ordinary resampling's pitch shift, applies the live gain matrix and equal-power stereo placement, adds restrained early room reflections, then soft-limits the master. The DSP path uses 32-bit floating-point samples; the downloadable interoperability file is 16-bit stereo PCM.

This is appropriate for a performance delivered to an audience. Interactive performer monitoring would additionally require an external WebRTC signaling/SFU service and careful acoustic echo handling.

## Analysis methods

- normalized autocorrelation pitch tracking with octave-error suppression and sub-sample lag interpolation;
- adaptive noise-floor estimation, high-pass filtering, downward expansion, and bounded loudness makeup;
- RMS envelope and dynamic-range measurement;
- active-region onset detection for phrase entry offset;
- onset-interval tempo estimation with half-time/double-time canonicalization and confidence;
- pitch comparison in cents;
- aligned pitch-contour coverage, normalized duration, and envelope similarity;
- bounded PCM mixing and RIFF/WAV encoding;
- five-stem cleaning, median-duration time mapping, equal-power stereo placement, early-reflection room modeling, and soft limiting.

Swarlink does not present a fabricated neural score. The current judge build uses deterministic, inspectable signal processing so every displayed number can be traced to the graph and audio. A learned source-separation model can later replace the adaptive gate without changing the comparison or synchronization contracts.

Scores are descriptive practice feedback, not clinical or academic assessment.

## Local development

```bash
pnpm install
pnpm run dev
```

Open `http://localhost:3000`.

## Validation

```bash
pnpm run test:unit
pnpm run lint
pnpm run test
```

The automated suite covers silence, pitch detection, note mapping, self-similarity, room-noise suppression, BPM alias stability, overlapping duet alignment, five-stem mixing, mono/stereo WAV headers, deterministic peer offer selection, role-safe media routing, direct room-link rendering, metadata, and starter-artifact removal. The room signaling API is also exercised locally across create, join, poll, signal, and conductor-control operations, and the video mesh is verified with three independent browser pages (host, performer, and audience).
