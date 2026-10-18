import { BaseError, ContractFunctionRevertedError, UserRejectedRequestError } from "viem";
import { formatTonnes } from "./report";

export interface DecodedError {
  /** custom-error name (e.g. DoubleClaimBlocked), "Error" for revert strings, or a short client-side tag */
  name: string;
  args: readonly unknown[];
  /** human sentence */
  message: string;
  /** raw technical text */
  raw: string;
  userRejected?: boolean;
}

/** Decode a thrown viem error into custom-error name + args (viem BaseError.walk -> ContractFunctionRevertedError). */
export function decodeError(err: unknown): DecodedError {
  if (err instanceof BaseError) {
    const rejected = err.walk((e) => e instanceof UserRejectedRequestError);
    if (rejected) {
      return { name: "UserRejected", args: [], message: "Signature request was rejected in the wallet.", raw: err.shortMessage, userRejected: true };
    }
    const revert = err.walk((e) => e instanceof ContractFunctionRevertedError);
    if (revert instanceof ContractFunctionRevertedError) {
      const name = revert.data?.errorName ?? (revert.reason ? "Error" : "Revert");
      const args = (revert.data?.args ?? []) as readonly unknown[];
      return { name, args, message: friendly(name, args, revert.reason), raw: revert.shortMessage };
    }
    return { name: "RpcError", args: [], message: err.shortMessage, raw: err.message };
  }
  const msg = err instanceof Error ? err.message : String(err);
  return { name: "Error", args: [], message: msg, raw: msg };
}

const n = (x: unknown) => (typeof x === "bigint" || typeof x === "number" ? formatTonnes(x) : String(x));

export function friendly(name: string, a: readonly unknown[], reason?: string): string {
  switch (name) {
    case "DoubleClaimBlocked":
      return `Double claim blocked: you asked for ${n(a[1])} t but only ${n(a[2])} t of batch #${n(a[0])} remain unallocated.`;
    case "ConsignmentReused":
      return "That consignment id is already used. Every consignment maps to exactly one allocation.";
    case "BatchNotAttested":
      return `Batch #${n(a[0])} is not Attested, so its tonnes cannot be allocated yet.`;
    case "NotBatchOperator":
      return `Only the installation's operator can allocate batch #${n(a[0])}.`;
    case "NotConsignee":
      return "Only the importer named in the allocation can claim it.";
    case "AlreadyClaimed":
      return "This allocation has already been claimed.";
    case "UnknownConsignment":
      return "No allocation exists for that consignment id.";
    case "ZeroTonnes":
      return "Tonnes must be greater than zero.";
    case "ZeroImporter":
      return "Pick an importer address.";
    case "EnforcedPause":
      return "The system is paused by the admin; writes are blocked.";
    case "ExpectedPause":
      return "The system is not paused.";
    case "AccessControlUnauthorizedAccount":
    case "MissingRole":
      return "This account does not hold the role required for that action.";
    case "AttestorRevoked":
      return `The attestor that signed batch #${n(a[0])} no longer holds the ATTESTOR role, so its tonnes cannot be allocated.`;
    case "VerifierConflict":
      return `Conflict of interest: the verifier cannot be the attestor or the operator of batch #${n(a[0])}.`;
    case "ZeroReasonHash":
      return "A written reason is required: its hash cannot be empty.";
    case "NotTimelock":
      return "This change is reserved for the 24-hour timelock.";
    case "NotPending":
      return `Batch #${n(a[0])} is not waiting for a verifier.`;
    case "DuplicatePeriod":
      return `That installation already has a batch for this period (#${n(a[2])}).`;
    case "UnknownBatch":
      return `Batch #${n(a[0])} does not exist.`;
    case "UnknownInstallation":
      return "That installation is not registered.";
    case "InstallationExists":
      return "That installation id is already registered.";
    case "ModelNotApproved":
      return "That model version is not approved on-chain.";
    case "Error":
      return reason ?? "Transaction reverted.";
    default:
      return `Reverted: ${name}`;
  }
}

export const isDoubleClaim = (e: DecodedError | null | undefined) => e?.name === "DoubleClaimBlocked";

export function formatArgs(args: readonly unknown[]): string {
  return args.map((x) => (typeof x === "bigint" ? x.toString() : typeof x === "string" ? x : JSON.stringify(x))).join(", ");
}
