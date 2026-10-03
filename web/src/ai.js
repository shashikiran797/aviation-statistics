// AI assistant backends (all run locally in the browser; no keys, no server).
//   1. Chrome built-in AI (Prompt API / Gemini Nano)   2. WebLLM (WebGPU)   3. none -> assistant unavailable
// Both emit a *chart spec* over the semantic model, which is validated by the same compiler the builder uses.
import { compileSpec, SpecError } from "./spec.js";

export const WEBLLM_MODELS = [
  { id: "Llama-3.2-1B-Instruct-q4f16_1-MLC", label: "Llama 3.2 1B – smallest, about 0.7 GB" },
  { id: "Qwen2.5-1.5B-Instruct-q4f16_1-MLC", label: "Qwen 2.5 1.5B – better at structure, about 1 GB" },
  { id: "Phi-3.5-mini-instruct-q4f16_1-MLC", label: "Phi 3.5 mini – most capable, about 2.4 GB" },
];

export async function chromeStatus() {
  const LM = globalThis.LanguageModel;
  if (!LM) return { state: "unsupported" };
  try { return { state: await LM.availability({ expectedInputs: [{ type: "text", languages: ["en"] }], expectedOutputs: [{ type: "text", languages: ["en"] }] }) }; }
  catch (e) { return { state: "unsupported", error: String(e) }; }
}
export const webgpuStatus = () => ({ state: navigator.gpu ? "maybe" : "unsupported" });

function schema(catalog) {
  const facts = Object.keys(catalog.facts);
  const dims = [...new Set(facts.flatMap((f) => catalog.facts[f].dimensions))];
  const meas = [...new Set(facts.flatMap((f) => catalog.facts[f].measures))];
  const filter = { type: "object", properties: { dim: { enum: dims }, op: { enum: ["in", "not_in", "between"] }, values: { type: "array", items: { type: "string" } } }, required: ["dim", "op", "values"] };
  return { type: "object", properties: {
    fact: { enum: facts }, dimensions: { type: "array", items: { enum: dims }, maxItems: 2 }, measures: { type: "array", items: { enum: meas }, minItems: 1, maxItems: 3 },
    filters: { type: "array", items: filter },
    top: { anyOf: [{ type: "null" }, { type: "object", properties: { dim: { enum: dims }, measure: { enum: meas }, n: { type: "integer" } }, required: ["dim", "measure", "n"] }] },
    sort: { anyOf: [{ type: "null" }, { type: "object", properties: { by: { type: "string" }, dir: { enum: ["asc", "desc"] } }, required: ["by", "dir"] }] },
    limit: { anyOf: [{ type: "null" }, { type: "integer" }] }, viz: { enum: ["auto", "line", "bar", "heatmap", "table"] } }, required: ["fact", "dimensions", "measures"] };
}

function factBlock(catalog, factId) {
  const f = catalog.facts[factId];
  const dims = f.dimensions.map((d) => {
    const x = catalog.dimensions[d];
    const vals = x.values && x.values.length <= 30 ? ` values: ${JSON.stringify(x.values)}` : x.values ? ` (${x.values.length} values, e.g. ${JSON.stringify(x.values.slice(0, 4))})` : "";
    return `  - ${d}: ${x.label}.${vals}`;
  }).join("\n");
  const meas = f.measures.map((m) => `  - ${m}: ${catalog.measures[m].label}${catalog.measures[m].additive ? "" : " (ratio)"}`).join("\n");
  return `Fact "${factId}": ${f.description.replace(/\s+/g, " ").trim().split(". ")[0]}.\n Dimensions:\n${dims}\n Measures:\n${meas}`;
}

