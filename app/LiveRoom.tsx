"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { roomMediaPlan, shouldInitiatePeer } from "./room-topology";

type RoomRole = "host" | "performer" | "audience";
type Participant = { id: string; name: string; role: RoomRole; state: string; latencyMs: number; lastSeenAt: number };
type RoomState = { id: string; title: string; conductorId: string; status: "lobby" | "countdown" | "live" | "ended"; startAt: number | null; audienceDelayMs: number };
type RoomSession = { roomId: string; participantId: string; role: RoomRole };
type SignalMessage = { id: number; senderId: string; recipientId: string; kind: "offer" | "answer" | "ice" | "bye"; payload: string };
type StemNode = { delay: DelayNode; gain: GainNode; panner: StereoPannerNode; latencyMs: number };

const RTC_CONFIGURATION: RTCConfiguration = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

async function roomPost(payload: Record<string, unknown>) {
  const response = await fetch("/api/room", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "Room request failed.");
  return data;
}

function shortCode(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase().slice(0, 6);
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "SL";
}

function RoomVideoTile({ participant, stream, isLocal, connected }: { participant: Participant; stream?: MediaStream; isLocal: boolean; connected: boolean }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const videoTrack = stream?.getVideoTracks()[0];

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = videoTrack ? new MediaStream([videoTrack]) : null;
    if (videoTrack) void video.play().catch(() => undefined);
  }, [videoTrack]);

  return <article className={`room-video-tile ${isLocal ? "is-local" : ""} ${videoTrack?.enabled ? "has-video" : ""}`}>
    {videoTrack ? <video ref={videoRef} autoPlay muted playsInline /> : <div className="room-video-fallback"><span>{initials(participant.name)}</span><i /></div>}
    <div className="room-video-scan" />
    <div className="room-video-meta"><div><strong>{participant.name}{isLocal ? " · YOU" : ""}</strong><small>{participant.role} / {videoTrack?.enabled ? "camera live" : "camera unavailable"}</small></div><b className={connected ? "online" : ""}>{connected ? "● LINKED" : "○ CONNECTING"}</b></div>
  </article>;
}

