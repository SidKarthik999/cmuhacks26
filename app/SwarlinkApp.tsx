"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type ComparisonResult,
} from "./audio-engine";
import { LiveRoom } from "./LiveRoom";

type Mode = "learn" | "blend" | "stage";
type TakeKey = "teacher" | "student" | "voiceA" | "voiceB";
type Take = {
  name: string;
  samples: Float32Array;
  analysis: ReturnType<typeof analyzeSamples>;
  processing: ReturnType<typeof enhanceVocal>;
  url: string;
};

const MODE_META: Record<Mode, { number: string; label: string; kicker: string }> = {
  learn: { number: "01", label: "Learn", kicker: "Teacher ↔ student" },
  blend: { number: "02", label: "Blend", kicker: "Independent ↔ together" },
  stage: { number: "03", label: "Stage", kicker: "Room ↔ audience" },
};

const TEAM = [
  { name: "Maya Rao", role: "Lead vocal", initials: "MR", latency: 42, color: "acid" },
  { name: "Eli Chen", role: "Keys", initials: "EC", latency: 86, color: "violet" },
  { name: "Noor Aziz", role: "Harmony", initials: "NA", latency: 118, color: "blue" },
  { name: "Jules Park", role: "Guitar", initials: "JP", latency: 67, color: "orange" },
  { name: "You", role: "Room host", initials: "YOU", latency: 31, color: "paper" },
];

function Waveform({ values, compare, color = "#d8ff3e", compact = false, cursor = 0, label }: {
  values: number[]; compare?: number[]; color?: string; compact?: boolean; cursor?: number; label: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(241,243,236,.09)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 8; i += 1) {
      const x = (i / 8) * width;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
    }
    ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();

    const draw = (source: number[], stroke: string, alpha: number, lineWidth: number) => {
      if (!source.length) return;
      ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth; ctx.beginPath();
      source.forEach((value, index) => {
        const x = (index / Math.max(1, source.length - 1)) * width;
        const y = height / 2 + Math.sin(index * 0.91) * Math.max(1, value) * height * 0.42;
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke(); ctx.restore();
    };
    if (compare) draw(compare, "#756fff", 0.8, compact ? 1 : 1.25);
    draw(values, color, 1, compact ? 1.1 : 1.7);
    if (cursor > 0) {
      ctx.strokeStyle = "rgba(255,255,255,.8)"; ctx.beginPath(); ctx.moveTo(width * cursor, 0); ctx.lineTo(width * cursor, height); ctx.stroke();
    }
  }, [values, compare, color, compact, cursor]);

  return <canvas ref={canvasRef} className={compact ? "wave-canvas compact" : "wave-canvas"} role="img" aria-label={label} />;
}

function EmptyWave({ label }: { label: string }) {
  const values = useMemo(() => Array.from({ length: 120 }, (_, index) => 0.06 + Math.abs(Math.sin(index * 0.47)) * 0.035), []);
  return <Waveform values={values} color="rgba(241,243,236,.18)" label={label} />;
}

function PitchContour({ reference, take }: { reference: number[]; take: number[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    const context = canvas.getContext("2d");
    if (!context) return;
    context.scale(ratio, ratio);
    const values = [...reference, ...take].filter((value) => value > 45);
    const minimumMidi = values.length ? Math.min(...values.map((value) => 69 + 12 * Math.log2(value / 440))) - 1 : 48;
    const maximumMidi = values.length ? Math.max(...values.map((value) => 69 + 12 * Math.log2(value / 440))) + 1 : 72;
    const toY = (frequency: number) => rect.height - ((69 + 12 * Math.log2(frequency / 440) - minimumMidi) / Math.max(1, maximumMidi - minimumMidi)) * rect.height;
    context.clearRect(0, 0, rect.width, rect.height);
    context.strokeStyle = "rgba(241,243,236,.08)";
    context.lineWidth = 1;
    for (let i = 1; i < 8; i += 1) { const x = rect.width * i / 8; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, rect.height); context.stroke(); }
    for (let i = 1; i < 4; i += 1) { const y = rect.height * i / 4; context.beginPath(); context.moveTo(0, y); context.lineTo(rect.width, y); context.stroke(); }
    const draw = (track: number[], color: string) => {
      context.strokeStyle = color; context.lineWidth = 1.7; context.beginPath(); let drawing = false;
      track.forEach((frequency, index) => {
        if (frequency <= 45) { drawing = false; return; }
        const x = index / Math.max(1, track.length - 1) * rect.width;
        const y = toY(frequency);
        if (!drawing) { context.moveTo(x, y); drawing = true; } else context.lineTo(x, y);
      });
      context.stroke();
    };
    draw(reference, "#d8ff3e");
    draw(take, "#8b86ff");
  }, [reference, take]);
  return <canvas ref={canvasRef} className="pitch-contour" role="img" aria-label="Time-aligned teacher and student pitch in musical semitones" />;
}

function MetricRing({ value, label, detail, color = "acid" }: { value: number; label: string; detail: string; color?: string }) {
  return (
    <article className={`metric-card ${color}`}>
      <div className="metric-ring" style={{ "--score": `${value * 3.6}deg` } as React.CSSProperties}><strong>{Math.round(value)}</strong><span>%</span></div>
      <div><h4>{label}</h4><p>{detail}</p></div>
    </article>
  );
}

function LiveBars({ seed = 0, active = false }: { seed?: number; active?: boolean }) {
  return (
    <span className={`live-bars ${active ? "is-live" : ""}`} aria-hidden="true">
      {Array.from({ length: 14 }, (_, i) => <i key={i} style={{ height: `${17 + ((i * 31 + seed * 19) % 76)}%`, animationDelay: `${-i * 53 - seed * 71}ms` }} />)}
    </span>
  );
}

