import type { Hex } from "viem";
import { bytesToHex, hexToBytes } from "viem";

// CIDv1, codec raw (0x55), multihash sha2-256 (0x12, 0x20), base32 lower, multibase prefix "b".
const PREFIX = Uint8Array.from([0x01, 0x55, 0x12, 0x20]);
const ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

export function base32Lower(bytes: Uint8Array): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += ALPHABET[(value << (5 - bits)) & 31];
  return out;
}

export function base32LowerDecode(s: string): Uint8Array {
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const ch of s) {
    const idx = ALPHABET.indexOf(ch);
    if (idx < 0) throw new Error("invalid base32 character");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 255);
      bits -= 8;
    }
  }
  return Uint8Array.from(out);
}

/** Rebuild the CIDv1 string from the on-chain `cidDigest` (the sha2-256 digest of the bundle bytes). */
export function cidFromDigest(digest: Hex): string {
  const d = hexToBytes(digest);
  if (d.length !== 32) throw new Error("cidDigest must be 32 bytes");
  const all = new Uint8Array(PREFIX.length + 32);
  all.set(PREFIX, 0);
  all.set(d, PREFIX.length);
  return "b" + base32Lower(all);
}

export function digestFromCid(cid: string): Hex {
  if (!cid.startsWith("b")) throw new Error("expected base32 CIDv1 (b...)");
  const raw = base32LowerDecode(cid.slice(1));
  if (raw.length !== 36 || !PREFIX.every((v, i) => raw[i] === v)) throw new Error("not a CIDv1 raw/sha2-256 CID");
  return bytesToHex(raw.slice(4));
}
