import test from "node:test";
import assert from "node:assert/strict";
import { calculateSummary, calculateTrip, evaluatePauseStatus, tripsOverlap } from "../static/js/calculations.js";

test("A-tid over 510 minutter flyttes ikke til B-tid", () => {
  const summary = calculateSummary([{ start: "07:30", end: "17:00" }], "07:30", 0);
  assert.equal(summary.a, 570);
  assert.equal(summary.b, 0);
});

test("B-tid starter først 16 timer efter vagtstart", () => {
  const before = calculateTrip("23:00", "23:30", "07:30");
  const after = calculateTrip("23:30", "00:30", "07:30");
  assert.deepEqual({ a: before.a, b: before.b }, { a: 30, b: 0 });
  assert.deepEqual({ a: after.a, b: after.b }, { a: 0, b: 60 });
});

test("før-vagt-overtid tælles separat", () => {
  const trip = calculateTrip("07:20", "08:00", "07:30");
  assert.deepEqual({ a: trip.a, b: trip.b, overtime: trip.overtime }, { a: 30, b: 0, overtime: 10 });
});

test("efter-vagt-overtid holdes ude af 1-4 minutter", () => {
  const summary = calculateSummary(
    [{ start: "07:30", end: "08:00", afterShift: true }],
    "07:30",
    0,
  );
  assert.equal(summary.overtime, 30);
  assert.equal(summary.total, 0);
  assert.equal(summary.oneToFour, 0);
});

test("fremskudt pause trækkes kun fra A-tid", () => {
  const summary = calculateSummary([{ start: "22:30", end: "00:30" }], "07:30", 30);
  assert.equal(summary.a, 30);
  assert.equal(summary.b, 60);
  assert.equal(summary.total, 90);
});

test("overlappende ture opdages over midnat", () => {
  assert.equal(
    tripsOverlap({ start: "23:50", end: "00:20" }, { start: "00:10", end: "00:40" }, "07:30"),
    true,
  );
});


test("en tur inden for pausens første 30 minutter registrerer afbrudt pause", () => {
  const result = evaluatePauseStatus(
    [{ start: "10:40", end: "11:08" }],
    "07:30",
    180,
    360,
    291,
  );
  assert.equal(result, "Afbrudt");
});

test("30 sammenhængende minutter før første tur betyder afholdt pause", () => {
  const result = evaluatePauseStatus(
    [{ start: "11:05", end: "11:30" }],
    "07:30",
    180,
    360,
    250,
  );
  assert.equal(result, "");
});

test("ingen 30 minutters pause ved intervallets slutning betyder ikke afholdt", () => {
  const result = evaluatePauseStatus(
    [{ start: "10:30", end: "13:20" }],
    "07:30",
    180,
    360,
    360,
  );
  assert.equal(result, "Ikke afholdt");
});

test("en igangværende kort pause markeres ikke før den afbrydes", () => {
  const result = evaluatePauseStatus([], "07:30", 180, 360, 190);
  assert.equal(result, null);
});
