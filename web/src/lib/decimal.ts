/**
 * Exact decimal rounding (docs/INTERFACES.md section 9G).
 *
 * seeDeclared = round_half_up(decimal(seeTco2PerT) x 10^4), computed on the SHORTEST ROUND-TRIP decimal
 * string of the number (what Python's Decimal(repr(x)) sees), never on the binary double:
 * Math.round(0.40045 * 1e4) is 4004 because the double is slightly below 4004.5, while the decimal
 * 0.40045 x 10^4 = 4004.5 rounds half up to 4005.
 */
export function roundHalfUpScaled(x: number, dp: number): bigint {
  if (!Number.isFinite(x) || x < 0) throw new Error("expected a finite, non-negative number");
  const s = x.toString(); // shortest round-trip digits; may be in exponent form (1e-7, 1.5e-7, 1e+21)
  const m = /^(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/.exec(s);
  if (!m) throw new Error(`cannot parse decimal string ${s}`);
  const frac = m[2] ?? "";
  const exp = Number(m[3] ?? 0);
  const digits = BigInt(m[1] + frac); // value = digits x 10^-(frac.length - exp)
  const shift = dp - (frac.length - exp); // we want digits x 10^shift, rounded half up to an integer
  if (shift >= 0) return digits * 10n ** BigInt(shift);
  const div = 10n ** BigInt(-shift);
  const q = digits / div;
  return (digits % div) * 2n >= div ? q + 1n : q;
}

/** tCO2 per tonne -> on-chain uint32 (x 1e4, exact decimal half-up). */
export function seeToUint(see: number): number {
  return Number(roundHalfUpScaled(see, 4));
}