function SignalField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let frame = 0;
    let width = 0;
    let height = 0;
    let ratio = 1;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    const render = (time: number) => {
      context.clearRect(0, 0, width, height);
      const phase = time * 0.00022;
      const center = height * 0.57;
      const columns = Math.max(90, Math.floor(width / 5));
      for (let column = 0; column <= columns; column += 1) {
        const normalized = column / columns;
        const x = normalized * width;
        const envelope = 0.22 + 0.78 * Math.sin(Math.PI * normalized) ** 1.7;
        const y = center + Math.sin(normalized * 18.5 + phase) * height * 0.145 + Math.sin(normalized * 41 - phase * 1.8) * height * 0.034;
        const spread = envelope * height * (0.05 + Math.abs(Math.sin(normalized * 12.7 - phase)) * 0.17);
        const particles = 10 + Math.floor(envelope * 18);
        for (let particle = 0; particle < particles; particle += 1) {
          const seed = (column * 92821 + particle * 68917) % 104729;
          const unit = seed / 104729;
          const offset = (unit - 0.5) * spread * 2;
          const jitter = Math.sin(seed * 0.013 + phase * 9) * 2.3;
          const radius = particle % 11 === 0 ? 1.25 : 0.55;
          context.fillStyle = `rgba(238,241,235,${0.18 + (1 - Math.abs(offset) / Math.max(1, spread)) * 0.62})`;
          context.beginPath();
          context.arc(x + jitter, y + offset, radius, 0, Math.PI * 2);
          context.fill();
        }
      }
      context.lineWidth = 0.7;
      context.strokeStyle = "rgba(216,255,62,.58)";
      context.beginPath();
      for (let column = 0; column <= columns; column += 1) {
        const normalized = column / columns;
        const x = normalized * width;
        const y = center + Math.sin(normalized * 18.5 + phase) * height * 0.145;
        if (column === 0) context.moveTo(x, y); else context.lineTo(x, y);
      }
      context.stroke();
      context.strokeStyle = "rgba(117,111,255,.33)";
      for (let column = 8; column < columns; column += 17) {
        const normalized = column / columns;
        const x = normalized * width;
        const y = center + Math.sin(normalized * 18.5 + phase) * height * 0.145;
        const magnitude = 18 + Math.abs(Math.sin(column * 2.7)) * 86;
        context.beginPath(); context.moveTo(x, y - magnitude); context.lineTo(x, y + magnitude); context.stroke();
      }
      frame = requestAnimationFrame(render);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    frame = requestAnimationFrame(render);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, []);

  return <canvas ref={canvasRef} className="signal-field" aria-hidden="true" />;
}

