export interface Seg {
  text: string;
  changed: boolean;
}

export interface DiffRow {
  kind: "changed" | "added" | "removed";
  /** 1-based line number in the original / edited text */
  aLine?: number;
  bLine?: number;
  a?: Seg[];
  b?: Seg[];
}

function charSegs(a: string, b: string): [Seg[], Seg[]] {
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  const mk = (t: string): Seg[] =>
    [
      { text: t.slice(0, p), changed: false },
      { text: t.slice(p, t.length - s), changed: true },
      { text: t.slice(t.length - s), changed: false },
    ].filter((x) => x.text.length > 0);
  return [mk(a), mk(b)];
}

/** Line diff (LCS) with character-level highlighting inside changed lines. Only differing rows are returned. */
export function diffText(original: string, edited: string): DiffRow[] {
  const A = original.split("\n");
  const B = edited.split("\n");
  const n = A.length;
  const m = B.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);

  type Op = { t: "same" | "del" | "add"; i?: number; j?: number };
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) ops.push({ t: "same", i: i++, j: j++ });
    else if (dp[i + 1][j] >= dp[i][j + 1]) ops.push({ t: "del", i: i++ });
    else ops.push({ t: "add", j: j++ });
  }
  while (i < n) ops.push({ t: "del", i: i++ });
  while (j < m) ops.push({ t: "add", j: j++ });

  const rows: DiffRow[] = [];
  for (let k = 0; k < ops.length; ) {
    if (ops[k].t === "same") {
      k++;
      continue;
    }
    const dels: number[] = [];
    const adds: number[] = [];
    while (k < ops.length && ops[k].t !== "same") {
      const o = ops[k++];
      if (o.t === "del") dels.push(o.i!);
      else adds.push(o.j!);
    }
    const pairs = Math.min(dels.length, adds.length);
    for (let q = 0; q < pairs; q++) {
      const [sa, sb] = charSegs(A[dels[q]], B[adds[q]]);
      rows.push({ kind: "changed", aLine: dels[q] + 1, bLine: adds[q] + 1, a: sa, b: sb });
    }
    for (let q = pairs; q < dels.length; q++) rows.push({ kind: "removed", aLine: dels[q] + 1, a: [{ text: A[dels[q]], changed: true }] });
    for (let q = pairs; q < adds.length; q++) rows.push({ kind: "added", bLine: adds[q] + 1, b: [{ text: B[adds[q]], changed: true }] });
  }
  return rows;
}
