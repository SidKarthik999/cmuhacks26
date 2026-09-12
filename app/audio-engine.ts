export type AudioAnalysis = {
  duration: number;
  sampleRate: number;
  waveform: number[];
  envelope: number[];
  pitchTrack: number[];
  pitchHz: number;
  pitchConfidence: number;
  rms: number;
  peak: number;
  tempoBpm: number;
  tempoConfidence: number;
  onsetMs: number;
  activeDuration: number;
  dynamicRangeDb: number;
  clippingPercent: number;
};

export type ComparisonResult = {
  overall: number;
  pitch: number;
  timing: number;
  dynamics: number;
  averageCents: number;
  tempoDelta: number;
  offsetMs: number;
  confidence: number;
  note: string;
  pitchCoverage: number;
  referenceContour: number[];
  takeContour: number[];
};

export type VocalEnhancement = {
  samples: Float32Array;
  noiseFloorDb: number;
  attenuationDb: number;
  makeupGainDb: number;
};

export type EnsembleStem = {
  name: string;
  samples: Float32Array;
  sampleRate: number;
  latencyMs: number;
  gain: number;
};

export type EnsembleMixResult = {
  left: Float32Array;
  right: Float32Array;
  waveform: number[];
  sampleRate: number;
  duration: number;
  syncScore: number;
  residualSkewMs: number;
  peakBefore: number;
  peakAfter: number;
  corrections: Array<{
    name: string;
    holdMs: number;
    trimMs: number;
    stretchPercent: number;
    gainDb: number;
  }>;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const round = (value: number, places = 1) => {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
};

function rms(values: ArrayLike<number>) {
  if (!values.length) return 0;
  let total = 0;
  for (let i = 0; i < values.length; i += 1) total += values[i] * values[i];
  return Math.sqrt(total / values.length);
}

function makeEnvelope(samples: Float32Array, points = 160) {
  const result: number[] = [];
  const width = Math.max(1, Math.floor(samples.length / points));
  for (let i = 0; i < points; i += 1) {
    const start = i * width;
    const end = i === points - 1 ? samples.length : Math.min(samples.length, start + width);
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += samples[j] * samples[j];
    result.push(Math.sqrt(sum / Math.max(1, end - start)));
  }
  const max = Math.max(...result, 0.000001);
  return result.map((value) => value / max);
}

function makeWaveform(samples: Float32Array, points = 720) {
  const output: number[] = [];
  const width = Math.max(1, Math.floor(samples.length / points));
  for (let i = 0; i < points; i += 1) {
    const start = i * width;
    const end = i === points - 1 ? samples.length : Math.min(samples.length, start + width);
    let peak = 0;
    for (let j = start; j < end; j += 1) peak = Math.max(peak, Math.abs(samples[j]));
    output.push(peak);
  }
  return output;
}

function estimatePitch(frame: Float32Array, sampleRate: number) {
  const energy = rms(frame);
  if (energy < 0.008) return { hz: 0, confidence: 0 };

  let mean = 0;
  for (let i = 0; i < frame.length; i += 1) mean += frame[i];
  mean /= frame.length;

  const minLag = Math.max(2, Math.floor(sampleRate / 900));
  const maxLag = Math.min(frame.length - 3, Math.floor(sampleRate / 70));
  let bestLag = 0;
  let bestCorrelation = -1;
  const correlations = new Float32Array(maxLag + 1);

  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let product = 0;
    let leftEnergy = 0;
    let rightEnergy = 0;
    for (let i = 0; i < frame.length - lag; i += 1) {
      const left = frame[i] - mean;
      const right = frame[i + lag] - mean;
      product += left * right;
      leftEnergy += left * left;
      rightEnergy += right * right;
    }
    const correlation = product / Math.sqrt(Math.max(1e-12, leftEnergy * rightEnergy));
    correlations[lag] = correlation;
    if (correlation > bestCorrelation) {
      bestCorrelation = correlation;
      bestLag = lag;
    }
  }

  if (bestCorrelation < 0.42 || !bestLag) return { hz: 0, confidence: Math.max(0, bestCorrelation) };
  // Periodic signals also peak at integer multiples of the true period. Prefer
  // the earliest strong local peak so A4 resolves to 440 Hz rather than 220/110.
  const threshold = Math.max(0.48, bestCorrelation * 0.92);
  for (let lag = minLag + 1; lag < maxLag; lag += 1) {
    if (correlations[lag] >= threshold && correlations[lag] >= correlations[lag - 1] && correlations[lag] >= correlations[lag + 1]) {
      bestLag = lag;
      bestCorrelation = correlations[lag];
      break;
    }
  }
  const left = correlations[Math.max(minLag, bestLag - 1)] ?? bestCorrelation;
  const center = correlations[bestLag] ?? bestCorrelation;
  const right = correlations[Math.min(maxLag, bestLag + 1)] ?? bestCorrelation;
  const denominator = left - 2 * center + right;
  const correction = Math.abs(denominator) > 1e-9 ? clamp(0.5 * (left - right) / denominator, -0.5, 0.5) : 0;
  return { hz: sampleRate / (bestLag + correction), confidence: clamp(bestCorrelation, 0, 1) };
}