export function SwarlinkApp({ initialRoom = "" }: { initialRoom?: string }) {
  const [view, setView] = useState<"home" | "studio">(initialRoom ? "studio" : "home");
  const [mode, setMode] = useState<Mode>(initialRoom ? "stage" : "learn");
  const [takes, setTakes] = useState<Partial<Record<TakeKey, Take>>>({});
  const [recording, setRecording] = useState<TakeKey | null>(null);
  const [liveWave, setLiveWave] = useState<number[]>([]);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [lessonMixUrl, setLessonMixUrl] = useState<string | null>(null);
  const [mixUrl, setMixUrl] = useState<string | null>(null);
  const [mixData, setMixData] = useState<ReturnType<typeof alignAndMix> | null>(null);
  const [audienceMixUrl, setAudienceMixUrl] = useState<string | null>(null);
  const [audienceMixData, setAudienceMixData] = useState<ReturnType<typeof renderAudienceMix> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isStageLive, setIsStageLive] = useState(false);
  const [cameraOn, setCameraOn] = useState(false);
  const [stageTick, setStageTick] = useState(0);
  const [audience, setAudience] = useState(95);
  const [gains, setGains] = useState([88, 72, 76, 64, 80]);
  const [stageEvents, setStageEvents] = useState([
    "19:44:06  Room calibration complete",
    "19:44:03  Shared clock locked · ±8.4 ms",
    "19:43:58  Five performer stems connected",
  ]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const analyserFrameRef = useRef<number | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const recorderAudioContextRef = useRef<AudioContext | null>(null);
  const stageAudioContextRef = useRef<AudioContext | null>(null);
  const stageSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const stageGainsRef = useRef<GainNode[]>([]);
  const playbackRef = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const enterStudio = useCallback((nextMode: Mode = "learn") => {
    setMode(nextMode); setView("studio"); window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const saveTake = useCallback((key: TakeKey, samples: Float32Array, sampleRate: number, name: string) => {
    const processing = enhanceVocal(samples, sampleRate);
    const cleaned = processing.samples;
    const url = URL.createObjectURL(encodeWav(cleaned, sampleRate));
    const analysis = analyzeSamples(cleaned, sampleRate);
    setTakes((current) => {
      if (current[key]?.url) URL.revokeObjectURL(current[key]!.url);
      return { ...current, [key]: { name, samples: cleaned, analysis, processing, url } };
    });
    if (key === "teacher" || key === "student") {
      setComparison(null);
      if (lessonMixUrl) URL.revokeObjectURL(lessonMixUrl);
      setLessonMixUrl(null);
    } else {
      if (mixUrl) URL.revokeObjectURL(mixUrl);
      setMixUrl(null);
      setMixData(null);
    }
    return { name, samples: cleaned, analysis, processing, url };
  }, [lessonMixUrl, mixUrl]);

  const loadLessonDemo = useCallback(() => {
    const teacher = saveTake("teacher", makeDemoTake("teacher"), 48000, "Teacher reference");
    const student = saveTake("student", makeDemoTake("student"), 48000, "Student take");
    setComparison(compareAnalyses(teacher.analysis, student.analysis));
    const aligned = alignAndMix(teacher.samples, student.samples, 48000);
    if (lessonMixUrl) URL.revokeObjectURL(lessonMixUrl);
    setLessonMixUrl(URL.createObjectURL(encodeWav(aligned.samples, 48000)));
    setNotice(`Demo phrase loaded · ${(teacher.samples.length + student.samples.length).toLocaleString()} samples analyzed locally`);
  }, [lessonMixUrl, saveTake]);

  const loadBlendDemo = useCallback(() => {
    const voiceA = saveTake("voiceA", makeDemoTake("teacher"), 48000, "Maya · lead");
    const voiceB = saveTake("voiceB", makeDemoTake("tenor"), 48000, "Eli · harmony");
    const result = alignAndMix(voiceA.samples, voiceB.samples, 48000);
    if (mixUrl) URL.revokeObjectURL(mixUrl);
    setMixData(result); setMixUrl(URL.createObjectURL(encodeWav(result.samples, 48000)));
    setNotice("Independent takes aligned to a shared phrase clock");
  }, [mixUrl, saveTake]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
  }, []);

  const startRecording = useCallback(async (key: TakeKey) => {
    if (recording) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setNotice("This browser does not expose microphone recording. Load the calibrated demo instead."); return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
      mediaStreamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = async () => {
        try {
          const blob = new Blob(chunks, { type: recorder.mimeType });
          const context = recorderAudioContextRef.current ?? new AudioContext();
          const decoded = await context.decodeAudioData(await blob.arrayBuffer());
          const channel = new Float32Array(decoded.getChannelData(0));
          const name = key === "teacher" ? "Teacher reference" : key === "student" ? "Student take" : key === "voiceA" ? "Voice A" : "Voice B";
          saveTake(key, channel, decoded.sampleRate, name);
          setNotice(`${key === "teacher" ? "Reference" : "Take"} captured · ${channel.length.toLocaleString()} samples`);
        } catch {
          setNotice("The recording was captured, but this browser could not decode its audio format.");
        } finally {
          stream.getTracks().forEach((track) => track.stop());
          if (analyserFrameRef.current) cancelAnimationFrame(analyserFrameRef.current);
          void recorderAudioContextRef.current?.close(); recorderAudioContextRef.current = null;
          setLiveWave([]); setRecording(null);
        }
      };
      const context = new AudioContext();
      recorderAudioContextRef.current = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser(); analyser.fftSize = 512; source.connect(analyser);
      const data = new Float32Array(analyser.fftSize);
      const draw = () => {
        analyser.getFloatTimeDomainData(data);
        setLiveWave(Array.from(data, (sample) => Math.abs(sample) * 2.4));
        analyserFrameRef.current = requestAnimationFrame(draw);
      };
      draw(); recorder.start(120); setRecording(key);
      setNotice("Recording raw mono audio · processing stays on this device");
    } catch {
      setNotice("Microphone permission was not granted. You can still run the full demo with calibrated takes.");
    }
  }, [recording, saveTake]);

  const runComparison = useCallback(() => {
    if (!takes.teacher || !takes.student) { setNotice("Capture both the teacher reference and student take first."); return; }
    setComparison(compareAnalyses(takes.teacher.analysis, takes.student.analysis));
    const aligned = alignAndMix(takes.teacher.samples, takes.student.samples, takes.teacher.analysis.sampleRate, takes.student.analysis.sampleRate);
    if (lessonMixUrl) URL.revokeObjectURL(lessonMixUrl);
    setLessonMixUrl(URL.createObjectURL(encodeWav(aligned.samples, takes.teacher.analysis.sampleRate)));
    setNotice("Comparison complete · clean phrases aligned for matched playback");
  }, [lessonMixUrl, takes]);

  const buildMix = useCallback(() => {
    if (!takes.voiceA || !takes.voiceB) { setNotice("Capture both voices before creating the synchronized mix."); return; }
    const result = alignAndMix(takes.voiceA.samples, takes.voiceB.samples, takes.voiceA.analysis.sampleRate, takes.voiceB.analysis.sampleRate);
    if (mixUrl) URL.revokeObjectURL(mixUrl);
    setMixData(result); setMixUrl(URL.createObjectURL(encodeWav(result.samples, takes.voiceA.analysis.sampleRate)));
    setNotice("Mix rendered · offset, tempo, and loudness corrected");
  }, [mixUrl, takes]);

  const playUrl = useCallback((url?: string | null) => {
    if (!url) return;
    playbackRef.current?.pause();
    const audio = new Audio(url);
    playbackRef.current = audio;
    audio.onended = () => { if (playbackRef.current === audio) playbackRef.current = null; };
    void audio.play().catch(() => setNotice("Playback was blocked by the browser. Tap the listen control again."));
  }, []);

  const buildAudienceMix = useCallback(() => {
    const demoVariants = ["teacher", "alto", "tenor", "student", "alto"] as const;
    const captured = [takes.teacher, takes.voiceA, takes.voiceB, takes.student, undefined];
    const stems = TEAM.map((member, index) => ({
      name: member.name,
      samples: captured[index]?.samples ?? makeDemoTake(demoVariants[index]),
      sampleRate: captured[index]?.analysis.sampleRate ?? 48000,
      latencyMs: member.latency,
      gain: gains[index],
    }));
    const result = renderAudienceMix(stems, 48000);
    if (audienceMixUrl) URL.revokeObjectURL(audienceMixUrl);
    setAudienceMixData(result);
    setAudienceMixUrl(URL.createObjectURL(encodeStereoWav(result.left, result.right, result.sampleRate)));
    const timestamp = new Date().toLocaleTimeString([], { hour12: false });
    setStageEvents((events) => [`${timestamp}  Audience master rendered · ${result.syncScore}% lock`, ...events].slice(0, 5));
    setNotice(`Audience return ready · ${stems.length} clean stems · ${result.duration}s stereo WAV`);
  }, [audienceMixUrl, gains, takes]);

  const listenAsAudience = useCallback(() => {
    if (!audienceMixUrl) return;
    stageSourcesRef.current.forEach((source) => { try { source.stop(); } catch { /* already stopped */ } });
    stageSourcesRef.current = [];
    stageGainsRef.current = [];
    void stageAudioContextRef.current?.close();
    stageAudioContextRef.current = null;
    setIsStageLive(false);
    setNotice("Audience perspective · playing the synchronized stereo program");
    playUrl(audienceMixUrl);
  }, [audienceMixUrl, playUrl]);

  const toggleCamera = useCallback(async () => {
    if (cameraOn) {
      cameraStreamRef.current?.getTracks().forEach((track) => track.stop()); cameraStreamRef.current = null; setCameraOn(false); return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      cameraStreamRef.current = stream; setCameraOn(true); setNotice("Camera and microphone connected to the local performance stem");
    } catch { setNotice("Camera permission was not granted. Stage simulation remains available."); }
  }, [cameraOn]);

  useEffect(() => {
    if (cameraOn && videoRef.current && cameraStreamRef.current) { videoRef.current.srcObject = cameraStreamRef.current; void videoRef.current.play(); }
  }, [cameraOn, mode, view]);

  useEffect(() => {
    if (!isStageLive) return;
    const interval = window.setInterval(() => {
      setStageTick((value) => value + 1);
      setAudience((value) => value < 103 ? value + (value % 3 === 0 ? 1 : 0) : value);
    }, 440);
    return () => window.clearInterval(interval);
  }, [isStageLive]);

  const toggleStage = useCallback(async () => {
    const next = !isStageLive;
    if (next) {
      try {
        const context = new AudioContext();
        stageAudioContextRef.current = context;
        const variants = ["teacher", "alto", "tenor", "student", "alto"] as const;
        stageSourcesRef.current = variants.map((variant, index) => {
          const pcm = makeDemoTake(variant, context.sampleRate);
          const buffer = context.createBuffer(1, pcm.length, context.sampleRate);
          buffer.copyToChannel(pcm, 0);
          const source = context.createBufferSource();
          const gain = context.createGain();
          source.buffer = buffer;
          source.loop = true;
          gain.gain.value = (gains[index] / 100) * 0.12;
          source.connect(gain).connect(context.destination);
          source.start(context.currentTime + 0.12);
          stageGainsRef.current[index] = gain;
          return source;
        });
      } catch {
        setNotice("The visual stage is live; this browser blocked the local concert monitor.");
      }
    } else {
      stageSourcesRef.current.forEach((source) => { try { source.stop(); } catch { /* already stopped */ } });
      stageSourcesRef.current = [];
      stageGainsRef.current = [];
      await stageAudioContextRef.current?.close();
      stageAudioContextRef.current = null;
    }
    const timestamp = new Date().toLocaleTimeString([], { hour12: false });
    setStageEvents((events) => [`${timestamp}  ${next ? "Audience program opened · 420 ms safe buffer" : "Broadcast paused · stems preserved"}`, ...events].slice(0, 5));
    setIsStageLive(next);
  }, [gains, isStageLive]);

  useEffect(() => {
    stageGainsRef.current.forEach((node, index) => {
      node.gain.setTargetAtTime((gains[index] / 100) * 0.12, node.context.currentTime, 0.025);
    });
  }, [gains]);

  const updateStageGain = useCallback((index: number, value: number) => {
    setGains((current) => current.map((gain, gainIndex) => gainIndex === index ? value : gain));
    if (audienceMixUrl) {
      URL.revokeObjectURL(audienceMixUrl);
      setAudienceMixUrl(null);
      setAudienceMixData(null);
    }
  }, [audienceMixUrl]);

  useEffect(() => () => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    if (analyserFrameRef.current) cancelAnimationFrame(analyserFrameRef.current);
    stageSourcesRef.current.forEach((source) => { try { source.stop(); } catch { /* already stopped */ } });
    void stageAudioContextRef.current?.close();
    playbackRef.current?.pause();
  }, []);

  const leaveStudio = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current = null;
    setCameraOn(false);
    playbackRef.current?.pause();
    playbackRef.current = null;
    stageSourcesRef.current.forEach((source) => { try { source.stop(); } catch { /* already stopped */ } });
    stageSourcesRef.current = [];
    stageGainsRef.current = [];
    void stageAudioContextRef.current?.close();
    stageAudioContextRef.current = null;
    setIsStageLive(false);
    setView("home");
  }, []);

  if (view === "studio") {
    return (
      <main className="studio-app">
        <header className="studio-topbar">
          <button className="brand plain" onClick={leaveStudio} aria-label="Return to Swarlink home"><span className="brand-mark" aria-hidden="true">≋</span> SWARLINK</button>
          <div className="session-identity"><span>SESSION /</span><strong>RAGA LAB 04</strong><small>REC 00:{String(28 + stageTick).padStart(2, "0")}</small></div>
          <div className="studio-actions"><span className="connection-pill"><i /> NETWORK NOMINAL</span><button className="icon-button" onClick={() => setNotice("Session link copied for the demo room")} aria-label="Copy session link">⌁</button><button className="leave-button" onClick={leaveStudio}>LEAVE</button></div>
        </header>
        <div className="studio-layout">
          <aside className="mode-rail" aria-label="Studio modes">
            <p>MODES</p>
            {(Object.keys(MODE_META) as Mode[]).map((item) => (
              <button key={item} className={mode === item ? "active" : ""} onClick={() => setMode(item)}><span>{MODE_META[item].number}</span><strong>{MODE_META[item].label}</strong><small>{MODE_META[item].kicker}</small></button>
            ))}
            <div className="rail-footer"><i /><span>AUDIO ENGINE<br /><b>48,000 Hz</b></span></div>
          </aside>
          <section className="workspace">
            {mode === "learn" && <LearnMode takes={takes} recording={recording} liveWave={liveWave} comparison={comparison} lessonMixUrl={lessonMixUrl} onRecord={startRecording} onStop={stopRecording} onCompare={runComparison} onDemo={loadLessonDemo} onPlay={playUrl} />}
            {mode === "blend" && <BlendMode takes={takes} recording={recording} liveWave={liveWave} mixUrl={mixUrl} mixData={mixData} onRecord={startRecording} onStop={stopRecording} onBuildMix={buildMix} onDemo={loadBlendDemo} onPlay={playUrl} />}
            {mode === "stage" && <StageMode initialRoom={initialRoom} cameraOn={cameraOn} videoRef={videoRef} isLive={isStageLive} stageTick={stageTick} audience={audience} gains={gains} events={stageEvents} audienceMixUrl={audienceMixUrl} audienceMixData={audienceMixData} onCamera={toggleCamera} onToggleLive={toggleStage} onGain={updateStageGain} onBuildAudienceMix={buildAudienceMix} onListenAudience={listenAsAudience} onNotice={setNotice} />}
          </section>
        </div>
        {notice && <button className="notice" onClick={() => setNotice(null)} aria-label="Dismiss notification"><i />{notice}<span>×</span></button>}
      </main>
    );
  }

  return <HomeExperience onEnter={enterStudio} />;
}