export function LiveRoom({ initialRoom = "", onNotice }: { initialRoom?: string; onNotice: (message: string) => void }) {
  const [name, setName] = useState("Maya");
  const [joinCode, setJoinCode] = useState(() => shortCode(initialRoom));
  const [joinRole, setJoinRole] = useState<Exclude<RoomRole, "host">>("performer");
  const [session, setSession] = useState<RoomSession | null>(null);
  const [room, setRoom] = useState<RoomState | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [connectedIds, setConnectedIds] = useState<string[]>([]);
  const [programReady, setProgramReady] = useState(false);
  const [monitoring, setMonitoring] = useState(false);
  const [localMedia, setLocalMedia] = useState<MediaStream | null>(null);
  const [remoteVideos, setRemoteVideos] = useState<Record<string, MediaStream>>({});
  const [cameraEnabled, setCameraEnabled] = useState(true);
  const [microphoneEnabled, setMicrophoneEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [clockOffset, setClockOffset] = useState(0);
  const [clockNow, setClockNow] = useState(() => Date.now());
  const [engineLog, setEngineLog] = useState<string[]>(["Room transport armed", "420 ms audience program buffer"]);

  const localStreamRef = useRef<MediaStream | null>(null);
  const outboundStreamRef = useRef<MediaStream | null>(null);
  const outboundContextRef = useRef<AudioContext | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const masterInputRef = useRef<GainNode | null>(null);
  const programDestinationRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const peerConnectionsRef = useRef(new Map<string, RTCPeerConnection>());
  const peerRolesRef = useRef(new Map<string, RoomRole>());
  const participantsRef = useRef<Participant[]>([]);
  const connectedCountRef = useRef(0);
  const pendingCandidatesRef = useRef(new Map<string, RTCIceCandidateInit[]>());
  const stemNodesRef = useRef(new Map<string, StemNode>());
  const lastSignalRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const advancingCountdownRef = useRef(false);
  const scheduledStartRef = useRef(0);
  const syntheticMediaContextRef = useRef<AudioContext | null>(null);
  const syntheticFrameTimerRef = useRef(0);

  const removeRemoteVideo = useCallback((participantId: string) => {
    setRemoteVideos((current) => {
      if (!current[participantId]) return current;
      const next = { ...current };
      delete next[participantId];
      return next;
    });
  }, []);

  const setRemoteVideoTrack = useCallback((participantId: string, track: MediaStreamTrack) => {
    setRemoteVideos((current) => ({ ...current, [participantId]: new MediaStream([track]) }));
    track.onended = () => removeRemoteVideo(participantId);
  }, [removeRemoteVideo]);

  const getAudioElement = useCallback(() => {
    if (!audioRef.current) audioRef.current = new Audio();
    return audioRef.current;
  }, []);

  const writeLog = useCallback((message: string) => {
    const timestamp = new Date().toLocaleTimeString([], { hour12: false });
    setEngineLog((entries) => [`${timestamp}  ${message}`, ...entries].slice(0, 5));
  }, []);

  const syncStemDelays = useCallback(() => {
    const nodes = [...stemNodesRef.current.values()];
    const maximumLatency = Math.max(0, ...nodes.map((node) => node.latencyMs));
    const now = audioContextRef.current?.currentTime ?? 0;
    nodes.forEach((node) => node.delay.delayTime.setTargetAtTime(0.42 + (maximumLatency - node.latencyMs) / 1000, now, 0.08));
  }, []);

  const ensureMixer = useCallback(async () => {
    if (audioContextRef.current && masterInputRef.current && programDestinationRef.current) return;
    const context = new AudioContext({ sampleRate: 48000, latencyHint: "playback" });
    await context.resume();
    await context.audioWorklet.addModule("/adaptive-gate.js").catch(() => undefined);
    const master = context.createGain();
    master.gain.value = 0.78;
    const compressor = context.createDynamicsCompressor();
    compressor.threshold.value = -16;
    compressor.knee.value = 9;
    compressor.ratio.value = 5;
    compressor.attack.value = 0.004;
    compressor.release.value = 0.16;
    const destination = context.createMediaStreamDestination();
    master.connect(compressor).connect(destination);
    [[0.061, 0.1], [0.103, 0.065], [0.167, 0.035]].forEach(([seconds, level]) => {
      const delay = context.createDelay(0.3); delay.delayTime.value = seconds;
      const wet = context.createGain(); wet.gain.value = level;
      master.connect(delay).connect(wet).connect(compressor);
    });
    audioContextRef.current = context;
    masterInputRef.current = master;
    programDestinationRef.current = destination;
  }, []);

  const attachStem = useCallback(async (stream: MediaStream, participantId: string) => {
    await ensureMixer();
    if (!audioContextRef.current || !masterInputRef.current || stemNodesRef.current.has(participantId)) return;
    const context = audioContextRef.current;
    const source = context.createMediaStreamSource(stream);
    const highPass = context.createBiquadFilter(); highPass.type = "highpass"; highPass.frequency.value = 74; highPass.Q.value = 0.72;
    let adaptiveGate: AudioNode;
    try { adaptiveGate = new AudioWorkletNode(context, "swarlink-adaptive-gate"); } catch { adaptiveGate = context.createGain(); }
    const presence = context.createBiquadFilter(); presence.type = "peaking"; presence.frequency.value = 3200; presence.Q.value = 0.8; presence.gain.value = 1.6;
    const compressor = context.createDynamicsCompressor(); compressor.threshold.value = -25; compressor.knee.value = 14; compressor.ratio.value = 3.6; compressor.attack.value = 0.006; compressor.release.value = 0.13;
    const delay = context.createDelay(1.5); delay.delayTime.value = 0.42;
    const panner = context.createStereoPanner();
    const performerIndex = stemNodesRef.current.size;
    panner.pan.value = Math.max(-0.72, Math.min(0.72, -0.72 + performerIndex * 0.36));
    const isLocalStem = localStreamRef.current === stream;
    const gain = context.createGain(); gain.gain.value = isLocalStem ? 0.72 : 0.88;
    source.connect(highPass).connect(adaptiveGate).connect(presence).connect(compressor).connect(delay).connect(panner).connect(gain).connect(masterInputRef.current);
    stemNodesRef.current.set(participantId, { delay, gain, panner, latencyMs: isLocalStem ? 0 : 80 });
    syncStemDelays();
    writeLog(`Clean stem attached · ${participantId.slice(0, 5)}`);
  }, [ensureMixer, syncStemDelays, writeLog]);

  const sendSignal = useCallback(async (targetId: string, kind: SignalMessage["kind"], payload: unknown) => {
    if (!session) return;
    await roomPost({ action: "signal", roomId: session.roomId, participantId: session.participantId, recipientId: targetId, kind, payload: JSON.stringify(payload) });
  }, [session]);

  const markConnection = useCallback((participantId: string, connected: boolean) => {
    setConnectedIds((current) => {
      const next = connected ? [...new Set([...current, participantId])] : current.filter((id) => id !== participantId);
      connectedCountRef.current = next.length;
      return next;
    });
  }, []);

  const flushCandidates = useCallback(async (participantId: string, peer: RTCPeerConnection) => {
    const candidates = pendingCandidatesRef.current.get(participantId) ?? [];
    for (const candidate of candidates) await peer.addIceCandidate(candidate).catch(() => undefined);
    pendingCandidatesRef.current.delete(participantId);
  }, []);

  const configurePeerMedia = useCallback(async (peer: RTCPeerConnection, targetRole: RoomRole) => {
    if (!session) return;
    const plan = roomMediaPlan(session.role, targetRole);
    const hasSender = (track: MediaStreamTrack) => peer.getSenders().some((sender) => sender.track?.id === track.id);
    const addOnce = (track: MediaStreamTrack, stream: MediaStream) => { if (!hasSender(track)) peer.addTrack(track, stream); };

    if (plan.sendCamera) {
      const cameraTrack = localStreamRef.current?.getVideoTracks()[0];
      if (cameraTrack) addOnce(cameraTrack, localStreamRef.current!);
      else if (!peer.getTransceivers().some((transceiver) => transceiver.receiver.track.kind === "video")) peer.addTransceiver("video", { direction: "recvonly" });
    }
    if (plan.sendPerformerStem) {
      outboundStreamRef.current?.getAudioTracks().forEach((track) => addOnce(track, outboundStreamRef.current!));
    }
    if (plan.sendAudienceProgram) {
      await ensureMixer();
      programDestinationRef.current?.stream.getAudioTracks().forEach((track) => addOnce(track, programDestinationRef.current!.stream));
    }
    if ((plan.receivePerformerStem || plan.receiveAudienceProgram) && !peer.getTransceivers().some((transceiver) => transceiver.receiver.track.kind === "audio")) {
      peer.addTransceiver("audio", { direction: "recvonly" });
    }
  }, [ensureMixer, session]);

  const createPeer = useCallback(async (targetId: string, targetRole: RoomRole, initiator: boolean) => {
    const existing = peerConnectionsRef.current.get(targetId);
    if (existing) return existing;
    const peer = new RTCPeerConnection(RTC_CONFIGURATION);
    peerConnectionsRef.current.set(targetId, peer);
    peerRolesRef.current.set(targetId, targetRole);
    peer.onicecandidate = (event) => { if (event.candidate) void sendSignal(targetId, "ice", event.candidate.toJSON()); };
    peer.onconnectionstatechange = () => {
      const connected = peer.connectionState === "connected";
      markConnection(targetId, connected);
      if (connected) writeLog(`${targetRole} camera link · locked`);
      if (["failed", "closed"].includes(peer.connectionState)) {
        peerConnectionsRef.current.delete(targetId);
        peerRolesRef.current.delete(targetId);
        removeRemoteVideo(targetId);
      }
    };
    peer.ontrack = (event) => {
      const stream = event.streams[0] ?? new MediaStream([event.track]);
      if (event.track.kind === "video") setRemoteVideoTrack(targetId, event.track);
      if (event.track.kind === "audio" && session?.role === "host" && targetRole === "performer" && stream.getAudioTracks().length) void attachStem(stream, targetId);
      if (event.track.kind === "audio" && session?.role === "audience" && targetRole === "host") {
        const audio = getAudioElement();
        audio.srcObject = new MediaStream([event.track]);
        setProgramReady(true);
        void audio.play().then(() => setMonitoring(true)).catch(() => undefined);
      }
    };
    if (initiator) {
      await configurePeerMedia(peer, targetRole);
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await sendSignal(targetId, "offer", offer);
    }
    return peer;
  }, [attachStem, configurePeerMedia, getAudioElement, markConnection, removeRemoteVideo, sendSignal, session?.role, setRemoteVideoTrack, writeLog]);

  const handleSignal = useCallback(async (signal: SignalMessage) => {
    if (!session) return;
    const sender = participantsRef.current.find((participant) => participant.id === signal.senderId);
    const senderRole = sender?.role ?? (session.role === "host" ? "performer" : "host");
    let peer = peerConnectionsRef.current.get(signal.senderId);
    const payload = JSON.parse(signal.payload) as RTCSessionDescriptionInit | RTCIceCandidateInit;
    if (signal.kind === "offer") {
      peer = await createPeer(signal.senderId, senderRole, false);
      await peer.setRemoteDescription(payload as RTCSessionDescriptionInit);
      await configurePeerMedia(peer, senderRole);
      await flushCandidates(signal.senderId, peer);
      const answer = await peer.createAnswer();
      await peer.setLocalDescription(answer);
      await sendSignal(signal.senderId, "answer", answer);
    } else if (signal.kind === "answer" && peer) {
      await peer.setRemoteDescription(payload as RTCSessionDescriptionInit);
      await flushCandidates(signal.senderId, peer);
    } else if (signal.kind === "ice") {
      if (peer?.remoteDescription) await peer.addIceCandidate(payload as RTCIceCandidateInit).catch(() => undefined);
      else pendingCandidatesRef.current.set(signal.senderId, [...(pendingCandidatesRef.current.get(signal.senderId) ?? []), payload as RTCIceCandidateInit]);
    } else if (signal.kind === "bye") {
      peer?.close(); peerConnectionsRef.current.delete(signal.senderId); removeRemoteVideo(signal.senderId); markConnection(signal.senderId, false);
    }
  }, [configurePeerMedia, createPeer, flushCandidates, markConnection, removeRemoteVideo, sendSignal, session]);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    let timer = 0;
    let heartbeatCounter = 0;
    const poll = async () => {
      const started = performance.now();
      try {
        const response = await fetch(`/api/room?room=${encodeURIComponent(session.roomId)}&participant=${encodeURIComponent(session.participantId)}&after=${lastSignalRef.current}`, { cache: "no-store" });
        const data = await response.json() as { room?: RoomState; participants?: Participant[]; signals?: SignalMessage[]; serverNow?: number; error?: string };
        if (!response.ok) throw new Error(data.error || "Room synchronization failed.");
        if (cancelled) return;
        const trip = performance.now() - started;
        if (typeof data.serverNow === "number") setClockOffset(data.serverNow + trip / 2 - Date.now());
        if (data.room) setRoom(data.room);
        if (data.participants) {
          participantsRef.current = data.participants;
          setParticipants(data.participants);
          const activeIds = new Set(data.participants.map((participant) => participant.id));
          for (const [participantId, peer] of peerConnectionsRef.current) {
            if (!activeIds.has(participantId)) {
              peer.close();
              peerConnectionsRef.current.delete(participantId);
              peerRolesRef.current.delete(participantId);
              removeRemoteVideo(participantId);
              markConnection(participantId, false);
            }
          }
        }
        for (const signal of data.signals ?? []) {
          lastSignalRef.current = Math.max(lastSignalRef.current, signal.id);
          await handleSignal(signal).catch((error) => writeLog(`Signal retry · ${error instanceof Error ? error.message : "unknown"}`));
        }
        for (const participant of data.participants ?? []) {
          if (participant.id !== session.participantId && !peerConnectionsRef.current.has(participant.id) && shouldInitiatePeer(session.participantId, participant.id)) {
            await createPeer(participant.id, participant.role, true);
          }
        }
        heartbeatCounter += 1;
        if (heartbeatCounter % 4 === 0) await roomPost({ action: "heartbeat", roomId: session.roomId, participantId: session.participantId, latencyMs: trip / 2, state: connectedCountRef.current ? "live" : "connecting" });
        for (const [participantId, peer] of peerConnectionsRef.current) {
          const reports = await peer.getStats().catch(() => null);
          reports?.forEach((report) => {
            if (report.type === "candidate-pair" && report.state === "succeeded" && typeof report.currentRoundTripTime === "number") {
              const node = stemNodesRef.current.get(participantId);
              if (node) { node.latencyMs = Math.round(report.currentRoundTripTime * 500); syncStemDelays(); }
            }
          });
        }
      } catch (error) {
        if (!cancelled) writeLog(error instanceof Error ? error.message : "Room poll failed");
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 1100);
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [createPeer, handleSignal, markConnection, removeRemoteVideo, session, syncStemDelays, writeLog]);

  useEffect(() => {
    if (room?.status !== "countdown") return;
    const timer = window.setInterval(() => setClockNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [room?.status]);

  const countdown = room?.status === "countdown" && room.startAt
    ? Math.max(0, Math.ceil((room.startAt - (clockNow + clockOffset)) / 1000))
    : room?.status === "live" ? 0 : null;

  useEffect(() => {
    if (!room?.startAt || room.status !== "countdown" || session?.role === "audience" || scheduledStartRef.current === room.startAt) return;
    const context = session?.role === "host" ? audioContextRef.current : outboundContextRef.current;
    if (!context) return;
    scheduledStartRef.current = room.startAt;
    const downbeatTime = context.currentTime + Math.max(0.05, (room.startAt - (clockNow + clockOffset)) / 1000);
    for (let count = 4; count >= 0; count -= 1) {
      const cueTime = downbeatTime - count;
      if (cueTime <= context.currentTime + 0.02) continue;
      const oscillator = context.createOscillator();
      const cueGain = context.createGain();
      oscillator.frequency.value = count === 0 ? 1046.5 : 659.25;
      cueGain.gain.setValueAtTime(0.0001, cueTime);
      cueGain.gain.exponentialRampToValueAtTime(count === 0 ? 0.22 : 0.12, cueTime + 0.008);
      cueGain.gain.exponentialRampToValueAtTime(0.0001, cueTime + 0.09);
      oscillator.connect(cueGain).connect(context.destination);
      oscillator.start(cueTime); oscillator.stop(cueTime + 0.1);
    }
    writeLog("Audible count-in scheduled on the shared clock");
  }, [clockNow, clockOffset, room?.startAt, room?.status, session?.role, writeLog]);

  useEffect(() => {
    if (!session || session.role !== "host" || room?.status !== "countdown" || countdown !== 0 || advancingCountdownRef.current) return;
    advancingCountdownRef.current = true;
    void roomPost({ action: "control", roomId: session.roomId, participantId: session.participantId, status: "live", startAt: room.startAt }).finally(() => { advancingCountdownRef.current = false; });
  }, [countdown, room?.startAt, room?.status, session]);

  useEffect(() => () => {
    peerConnectionsRef.current.forEach((peer) => peer.close());
    localStreamRef.current?.getTracks().forEach((track) => track.stop());
    outboundStreamRef.current?.getTracks().forEach((track) => track.stop());
    void audioContextRef.current?.close();
    void outboundContextRef.current?.close();
    void syntheticMediaContextRef.current?.close();
    window.clearInterval(syntheticFrameTimerRef.current);
    audioRef.current?.pause();
  }, []);

  const prepareLocalMedia = useCallback(async (needsAudio: boolean) => {
    if (localStreamRef.current) return localStreamRef.current;
    const useSyntheticMedia = window.location.hostname === "localhost" && new URLSearchParams(window.location.search).get("media") === "synthetic";
    if (useSyntheticMedia) {
      const canvas = document.createElement("canvas");
      canvas.width = 960;
      canvas.height = 540;
      const context2d = canvas.getContext("2d");
      let frame = 0;
      const drawFrame = () => {
        if (!context2d) return;
        const hue = (frame * 2 + (needsAudio ? 82 : 256)) % 360;
        context2d.fillStyle = "#080909";
        context2d.fillRect(0, 0, canvas.width, canvas.height);
        context2d.strokeStyle = `hsl(${hue} 92% 62%)`;
        context2d.lineWidth = 4;
        context2d.beginPath();
        for (let x = 0; x <= canvas.width; x += 8) {
          const y = canvas.height / 2 + Math.sin(x / 64 + frame / 9) * 74;
          if (x === 0) context2d.moveTo(x, y); else context2d.lineTo(x, y);
        }
        context2d.stroke();
        context2d.fillStyle = "#f1f3ec";
        context2d.font = "700 52px sans-serif";
        context2d.fillText(needsAudio ? "PERFORMER TEST FEED" : "AUDIENCE TEST FEED", 54, 92);
        frame += 1;
      };
      drawFrame();
      syntheticFrameTimerRef.current = window.setInterval(drawFrame, 80);
      const stream = canvas.captureStream(12);
      if (needsAudio) {
        const audioContext = new AudioContext({ sampleRate: 48000 });
        await audioContext.resume();
        const oscillator = audioContext.createOscillator();
        const level = audioContext.createGain();
        const destination = audioContext.createMediaStreamDestination();
        oscillator.frequency.value = 220;
        level.gain.value = 0.002;
        oscillator.connect(level).connect(destination);
        oscillator.start();
        destination.stream.getAudioTracks().forEach((track) => stream.addTrack(track));
        syntheticMediaContextRef.current = audioContext;
      }
      localStreamRef.current = stream;
      setLocalMedia(stream);
      setCameraEnabled(true);
      setMicrophoneEnabled(needsAudio);
      return stream;
    }
    const audio = needsAudio ? { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } : false;
    const video = { width: { ideal: 960 }, height: { ideal: 540 }, frameRate: { ideal: 24, max: 30 }, facingMode: "user" };
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio, video });
    } catch (cameraError) {
      if (!needsAudio) {
        stream = new MediaStream();
        onNotice("Camera permission was not granted. You can still join and see everyone else.");
      } else {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
          onNotice("Camera unavailable; your clean microphone stem is still connected.");
        } catch {
          throw new Error(cameraError instanceof Error ? `Camera/microphone access failed: ${cameraError.message}` : "Microphone permission is required to perform.");
        }
      }
    }
    localStreamRef.current = stream;
    setLocalMedia(stream);
    setCameraEnabled(Boolean(stream.getVideoTracks()[0]?.enabled));
    setMicrophoneEnabled(Boolean(stream.getAudioTracks()[0]?.enabled));
    return stream;
  }, [onNotice]);

  const prepareCleanOutbound = useCallback(async () => {
    if (outboundStreamRef.current) return outboundStreamRef.current;
    const raw = await prepareLocalMedia(true);
    if (!raw.getAudioTracks().length) throw new Error("A microphone is required for a performer stem.");
    const context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    await context.resume();
    await context.audioWorklet.addModule("/adaptive-gate.js").catch(() => undefined);
    const source = context.createMediaStreamSource(raw);
    const highPass = context.createBiquadFilter(); highPass.type = "highpass"; highPass.frequency.value = 74; highPass.Q.value = 0.72;
    let adaptiveGate: AudioNode;
    try { adaptiveGate = new AudioWorkletNode(context, "swarlink-adaptive-gate"); } catch { adaptiveGate = context.createGain(); }
    const compressor = context.createDynamicsCompressor(); compressor.threshold.value = -25; compressor.knee.value = 14; compressor.ratio.value = 3.4; compressor.attack.value = 0.006; compressor.release.value = 0.13;
    const destination = context.createMediaStreamDestination();
    source.connect(highPass).connect(adaptiveGate).connect(compressor).connect(destination);
    outboundContextRef.current = context;
    outboundStreamRef.current = destination.stream;
    return destination.stream;
  }, [prepareLocalMedia]);

  const createRoom = useCallback(async () => {
    if (!name.trim()) { onNotice("Enter a performer name first."); return; }
    setBusy(true);
    try {
      const stream = await prepareLocalMedia(true);
      if (!stream.getAudioTracks().length) throw new Error("A microphone is required to conduct a live room.");
      const created = await roomPost({ action: "create", name, title: "Swarlink Live Concert" }) as unknown as RoomSession;
      const next = { ...created, role: "host" as const };
      setSession(next);
      await ensureMixer();
      await attachStem(stream, next.participantId);
      window.history.replaceState({}, "", `${window.location.pathname}?room=${created.roomId}`);
      writeLog(`Room ${created.roomId} created · conductor stem armed`);
      onNotice(`Live room ${created.roomId} created. Share the link with performers and audience.`);
    } catch (error) { onNotice(error instanceof Error ? error.message : "Could not create the room."); }
    finally { setBusy(false); }
  }, [attachStem, ensureMixer, name, onNotice, prepareLocalMedia, writeLog]);

  const joinRoom = useCallback(async () => {
    const roomId = shortCode(joinCode);
    if (!roomId || !name.trim()) { onNotice("Enter your name and the six-character room code."); return; }
    setBusy(true);
    try {
      if (joinRole === "performer") await prepareCleanOutbound();
      else await prepareLocalMedia(false);
      const joined = await roomPost({ action: "join", roomId, name, role: joinRole }) as unknown as RoomSession;
      setSession({ ...joined, role: joinRole });
      window.history.replaceState({}, "", `${window.location.pathname}?room=${roomId}`);
      writeLog(`${name} joined as ${joinRole}`);
      onNotice(joinRole === "performer" ? "Microphone armed. Waiting for the conductor’s shared downbeat." : "Audience seat connected. The concert return will appear here.");
    } catch (error) { onNotice(error instanceof Error ? error.message : "Could not join the room."); }
    finally { setBusy(false); }
  }, [joinCode, joinRole, name, onNotice, prepareCleanOutbound, prepareLocalMedia, writeLog]);

  const copyInvite = useCallback(async () => {
    if (!session) return;
    const link = `${window.location.origin}${window.location.pathname}?room=${session.roomId}`;
    try {
      await navigator.clipboard.writeText(link);
      onNotice("Meeting link copied. Open it on each performer or audience device.");
    } catch {
      const field = document.createElement("textarea");
      field.value = link;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      onNotice(copied ? "Meeting link copied. Open it on each performer or audience device." : `Share this room: ${link}`);
    }
  }, [onNotice, session]);

  const leaveRoom = useCallback(async () => {
    if (session) await Promise.all([...peerConnectionsRef.current.keys()].map((targetId) => sendSignal(targetId, "bye", {}).catch(() => undefined)));
    peerConnectionsRef.current.forEach((peer) => peer.close());
    peerConnectionsRef.current.clear(); peerRolesRef.current.clear(); pendingCandidatesRef.current.clear(); stemNodesRef.current.clear();
    localStreamRef.current?.getTracks().forEach((track) => track.stop()); localStreamRef.current = null;
    outboundStreamRef.current?.getTracks().forEach((track) => track.stop()); outboundStreamRef.current = null;
    await audioContextRef.current?.close(); audioContextRef.current = null; masterInputRef.current = null; programDestinationRef.current = null;
    await outboundContextRef.current?.close(); outboundContextRef.current = null;
    await syntheticMediaContextRef.current?.close(); syntheticMediaContextRef.current = null;
    window.clearInterval(syntheticFrameTimerRef.current); syntheticFrameTimerRef.current = 0;
    audioRef.current?.pause(); audioRef.current = null;
    setSession(null); setRoom(null); setParticipants([]); participantsRef.current = []; setConnectedIds([]); connectedCountRef.current = 0; setProgramReady(false); setMonitoring(false);
    setLocalMedia(null); setRemoteVideos({}); setCameraEnabled(true); setMicrophoneEnabled(true); lastSignalRef.current = 0;
    window.history.replaceState({}, "", window.location.pathname);
    onNotice("You left the concert room. Local microphone and peer connections are closed.");
  }, [onNotice, sendSignal, session]);

  const toggleCamera = useCallback(() => {
    const track = localStreamRef.current?.getVideoTracks()[0];
    if (!track) { onNotice("No camera is available on this device. You can still see the room."); return; }
    track.enabled = !track.enabled;
    setCameraEnabled(track.enabled);
    onNotice(track.enabled ? "Camera is live to everyone in the room." : "Camera paused. Your audio connection remains active.");
  }, [onNotice]);

  const toggleMicrophone = useCallback(() => {
    const track = localStreamRef.current?.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    setMicrophoneEnabled(track.enabled);
    onNotice(track.enabled ? "Performance microphone live." : "Performance microphone muted.");
  }, [onNotice]);

  const controlRoom = useCallback(async (status: RoomState["status"]) => {
    if (!session) return;
    try {
      const result = await roomPost({ action: "control", roomId: session.roomId, participantId: session.participantId, status });
      writeLog(status === "countdown" ? "Shared downbeat scheduled · T−6 seconds" : `Room ${status}`);
      if (status === "countdown") onNotice(`Shared clock armed for ${new Date(Number(result.startAt)).toLocaleTimeString()}. Every device sees the same downbeat.`);
    } catch (error) { onNotice(error instanceof Error ? error.message : "Room control failed."); }
  }, [onNotice, session, writeLog]);

  const listenProgram = useCallback(async () => {
    if (session?.role === "host") {
      await ensureMixer();
      if (programDestinationRef.current) getAudioElement().srcObject = programDestinationRef.current.stream;
      setProgramReady(true);
    }
    const audio = getAudioElement();
    audio.muted = false;
    await audio.play();
    setMonitoring(true);
    onNotice("Audience program monitor active · use headphones near an open microphone.");
  }, [ensureMixer, getAudioElement, onNotice, session?.role]);

  const shareLink = useMemo(() => session ? `${typeof window === "undefined" ? "" : window.location.origin}${typeof window === "undefined" ? "" : window.location.pathname}?room=${session.roomId}` : "", [session]);
  const performerCount = participants.filter((participant) => participant.role === "performer" || participant.role === "host").length;
  const audienceCount = participants.filter((participant) => participant.role === "audience").length;
  const visibleParticipants = useMemo(() => {
    if (!session) return participants;
    if (participants.some((participant) => participant.id === session.participantId)) return participants;
    return [{ id: session.participantId, name, role: session.role, state: "connecting", latencyMs: 0, lastSeenAt: 0 }, ...participants];
  }, [name, participants, session]);

  return (
    <section className={`live-room ${session ? "room-connected" : ""}`}>
      <div className="room-intro"><div><p className="section-label">[ MULTI-DEVICE CONCERT LINK ]</p><h2>One URL.<br /><em>One room.</em></h2></div><p>Create a room on the conductor device, then send the exact link to the band. Every device joins one shared camera grid; performer microphones travel as isolated WebRTC stems while the audience receives the synchronized stereo program.</p></div>
      {!session ? <div className="room-entry">
        <label><span>YOUR NAME</span><input value={name} maxLength={40} onChange={(event) => setName(event.target.value)} placeholder="Maya" /></label>
        <div className="room-create"><small>CONDUCTOR</small><strong>Start a new concert</strong><button onClick={createRoom} disabled={busy}>{busy ? "ARMING…" : "CREATE LIVE ROOM ↗"}</button></div>
        <div className="room-divider"><span>OR</span></div>
        <div className="room-join"><label><span>ROOM CODE</span><input value={joinCode} maxLength={6} onChange={(event) => setJoinCode(shortCode(event.target.value))} placeholder="A7K2Q9" /></label><div className="role-switch"><button className={joinRole === "performer" ? "active" : ""} onClick={() => setJoinRole("performer")}>PERFORMER</button><button className={joinRole === "audience" ? "active" : ""} onClick={() => setJoinRole("audience")}>AUDIENCE</button></div><button onClick={joinRoom} disabled={busy}>{busy ? "CONNECTING…" : "JOIN ROOM ↗"}</button></div>
      </div> : <div className="room-session">
        <div className="room-command"><div><span>ROOM / {session.roomId}</span><strong>{room?.title ?? "Connecting to room…"}</strong><small>{shareLink}</small></div><div><button onClick={copyInvite}>COPY MEETING LINK</button><button onClick={leaveRoom}>LEAVE ROOM</button></div></div>
        <div className="room-status-grid"><div className={`downbeat ${room?.status ?? "lobby"}`}><small>SHARED DOWNBEAT</small><strong>{room?.status === "countdown" ? countdown : room?.status === "live" ? "SING" : room?.status === "ended" ? "END" : "ARMED"}</strong><span>{room?.status === "live" ? "All performers are now on the same musical clock" : "The audience path deliberately trails by 420 ms"}</span></div><div><small>PERFORMERS</small><strong>{String(performerCount).padStart(2, "0")}</strong><span>{connectedIds.length} visual peer links locked</span></div><div><small>AUDIENCE</small><strong>{String(audienceCount).padStart(2, "0")}</strong><span>{programReady ? "program available" : "return standing by"}</span></div><div><small>ROLE</small><strong>{session.role.toUpperCase()}</strong><span>camera mesh + 48 kHz audio</span></div></div>
        <div className="room-video-stage">
          <div className="room-video-head"><div><span>LIVE ROOM / VISUAL PRESENCE</span><strong>{visibleParticipants.length} {visibleParticipants.length === 1 ? "DEVICE" : "DEVICES"} IN THE SAME SPACE</strong></div><small>VIDEO IS REAL-TIME · AUDIENCE AUDIO IS SYNC-BUFFERED</small></div>
          <div className="room-video-grid">{visibleParticipants.map((participant) => {
            const isLocal = participant.id === session.participantId;
            return <RoomVideoTile key={participant.id} participant={participant} stream={isLocal ? localMedia ?? undefined : remoteVideos[participant.id]} isLocal={isLocal} connected={isLocal || connectedIds.includes(participant.id)} />;
          })}</div>
        </div>
        <div className="room-roster">{participants.map((participant) => <article key={participant.id}><span className={`presence ${connectedIds.includes(participant.id) || participant.id === session.participantId ? "online" : ""}`} /><div><strong>{participant.name}{participant.id === session.participantId ? " · YOU" : ""}</strong><small>{participant.role} / {participant.state}</small></div><b>{participant.latencyMs || "—"} ms</b></article>)}</div>
        <div className="room-controls">{session.role === "host" && room?.status === "lobby" && <button className="room-primary" onClick={() => controlRoom("countdown")}>START 6-SECOND DOWNBEAT</button>}{session.role === "host" && room?.status === "countdown" && <button className="room-primary" disabled>DOWNBEAT ARMED · {countdown}</button>}{session.role === "host" && room?.status === "live" && <button className="room-stop" onClick={() => controlRoom("ended")}>END CONCERT</button>}<button className={cameraEnabled ? "room-media-on" : "room-media-off"} onClick={toggleCamera}>{cameraEnabled ? "CAMERA ON" : "CAMERA OFF"}</button>{session.role !== "audience" && <button className={microphoneEnabled ? "room-media-on" : "room-media-off"} onClick={toggleMicrophone}>{microphoneEnabled ? "MIC LIVE" : "MIC MUTED"}</button>}{(session.role === "host" || session.role === "audience") && <button onClick={listenProgram} disabled={session.role === "audience" && !programReady}>{monitoring ? "AUDIENCE MONITOR ON" : "LISTEN TO LIVE PROGRAM"}</button>}</div>
        <div className="room-engine-log"><span>LIVE ENGINE LOG</span>{engineLog.map((entry) => <code key={entry}>{entry}</code>)}</div>
      </div>}
      <div className="room-truth"><span>VIDEO NOW / HARMONY LATER</span><p>The camera grid stays conversational and immediate so bandmates can see cues and expressions. Music takes a different path: every device follows one future downbeat, faster stems are held to the slowest admitted path, and the audience receives one intentionally delayed enhanced mix. Seeing one another never contaminates the concert master.</p></div>
    </section>
  );
}