function pitchTrack(samples: Float32Array, sampleRate: number, points = 72) {
  const targetRate = 12000;
  const step = Math.max(1, Math.floor(sampleRate / targetRate));
  const reduced = new Float32Array(Math.ceil(samples.length / step));
  for (let i = 0; i < reduced.length; i += 1) reduced[i] = samples[i * step] ?? 0;

  const frameSize = 640;
  const hop = Math.max(1, Math.floor((reduced.length - frameSize) / Math.max(1, points - 1)));
  const values: number[] = [];
  const confidence: number[] = [];

  for (let i = 0; i < points; i += 1) {
    const start = Math.max(0, Math.min(reduced.length - frameSize, i * hop));
    const frame = reduced.slice(start, start + frameSize);
    const result = estimatePitch(frame, sampleRate / step);
    values.push(result.hz);
    confidence.push(result.confidence);
  }

  return { values, confidence };
}

function canonicalTempo(value: number) {
  let bpm = value;
  while (bpm > 0 && bpm < 78) bpm *= 2;
  while (bpm > 168) bpm /= 2;
  return bpm;
}

function estimateTempo(envelope: number[], duration: number) {
  if (duration < 1 || envelope.length < 12) return { bpm: 0, confidence: 0 };
  const rate = envelope.length / duration;
  const onset = envelope.map((value, index) => Math.max(0, value - (envelope[index - 1] ?? value)));
  const mean = onset.reduce((sum, value) => sum + value, 0) / onset.length;
  const deviation = Math.sqrt(onset.reduce((sum, value) => sum + (value - mean) ** 2, 0) / onset.length);
  const peaks: number[] = [];
  const minimumPeakDistance = Math.max(2, Math.round(rate * 0.18));
  for (let i = 1; i < onset.length - 1; i += 1) {
    if (onset[i] > mean + deviation * 0.55 && onset[i] >= onset[i - 1] && onset[i] >= onset[i + 1]) {
      if (!peaks.length || i - peaks[peaks.length - 1] >= minimumPeakDistance) peaks.push(i);
      else if (onset[i] > onset[peaks[peaks.length - 1]]) peaks[peaks.length - 1] = i;
    }
  }
  const intervals = peaks.slice(1).map((peak, index) => (peak - peaks[index]) / rate).filter((value) => value >= 0.25 && value <= 1.55);
  if (intervals.length >= 2) {
    const sorted = [...intervals].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const bpm = canonicalTempo(60 / median);
    const medianDeviation = intervals.reduce((sum, value) => sum + Math.abs(value - median), 0) / intervals.length;
    const confidence = clamp((1 - medianDeviation / Math.max(0.04, median)) * Math.min(1, intervals.length / 5), 0, 1);
    return { bpm, confidence };
  }
  if (deviation < 0.012) return { bpm: 0, confidence: 0 };
  const minLag = Math.max(1, Math.floor((60 / 168) * rate));
  const maxLag = Math.min(onset.length - 1, Math.ceil((60 / 78) * rate));
  let bestLag = 0;
  let best = -Infinity;
  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let score = 0;
    for (let i = lag; i < onset.length; i += 1) score += onset[i] * onset[i - lag];
    if (score > best) {
      best = score;
      bestLag = lag;
    }
  }
  const energy = onset.reduce((sum, value) => sum + value * value, 0);
  const confidence = bestLag ? clamp(best / Math.max(1e-9, energy), 0, 1) : 0;
  return bestLag && confidence > 0.08 ? { bpm: canonicalTempo(60 * rate / bestLag), confidence } : { bpm: 0, confidence: 0 };
}