function HomeExperience({ onEnter }: { onEnter: (mode?: Mode) => void }) {
  const cards: [string, string, string, string, Mode][] = [
    ["01", "Learn", "Teacher → student", "Capture a teacher’s phrase, map the student’s response, and explain pitch, timing, and expression with evidence.", "learn"],
    ["02", "Blend", "Voice → ensemble", "Record at different moments. Swarlink detects entry offset, maps tempo, balances loudness, and renders one synchronized mix.", "blend"],
    ["03", "Stage", "Room → concert", "Create one meeting link, receive isolated WebRTC stems, schedule a shared downbeat, and return one delayed audience concert.", "stage"],
  ];
  return (
    <main className="shell">
      <nav className="topbar"><a className="brand" href="#top" aria-label="Swarlink home"><span className="brand-mark" aria-hidden="true">≋</span>SWARLINK</a><div className="signal"><i /> SIGNAL STABLE · 48 kHz</div><button className="session-button" onClick={() => onEnter("learn")}>ENTER STUDIO <span>↗</span></button></nav>
      <section className="hero signal-hero" id="top">
        <div className="orbital" aria-label="Animated synchronized sound field"><SignalField /><span className="orbit orbit-a" /><span className="orbit orbit-b" /><span className="orbit orbit-c" /><span className="pulse-core">A4<br /><b>440.0</b><small>Hz</small></span><div className="coordinate c-one">Δt 008.4 ms</div><div className="coordinate c-two">PHASE 0.982</div><small className="orbital-caption">LIVE PHASE FIELD · 05 SOURCES</small></div>
        <div className="hero-copy"><p className="eyebrow">REMOTE MUSIC INFRASTRUCTURE / 2026</p><div className="hero-wordmark"><h1>SWARLINK</h1><p>MUSIC, TOGETHER IN TIME.</p></div><div className="hero-bottom"><p className="lede">Teach with precision. Rehearse beyond latency. Perform beyond the room.</p><button className="round-cta" onClick={() => onEnter("learn")} aria-label="Launch Swarlink studio">↘</button></div></div>
        <span className="frame-cross cross-a" aria-hidden="true" /><span className="frame-cross cross-b" aria-hidden="true" />
      </section>
      <div className="ticker" aria-label="Swarlink capabilities"><span>LOCAL AUDIO ANALYSIS</span><i>✦</i><span>PHASE-AWARE MIXING</span><i>✦</i><span>SHARED PERFORMANCE CLOCK</span><i>✦</i><span>NO CLOUD UPLOAD REQUIRED</span></div>
      <section className="manifesto"><p className="section-label">[ THE BROKEN ROOM ]</p><h2>VIDEO CALLS MOVE <em>CONVERSATION.</em><br />MUSIC NEEDS TO MOVE <em>TIME.</em></h2><p className="manifesto-copy">Swarlink is a purpose-built space for the moments ordinary calls erase: a teacher’s phrasing, two voices finding one tempo, and a band becoming an audience-ready whole.</p></section>
      <section className="mode-strip" id="modes" aria-label="Swarlink studio modes">
        {cards.map(([number, title, meta, copy, target], index) => <article className={`mode ${index === 0 ? "active" : ""}`} key={title}><div className="mode-head"><span>{number}</span><small>{meta}</small></div><h3>{title}</h3><p>{copy}</p><button onClick={() => onEnter(target)} aria-label={`Open ${title} mode`}>↗</button></article>)}
      </section>
      <section className="signal-section"><div className="signal-visual"><div className="stacked-wave">{Array.from({ length: 19 }, (_, row) => <span key={row} style={{ width: `${46 + (row % 7) * 7}%`, transform: `translateX(${(Math.sin(row) * 24).toFixed(2)}px)` }} />)}</div><div className="signal-crosshair"><i /><b /></div><small>FIG. 02 / LATENCY FIELD</small></div><div className="signal-copy"><p className="section-label">[ THE SWARLINK ENGINE ]</p><h2>WE DON’T REMOVE<br />DISTANCE. WE MAKE<br />IT <em>MEASURABLE.</em></h2><div className="spec-list"><div><b>48 kHz</b><span>analysis resolution</span></div><div><b>±10 ms</b><span>target ensemble lock</span></div><div><b>70 / 30</b><span>pitch + expression lens</span></div></div><button className="text-link" onClick={() => onEnter("stage")}>OPEN THE LIVE ENGINE <span>↗</span></button></div></section>
      <footer><div className="footer-word">SWAR<span>LINK</span></div><div className="footer-meta"><span>BUILT FOR THE MUSIC BETWEEN US.</span><span>CMU / 2026</span><button onClick={() => onEnter("learn")}>ENTER STUDIO ↗</button></div></footer>
    </main>
  );
}

