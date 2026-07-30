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

function normalizeTrip(trip, shiftStart) {
  const startClock = timeToMinutes(trip.start);
  const endClock = timeToMinutes(trip.end);

  if (trip.afterShift) {
    let start = normalizeToShiftMinute(startClock, shiftStart) + 1440;
    let end = normalizeToShiftMinute(endClock, shiftStart) + 1440;
    if (end <= start) end += 1440;
    return { start, end };
  }

  // En tur, der begynder kort før mødetid og slutter efter mødetid,
  // behandles som før-vagt-overtid i stedet for som næste døgn.
  if (startClock < shiftStart && endClock >= shiftStart) {
    return { start: startClock, end: endClock };
  }

  let start = normalizeToShiftMinute(startClock, shiftStart);
  let end = normalizeToShiftMinute(endClock, shiftStart);
  if (end <= start) end += 1440;
  return { start, end };
}

export function calculateTrip(startValue, endValue, shiftStartValue, options = {}) {
  const shiftStart = timeToMinutes(shiftStartValue);
  const { start, end } = normalizeTrip(
    { start: startValue, end: endValue, afterShift: Boolean(options.afterShift) },
    shiftStart,
  );

  const shiftEnd = shiftStart + 1440;
  const bStart = shiftStart + 960;
  let a = 0;
  let b = 0;
  let overtime = 0;

  for (let minute = start; minute < end; minute += 1) {
    if (minute < shiftStart || minute >= shiftEnd) overtime += 1;
    else if (minute >= bStart) b += 1;
    else a += 1;
  }

  return { total: end - start, a, b, overtime, start, end };
}

export function tripsOverlap(first, second, shiftStartValue) {
  const shiftStart = timeToMinutes(shiftStartValue);
  const a = normalizeTrip(first, shiftStart);
  const b = normalizeTrip(second, shiftStart);
  return a.start < b.end && b.start < a.end;
}

export function evaluatePauseStatus(
  trips,
  shiftStartValue,
  intervalStart,
  intervalEnd,
  elapsedMinutes,
) {
  if (elapsedMinutes < intervalStart) return null;

  const visibleEnd = Math.min(intervalEnd, elapsedMinutes);
  const segments = trips
    .map(trip => calculateTrip(trip.start, trip.end, shiftStartValue, {
      afterShift: trip.afterShift,
    }))
    .map(({ start, end }) => [
      Math.max(intervalStart, start - timeToMinutes(shiftStartValue)),
      Math.min(visibleEnd, end - timeToMinutes(shiftStartValue)),
    ])
    .filter(([start, end]) => end > start && start < visibleEnd)
    .sort((first, second) => first[0] - second[0]);

  let cursor = intervalStart;
  for (const [segmentStart, segmentEnd] of segments) {
    if (segmentStart > cursor) {
      const gap = segmentStart - cursor;
      if (gap >= 30) return "";
      if (gap > 0) return "Afbrudt";
    }
    cursor = Math.max(cursor, segmentEnd);
  }

  if (visibleEnd > cursor && visibleEnd - cursor >= 30) return "";
  return elapsedMinutes >= intervalEnd ? "Ikke afholdt" : null;
}

export function calculateSummary(trips, shiftStartValue, advancedBreak = 0) {
  const raw = trips.reduce((sum, trip) => {
    const result = calculateTrip(trip.start, trip.end, shiftStartValue, {
      afterShift: trip.afterShift,
    });
    sum.a += result.a;
    sum.b += result.b;
    sum.overtime += result.overtime;
    return sum;
  }, { a: 0, b: 0, overtime: 0 });

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
