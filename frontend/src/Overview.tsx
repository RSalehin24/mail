import { useEffect, useState } from 'react';
import { Alert, Box, Stack, Tooltip } from '@mui/material';
import { api, cached, type Diagnostics, type Mailbox } from './api';
import { formatBytes } from './ResourceCharts';
import { formatAge, formatDate, formatDateTime } from './format';
import { LoadingState } from './PageState';
import { PageHeader, StatCard, StatGrid, StatusDot, type Tone } from './ui';

type Summary = NonNullable<Diagnostics['host']['summary']>;
const CERTIFICATE_WARNING_DAYS = 21, CERTIFICATE_CRITICAL_DAYS = 7, DAY_SECONDS = 86400;

function certificateExpiry(summary?: Summary): number | undefined {
  const certificates = summary?.certificates;
  const served = Object.values(certificates?.consumers ?? {}).map(consumer => consumer.served_expiry);
  const expiries = [...served, certificates?.expiry].filter((value): value is number => typeof value === 'number');
  return expiries.length ? Math.min(...expiries) : undefined;
}

function backupTone(summary: Summary | undefined, now: number): { tone: Tone; value: string; detail: string } {
  const backup = summary?.backup;
  if (!backup?.cutoff) return { tone: 'warning', value: 'Unknown', detail: 'No backup recorded' };
  const limit = summary?.thresholds?.recovery_point_seconds;
  const stale = limit !== undefined && now - Date.parse(backup.cutoff) / 1000 > limit;
  const healthy = backup.verified === true && !stale;
  return { tone: healthy ? 'success' : 'warning', value: healthy ? 'Verified' : stale ? 'Out of date' : 'Not verified',
    detail: formatDateTime(backup.cutoff) };
}

const PERCENT = 100, WARNING_PERCENT = 75, CRITICAL_PERCENT = 90;

function usageTone(percent: number): Tone { return percent >= CRITICAL_PERCENT ? 'error' : percent >= WARNING_PERCENT ? 'warning' : 'neutral'; }

export function Overview() {
  const [mailboxes, setMailboxes] = useState<Mailbox[] | null>(() => cached<Mailbox[]>('/accounts') ?? null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(() => cached<Diagnostics>('/diagnostics') ?? null);
  const [error, setError] = useState('');
  useEffect(() => {
    api<Mailbox[]>('/accounts').then(setMailboxes).catch(failure => setError((failure as Error).message));
    api<Diagnostics>('/diagnostics').then(setDiagnostics).catch(() => {});
  }, []);
  if (error && !mailboxes) return <Alert severity="error">{error}</Alert>;
  if (!mailboxes) return <LoadingState label="Loading overview"/>;
  const now = Date.now() / 1000;
  const summary = diagnostics?.host.summary;
  const backup = backupTone(summary, now);
  const expiry = certificateExpiry(summary);
  const days = expiry === undefined ? undefined : Math.floor((expiry - now) / DAY_SECONDS);
  const diskPercent = summary?.disk_total_bytes && summary.disk_used_bytes !== undefined ? PERCENT * summary.disk_used_bytes / summary.disk_total_bytes : undefined;
  const memoryPercent = summary?.memory_total_bytes && summary.memory_available_bytes !== undefined ? PERCENT * (1 - summary.memory_available_bytes / summary.memory_total_bytes) : undefined;
  const ready = mailboxes.filter(mailbox => mailbox.status === 'ready').length;
  const verified = mailboxes.filter(mailbox => mailbox.outbound_readiness === 'confirmed').length;
  const waiting = mailboxes.filter(mailbox => mailbox.sender_tests?.some(test => test.accepted && !test.confirmed)).length;
  const diskLow = summary?.thresholds?.disk_free_minimum_bytes !== undefined && summary.disk_free_bytes !== undefined && summary.disk_free_bytes < summary.thresholds.disk_free_minimum_bytes;
  const certificateTone: Tone = days === undefined ? 'neutral' : days < CERTIFICATE_CRITICAL_DAYS ? 'error' : days < CERTIFICATE_WARNING_DAYS ? 'warning' : 'success';
  const cards: { key: string; label: string; value: string; detail?: string; tone: Tone; to: string; meter?: number }[] = [
    { key: 'mailboxes', label: 'Mailboxes', value: String(mailboxes.length), detail: `${ready} ready`, tone: ready === mailboxes.length ? 'neutral' : 'warning', to: '/mailboxes' },
    { key: 'outgoing', label: 'Outgoing email', value: `${verified} of ${mailboxes.length}`, detail: waiting ? `${waiting} awaiting receipt` : 'verified', tone: waiting ? 'warning' : 'neutral', to: '/outgoing' },
    { key: 'backup', label: 'Backup', value: backup.value, detail: backup.detail, tone: backup.tone, to: '/backups' },
    { key: 'certificate', label: 'Certificate', value: days === undefined ? 'Unknown' : `${days} days`, detail: expiry === undefined ? undefined : `Expires ${formatDate(expiry)}`, tone: certificateTone, to: '/server' },
    { key: 'disk', label: 'Disk', value: diskPercent === undefined ? 'Unknown' : `${Math.round(diskPercent)}%`, detail: summary?.disk_free_bytes === undefined ? undefined : `${formatBytes(summary.disk_free_bytes)} free`, tone: diskLow ? 'error' : diskPercent === undefined ? 'neutral' : usageTone(diskPercent), to: '/server', meter: diskPercent },
    { key: 'memory', label: 'Memory', value: memoryPercent === undefined ? 'Unknown' : `${Math.round(memoryPercent)}%`, detail: summary?.memory_total_bytes === undefined ? undefined : `of ${formatBytes(summary.memory_total_bytes)}`, tone: memoryPercent === undefined ? 'neutral' : usageTone(memoryPercent), to: '/server', meter: memoryPercent },
  ];
  const review = cards.filter(card => card.tone === 'warning' || card.tone === 'error').length;
  return <Stack spacing={3}>
    <PageHeader title="Overview" action={<Tooltip placement="left" title={summary ? `Checked ${formatAge(summary.observed_at)}` : 'Waiting for the first check'}><Box><StatusDot tone={review ? 'warning' : 'success'} label={review ? `${review} to review` : 'Healthy'}/></Box></Tooltip>}/>
    <StatGrid columns={3}>{cards.map(({ key, ...card }) => <StatCard key={key} {...card}/>)}</StatGrid>
  </Stack>;
}