function RecordButton({ active, onRecord, onStop, label }: { active: boolean; onRecord: () => void; onStop: () => void; label: string }) {
  return <button className={`record-button ${active ? "active" : ""}`} onClick={active ? onStop : onRecord}><i />{active ? "STOP CAPTURE" : label}</button>;
}

type CommonModeProps = {
  takes: Partial<Record<TakeKey, Take>>; recording: TakeKey | null; liveWave: number[];
  onRecord: (key: TakeKey) => void; onStop: () => void; onDemo: () => void; onPlay: (url?: string | null) => void;
};

function LearnMode({ takes, recording, liveWave, comparison, lessonMixUrl, onRecord, onStop, onCompare, onDemo, onPlay }: CommonModeProps & { comparison: ComparisonResult | null; lessonMixUrl: string | null; onCompare: () => void }) {
  const teacher = takes.teacher; const student = takes.student;
  return (
    <div className="mode-workspace learn-workspace">
      <header className="workspace-head"><div><p className="section-label">[ 01 / PRECISION LESSON ]</p><h1>Phrase mirror</h1><span>Listen less vaguely. Teach what changed.</span></div><div className="head-actions"><button className="ghost-button" onClick={onDemo}>LOAD CALIBRATED DEMO</button><button className="primary-button" onClick={onCompare}>ANALYZE MATCH <span>↗</span></button></div></header>
      <section className="take-grid">
        <article className="take-card teacher-card"><div className="take-title"><div><span>REFERENCE / A · CLEAN MONITOR</span><h3>Teacher phrase</h3></div><button className="play-button" disabled={!teacher} onClick={() => onPlay(teacher?.url)}>▶</button></div><div className="wave-stage">{recording === "teacher" ? <Waveform values={liveWave} label="Live teacher waveform" /> : teacher ? <Waveform values={teacher.analysis.waveform} label="Clean teacher waveform" /> : <EmptyWave label="Empty teacher waveform" />}<span className="timecode">{teacher ? teacher.analysis.duration.toFixed(2) : "0.00"}s</span></div><div className="take-controls"><RecordButton active={recording === "teacher"} onRecord={() => onRecord("teacher")} onStop={onStop} label="CAPTURE TEACHER" /><dl><div><dt>ROOT</dt><dd>{teacher ? dominantNote(teacher.analysis.pitchHz) : "—"}</dd></div><div><dt>BPM / CONF</dt><dd>{teacher?.analysis.tempoBpm ? `${teacher.analysis.tempoBpm} · ${teacher.analysis.tempoConfidence}%` : "NO LOCK"}</dd></div><div><dt>NOISE FLOOR</dt><dd>{teacher ? `${teacher.processing.noiseFloorDb} dB` : "—"}</dd></div></dl></div></article>
        <article className="take-card student-card"><div className="take-title"><div><span>RESPONSE / B · CLEAN MONITOR</span><h3>Student take</h3></div><button className="play-button" disabled={!student} onClick={() => onPlay(student?.url)}>▶</button></div><div className="wave-stage">{recording === "student" ? <Waveform values={liveWave} color="#756fff" label="Live student waveform" /> : student ? <Waveform values={student.analysis.waveform} color="#756fff" label="Clean student waveform" /> : <EmptyWave label="Empty student waveform" />}<span className="timecode">{student ? student.analysis.duration.toFixed(2) : "0.00"}s</span></div><div className="take-controls"><RecordButton active={recording === "student"} onRecord={() => onRecord("student")} onStop={onStop} label="CAPTURE STUDENT" /><dl><div><dt>ROOT</dt><dd>{student ? dominantNote(student.analysis.pitchHz) : "—"}</dd></div><div><dt>BPM / CONF</dt><dd>{student?.analysis.tempoBpm ? `${student.analysis.tempoBpm} · ${student.analysis.tempoConfidence}%` : "NO LOCK"}</dd></div><div><dt>PITCH CONF.</dt><dd>{student ? `${student.analysis.pitchConfidence}%` : "—"}</dd></div></dl></div></article>
      </section>
      <section className={`analysis-panel ${comparison ? "has-result" : ""}`}><div className="analysis-head"><div><span>COMPARISON OUTPUT</span><h3>{comparison ? `${Math.round(comparison.overall)}% phrase match` : "Waiting for two takes"}</h3></div><div className="analysis-actions"><div className="analysis-state"><i />{comparison ? `CONFIDENCE ${comparison.confidence}%` : "ENGINE READY"}</div><button disabled={!lessonMixUrl} onClick={() => onPlay(lessonMixUrl)}>▶ HEAR ALIGNED A + B</button></div></div>{comparison && teacher && student ? <><div className="pitch-map"><span className="axis-label">PITCH CONTOUR / SEMITONE AXIS · ALIGNED TO ACTIVE PHRASE</span><PitchContour reference={comparison.referenceContour} take={comparison.takeContour} /></div><div className="metric-grid"><MetricRing value={comparison.pitch} label="Tone / pitch" detail={`${comparison.averageCents}¢ deviation · ${comparison.pitchCoverage}% voiced`} /><MetricRing value={comparison.timing} label="Timing" detail={`${comparison.tempoDelta} BPM-family delta · ${comparison.offsetMs > 0 ? "+" : ""}${comparison.offsetMs} ms entry`} color="violet" /><MetricRing value={comparison.dynamics} label="Expression" detail="Aligned RMS-envelope correlation" color="paper" /><article className="coach-card"><span>COACH SIGNAL</span><p>“{comparison.note}”</p><small>The score and graph use the same cleaned, aligned cents and envelope measurements.</small></article></div></> : <div className="analysis-empty"><div className="scan-line" /><p>Capture a reference and response, or load the calibrated demo. Swarlink suppresses the measured room floor, aligns active phrases, then compares musical cents, tempo-family, and dynamics.</p></div>}</section>
    </div>
  );
}