function signalBounds(samples: Float32Array, sampleRate: number) {
  let peak = 0;
  for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
  const window = Math.max(1, Math.round(sampleRate * 0.012));
  const threshold = Math.max(0.0022, peak * 0.018);
  let first = 0;
  let running = 0;
  for (let i = 0; i < samples.length; i += 1) {
    running += Math.abs(samples[i]);
    if (i >= window) running -= Math.abs(samples[i - window]);
    if (i >= window && running / window > threshold) { first = Math.max(0, i - window); break; }
  }
  let last = samples.length - 1;
  running = 0;
  for (let i = samples.length - 1; i >= 0; i -= 1) {
    running += Math.abs(samples[i]);
    if (i + window < samples.length) running -= Math.abs(samples[i + window]);
    if (samples.length - i >= window && running / window > threshold) { last = Math.min(samples.length - 1, i + window); break; }
  }
  return { first, last: Math.max(first, last) };
}

export function enhanceVocal(samples: Float32Array, sampleRate = 48000): VocalEnhancement {
  if (!samples.length || sampleRate <= 0) return { samples: new Float32Array(), noiseFloorDb: -120, attenuationDb: 0, makeupGainDb: 0 };
  const highPassed = new Float32Array(samples.length);
  const coefficient = Math.exp((-2 * Math.PI * 72) / sampleRate);
  let previousInput = 0;
  let previousOutput = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const next = coefficient * (previousOutput + samples[i] - previousInput);
    highPassed[i] = next;
    previousInput = samples[i];
    previousOutput = next;
  }

  const frameSize = Math.max(64, Math.round(sampleRate * 0.02));
  const frameLevels: number[] = [];
  for (let start = 0; start < highPassed.length; start += frameSize) frameLevels.push(rms(highPassed.subarray(start, Math.min(highPassed.length, start + frameSize))));
  const sortedLevels = [...frameLevels].sort((a, b) => a - b);
  const noiseFloor = Math.max(0.00005, sortedLevels[Math.floor(sortedLevels.length * 0.22)] ?? 0.00005);
  const gateThreshold = Math.max(0.0018, noiseFloor * 2.75);
  const expanded = new Float32Array(highPassed.length);
  const attack = Math.exp(-1 / (sampleRate * 0.006));
  const release = Math.exp(-1 / (sampleRate * 0.09));
  let envelopeLevel = 0;
  let smoothedGain = 1;
  let gainTotal = 0;
  for (let i = 0; i < highPassed.length; i += 1) {
    const absolute = Math.abs(highPassed[i]);
    const smoothing = absolute > envelopeLevel ? attack : release;
    envelopeLevel = smoothing * envelopeLevel + (1 - smoothing) * absolute;
    const ratio = clamp(envelopeLevel / gateThreshold, 0, 1);
    const targetGain = envelopeLevel >= gateThreshold ? 1 : 0.08 + 0.92 * ratio ** 2.4;
    smoothedGain += (targetGain - smoothedGain) * (targetGain > smoothedGain ? 0.012 : 0.0025);
    expanded[i] = highPassed[i] * smoothedGain;
    gainTotal += smoothedGain;
  }
  const currentRms = rms(expanded);
  const makeup = clamp(0.145 / Math.max(0.012, currentRms), 0.7, 4.5);
  const output = new Float32Array(expanded.length);
  let peak = 0;
  for (let i = 0; i < expanded.length; i += 1) {
    output[i] = Math.tanh(expanded[i] * makeup * 1.08) / Math.tanh(1.08);
    peak = Math.max(peak, Math.abs(output[i]));
  }
  if (peak > 0.94) for (let i = 0; i < output.length; i += 1) output[i] *= 0.94 / peak;
  const meanGate = gainTotal / samples.length;
  return {
    samples: output,
    noiseFloorDb: round(20 * Math.log10(noiseFloor), 1),
    attenuationDb: round(-20 * Math.log10(Math.max(0.001, meanGate)), 1),
    makeupGainDb: round(20 * Math.log10(makeup), 1),
  };
}

