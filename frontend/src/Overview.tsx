import { useEffect, useState } from 'react';
import { Alert, Box, Button, Paper, Stack, Typography } from '@mui/material';
import { api, cached, type Diagnostics, type Mailbox } from './api';
import { formatBytes } from './ResourceCharts';
import { formatDate, formatDateTime } from './format';
import { navigate } from './navigation';
import { LoadingState } from './PageState';
import { PageHeader, StatCard, StatGrid, StatusDot, type Tone } from './ui';

type Summary = NonNullable<Diagnostics['host']['summary']>;
interface Attention { tone: Tone; text: string; path: string; action: string }
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
    detail: `Latest ${formatDateTime(backup.cutoff)}` };
}

function attentionItems(mailboxes: Mailbox[], summary: Summary | undefined, now: number): Attention[] {
  const items: Attention[] = [];
  const backup = backupTone(summary, now);
  if (backup.tone !== 'success') items.push({ tone: 'warning', text: `Backup: ${backup.value.toLowerCase()}. ${backup.detail}.`, path: '/settings?tab=backups', action: 'Open backups' });
  const expiry = certificateExpiry(summary);
  const days = expiry === undefined ? undefined : (expiry - now) / DAY_SECONDS;
  if (days !== undefined && days < CERTIFICATE_WARNING_DAYS) items.push({ tone: days < CERTIFICATE_CRITICAL_DAYS ? 'error' : 'warning', text: `A certificate expires in ${Math.max(0, Math.floor(days))} days.`, path: '/server', action: 'Open server' });
  const minimum = summary?.thresholds?.disk_free_minimum_bytes;
  if (minimum !== undefined && summary?.disk_free_bytes !== undefined && summary.disk_free_bytes < minimum) items.push({ tone: 'error', text: 'Free disk space is below the warning limit.', path: '/server', action: 'Open server' });
  const waiting = mailboxes.filter(mailbox => mailbox.sender_tests?.some(test => test.accepted && !test.confirmed)).length;
  if (waiting) items.push({ tone: 'neutral', text: `${waiting} test ${waiting === 1 ? 'email is' : 'emails are'} waiting for confirmation.`, path: '/mailboxes', action: 'Review' });
  return items;
}

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
  const diskRatio = summary?.disk_total_bytes && summary.disk_used_bytes !== undefined ? summary.disk_used_bytes / summary.disk_total_bytes : undefined;
  const attention = attentionItems(mailboxes, summary, now);
  const ready = mailboxes.filter(mailbox => mailbox.status === 'ready').length;
  return <Stack spacing={3}>
    <PageHeader title="Overview" action={<Button variant="contained" onClick={() => navigate('/mailboxes')}>Manage mailboxes</Button>}/>
    <StatGrid>
      <StatCard label="Mailboxes" value={mailboxes.length} detail={`${ready} ready`} tone={ready === mailboxes.length ? 'neutral' : 'warning'}/>
      <StatCard label="Backup" value={backup.value} detail={backup.detail} tone={backup.tone}/>
      <StatCard label="Certificate" value={days === undefined ? 'Unknown' : `${days} days`} detail={expiry === undefined ? 'No reading yet' : `Expires ${formatDate(expiry)}`}
        tone={days === undefined ? 'neutral' : days < CERTIFICATE_CRITICAL_DAYS ? 'error' : days < CERTIFICATE_WARNING_DAYS ? 'warning' : 'success'}/>
      <StatCard label="Disk" value={diskRatio === undefined ? 'Unknown' : `${Math.round(diskRatio * 100)}% used`} detail={summary?.disk_free_bytes === undefined ? undefined : `${formatBytes(summary.disk_free_bytes)} free`}
        tone={diskRatio !== undefined && diskRatio > 0.85 ? 'warning' : 'neutral'}/>
    </StatGrid>
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Typography variant="h6" component="h2" sx={{ mb: 1.5 }}>Needs attention</Typography>
      {attention.length === 0 ? <StatusDot tone="success" label="Everything looks healthy."/> : <Stack spacing={1.5} divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }}/>}>
        {attention.map(item => <Stack key={item.text} direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
          <StatusDot tone={item.tone} label={item.text}/>
          <Button size="small" variant="outlined" onClick={() => navigate(item.path)}>{item.action}</Button>
        </Stack>)}
      </Stack>}
    </Paper>
  </Stack>;
}
