import assert from "node:assert/strict";
import test from "node:test";
import {
  alignAndMix,
  analyzeSamples,
  compareAnalyses,
  dominantNote,
  encodeStereoWav,
  encodeWav,
  enhanceVocal,
  makeDemoTake,
  renderAudienceMix,
} from "../app/audio-engine.ts";

function sine(frequency: number, seconds = 1.2, sampleRate = 48000) {
  return Float32Array.from({ length: Math.floor(seconds * sampleRate) }, (_, index) =>
    0.5 * Math.sin((2 * Math.PI * frequency * index) / sampleRate),
  );
}

test("empty audio returns finite zeroed metrics", () => {
  const analysis = analyzeSamples(new Float32Array(), 48000);
  assert.equal(analysis.duration, 0);
  for (const value of [analysis.rms, analysis.peak, analysis.pitchHz, analysis.clippingPercent]) assert.ok(Number.isFinite(value));
});

test("detects concert A within musical tolerance", () => {
  const analysis = analyzeSamples(sine(440), 48000);
  assert.ok(Math.abs(analysis.pitchHz - 440) < 7, `detected ${analysis.pitchHz} Hz`);
  assert.equal(dominantNote(analysis.pitchHz), "A4");
  assert.ok(analysis.pitchConfidence > 70);
});

test("maps low and high pitches to stable note names", () => {
  assert.equal(dominantNote(261.63), "C4");
  assert.equal(dominantNote(880), "A5");
  assert.equal(dominantNote(0), "—");
});

test("identical phrases score substantially higher than an imperfect take", () => {
  const teacher = analyzeSamples(makeDemoTake("teacher"), 48000);
  const same = compareAnalyses(teacher, teacher);
  const student = compareAnalyses(teacher, analyzeSamples(makeDemoTake("student"), 48000));
  assert.ok(same.overall > 94, `self score ${same.overall}`);
  assert.ok(student.overall < same.overall);
  assert.equal(same.referenceContour.length, same.takeContour.length);
  assert.equal(same.pitchCoverage, 100);
  for (const value of [student.overall, student.pitch, student.timing, student.dynamics, student.confidence]) assert.ok(value >= 0 && value <= 100);
});

test("alignment mixer removes sequential entrances and produces overlapping bounded PCM", () => {
  const first = makeDemoTake("teacher");
  const second = makeDemoTake("tenor");
  const result = alignAndMix(first, second, 48000);
  assert.ok(result.samples.length < first.length + second.length, "duet should overlap instead of playing sequentially");
  assert.ok(result.samples.some((sample) => Math.abs(sample) > 0.05));
  assert.ok(result.samples.every((sample) => Number.isFinite(sample) && Math.abs(sample) <= 1));
  assert.equal(result.alignedWaveA.length, result.alignedWaveB.length);
  assert.ok(result.phaseScore > 90 && result.phaseScore <= 100);
  assert.ok(Math.abs(result.shiftMs) > 20, "source entry difference should be detected and removed");
  assert.ok(result.residualMs < 10);
});

test("adaptive vocal cleanup suppresses room floor while preserving pitch", () => {
  const sampleRate = 48000;
  const samples = new Float32Array(sampleRate * 2);
  for (let i = 0; i < samples.length; i += 1) {
    const room = 0.012 * Math.sin(2 * Math.PI * 60 * i / sampleRate) + 0.003 * Math.sin(i * 12.9898);
    const voice = i > sampleRate * 0.45 && i < sampleRate * 1.6 ? 0.32 * Math.sin(2 * Math.PI * 440 * i / sampleRate) : 0;
    samples[i] = room + voice;
  }
  const enhanced = enhanceVocal(samples, sampleRate);
  const rawSilence = Math.sqrt(samples.slice(0, sampleRate * 0.35).reduce((sum, value) => sum + value * value, 0) / (sampleRate * 0.35));
  const cleanSilence = Math.sqrt(enhanced.samples.slice(0, sampleRate * 0.35).reduce((sum, value) => sum + value * value, 0) / (sampleRate * 0.35));
  assert.ok(cleanSilence < rawSilence * 0.65, `${cleanSilence} should be below ${rawSilence}`);
  assert.ok(Math.abs(analyzeSamples(enhanced.samples, sampleRate).pitchHz - 440) < 6);
  assert.ok(enhanced.noiseFloorDb < -25);
});

test("tempo estimator resolves half-time ambiguity consistently across related takes", () => {
  const teacher = analyzeSamples(enhanceVocal(makeDemoTake("teacher"), 48000).samples, 48000);
  const student = analyzeSamples(enhanceVocal(makeDemoTake("student"), 48000).samples, 48000);
  assert.ok(teacher.tempoConfidence > 75 && student.tempoConfidence > 75);
  assert.ok(Math.abs(teacher.tempoBpm - student.tempoBpm) < 10, `${teacher.tempoBpm} vs ${student.tempoBpm}`);
  assert.ok(teacher.tempoBpm >= 78 && teacher.tempoBpm <= 168);
});

test("WAV encoder emits a valid 16-bit mono RIFF header", async () => {
  const blob = encodeWav(sine(220, 0.1), 48000);
  const view = new DataView(await blob.arrayBuffer());
  const text = (offset: number, length: number) => String.fromCharCode(...Array.from({ length }, (_, index) => view.getUint8(offset + index)));
  assert.equal(text(0, 4), "RIFF");
  assert.equal(text(8, 4), "WAVE");
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint16(34, true), 16);
});

test("audience renderer cleans, aligns, balances, and spatializes five stems", () => {
  const variants = ["teacher", "alto", "tenor", "student", "alto"] as const;
  const latencies = [42, 86, 118, 67, 31];
  const mix = renderAudienceMix(variants.map((variant, index) => ({
    name: `Stem ${index + 1}`,
    samples: makeDemoTake(variant),
    sampleRate: 48000,
    latencyMs: latencies[index],
    gain: [88, 72, 76, 64, 80][index],
  })));
  assert.equal(mix.left.length, mix.right.length);
  assert.ok(mix.left.length > makeDemoTake("teacher").length);
  assert.equal(mix.corrections.length, 5);
  assert.ok(mix.left.some((sample) => Math.abs(sample) > 0.02));
  assert.ok(mix.right.some((sample) => Math.abs(sample) > 0.02));
  assert.ok(mix.left.some((sample, index) => Math.abs(sample - mix.right[index]) > 0.0001), "mix should be spatial stereo");
  assert.ok([...mix.left, ...mix.right].every((sample) => Number.isFinite(sample) && Math.abs(sample) <= 1));
  assert.ok(mix.syncScore >= 0 && mix.syncScore <= 100);
  assert.ok(mix.residualSkewMs > 0 && mix.residualSkewMs < 20);
  assert.ok(mix.corrections.every((item) => item.holdMs >= 240));
});

test("stereo WAV encoder writes interleaved 48 kHz PCM", async () => {
  const left = sine(220, 0.1);
  const right = sine(330, 0.1);
  const blob = encodeStereoWav(left, right, 48000);
  const view = new DataView(await blob.arrayBuffer());
  const text = (offset: number, length: number) => String.fromCharCode(...Array.from({ length }, (_, index) => view.getUint8(offset + index)));
  assert.equal(text(0, 4), "RIFF");
  assert.equal(text(8, 4), "WAVE");
  assert.equal(view.getUint16(22, true), 2);
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint32(28, true), 192000);
  assert.equal(view.getUint16(32, true), 4);
  assert.equal(view.getUint32(40, true), left.length * 4);
});
