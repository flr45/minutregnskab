export function timeToMinutes(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(value || "");
  if (!match) throw new Error("Ugyldigt tidspunkt");
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) throw new Error("Ugyldigt tidspunkt");
  return hours * 60 + minutes;
}

export function normalizeToShiftMinute(clockMinutes, shiftStart) {
  return clockMinutes >= shiftStart ? clockMinutes : clockMinutes + 1440;
}

export function calculateTrip(startValue, endValue, shiftStartValue) {
  const shiftStart = timeToMinutes(shiftStartValue);
  let start = normalizeToShiftMinute(timeToMinutes(startValue), shiftStart);
  let end = normalizeToShiftMinute(timeToMinutes(endValue), shiftStart);
  if (end <= start) end += 1440;

  const shiftEnd = shiftStart + 1440;
  const bStart = shiftStart + 960;
  let a = 0;
  let b = 0;
  let overtime = 0;

  for (let minute = start; minute < end; minute += 1) {
    if (minute >= shiftEnd) overtime += 1;
    else if (minute >= bStart) b += 1;
    else a += 1;
  }

  return { total: end - start, a, b, overtime, start, end };
}

export function tripsOverlap(first, second, shiftStartValue) {
  const shiftStart = timeToMinutes(shiftStartValue);
  const normalizeTrip = (trip) => {
    let start = normalizeToShiftMinute(timeToMinutes(trip.start), shiftStart);
    let end = normalizeToShiftMinute(timeToMinutes(trip.end), shiftStart);
    if (end <= start) end += 1440;
    return { start, end };
  };
  const a = normalizeTrip(first);
  const b = normalizeTrip(second);
  return a.start < b.end && b.start < a.end;
}

export function calculateSummary(trips, shiftStartValue, advancedBreak = 0) {
  const raw = trips.reduce((sum, trip) => {
    const result = calculateTrip(trip.start, trip.end, shiftStartValue);
    sum.total += result.total;
    sum.a += result.a;
    sum.b += result.b;
    sum.overtime += result.overtime;
    return sum;
  }, { total: 0, a: 0, b: 0, overtime: 0 });

  const breakMinutes = Math.max(0, Number(advancedBreak) || 0);
  const deductedFromA = Math.min(raw.a, breakMinutes);
  const a = raw.a - deductedFromA;
  const total = a + raw.b;

  // A-tid må gerne overstige 510 minutter. Der flyttes aldrig kunstigt tid til B.
  const oneToFour = total > 720
    ? total - 720
    : raw.b > 210
      ? raw.b - 210
      : 0;

  return {
    total,
    a,
    b: raw.b,
    overtime: raw.overtime,
    oneToFour,
    oneToFourWeighted: oneToFour * 4,
  };
}
