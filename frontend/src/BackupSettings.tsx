import { formatAge, formatDate, formatTime, formatDateTime } from './format';
import { LoadingState } from './PageState';
import ContentCopyRounded from '@mui/icons-material/ContentCopyRounded';
import { AdminTable } from './AdminTable';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, IconButton, Link, Paper, Stack, Tab, Tabs, Tooltip, Typography } from '@mui/material';
import { api, errorMessage, cached, type Notify } from './api';
import { PaginationControls } from './PaginationControls';
import { CommandBlock, Disclosure, InfoList, PageHeader, StatusBadge, StatusDot } from './ui';

interface Snapshot { id: string; cutoff: number; observed: number; verification?: { verified_at?: number }; temporary_pin?: boolean; cleanup?: { state?: string; error?: string } }
interface Job { id: string; kind: string; snapshot: string; state: string; phase: string; created: number; updated: number; error: string; result: { cleanup?: { state?: string; error?: string } } }
interface Listing<T> { busy: boolean; items: T[]; total: number; page: number; page_size: number; observation: { observed: number; error: string } | null }
const date = formatDateTime;
// The update time alone when it is the same day as the start; the full date and time otherwise.
const sameDay = (first: number, second: number) => backupDay(first) === backupDay(second);
const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
const backupDay = formatDate;
const backupTime = formatTime;

type View = 'overview' | 'backups' | 'logs';

