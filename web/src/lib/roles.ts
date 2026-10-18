import type { Roles } from "./chainReads";

/** The five views of PRD FR-F1. Importer is the default: any account without another role. */
export type ViewRole = "operator" | "verifier" | "importer" | "regulator" | "admin";

export const ROLE_LABEL: Record<ViewRole, string> = {
  operator: "Exporter",
  verifier: "Verifier",
  importer: "Importer",
  regulator: "Regulator",
  admin: "Admin",
};

/** The role an account is acting in, highest authority first. */
export function primaryRole(r: Roles): ViewRole {
  if (r.admin) return "admin";
  if (r.verifier) return "verifier";
  if (r.operatorOf.length > 0) return "operator";
  if (r.regulator) return "regulator";
  return "importer";
}

/** Whether the account may use the write actions of a role's page. */
export function holdsRole(r: Roles, need: ViewRole): boolean {
  switch (need) {
    case "admin":
      return r.admin;
    case "verifier":
      return r.verifier;
    case "operator":
      return r.operatorOf.length > 0;
    case "regulator":
      return r.regulator;
    case "importer":
      return !r.admin && !r.verifier && r.operatorOf.length === 0 && !r.regulator;
  }
}

/** "You're using Verifier; this page is for Exporter." or null when the account fits the page (or roles are not read yet). */
export function roleMismatchMessage(r: Roles, need: ViewRole): string | null {
  if (!r.known || holdsRole(r, need)) return null;
  return `You're using ${ROLE_LABEL[primaryRole(r)]}; this page is for ${ROLE_LABEL[need]}.`;
}