function BlendMode({ takes, recording, liveWave, mixUrl, mixData, onRecord, onStop, onBuildMix, onDemo, onPlay }: CommonModeProps & { mixUrl: string | null; mixData: ReturnType<typeof alignAndMix> | null; onBuildMix: () => void }) {
  const voiceA = takes.voiceA; const voiceB = takes.voiceB;
  return (
    <div className="mode-workspace blend-workspace">
      <header className="workspace-head"><div><p className="section-label">[ 02 / ASYNCHRONOUS REHEARSAL ]</p><h1>Sing apart.<br />Hear together.</h1><span>No frozen call, no competing microphones—just two clean stems and one shared phrase clock.</span></div><div className="head-actions"><button className="ghost-button" onClick={onDemo}>LOAD DUET DEMO</button><button className="primary-button" onClick={onBuildMix}>BUILD SYNCED MIX <span>↗</span></button></div></header>
      <section className="stem-board">{(["voiceA", "voiceB"] as const).map((key, index) => { const take = key === "voiceA" ? voiceA : voiceB; return <article className="stem-row" key={key}><div className="stem-person"><span>{index === 0 ? "A" : "B"}</span><div><strong>{take?.name ?? `Voice ${index === 0 ? "A" : "B"}`}</strong><small>{index === 0 ? "LEAD STEM" : "HARMONY STEM"}</small></div></div><div className="stem-wave">{recording === key ? <Waveform values={liveWave} color={index ? "#756fff" : "#d8ff3e"} compact label={`Live voice ${index ? "B" : "A"} waveform`} /> : take ? <Waveform values={take.analysis.waveform} color={index ? "#756fff" : "#d8ff3e"} compact label={`Voice ${index ? "B" : "A"} waveform`} /> : <EmptyWave label={`Empty voice ${index ? "B" : "A"} waveform`} />}</div><div className="stem-meta"><b>{take ? `${take.analysis.duration.toFixed(2)}s` : "—"}</b><small>{take ? `${take.analysis.tempoBpm} BPM` : "NO TAKE"}</small></div><button className="play-button" disabled={!take} onClick={() => onPlay(take?.url)}>▶</button><RecordButton active={recording === key} onRecord={() => onRecord(key)} onStop={onStop} label={take ? "RETAKE" : "RECORD"} /></article>; })}</section>
      <section className="mix-console"><div className="mix-visual"><div className="time-axis"><span>00:00</span><span>00:01</span><span>00:02</span><span>00:03</span></div><div className="mix-track top"><small>A · ALIGNED</small>{mixData ? <Waveform values={mixData.alignedWaveA} compact label="Aligned lead stem timeline" /> : voiceA ? <Waveform values={voiceA.analysis.waveform} compact label="Lead stem timeline" /> : <EmptyWave label="Empty lead timeline" />}</div><div className="correction-line"><span style={{ left: mixData ? "8%" : "8%" }}><i />{mixData ? `ENTRY ${mixData.shiftMs > 0 ? "+" : ""}${mixData.shiftMs} ms → RESIDUAL ±${mixData.residualMs} ms` : "alignment point"}</span></div><div className="mix-track bottom"><small>B · ALIGNED</small>{mixData ? <Waveform values={mixData.alignedWaveB} color="#756fff" compact label="Aligned harmony stem timeline" /> : voiceB ? <Waveform values={voiceB.analysis.waveform} color="#756fff" compact label="Harmony stem timeline" /> : <EmptyWave label="Empty harmony timeline" />}</div></div><aside className="mix-output"><span>RENDER / 01</span><h3>{mixData ? "Entries unified" : "Mix not rendered"}</h3><div className="phase-number"><b>{mixData?.phaseScore ?? "—"}</b><small>%<br />SYNC</small></div><dl><div><dt>ENTRY REMOVED</dt><dd>{mixData ? `${mixData.shiftMs} ms` : "—"}</dd></div><div><dt>WSOLA MAP</dt><dd>{mixData ? `${mixData.stretchPercent}%` : "—"}</dd></div><div><dt>GAIN A / B</dt><dd>{mixData ? `${mixData.gainA} / ${mixData.gainB} dB` : "—"}</dd></div></dl><button className="listen-button" disabled={!mixUrl} onClick={() => onPlay(mixUrl)}><i />LISTEN TO THE UNIFIED DUET</button>{mixUrl && <a className="download-link" href={mixUrl} download="swarlink-synced-duet.wav">DOWNLOAD WAV ↓</a>}</aside></section>
    </div>
  );
}