export function BackupSettings({ notify }: { notify: Notify }) {
  const [view, setView] = useState<View>(() => { const value = new URLSearchParams(location.search).get('backup_view'); return value === 'logs' || value === 'backups' ? value : 'overview'; });
  function selectView(value: View) {
    setView(value); const url = new URL(location.href); url.searchParams.set('backup_view', value); history.replaceState(null, '', url);
  }
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const [operationPage, setOperationPage] = useState(0);
  const [operationSize, setOperationSize] = useState(10);
  const [snapshots, setSnapshots] = useState<Listing<Snapshot> | null>(() => cached<Listing<Snapshot>>('/backups?page=1&page_size=10') ?? null);
  const [jobs, setJobs] = useState<Listing<Job> | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      api<Listing<Snapshot>>(`/backups?page=${page + 1}&page_size=${size}`),
      api<Listing<Job>>(`/backups?kind=operations&page=${operationPage + 1}&page_size=${operationSize}`),
    ]);
    if (results[0].status === 'fulfilled') { setSnapshots(results[0].value); if (page !== results[0].value.page - 1) setPage(results[0].value.page - 1); }
    if (results[1].status === 'fulfilled') { setJobs(results[1].value); if (operationPage !== results[1].value.page - 1) setOperationPage(results[1].value.page - 1); }
    const failed = results.find(result => result.status === 'rejected');
    setError(failed?.status === 'rejected' ? (failed.reason as Error).message : '');
  }, [page, size, operationPage, operationSize]);
  useEffect(() => { void load(); }, [load]);
  return <Stack spacing={3}>
    <PageHeader title="Backups"/>
    <Tabs value={view} onChange={(_, value: View) => selectView(value)} aria-label="Backup sections">
      <Tab value="overview" label="Overview & Config" id="backup-section-overview" aria-controls="backup-panel-overview"/>
      <Tab value="backups" label={<Tooltip placement="top" title={snapshots?.observation ? `Last checked ${formatAge(snapshots.observation.observed)}` : 'Not checked yet'}><span>Backups</span></Tooltip>} id="backup-section-backups" aria-controls="backup-panel-backups"/>
      <Tab value="logs" label="Logs" id="backup-section-logs" aria-controls="backup-panel-logs"/>
    </Tabs>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Retry loading</Button>}>{error}</Alert>}
    {view === 'overview' && <Stack spacing={2} role="tabpanel" id="backup-panel-overview" aria-labelledby="backup-section-overview">
    <Paper variant="outlined" sx={{ p: 2 }}><InfoList centered items={[
      { label: 'Schedule', value: 'Daily at 3:00 AM' }, { label: 'Time zone', value: 'Asia/Dhaka' },
      { label: 'Retention', value: '7 days' }, { label: 'Encryption', value: 'Encrypted' },
    ]}/></Paper>
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, alignItems: 'start' }}>
      <Disclosure title="Back up now">
        <CommandBlock label="Back up now" showLabel={false} command="sudo systemctl start mail-admin-backup.service"/>
      </Disclosure>
      <Disclosure title="Restore a backup">
        <CommandBlock label="List backups" command="cd /usr/local/lib/mail-admin && sudo python3 -m host.restore_backup --list"/>
        <CommandBlock label="Restore" command="cd /usr/local/lib/mail-admin && sudo python3 -m host.restore_backup <snapshot-id>"/>
        <Typography variant="caption" color="text.secondary">Copy a snapshot ID from the <Link component="button" type="button" onClick={() => selectView('backups')} sx={{ verticalAlign: 'baseline' }}>Backups tab</Link>.</Typography>
      </Disclosure>
    </Box>
    </Stack>}
    {view === 'backups' && <Stack spacing={2} role="tabpanel" id="backup-panel-backups" aria-labelledby="backup-section-backups">
    {snapshots?.observation?.error && <Alert severity="warning">{errorMessage(snapshots.observation.error)} Previously observed backups may have changed.</Alert>}
    {!snapshots && !error && <LoadingState label="Loading backups…"/>}
    <AdminTable columns={['Date', 'Time', 'Snapshot', 'Verification']} empty="No backups recorded." rows={(snapshots?.items ?? []).map(snapshot => ({ key: snapshot.id, cells: [
      backupDay(snapshot.cutoff), backupTime(snapshot.cutoff),
      <Stack key="snapshot" direction="row" spacing={0.5} sx={{ alignItems: 'center' }}><Tooltip title={snapshot.id}><Typography component="code" variant="body2" sx={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>{snapshot.id.slice(0, 8)}…{snapshot.id.slice(-6)}</Typography></Tooltip><Tooltip title="Copy snapshot ID"><IconButton size="small" aria-label={`Copy snapshot ${snapshot.id}`} onClick={() => { void navigator.clipboard.writeText(snapshot.id).then(() => notify('Snapshot ID copied')).catch(() => notify('Could not copy snapshot ID')); }}><ContentCopyRounded sx={{ fontSize: 16 }}/></IconButton></Tooltip></Stack>,
      <Stack key="verification" spacing={0.25}><StatusDot tone={snapshot.verification?.verified_at ? 'success' : 'neutral'} label={snapshot.verification?.verified_at ? 'Verified' : 'Not verified'}/>{snapshot.verification?.verified_at && <Typography variant="caption" color="text.secondary">{date(snapshot.verification.verified_at)}</Typography>}{snapshot.temporary_pin && <Typography variant="caption" color="text.secondary">Temporarily pinned</Typography>}{snapshot.cleanup?.error && <Typography variant="caption" color="error">Cleanup: {errorMessage(snapshot.cleanup.error)}</Typography>}</Stack>,
    ] }))} footer={snapshots && <PaginationControls count={snapshots.total} page={snapshots.page - 1} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/>}/>
    </Stack>}
    {view === 'logs' && <Stack spacing={2} role="tabpanel" id="backup-panel-logs" aria-labelledby="backup-section-logs">
    <AdminTable columns={['Date', 'Time', 'Operation', 'Status', 'Last update']} empty="No operations recorded." rows={(jobs?.items ?? []).map(job => ({ key: job.id, cells: [
      backupDay(job.created), backupTime(job.created),
      <Tooltip key="operation" title={`ID ${job.id}`} placement="bottom"><Typography variant="body2" tabIndex={0} sx={{ textTransform: 'capitalize', cursor: 'help', width: 'fit-content', mx: 'auto' }}>{job.kind.replaceAll('_', ' ')}</Typography></Tooltip>,
      <Tooltip key="status" title={[job.error && errorMessage(job.error), job.result.cleanup?.error && `Cleanup failed: ${errorMessage(job.result.cleanup.error)}`].filter(Boolean).join(' ') || `Phase: ${label(job.phase)}`} placement="bottom"><Box tabIndex={0} sx={{ cursor: 'help', display: 'inline-block' }}><StatusBadge tone={job.state === 'complete' ? 'success' : job.state === 'failed' || job.state === 'reconciliation_required' ? 'error' : 'neutral'} label={label(job.state)}/></Box></Tooltip>,
      sameDay(job.created, job.updated) ? backupTime(job.updated) : date(job.updated),
    ] }))}/>
    {jobs && <PaginationControls count={jobs.total} page={jobs.page - 1} size={operationSize} onPage={setOperationPage} onSize={value => { setOperationSize(value); setOperationPage(0); }}/>} 
    </Stack>}
  </Stack>;
}