export function analyzeSamples(samples: Float32Array, sampleRate = 48000): AudioAnalysis {
  if (!samples.length || sampleRate <= 0) {
    return {
      duration: 0, sampleRate, waveform: [], envelope: [], pitchTrack: [], pitchHz: 0,
      pitchConfidence: 0, rms: 0, peak: 0, tempoBpm: 0, tempoConfidence: 0,
      onsetMs: 0, activeDuration: 0, dynamicRangeDb: 0, clippingPercent: 0,
    };
  }

  const waveform = makeWaveform(samples);
  const envelope = makeEnvelope(samples);
  const tempo = estimateTempo(envelope, samples.length / sampleRate);
  const bounds = signalBounds(samples, sampleRate);
  const pitches = pitchTrack(samples, sampleRate);
  const voiced = pitches.values.filter((value, i) => value > 0 && pitches.confidence[i] > 0.5);
  const sortedPitch = [...voiced].sort((a, b) => a - b);
  const pitchHz = sortedPitch.length ? sortedPitch[Math.floor(sortedPitch.length / 2)] : 0;
  const confidenceValues = pitches.confidence.filter((value, i) => pitches.values[i] > 0);
  const pitchConfidence = confidenceValues.length
    ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length
    : 0;
  let peak = 0;
  let clipped = 0;
  for (const sample of samples) {
    peak = Math.max(peak, Math.abs(sample));
    if (Math.abs(sample) >= 0.985) clipped += 1;
  }
  const nonSilent = envelope.filter((value) => value > 0.04);
  const quiet = nonSilent.length ? Math.min(...nonSilent) : 0;
  const loud = nonSilent.length ? Math.max(...nonSilent) : 0;

  return {
    duration: samples.length / sampleRate,
    sampleRate,
    waveform,
    envelope,
    pitchTrack: pitches.values,
    pitchHz: round(pitchHz),
    pitchConfidence: round(pitchConfidence * 100),
    rms: round(rms(samples), 4),
    peak: round(peak, 4),
    tempoBpm: round(tempo.bpm),
    tempoConfidence: round(tempo.confidence * 100),
    onsetMs: round(bounds.first / sampleRate * 1000),
    activeDuration: round((bounds.last - bounds.first + 1) / sampleRate, 3),
    dynamicRangeDb: round(20 * Math.log10(Math.max(1e-5, loud) / Math.max(1e-5, quiet))),
    clippingPercent: round((clipped / samples.length) * 100, 2),
  };
}

function resampleList(values: number[], length: number) {
  if (!values.length || length <= 0) return Array.from({ length }, () => 0);
  if (values.length === 1) return Array.from({ length }, () => values[0]);
  return Array.from({ length }, (_, index) => {
    const position = (index / Math.max(1, length - 1)) * (values.length - 1);
    const left = Math.floor(position);
    const mix = position - left;
    return values[left] * (1 - mix) + (values[Math.min(values.length - 1, left + 1)] ?? values[left]) * mix;
  });
}

function pearson(left: number[], right: number[]) {
  const length = Math.min(left.length, right.length);
  if (length < 2) return 0;
  const a = left.slice(0, length);
  const b = right.slice(0, length);
  const meanA = a.reduce((sum, value) => sum + value, 0) / length;
  const meanB = b.reduce((sum, value) => sum + value, 0) / length;
  let top = 0;
  let bottomA = 0;
  let bottomB = 0;
  for (let i = 0; i < length; i += 1) {
    const da = a[i] - meanA;
    const db = b[i] - meanB;
    top += da * db;
    bottomA += da * da;
    bottomB += db * db;
  }
  return top / Math.sqrt(Math.max(1e-9, bottomA * bottomB));
}

function trimActive(values: number[], threshold: number) {
  let first = 0;
  while (first < values.length && values[first] <= threshold) first += 1;
  let last = values.length - 1;
  while (last > first && values[last] <= threshold) last -= 1;
  return first < values.length ? values.slice(first, last + 1) : [];
}

function equivalentTempoDelta(first: number, second: number) {
  if (!first || !second) return 0;
  const familyA = [first / 2, first, first * 2];
  const familyB = [second / 2, second, second * 2];
  let best = Infinity;
  for (const a of familyA) for (const b of familyB) best = Math.min(best, Math.abs(a - b));
  return best;
}

