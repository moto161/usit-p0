import { cycleEntry } from "./a";
export function cycleOther(): string { return "cycle"; }
export function cycleBack(): string { return cycleEntry(); }
