import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { compileSpec, SpecError } from "../src/spec.js";

const vec = JSON.parse(readFileSync(new URL("../../tests/vectors/spec_cases.json", import.meta.url)));
const catalog = JSON.parse(readFileSync(new URL("../../tests/vectors/catalog.json", import.meta.url)));

for (const c of vec.cases) {
  test(`spec vector: ${c.name}`, () => {
    if (c.error) assert.throws(() => compileSpec(catalog, c.spec), SpecError);
    else assert.equal(compileSpec(catalog, c.spec), c.sql);
  });
}
