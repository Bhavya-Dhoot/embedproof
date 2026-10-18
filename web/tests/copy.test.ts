import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { bytes32ToPrintable, plain, stringToBytes32 } from "../src/lib/report";

const src = fileURLToPath(new URL("../src", import.meta.url));
const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (n === "generated") return [];
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|css)$/.test(n) ? [p] : [];
  });

describe("copy rules", () => {
  it("no em dash, en dash or minus sign anywhere in src (visible copy and comments)", () => {
    const bad: string[] = [];
    for (const f of walk(src)) {
      readFileSync(f, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/[—–−]/.test(line)) bad.push(`${f.slice(src.length + 1)}:${i + 1}`);
        });
    }
    expect(bad).toEqual([]);
  });

  it("plain() turns server-provided dashes into plain punctuation", () => {
    expect(plain("range 0.43–0.96 plausible — inside it")).toBe("range 0.43 to 0.96 plausible, inside it");
    expect(plain("a – b")).toBe("a, b");
    expect(plain("0.43-0.96 stays")).toBe("0.43-0.96 stays");
  });

  it("consignment ids decode to their ASCII string, with hex only as a fallback", () => {
    expect(bytes32ToPrintable(stringToBytes32("EP-1-A-d21630"))).toBe("EP-1-A-d21630");
    expect(bytes32ToPrintable(("0x" + "ff".repeat(32)) as `0x${string}`)).toBeNull();
    expect(bytes32ToPrintable(("0x" + "00".repeat(32)) as `0x${string}`)).toBeNull();
  });
});
