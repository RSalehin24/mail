import { formatDate, formatTime, formatDateTime } from './format';
import CloudDoneOutlined from '@mui/icons-material/CloudDoneOutlined';
import { LoadingState } from './PageState';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import ContentCopyRounded from '@mui/icons-material/ContentCopyRounded';
import { AdminTable } from './AdminTable';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, IconButton, Paper, Stack, Tab, Tabs, Tooltip, Typography } from '@mui/material';
import { api, errorMessage, cached, type Notify } from './api';
import { PaginationControls } from './PaginationControls';

interface Snapshot { id: string; cutoff: number; observed: number; verification?: { verified_at?: number }; temporary_pin?: boolean; cleanup?: { state?: string; error?: string } }
interface Job { id: string; kind: string; snapshot: string; state: string; phase: string; created: number; updated: number; error: string; result: { cleanup?: { state?: string; error?: string } } }
interface Listing<T> { busy: boolean; items: T[]; total: number; page: number; page_size: number; observation: { observed: number; error: string } | null }
const date = formatDateTime;
const backupDay = formatDate;
const backupTime = formatTime;

export function BackupSettings({ notify }: { notify: Notify }) {
  const [view, setView] = useState<'backups' | 'logs'>(() => new URLSearchParams(location.search).get('backup_view') === 'logs' ? 'logs' : 'backups');
  function selectView(value: 'backups' | 'logs') {
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
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
      <Box><Typography variant="h5" component="h2">Backup &amp; Recovery</Typography></Box>
    </Stack>
    <Tabs value={view} onChange={(_, value: 'backups' | 'logs') => selectView(value)} aria-label="Backup and recovery sections">
      <Tab value="backups" label="Backups" id="backup-section-backups" aria-controls="backup-panel-backups"/>
      <Tab value="logs" label="Logs" id="backup-section-logs" aria-controls="backup-panel-logs"/>
    </Tabs>
    <Alert severity="info">Back up and restore on the server over SSH. Back up now: <code>sudo systemctl start mail-admin-backup.service</code>. Restore: <code>cd /usr/local/lib/mail-admin &amp;&amp; sudo python3 -m host.restore_backup --list</code>, then the same command with a snapshot id.</Alert>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Retry loading</Button>}>{error}</Alert>}
    {view === 'backups' && <Stack spacing={2} role="tabpanel" id="backup-panel-backups" aria-labelledby="backup-section-backups">
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 }, borderRadius: 3, bgcolor: 'action.hover' }}>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'flex-start' }}>
        <Box sx={{ p: 1, borderRadius: 2, bgcolor: 'background.paper', color: 'primary.main', display: { xs: 'none', sm: 'flex' } }}><CloudDoneOutlined/></Box>
        <Box sx={{ minWidth: 0, flex: 1 }}><Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Daily encrypted backups</Typography>
          <Typography variant="body2" color="text.secondary">3:00 AM · Asia/Dhaka · 7-day retention</Typography>
        </Box>
      </Stack>
    </Paper>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}><Typography variant="h6" sx={{ fontSize: '1rem' }}>Available backups</Typography>{snapshots && <Chip size="small" variant="outlined" label={snapshots.total}/>}</Stack>
      <Typography variant="caption" color="text.secondary">{snapshots?.observation ? `Last checked ${backupDay(snapshots.observation.observed)}, ${backupTime(snapshots.observation.observed)} Dhaka` : 'Waiting for the first repository check'}</Typography>
    </Stack>
    {snapshots?.observation?.error && <Alert severity="warning">{errorMessage(snapshots.observation.error)} Previously observed backups may have changed.</Alert>}
    {!snapshots && !error && <LoadingState label="Loading backups…"/>}
    <AdminTable columns={['Backup Date', 'Snapshot', 'Verification']} empty="No backups recorded." rows={(snapshots?.items ?? []).map(snapshot => ({ key: snapshot.id, cells: [
      <Stack key="date" spacing={0.5}><Typography variant="body2" sx={{ fontWeight: 600 }}>{backupDay(snapshot.cutoff)}</Typography><Typography variant="caption" color="text.secondary">{backupTime(snapshot.cutoff)} · Dhaka</Typography></Stack>,
      <Stack key="snapshot" direction="row" spacing={0.5} sx={{ alignItems: 'center', justifyContent: { xs: 'flex-start', md: 'center' } }}><Tooltip title={snapshot.id}><Typography component="code" variant="body2" sx={{ fontFamily: 'monospace', bgcolor: 'action.hover', px: 1, py: 0.5, borderRadius: 1 }}>{snapshot.id.slice(0, 12)}…{snapshot.id.slice(-8)}</Typography></Tooltip><Tooltip title="Copy snapshot ID"><IconButton size="small" aria-label={`Copy snapshot ${snapshot.id}`} onClick={() => { void navigator.clipboard.writeText(snapshot.id).then(() => notify('Snapshot ID copied')).catch(() => notify('Could not copy snapshot ID')); }}><ContentCopyRounded sx={{ fontSize: 16 }}/></IconButton></Tooltip></Stack>,
      <Stack key="verification" spacing={0.75} sx={{ alignItems: { xs: 'flex-start', md: 'center' } }}><Chip size="small" variant="outlined" color={snapshot.verification?.verified_at ? 'success' : 'default'} icon={snapshot.verification?.verified_at ? <CheckCircleOutlineRounded/> : undefined} label={snapshot.verification?.verified_at ? 'Verified' : 'Not yet verified'}/>{snapshot.verification?.verified_at && <Typography variant="caption" color="text.secondary">{backupDay(snapshot.verification.verified_at)}, {backupTime(snapshot.verification.verified_at)}</Typography>}{snapshot.temporary_pin && <Chip label="Temporarily pinned"/>}{snapshot.cleanup?.error && <Typography color="error">Cleanup: {errorMessage(snapshot.cleanup.error)}</Typography>}</Stack>,
    ] }))} footer={snapshots && <PaginationControls count={snapshots.total} page={snapshots.page - 1} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/>}/> 
    </Stack>}
    {view === 'logs' && <Stack spacing={2} role="tabpanel" id="backup-panel-logs" aria-labelledby="backup-section-logs">
    <Typography variant="h6" component="h3">Backup and restore operations</Typography>
    {jobs?.total === 0 && <Typography>No operations recorded.</Typography>}
    <AdminTable columns={['Started', 'Operation', 'Status', 'Updated']} empty="No operations recorded." rows={(jobs?.items ?? []).map(job => ({ key: job.id, cells: [date(job.created), <Stack key="operation"><Typography sx={{ textTransform: 'capitalize' }}>{job.kind.replaceAll('_', ' ')}</Typography><Typography variant="caption">{job.id}</Typography></Stack>,
      <Stack key="status"><Chip size="small" label={job.state.replaceAll('_', ' ')} color={job.state === 'complete' ? 'success' : job.state === 'failed' || job.state === 'reconciliation_required' ? 'error' : 'default'}/><Typography variant="caption">{job.phase.replaceAll('_', ' ')}</Typography>{job.error && <Typography color="error">{errorMessage(job.error)}</Typography>}{job.result.cleanup?.error && <Typography color="warning.main">Backup verified; cleanup failed: {errorMessage(job.result.cleanup.error)}</Typography>}</Stack>, date(job.updated),
    ] }))}/>
    {jobs && <PaginationControls count={jobs.total} page={jobs.page - 1} size={operationSize} onPage={setOperationPage} onSize={value => { setOperationSize(value); setOperationPage(0); }}/>} 
    </Stack>}
  </Stack>;
}
