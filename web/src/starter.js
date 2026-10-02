// Curated starter dashboards. Specs are plain chart specs over the semantic model; users fork them on first edit.
const w = (id, title, spec, size = 6) => ({ id, title, spec: { fact: "airline_monthly", ...spec }, w: size });

export const STARTERS = [
  {
    id: "overview", name: "Air traffic overview", starter: true, widgets: [
      w("kpi", "Headline numbers (selected filters)", { measures: ["pax", "departures", "pax_load_factor", "cargo_t"] }, 12),
      w("pax-fy", "Passengers by fiscal year and segment", { dimensions: ["fy", "segment"], measures: ["pax"], viz: "bar" }),
      w("pax-month", "Monthly passengers, domestic vs international", { dimensions: ["period", "segment"], measures: ["pax"] }),
      w("top-airlines", "Passengers by airline", { dimensions: ["airline"], measures: ["pax"], sort: { by: "pax", dir: "desc" }, limit: 12 }),
      w("plf-fy", "Passenger load factor by fiscal year", { dimensions: ["fy", "segment"], measures: ["pax_load_factor"], viz: "line" }),
    ],
  },
  {
    id: "airlines", name: "Airline comparison", starter: true, widgets: [
      w("top5-time", "Top 5 airlines – monthly passengers", { dimensions: ["period", "airline"], measures: ["pax"], top: { dim: "airline", measure: "pax", n: 5 } }, 12),
      w("plf-airline", "Passenger load factor by airline", { dimensions: ["airline"], measures: ["pax_load_factor"], sort: { by: "pax_load_factor", dir: "desc" }, limit: 12 }),
      w("ppd-airline", "Passengers per departure by airline", { dimensions: ["airline"], measures: ["pax_per_departure"], sort: { by: "pax_per_departure", dir: "desc" }, limit: 12 }),
      w("heat", "Passengers: airline × fiscal year", { dimensions: ["fy", "airline"], measures: ["pax"], top: { dim: "airline", measure: "pax", n: 10 }, viz: "heatmap" }, 12),
    ],
  },
  {
    id: "load", name: "Load factor trends", starter: true, widgets: [
      w("plf-line", "Passenger load factor – top 5 airlines (domestic)", { dimensions: ["period", "airline"], measures: ["pax_load_factor"], top: { dim: "airline", measure: "pax", n: 5 }, filters: [{ dim: "segment", op: "in", values: ["Domestic"] }] }, 12),
      w("plf-seg", "Load factor by month, domestic vs international", { dimensions: ["period", "segment"], measures: ["pax_load_factor"] }),
      w("cap", "Capacity (available seat-km) by fiscal year", { dimensions: ["fy"], measures: ["ask"], viz: "bar" }),
      w("tbl", "Fiscal-year summary table", { dimensions: ["fy", "segment"], measures: ["pax", "departures", "pax_load_factor", "pax_per_departure"], viz: "table" }, 12),
    ],
  },
];