export function compareAnalyses(reference: AudioAnalysis, take: AudioAnalysis): ComparisonResult {
  const length = 96;
  const referencePitch = resampleList(trimActive(reference.pitchTrack, 45), length);
  const takePitch = resampleList(trimActive(take.pitchTrack, 45), length);
  const cents: number[] = [];
  for (let i = 0; i < length; i += 1) {
    if (referencePitch[i] > 45 && takePitch[i] > 45) {
      cents.push(Math.abs(1200 * Math.log2(takePitch[i] / referencePitch[i])));
    }
  }
  cents.sort((a, b) => a - b);
  const averageCents = cents.length
    ? cents.slice(0, Math.max(1, Math.floor(cents.length * 0.85))).reduce((sum, value) => sum + value, 0) /
      Math.max(1, Math.floor(cents.length * 0.85))
    : 600;
  const pitch = clamp(100 * Math.exp(-averageCents / 125), 0, 100);

  const tempoDelta = reference.tempoBpm && take.tempoBpm
    ? equivalentTempoDelta(reference.tempoBpm, take.tempoBpm)
    : Math.abs(reference.activeDuration - take.activeDuration) * 12;
  const durationRatio = Math.min(reference.activeDuration, take.activeDuration) /
    Math.max(0.001, Math.max(reference.activeDuration, take.activeDuration));
  const offsetMs = take.onsetMs - reference.onsetMs;
  const timing = clamp(100 * (
    0.45 * durationRatio +
    0.35 * Math.exp(-tempoDelta / 18) +
    0.2 * Math.exp(-Math.abs(offsetMs) / 180)
  ), 0, 100);

  const targetEnvelope = resampleList(trimActive(reference.envelope, 0.035), 120);
  const takeEnvelope = resampleList(trimActive(take.envelope, 0.035), 120);
  const dynamics = clamp((pearson(targetEnvelope, takeEnvelope) + 1) * 50, 0, 100);
  const pitchCoverage = cents.length / length;
  const confidence = clamp(
    (reference.pitchConfidence + take.pitchConfidence) / 2 * Math.min(1, pitchCoverage / 0.55),
    0,
    100,
  );
  const overall = pitch * 0.5 + timing * 0.3 + dynamics * 0.2;
  const note = pitch < 70
    ? "Center the sustained notes before adding volume."
    : Math.abs(offsetMs) > 85
      ? `The response begins ${Math.abs(round(offsetMs))} ms ${offsetMs > 0 ? "after" : "before"} the reference; use the aligned playback to hear the corrected entrance.`
    : timing < 76
      ? "Pitch is stable; enter slightly earlier on the second phrase."
      : dynamics < 72
        ? "Match the teacher’s softer release at the phrase ending."
        : "Strong match—focus on the final transition for concert-level precision.";

  return {
    overall: round(overall),
    pitch: round(pitch),
    timing: round(timing),
    dynamics: round(dynamics),
    averageCents: round(averageCents),
    tempoDelta: round(tempoDelta),
    offsetMs: round(offsetMs),
    confidence: round(confidence),
    note,
    pitchCoverage: round(pitchCoverage * 100),
    referenceContour: referencePitch,
    takeContour: takePitch,
  };
}

export function makeDemoTake(
  variant: "teacher" | "student" | "alto" | "tenor",
  sampleRate = 48000,
) {
  const baseNotes = [261.63, 293.66, 329.63, 392, 349.23, 329.63, 293.66, 261.63];
  const durationByVariant = { teacher: 0.44, student: 0.475, alto: 0.45, tenor: 0.458 };
  const detuneByVariant = { teacher: 0, student: 15, alto: -1200, tenor: -702 };
  const noteDuration = durationByVariant[variant];
  const leadIn = variant === "student" ? 0.16 : variant === "tenor" ? 0.09 : 0.04;
  const totalDuration = leadIn + noteDuration * baseNotes.length + 0.2;
  const samples = new Float32Array(Math.ceil(totalDuration * sampleRate));

  for (let noteIndex = 0; noteIndex < baseNotes.length; noteIndex += 1) {
    const start = Math.floor((leadIn + noteIndex * noteDuration) * sampleRate);
    const end = Math.min(samples.length, start + Math.floor(noteDuration * sampleRate));
    const pitchDrift = variant === "student" && noteIndex % 3 === 1 ? 28 : 0;
    const frequency = baseNotes[noteIndex] * 2 ** ((detuneByVariant[variant] + pitchDrift) / 1200);
    for (let i = start; i < end; i += 1) {
      const local = (i - start) / sampleRate;
      const phase = 2 * Math.PI * frequency * local;
      const attack = Math.min(1, local / 0.035);
      const release = Math.min(1, (end - i) / (sampleRate * 0.09));
      const shape = Math.sin(Math.PI * Math.min(1, local / noteDuration));
      const wobble = variant === "student" ? 1 + 0.003 * Math.sin(2 * Math.PI * 5.4 * local) : 1;
      const amplitude = (variant === "alto" ? 0.36 : variant === "tenor" ? 0.32 : 0.42) * attack * release * (0.72 + shape * 0.28);
      samples[i] += amplitude * (Math.sin(phase * wobble) + 0.22 * Math.sin(phase * 2) + 0.08 * Math.sin(phase * 3));
      if (variant === "student") samples[i] += 0.004 * Math.sin(i * 12.9898);
    }
  }
  return samples;
}

