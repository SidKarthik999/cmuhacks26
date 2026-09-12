// Cross-language boundary probe: encodes PCM samples using Person A's
// *real* platform-side encoder (not a re-implementation), so the Python-side
// contract test in modes/integration/tests/test_boundary_contract.py can
// prove the two languages actually agree on the wire format described in
// docs/integration-contracts.md, not just that each side's own tests pass.
//
// Reads {"samples": number[]} as JSON from stdin, writes
// {"samples": number[], "base64": string} to stdout.
//
// Run standalone (uses a small built-in fixture if stdin is empty):
//   npx tsx modes/integration/encode_probe.mjs
import { float32ToBase64 } from "../../platform/src/audio/AudioTrackTap.ts";

const FALLBACK_SAMPLES = [0, 0.25, -0.5, 0.999969482421875, -1, 0.0001234];

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

const raw = await readStdin();
let inputSamples: number[] = FALLBACK_SAMPLES;
if (raw.trim()) {
  const parsed = JSON.parse(raw) as { samples: number[] };
  inputSamples = parsed.samples;
}

const samples = new Float32Array(inputSamples);
process.stdout.write(
  JSON.stringify({
    samples: Array.from(samples),
    base64: float32ToBase64(samples),
  }),
);
