import { fallbackTarget } from "@route/api";
import { priorityTarget } from "@route/special/api";
import { conditionalTarget } from "conditional-lib";
import { reexported } from "./barrel";
import { named as shadowed } from "./a";

export function exerciseExtras(shadowed: () => string): string {
  const a = fallbackTarget();
  const b = priorityTarget();
  const c = reexported();
  const d = conditionalTarget();
  const e = shadowed();
  return a + b + c + d + e;
}

export function callImportedShadowed(): string {
  return shadowed();
}

export function dynamicExtra(object: Record<string, () => string>, key: string): string {
  return object[key]();
}