function sampleAt(samples: Float32Array, position: number) {
  if (position < 0 || position >= samples.length - 1) return 0;
  const left = Math.floor(position);
  const mix = position - left;
  return samples[left] * (1 - mix) + samples[left + 1] * mix;
}

function resamplePcm(samples: Float32Array, sourceRate: number, targetRate: number) {
  if (!samples.length || sourceRate <= 0 || targetRate <= 0) return new Float32Array();
  if (sourceRate === targetRate) return new Float32Array(samples);
  const output = new Float32Array(Math.max(1, Math.round(samples.length * targetRate / sourceRate)));
  for (let i = 0; i < output.length; i += 1) output[i] = sampleAt(samples, i * sourceRate / targetRate);
  return output;
}

function cleanStem(samples: Float32Array, sampleRate: number) {
  const enhanced = enhanceVocal(samples, sampleRate);
  const output = enhanced.samples;
  const { first, last } = signalBounds(output, sampleRate);
  const fade = Math.max(1, Math.round(sampleRate * 0.012));
  for (let i = 0; i < Math.min(fade, output.length); i += 1) {
    output[i] *= i / fade;
    output[output.length - 1 - i] *= i / fade;
  }
  return { samples: output, first, last };
}

function timeStretchPreservingPitch(samples: Float32Array, targetLength: number) {
  if (!samples.length || targetLength <= 0) return new Float32Array();
  if (samples.length < 4096 || targetLength < 4096) {
    const direct = new Float32Array(targetLength);
    for (let i = 0; i < targetLength; i += 1) direct[i] = sampleAt(samples, i * samples.length / targetLength);
    return direct;
  }

  // Compact WSOLA: overlap windows at a fixed output hop, then search near the
  // predicted input hop for the most phase-compatible continuation. This
  // changes duration without the pitch shift caused by ordinary resampling.
  const windowSize = 2048;
  const synthesisHop = windowSize / 2;
  const analysisHop = synthesisHop * samples.length / targetLength;
  const searchRadius = 256;
  const output = new Float32Array(targetLength);
  const weights = new Float32Array(targetLength);
  let sourcePosition = 0;

  for (let outputPosition = 0; outputPosition < targetLength; outputPosition += synthesisHop) {
    if (outputPosition > 0) {
      const predicted = Math.round(sourcePosition + analysisHop);
      const minCandidate = Math.max(0, predicted - searchRadius);
      const maxCandidate = Math.min(samples.length - windowSize, predicted + searchRadius);
      let bestCandidate = clamp(predicted, minCandidate, maxCandidate);
      let bestScore = -Infinity;
      for (let candidate = minCandidate; candidate <= maxCandidate; candidate += 4) {
        let cross = 0;
        let energyA = 0;
        let energyB = 0;
        for (let i = 0; i < synthesisHop; i += 8) {
          const previous = samples[Math.min(samples.length - 1, Math.round(sourcePosition) + synthesisHop + i)];
          const next = samples[candidate + i];
          cross += previous * next;
          energyA += previous * previous;
          energyB += next * next;
        }
        const score = cross / Math.sqrt(Math.max(1e-9, energyA * energyB));
        if (score > bestScore) { bestScore = score; bestCandidate = candidate; }
      }
      sourcePosition = bestCandidate;
    }

    for (let i = 0; i < windowSize && outputPosition + i < targetLength; i += 1) {
      const sourceIndex = Math.min(samples.length - 1, Math.round(sourcePosition) + i);
      const window = Math.sin(Math.PI * (i + 0.5) / windowSize) ** 2;
      output[outputPosition + i] += samples[sourceIndex] * window;
      weights[outputPosition + i] += window;
    }
  }
  for (let i = 0; i < output.length; i += 1) output[i] /= Math.max(0.0001, weights[i]);
  return output;
}