export function systemPrompt(catalog) {
  const ids = Object.keys(catalog.facts);
  const [a, b] = [ids[0], ids[1] || ids[0]];
  return `You turn questions about Indian aviation statistics into ONE JSON chart spec. Reply with JSON only.
Spec: {"fact":id,"dimensions":[ids],"measures":[ids],"filters":[{"dim":id,"op":"in"|"not_in"|"between","values":[...]}],"top":null|{"dim":id,"measure":id,"n":int},"sort":null|{"by":id,"dir":"asc"|"desc"},"limit":null|int,"viz":"auto"|"line"|"bar"|"heatmap"|"table"}
Pick the fact that fits: Indian airlines' domestic/international statistics use "airline_monthly"; domestic routes, city pairs and airports use "route_monthly"; international traffic (foreign carriers, countries, to/from India, international routes) uses "intl_airline_monthly" (by carrier), "intl_country_quarterly" (by country) or "intl_citypair_quarterly" (international routes). Use ONLY that fact's dimension and measure ids; copy filter values exactly from the lists.
${ids.map((f) => factBlock(catalog, f)).join("\n")}
Rules: first dimension is the X axis, second is the series. Use "period" on X for monthly trends, "fy" for yearly. "Top N airlines/routes" => top {"dim":"airline" or "route",...} and that dimension must also be in dimensions. Fiscal years look like "2017-18". Leave filters empty unless the question names a value. Do not add airline_type filters.
Examples:
Q: Passengers by fiscal year -> {"fact":"${a}","dimensions":["fy"],"measures":["pax"],"filters":[],"top":null,"sort":null,"limit":null,"viz":"auto"}
Q: Top 5 airlines by passengers in 2018-19 by month -> {"fact":"${a}","dimensions":["month","airline"],"measures":["pax"],"filters":[{"dim":"fy","op":"in","values":["2018-19"]}],"top":{"dim":"airline","measure":"pax","n":5},"sort":null,"limit":null,"viz":"line"}
Q: Compare load factor of IndiGo and SpiceJet over time -> {"fact":"${a}","dimensions":["period","airline"],"measures":["pax_load_factor"],"filters":[{"dim":"airline","op":"in","values":["IndiGo","SpiceJet"]}],"top":null,"sort":null,"limit":null,"viz":"line"}
Q: Busiest 10 routes -> {"fact":"${b}","dimensions":["route"],"measures":["route_pax"],"filters":[],"top":null,"sort":{"by":"route_pax","dir":"desc"},"limit":10,"viz":"auto"}
Q: Which countries have the most passengers? -> {"fact":"intl_country_quarterly","dimensions":["country"],"measures":["intl_pax"],"filters":[],"top":null,"sort":{"by":"intl_pax","dir":"desc"},"limit":10,"viz":"auto"}
Q: Emirates vs Qatar Airways passengers by quarter -> {"fact":"intl_airline_monthly","dimensions":["quarter","carrier"],"measures":["intl_pax"],"filters":[{"dim":"carrier","op":"in","values":["Emirates","Qatar Airways"]}],"top":null,"sort":null,"limit":null,"viz":"line"}
Q: Monthly passengers from Delhi to Mumbai -> {"fact":"${b}","dimensions":["period"],"measures":["route_pax"],"filters":[{"dim":"origin","op":"in","values":["Delhi"]},{"dim":"destination","op":"in","values":["Mumbai"]}],"top":null,"sort":null,"limit":null,"viz":"line"}`;
}

export function parseSpec(text) {
  const t = text.trim().replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  const s = t.slice(t.indexOf("{"), t.lastIndexOf("}") + 1);
  const o = JSON.parse(s);
  for (const k of ["top", "sort", "limit"]) if (o[k] === null || o[k] === undefined) delete o[k];
  if (!o.filters) o.filters = [];
  if (!o.dimensions) o.dimensions = [];
  return o;
}

let chromeSession = null, webllmEngine = null, webllmModel = null;

export async function enableChrome(onProgress) {
  const LM = globalThis.LanguageModel;
  chromeSession = await LM.create({
    expectedInputs: [{ type: "text", languages: ["en"] }], expectedOutputs: [{ type: "text", languages: ["en"] }],
    monitor(m) { m.addEventListener("downloadprogress", (e) => onProgress?.(e.loaded)); },
  });
  return true;
}
export async function enableWebLLM(modelId, onProgress) {
  const webllm = await import("https://esm.run/@mlc-ai/web-llm");
  webllmEngine = await webllm.CreateMLCEngine(modelId, { initProgressCallback: (p) => onProgress?.(p.progress, p.text) });
  webllmModel = modelId;
  return true;
}
export const enabled = () => ({ chrome: !!chromeSession, webllm: !!webllmEngine });

async function run(backend, system, messages, catalog) {
  if (window.__DGCA_AI_MOCK) return window.__DGCA_AI_MOCK(system, messages);  // test hook
  if (backend === "chrome") {
    const history = messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content }));
    const sess = await globalThis.LanguageModel.create({ initialPrompts: [{ role: "system", content: system }, ...history],
      expectedInputs: [{ type: "text", languages: ["en"] }], expectedOutputs: [{ type: "text", languages: ["en"] }] });
    try { return await sess.prompt(messages.at(-1).content, { responseConstraint: schema(catalog) }); }
    finally { sess.destroy?.(); }
  }
  const r = await webllmEngine.chat.completions.create({ messages: [{ role: "system", content: system }, ...messages], temperature: 0, response_format: { type: "json_object" } });
  return r.choices[0].message.content;
}

/** question -> validated spec. One self-repair retry using the compiler's error message. */
export async function generateSpec(backend, question, catalog) {
  const system = systemPrompt(catalog);
  const messages = [{ role: "user", content: question }];
  let lastErr = "";
  for (let attempt = 0; attempt < 2; attempt++) {
    const text = await run(backend, system, messages, catalog);
    try {
      const spec = parseSpec(text);
      spec.fact ||= Object.keys(catalog.facts)[0];
      compileSpec(catalog, spec);
      return { spec, attempts: attempt + 1 };
    } catch (e) {
      lastErr = e instanceof SpecError || e instanceof SyntaxError ? e.message : String(e);
      messages.push({ role: "assistant", content: text }, { role: "user", content: `That spec was invalid: ${lastErr}. Reply with corrected JSON only.` });
    }
  }
  throw new Error(`The model did not produce a valid chart spec (${lastErr}). Try rephrasing, or build the chart manually.`);
}
