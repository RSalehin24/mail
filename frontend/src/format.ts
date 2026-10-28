// One presentation convention for all dates; backup days follow Asia/Dhaka.
const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'Asia/Dhaka' });
const clock = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Dhaka' });
type DateValue = number | string | Date;
function instant(value: DateValue): Date { return value instanceof Date ? value : new Date(typeof value === 'number' ? value * 1000 : value); }
export function formatDate(value: DateValue): string { return day.format(instant(value)); }
export function formatTime(value: DateValue): string { return clock.format(instant(value)); }
export function formatDateTime(value: DateValue): string { return `${formatDate(value)} · ${formatTime(value)}`; }