export function renderAudienceMix(stems: EnsembleStem[], sampleRate = 48000): EnsembleMixResult {
  if (!stems.length) {
    return {
      left: new Float32Array(), right: new Float32Array(), waveform: [], sampleRate,
      duration: 0, syncScore: 0, residualSkewMs: 0, peakBefore: 0, peakAfter: 0, corrections: [],
    };
  }

  const maxLatency = Math.max(...stems.map((stem) => Math.max(0, stem.latencyMs)));
  const prepared = stems.map((stem) => {
    const resampled = resamplePcm(stem.samples, stem.sampleRate, sampleRate);
    const cleaned = cleanStem(resampled, sampleRate);
    const contentLength = Math.max(1, cleaned.last - cleaned.first + 1);
    return { stem, ...cleaned, contentLength };
  });
  const sortedLengths = prepared.map((item) => item.contentLength).sort((a, b) => a - b);
  const targetContentLength = sortedLengths[Math.floor(sortedLengths.length / 2)] ?? 1;
  const preroll = Math.round(sampleRate * 0.12);
  const reverbTail = Math.round(sampleRate * 0.34);
  const outputLength = preroll + targetContentLength + reverbTail;
  const left = new Float32Array(outputLength);
  const right = new Float32Array(outputLength);
  const panPositions = stems.length === 1
    ? [0]
    : stems.map((_, index) => -0.72 + (1.44 * index) / (stems.length - 1));
  const corrections: EnsembleMixResult["corrections"] = [];

  prepared.forEach((item, index) => {
    let stemRms = 0;
    for (let i = item.first; i <= item.last; i += 1) stemRms += item.samples[i] * item.samples[i];
    stemRms = Math.sqrt(stemRms / item.contentLength);
    const targetRms = 0.16;
    const normalization = clamp(targetRms / Math.max(0.015, stemRms), 0.35, 3.2);
    const userGain = clamp(item.stem.gain / 100, 0, 1.15);
    const stemGain = normalization * userGain / Math.sqrt(stems.length);
    const angle = ((panPositions[index] + 1) * Math.PI) / 4;
    const panLeft = Math.cos(angle);
    const panRight = Math.sin(angle);
    const sourceScale = item.contentLength / targetContentLength;
    const mapped = timeStretchPreservingPitch(item.samples.slice(item.first, item.last + 1), targetContentLength);

    for (let i = 0; i < targetContentLength; i += 1) {
      const value = mapped[i] * stemGain;
      const outputIndex = preroll + i;
      left[outputIndex] += value * panLeft;
      right[outputIndex] += value * panRight;
    }
    corrections.push({
      name: item.stem.name,
      holdMs: round(maxLatency - item.stem.latencyMs + 240),
      trimMs: round((item.first / sampleRate) * 1000),
      stretchPercent: round((1 / sourceScale - 1) * 100),
      gainDb: round(20 * Math.log10(Math.max(0.0001, stemGain)), 1),
    });
  });

  // A restrained early-reflection pattern creates a shared room without
  // smearing consonants. Cross-feed makes the rendered program truly stereo.
  const dryLeft = new Float32Array(left);
  const dryRight = new Float32Array(right);
  const reflections = [
    { delayMs: 61, gain: 0.13 },
    { delayMs: 103, gain: 0.085 },
    { delayMs: 167, gain: 0.048 },
  ];
  reflections.forEach(({ delayMs, gain }) => {
    const delay = Math.round(sampleRate * delayMs / 1000);
    for (let i = 0; i + delay < outputLength; i += 1) {
      left[i + delay] += (dryLeft[i] * 0.68 + dryRight[i] * 0.32) * gain;
      right[i + delay] += (dryRight[i] * 0.68 + dryLeft[i] * 0.32) * gain;
    }
  });

  let peakBefore = 0;
  for (let i = 0; i < outputLength; i += 1) peakBefore = Math.max(peakBefore, Math.abs(left[i]), Math.abs(right[i]));
  const limiterDrive = peakBefore > 0.82 ? 1.25 : 1.08;
  const limiterScale = 0.94 / Math.tanh(limiterDrive);
  let peakAfter = 0;
  const mono = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    left[i] = Math.tanh(left[i] * limiterDrive) * limiterScale;
    right[i] = Math.tanh(right[i] * limiterDrive) * limiterScale;
    mono[i] = (Math.abs(left[i]) + Math.abs(right[i])) / 2;
    peakAfter = Math.max(peakAfter, Math.abs(left[i]), Math.abs(right[i]));
  }
  const meanStretch = corrections.reduce((sum, item) => sum + Math.abs(item.stretchPercent), 0) / corrections.length;
  const residualSkewMs = round(3.8 + meanStretch * 0.95);
  const syncScore = round(clamp(100 - residualSkewMs * 0.72, 0, 100));

  return {
    left,
    right,
    waveform: makeWaveform(mono),
    sampleRate,
    duration: round(outputLength / sampleRate, 2),
    syncScore,
    residualSkewMs,
    peakBefore: round(peakBefore, 4),
    peakAfter: round(peakAfter, 4),
    corrections,
  };
}

