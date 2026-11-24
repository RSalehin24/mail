// One convention for every date and time in the dashboard, all in Dhaka time:
//   date  "24 Nov 2026", or "Today" / "Yesterday" for the last two days
//   time  "8:18 PM" (12-hour, no leading zero, no seconds); "8:18:40 PM" only on chart axes
//   both  "24 Nov 2026, 8:18 PM" or "Today, 8:18 PM"
//   age   "just now", "5 min ago", "2 h 8 min ago" for "last checked" lines; older than a day falls back to date and time
const ZONE = 'Asia/Dhaka';
const SECONDS_PER_MINUTE = 60, SECONDS_PER_HOUR = 3600, SECONDS_PER_DAY = 86400, JUST_NOW_SECONDS = 5;
const day = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: ZONE });
const clock = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: ZONE });
const clockWithSeconds = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, timeZone: ZONE });
const calendarKey = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: ZONE });
type DateValue = number | string | Date;
function instant(value: DateValue): Date { return value instanceof Date ? value : new Date(typeof value === 'number' ? value * 1000 : value); }
function dayName(value: Date, now: Date): string | undefined {
  if (calendarKey.format(value) === calendarKey.format(now)) return 'Today';
  return calendarKey.format(value) === calendarKey.format(new Date(now.getTime() - SECONDS_PER_DAY * 1000)) ? 'Yesterday' : undefined;
}
export function formatDate(value: DateValue, now: Date = new Date()): string { const date = instant(value); return dayName(date, now) ?? day.format(date); }
// Newer ICU versions put a narrow no-break space before AM/PM; a plain space keeps copied and searched text predictable.
function plainSpaces(text: string): string { return text.replace(/[\u202f\u00a0]/g, ' '); }
export function formatTime(value: DateValue): string { return plainSpaces(clock.format(instant(value))); }
export function formatClock(value: DateValue): string { return plainSpaces(clockWithSeconds.format(instant(value))); }
export function formatDateTime(value: DateValue, now: Date = new Date()): string { return `${formatDate(value, now)}, ${formatTime(value)}`; }
export function formatAge(value: DateValue, now: Date = new Date()): string {
  const seconds = Math.max(0, (now.getTime() - instant(value).getTime()) / 1000);
  if (seconds < JUST_NOW_SECONDS) return 'just now';
  if (seconds < SECONDS_PER_MINUTE) return `${Math.floor(seconds)} s ago`;
  if (seconds < SECONDS_PER_HOUR) return `${Math.floor(seconds / SECONDS_PER_MINUTE)} min ago`;
  if (seconds >= SECONDS_PER_DAY) return formatDateTime(value, now);
  const hours = Math.floor(seconds / SECONDS_PER_HOUR), minutes = Math.floor((seconds % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE);
  return minutes ? `${hours} h ${minutes} min ago` : `${hours} h ago`;
}
