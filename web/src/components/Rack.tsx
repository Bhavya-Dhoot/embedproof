"use client";

import type { ReactNode } from "react";
import { cx } from "./ui";

/**
 * Rack focus: the active panel comes forward and sharpens; the others drop back, shrink, dim and
 * collapse to a one-line summary. A dropped-back panel that can be revisited is a button.
 */
export function Rack({
  n,
  title,
  summary,
  active,
  done,
  onSelect,
  children,
  className,
  tone,
  id,
}: {
  n: number | string;
  title: string;
  summary?: ReactNode;
  active: boolean;
  /** step finished (shows a check) */
  done?: boolean;
  /** provide to make a dropped-back panel selectable */
  onSelect?: () => void;
  children: ReactNode;
  className?: string;
  tone?: "green" | "amber" | "red";
  id?: string;
}) {
  const selectable = !active && !!onSelect;
  const head = (
    <>
      <span
        className={cx(
          "num grid h-9 w-9 shrink-0 place-items-center rounded-full border text-base",
          done ? "border-[var(--color-teal)] bg-[var(--color-teal)] text-[var(--color-ink)]" : "border-white/50 bg-white/10",
          tone === "red" && "!border-[var(--color-rose)] !bg-[var(--color-rose)] !text-[var(--color-ink)]",
          tone === "amber" && "!border-[var(--color-amber)] !bg-[var(--color-amber)] !text-[var(--color-ink)]",
        )}
        aria-hidden
      >
        {done ? "✓" : n}
      </span>
      <span className="min-w-0 flex-1 text-left">
        <span className="block text-lg font-bold leading-tight sm:text-xl">{title}</span>
        {!active && summary && <span className="mt-0.5 block truncate text-sm text-[var(--color-mute)]">{summary}</span>}
      </span>
      {selectable && <span className="text-xs font-bold uppercase tracking-wider text-white/80">Open</span>}
    </>
  );

  return (
    <section
      id={id}
      className={cx("glass rack", className)}
      data-focus={active ? "front" : "back"}
      data-selectable={selectable}
      aria-label={title}
    >
      <span className="sheen" aria-hidden />
      <div className="rack-inner relative z-[1] p-4 sm:p-6">
        {selectable ? (
          <button type="button" className="flex w-full items-center gap-3 rounded-xl" onClick={onSelect} aria-expanded={false}>
            {head}
          </button>
        ) : (
          <div className="flex items-center gap-3">{head}</div>
        )}
        <div className="rack-collapse">
          {/* `inert` keeps keyboard focus out of the collapsed body */}
          <div className="rack-body" inert={!active}>
            <div className="pt-4">{children}</div>
          </div>
        </div>
      </div>
    </section>
  );
}
