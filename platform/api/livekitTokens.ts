/**
 * LiveKit JWT minting. Uses LIVEKIT_API_KEY / LIVEKIT_API_SECRET when set.
 * Without credentials, returns a mock token for local/dev UI flows.
 */
import { createHmac } from "node:crypto";

export interface TokenRequest {
  room_id: string;
  participant_id: string;
  display_name: string;
}

function b64url(input: Buffer | string): string {
  const buf = typeof input === "string" ? Buffer.from(input) : input;
  return buf
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

export function mintLiveKitToken(req: TokenRequest): {
  token: string;
  url: string;
  mock: boolean;
} {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const url = process.env.LIVEKIT_URL ?? "ws://localhost:7880";

  if (!apiKey || !apiSecret) {
    return {
      token: `mock.${b64url(JSON.stringify(req))}.dev`,
      url,
      mock: true,
    };
  }

  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const now = Math.floor(Date.now() / 1000);
  const payload = b64url(
    JSON.stringify({
      iss: apiKey,
      sub: req.participant_id,
      name: req.display_name,
      nbf: now - 10,
      exp: now + 60 * 60,
      video: {
        roomJoin: true,
        room: req.room_id,
        canPublish: true,
        canSubscribe: true,
        canPublishData: true,
      },
    }),
  );
  const data = `${header}.${payload}`;
  const sig = createHmac("sha256", apiSecret).update(data).digest();
  return { token: `${data}.${b64url(sig)}`, url, mock: false };
}
