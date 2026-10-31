import { named as renamed } from "./a";
import defaultFn from "./b";
import * as namespaceMod from "./modules/namespace";
import { reexported } from "./barrel";
import { same as leftSame } from "./modules/left";
import { same as rightSame } from "./modules/right";
import { aliased } from "@app/aliased";
import { cycleEntry } from "./cycle/a";
import { externalCall } from "external-lib";
import { missingCall } from "./missing";
import { localSame as importedLocalSame } from "./modules/local";
import { typeOnlyCall } from "./types-only";
import { overloaded } from "./modules/overloads";

function localSame(): string { return "local"; }

export function run(key: string): string {
  const a = renamed();
  const b = defaultFn();
  const c = namespaceMod.namespaceCall();
  const d = reexported();
  const e = leftSame(3);
  const f = rightSame(4);
  const g = aliased();
  const h = cycleEntry();
  const i = externalCall();
  const j = missingCall();
  const k = importedLocalSame();
  const l = localSame();
  const m = (namespaceMod as any)[key]();
  const n = typeOnlyCall();
  const o = overloaded("value");
  const choose = Math.random() > 0.5 ? leftSame : rightSame;
  const p = choose(5);
  return [a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p].join("");
}
