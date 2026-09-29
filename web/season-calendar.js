/* Calendar boundaries use date-only UTC arithmetic, independent of browser timezone. */
(function (global) {
  "use strict";
  const DAY = 86400000;
  const time = date => Date.parse(date + "T00:00:00Z");
  const date = value => new Date(value).toISOString().slice(0, 10);
  function weeks(schedule) {
    if (!schedule.length) return [];
    const dates = schedule.map(g => g.date).sort();
    const first = time(dates[0]);
    const monday = first - ((new Date(first).getUTCDay() + 6) % 7) * DAY;
    const result = [];
    for (let start = monday; start <= time(dates[dates.length - 1]); start += 7 * DAY) {
      result.push({start: date(start), end: date(start + 6 * DAY),
        days: Array.from({length: 7}, (_, i) => date(start + i * DAY))});
    }
    return result;
  }
  function index(weeks, day) {
    return weeks.findIndex(w => day >= w.start && day <= w.end);
  }
  global.NBAI_CALENDAR = {weeks, index};
})(typeof window !== "undefined" ? window : globalThis);