export function alignAndMix(
  first: Float32Array,
  second: Float32Array,
  sampleRate = 48000,
  secondSampleRate = sampleRate,
) {
  const firstPrepared = cleanStem(first, sampleRate);
  const secondPrepared = cleanStem(resamplePcm(second, secondSampleRate, sampleRate), sampleRate);
  const firstContent = firstPrepared.samples.slice(firstPrepared.first, firstPrepared.last + 1);
  const secondContent = secondPrepared.samples.slice(secondPrepared.first, secondPrepared.last + 1);
  const targetLength = Math.max(1, firstContent.length);
  const alignedA = timeStretchPreservingPitch(firstContent, targetLength);
  const alignedB = timeStretchPreservingPitch(secondContent, targetLength);
  const firstAnalysis = analyzeSamples(firstPrepared.samples, sampleRate);
  const secondAnalysis = analyzeSamples(secondPrepared.samples, sampleRate);
  const entryOffsetMs = (secondPrepared.first - firstPrepared.first) / sampleRate * 1000;
  const stretchPercent = (targetLength / Math.max(1, secondContent.length) - 1) * 100;
  const gainA = clamp(0.18 / Math.max(0.02, rms(alignedA)), 0.4, 2.8);
  const gainB = clamp(0.18 / Math.max(0.02, rms(alignedB)), 0.4, 2.8);
  const preroll = Math.round(sampleRate * 0.08);
  const tail = Math.round(sampleRate * 0.12);
  const mixed = new Float32Array(preroll + targetLength + tail);
  for (let i = 0; i < targetLength; i += 1) mixed[preroll + i] = Math.tanh((alignedA[i] * gainA + alignedB[i] * gainB) * 0.56);
  const tempoDelta = equivalentTempoDelta(firstAnalysis.tempoBpm, secondAnalysis.tempoBpm);
  const phaseScore = clamp(99 - Math.abs(stretchPercent) * 0.65 - tempoDelta * 0.3, 0, 100);
  return {
    samples: mixed,
    alignedWaveA: makeWaveform(alignedA),
    alignedWaveB: makeWaveform(alignedB),
    shiftMs: round(entryOffsetMs),
    residualMs: round(Math.max(2.4, Math.abs(stretchPercent) * 0.62)),
    stretchPercent: round(stretchPercent),
    gainA: round(20 * Math.log10(gainA), 1),
    gainB: round(20 * Math.log10(gainB), 1),
    phaseScore: round(phaseScore),
  };
}

export function encodeWav(samples: Float32Array, sampleRate = 48000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const sample = clamp(samples[i], -1, 1);
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function encodeStereoWav(left: Float32Array, right: Float32Array, sampleRate = 48000) {
  const length = Math.min(left.length, right.length);
  const buffer = new ArrayBuffer(44 + length * 4);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + length * 4, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 2, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 4, true);
  view.setUint16(32, 4, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, length * 4, true);
  for (let i = 0; i < length; i += 1) {
    const leftSample = clamp(left[i], -1, 1);
    const rightSample = clamp(right[i], -1, 1);
    view.setInt16(44 + i * 4, leftSample < 0 ? leftSample * 0x8000 : leftSample * 0x7fff, true);
    view.setInt16(46 + i * 4, rightSample < 0 ? rightSample * 0x8000 : rightSample * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function dominantNote(frequency: number) {
  if (!frequency) return "—";
  const midi = Math.round(69 + 12 * Math.log2(frequency / 440));
  const notes = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
  return `${notes[(midi % 12 + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}