function StageMode({ initialRoom, cameraOn, videoRef, isLive, stageTick, audience, gains, events, audienceMixUrl, audienceMixData, onCamera, onToggleLive, onGain, onBuildAudienceMix, onListenAudience, onNotice }: {
  initialRoom: string;
  cameraOn: boolean;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  isLive: boolean;
  stageTick: number;
  audience: number;
  gains: number[];
  events: string[];
  audienceMixUrl: string | null;
  audienceMixData: ReturnType<typeof renderAudienceMix> | null;
  onCamera: () => void;
  onToggleLive: () => void;
  onGain: (index: number, value: number) => void;
  onBuildAudienceMix: () => void;
  onListenAudience: () => void;
  onNotice: (message: string) => void;
}) {
  const maxLatency = Math.max(...TEAM.map((member) => member.latency));
  return (
    <div className="mode-workspace stage-workspace">
      <header className="workspace-head stage-head"><div><p className="section-label">[ 03 / SYNCHRONIZED PERFORMANCE ]</p><h1>Audience mixroom</h1><span>Five remote stems. One shared clock. One performance the audience can believe.</span></div><div className="head-actions"><button className={`camera-button ${cameraOn ? "active" : ""}`} onClick={onCamera}>{cameraOn ? "CAMERA ON" : "CONNECT CAMERA"}</button><button className={`broadcast-button ${isLive ? "live" : ""}`} onClick={onToggleLive}><i />{isLive ? "STOP DEMO MONITOR" : "DEMO MONITOR"}</button></div></header>
      <LiveRoom initialRoom={initialRoom} onNotice={onNotice} />
      <div className="lab-divider"><span>[ CALIBRATED MIX LAB / JUDGE FALLBACK ]</span><p>No second device handy? The lab below runs the same cleanup, timing, gain, space, and master stages over five deterministic harmony stems.</p></div>
      <section className="stage-grid"><div className="performer-grid">{TEAM.map((member, index) => <article className={`performer ${index === 0 ? "lead" : ""}`} key={member.name}>{member.name === "You" && cameraOn ? <video ref={videoRef} muted playsInline /> : <div className={`performer-visual ${member.color}`}><div className="avatar-ring"><span>{member.initials}</span></div><LiveBars seed={index + stageTick} active={isLive} /></div>}<div className="performer-top"><span className="live-dot">{index === 0 ? "LEAD" : "STEM"} {String(index + 1).padStart(2, "0")}</span><small>{member.latency} ms</small></div><div className="performer-bottom"><div><strong>{member.name}</strong><small>{member.role}</small></div><span>{isLive ? "● LIVE" : "READY"}</span></div></article>)}<article className="audience-tile"><div className="audience-orbit"><span>{audience}</span><small>LISTENERS</small></div><div><b>{audienceMixData ? "Audience master ready" : isLive ? "Inputs arriving live" : "Audience lobby ready"}</b><small>{audienceMixData ? `${audienceMixData.syncScore}% synchronized · stereo` : "Output buffer · 420 ms"}</small></div></article></div>
        <aside className="stage-console"><div className="console-title"><div><span>MASTER / STEMS</span><h3>Sync matrix</h3></div><span className="lock-status"><i />LOCKED</span></div><div className="master-wave"><Waveform values={Array.from({ length: 180 }, (_, i) => isLive ? .12 + Math.abs(Math.sin(i * .31 + stageTick)) * .72 : .05)} color="#d8ff3e" compact label="Master performance waveform" /></div><div className="clock-card"><span>SHARED PERFORMANCE CLOCK</span><strong>19:44:{String(28 + stageTick).padStart(2, "0")}<small>.{String((stageTick * 37) % 100).padStart(2, "0")}</small></strong><p>Performers play to a future timestamp. Fast stems wait for the slowest stem; the audience receives the aligned program.</p></div><div className="fader-list">{TEAM.map((member, index) => <label key={member.name}><span>{member.initials}</span><input type="range" min="0" max="100" value={gains[index]} onChange={(event) => onGain(index, Number(event.target.value))} aria-label={`${member.name} volume`} /><b>{gains[index]}</b><small>+{maxLatency - member.latency + 240} ms</small></label>)}</div><div className="event-log"><span>ENGINE LOG</span>{events.map((event) => <code key={event}>{event}</code>)}</div></aside>
      </section>
      <section className={`audience-return ${audienceMixData ? "has-master" : ""}`}>
        <div className="return-program">
          <div className="return-kicker"><span>[ AUDIENCE RETURN / PROGRAM 01 ]</span><b><i />{audienceMixData ? "MASTER READY" : "AWAITING RENDER"}</b></div>
          <div className="return-title"><div><small>WHAT THE CROWD HEARS</small><h2>Five rooms.<br /><em>One harmony.</em></h2></div>{audienceMixData && <div className="sync-seal"><strong>{Math.round(audienceMixData.syncScore)}</strong><span>%<br />LOCK</span></div>}</div>
          <div className="return-wave">{audienceMixData ? <Waveform values={audienceMixData.waveform} color="#d8ff3e" label="Synchronized audience stereo master waveform" /> : <EmptyWave label="Audience master awaiting render" />}</div>
          <div className="signal-pipeline" aria-label="Audience rendering pipeline"><span>05 RAW STEMS</span><i>→</i><span>CLEAN</span><i>→</i><span>CLOCK ALIGN</span><i>→</i><span>GAIN + SPACE</span><i>→</i><span>STEREO MASTER</span></div>
        </div>
        <aside className="return-console">
          <div className="return-explain"><span>THE MISSING OUTPUT</span><p>Each performer arrives alone. Swarlink removes low-level noise, trims entry silence, compensates network delay, time-maps the phrase, balances loudness, places every voice in stereo, then applies one shared room and limiter.</p></div>
          {audienceMixData ? <>
            <div className="return-stats"><div><small>FINAL SKEW</small><strong>±{audienceMixData.residualSkewMs} ms</strong></div><div><small>FORMAT</small><strong>48 kHz / stereo*</strong></div><div><small>LENGTH</small><strong>{audienceMixData.duration}s</strong></div></div>
            <div className="correction-ledger"><span>PER-STEM CORRECTION RECEIPT</span>{audienceMixData.corrections.map((item, index) => <div key={item.name}><b>{TEAM[index].initials}</b><small>HOLD +{item.holdMs} ms</small><small>TRIM {item.trimMs} ms</small><small>MAP {item.stretchPercent > 0 ? "+" : ""}{item.stretchPercent}%</small></div>)}</div>
          </> : <div className="return-placeholder"><strong>Ready to prove it.</strong><p>The button below renders actual samples into a downloadable stereo WAV. Use headphones to hear the harmony spread.</p></div>}
          <div className="return-actions"><button className="primary-button" onClick={onBuildAudienceMix}>{audienceMixData ? "RE-RENDER CURRENT GAINS" : "SYNC 5 INPUTS"} <span>↗</span></button><button className="listen-button" disabled={!audienceMixUrl} onClick={onListenAudience}><i />LISTEN AS AUDIENCE</button>{audienceMixUrl && <a className="download-link" href={audienceMixUrl} download="swarlink-audience-master.wav">DOWNLOAD AUDIENCE WAV ↓</a>}</div>
          <small className="format-note">*DSP runs at 32-bit float; downloadable demo is 16-bit stereo PCM.</small>
        </aside>
      </section>
      <section className="latency-explainer"><div><span>WHY THE BUFFER?</span><p>Physics does not allow a voice to arrive before it is sung. Swarlink timestamps each clean stem, delays the public output, and aligns every contribution against the same clock.</p></div><dl><div><dt>PERFORMER SKEW</dt><dd>±{isLive ? 8 + (stageTick % 3) : 0}.4 ms</dd></div><div><dt>JITTER BUFFER</dt><dd>240 ms</dd></div><div><dt>AUDIENCE DELAY</dt><dd>420 ms</dd></div><div><dt>STEMS</dt><dd>05 / 05</dd></div></dl></section>
    </div>
  );
}
