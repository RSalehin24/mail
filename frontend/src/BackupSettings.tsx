import { formatDate, formatTime, formatDateTime } from './format';
import CloudDoneOutlined from '@mui/icons-material/CloudDoneOutlined';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import ContentCopyRounded from '@mui/icons-material/ContentCopyRounded';
import RestoreRounded from '@mui/icons-material/RestoreRounded';
import { AdminTable } from './AdminTable';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Paper, Stack, Tab, Tabs, TextField, Tooltip, Typography } from '@mui/material';
import { api, errorMessage, cached, type Authorize, type Notify } from './api';
import { PaginationControls } from './PaginationControls';

interface Snapshot { id: string; cutoff: number; observed: number; verification?: { verified_at?: number }; temporary_pin?: boolean; cleanup?: { state?: string; error?: string } }
interface Job { id: string; kind: string; snapshot: string; state: string; phase: string; created: number; updated: number; error: string; result: { mailboxes?: string[]; missing?: string[]; bytes?: number; verified?: boolean; cleanup?: { state?: string; error?: string } } }
interface Listing<T> { busy: boolean; items: T[]; total: number; page: number; page_size: number; observation: { observed: number; error: string } | null }
const date = formatDateTime;
const backupDay = formatDate;
const backupTime = formatTime;

export function BackupSettings({ authorize, notify }: { authorize: Authorize; notify: Notify }) {
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
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Snapshot | null>(null);
  const [prepared, setPrepared] = useState<Job | null>(null);
  const [passwords, setPasswords] = useState<Record<string, string>>({});
  const [repeats, setRepeats] = useState<Record<string, string>>({});
  const [confirmation, setConfirmation] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState<{ key: string; id: string } | null>(null);
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
  useEffect(() => { let active = true; let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => { await load(); if (active) timer = setTimeout(() => { void refresh(); }, 3000); };
    void refresh(); return () => { active = false; clearTimeout(timer); };
  }, [load]);
  useEffect(() => {
    if (!prepared || !['queued', 'running'].includes(prepared.state)) return;
    let active = true; const timer = setTimeout(() => {
      void api<Job>(`/backups/operations/${prepared.id}`).then(value => { if (active) setPrepared(value); }).catch(failure => { if (active) setError((failure as Error).message); });
    }, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [prepared]);
  function submit(kind: string, snapshot = '', previous = '') {
    const key = `${kind}:${snapshot}:${previous}`;
    const id = pending?.key === key ? pending.id : crypto.randomUUID();
    setPending({ key, id });
    authorize(async () => {
      setBusy(true); setError('');
      try {
        const result = await api<Job>('/backups/operations', { request_id: id, kind, snapshot, previous, confirmation: snapshot, acknowledged: true, passwords: kind === 'restore' ? passwords : {} });
        setPending(null);
        if (kind === 'preflight') setPrepared(result);
        else { setSelected(null); setPrepared(null); setPasswords({}); setRepeats({}); if (kind !== 'refresh') selectView('logs'); notify(kind === 'restore' ? 'Mail restoration queued. Follow progress in Logs.' : 'Backup operation queued'); }
        await load();
      } catch (failure) { setError((failure as Error).message); throw failure; }
      finally { setBusy(false); }
    });
  }
  const missing = prepared?.result.missing ?? [];
  const valid = confirmation === selected?.id && acknowledged && missing.every(email => [...(passwords[email] ?? '')].length >= 16 && new TextEncoder().encode(passwords[email] ?? '').length <= 72 && passwords[email] === repeats[email]);
  const blocked = jobs?.busy;
  const logHasActions = jobs?.items.some(job => (job.state === 'failed' && job.kind === 'backup') || job.state === 'reconciliation_required' || (job.kind === 'preflight' && job.state === 'complete')) ?? false;
  return <Stack spacing={3}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
      <Box><Typography variant="h5" component="h2">Backup &amp; Recovery</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Verified restore points for your mailbox data.</Typography></Box>
      <Stack direction="row" spacing={1}><Button disabled={busy || blocked} onClick={() => submit('refresh')}>Refresh backups</Button><Button variant="contained" disabled={busy || blocked} onClick={() => submit('backup')}>Back up now</Button></Stack>
    </Stack>
    <Tabs value={view} onChange={(_, value: 'backups' | 'logs') => selectView(value)} aria-label="Backup and recovery sections">
      <Tab value="backups" label="Backups" id="backup-section-backups" aria-controls="backup-panel-backups"/>
      <Tab value="logs" label="Logs" id="backup-section-logs" aria-controls="backup-panel-logs"/>
    </Tabs>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Retry loading</Button>}>{error}</Alert>}
    {view === 'backups' && <Stack spacing={2} role="tabpanel" id="backup-panel-backups" aria-labelledby="backup-section-backups">
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 }, borderRadius: 3, bgcolor: 'action.hover' }}>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'flex-start' }}>
        <Box sx={{ p: 1, borderRadius: 2, bgcolor: 'background.paper', color: 'primary.main', display: { xs: 'none', sm: 'flex' } }}><CloudDoneOutlined/></Box>
        <Box sx={{ minWidth: 0, flex: 1 }}><Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Daily encrypted backups</Typography>
          <Typography variant="body2" color="text.secondary">3:00 AM · Asia/Dhaka · 7-day retention</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>The latest verified backup each day is kept. Recovery may temporarily retain extra snapshots.</Typography>
        </Box>
      </Stack>
    </Paper>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}><Typography variant="h6" sx={{ fontSize: '1rem' }}>Available backups</Typography>{snapshots && <Chip size="small" variant="outlined" label={snapshots.total}/>}</Stack>
      <Typography variant="caption" color="text.secondary">{snapshots?.observation ? `Last checked ${backupDay(snapshots.observation.observed)}, ${backupTime(snapshots.observation.observed)} Dhaka` : 'Waiting for the first repository check'}</Typography>
    </Stack>
    {blocked && <Alert severity="info" action={<Button color="inherit" onClick={() => selectView('logs')}>View logs</Button>}>A backup or recovery operation is in progress or needs reconciliation. Follow its status in Logs.</Alert>}
    {snapshots?.observation?.error && <Alert severity="warning">{errorMessage(snapshots.observation.error)} Previously observed backups may have changed.</Alert>}
    {!snapshots && !error && <Typography role="status">Loading backups…</Typography>}
    <AdminTable columns={['Backup Date', 'Snapshot', 'Verification', 'Actions']} empty="No backups recorded." rows={(snapshots?.items ?? []).map(snapshot => ({ key: snapshot.id, cells: [
      <Stack key="date" spacing={0.5}><Typography variant="body2" sx={{ fontWeight: 600 }}>{backupDay(snapshot.cutoff)}</Typography><Typography variant="caption" color="text.secondary">{backupTime(snapshot.cutoff)} · Dhaka</Typography></Stack>,
      <Stack key="snapshot" direction="row" spacing={0.5} sx={{ alignItems: 'center', justifyContent: { xs: 'flex-start', md: 'center' } }}><Tooltip title={snapshot.id}><Typography component="code" variant="body2" sx={{ fontFamily: 'monospace', bgcolor: 'action.hover', px: 1, py: 0.5, borderRadius: 1 }}>{snapshot.id.slice(0, 12)}…{snapshot.id.slice(-8)}</Typography></Tooltip><Tooltip title="Copy snapshot ID"><IconButton size="small" aria-label={`Copy snapshot ${snapshot.id}`} onClick={() => { void navigator.clipboard.writeText(snapshot.id).then(() => notify('Snapshot ID copied')).catch(() => notify('Could not copy snapshot ID')); }}><ContentCopyRounded sx={{ fontSize: 16 }}/></IconButton></Tooltip></Stack>,
      <Stack key="verification" spacing={0.75} sx={{ alignItems: { xs: 'flex-start', md: 'center' } }}><Chip size="small" variant="outlined" color={snapshot.verification?.verified_at ? 'success' : 'default'} icon={snapshot.verification?.verified_at ? <CheckCircleOutlineRounded/> : undefined} label={snapshot.verification?.verified_at ? 'Verified' : 'Not yet verified'}/>{snapshot.verification?.verified_at && <Typography variant="caption" color="text.secondary">{backupDay(snapshot.verification.verified_at)}, {backupTime(snapshot.verification.verified_at)}</Typography>}{snapshot.temporary_pin && <Chip label="Temporarily pinned"/>}{snapshot.cleanup?.error && <Typography color="error">Cleanup: {errorMessage(snapshot.cleanup.error)}</Typography>}</Stack>,
      <Button key="restore" startIcon={<RestoreRounded/>} variant="outlined" sx={{ whiteSpace: 'nowrap' }} disabled={busy || blocked} onClick={() => { setSelected(snapshot); setPrepared(null); setConfirmation(''); setAcknowledged(false); setPasswords({}); setRepeats({}); }}>Restore mail data</Button>,
    ] }))} footer={snapshots && <PaginationControls count={snapshots.total} page={snapshots.page - 1} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/>}/> 
    </Stack>}
    {view === 'logs' && <Stack spacing={2} role="tabpanel" id="backup-panel-logs" aria-labelledby="backup-section-logs">
    <Typography variant="h6" component="h3">Backup and restore operations</Typography>
    {jobs?.total === 0 && <Typography>No operations recorded.</Typography>}
    <AdminTable columns={['Started', 'Operation', 'Status', 'Updated', ...(logHasActions ? ['Actions'] : [])]} empty="No operations recorded." rows={(jobs?.items ?? []).map(job => ({ key: job.id, cells: [date(job.created), <Stack key="operation"><Typography sx={{ textTransform: 'capitalize' }}>{job.kind.replaceAll('_', ' ')}</Typography><Typography variant="caption">{job.id}</Typography></Stack>,
      <Stack key="status"><Chip size="small" label={job.state.replaceAll('_', ' ')} color={job.state === 'complete' ? 'success' : job.state === 'failed' || job.state === 'reconciliation_required' ? 'error' : 'default'}/><Typography variant="caption">{job.phase.replaceAll('_', ' ')}</Typography>{job.error && <Typography color="error">{errorMessage(job.error)}</Typography>}{job.result.cleanup?.error && <Typography color="warning.main">Backup verified; cleanup failed: {errorMessage(job.result.cleanup.error)}</Typography>}</Stack>, date(job.updated),
      ...(logHasActions ? [<Stack key="actions" spacing={1}>
        {job.state === 'failed' && job.kind === 'backup' && <Button disabled={busy || blocked} onClick={() => submit('backup', '', job.id)}>Retry backup</Button>}
        {job.state === 'reconciliation_required' && <Button disabled={busy} onClick={() => submit('reconcile', job.snapshot, job.id)}>Reconcile operation</Button>}
        {job.kind === 'preflight' && job.state === 'complete' && <Button disabled={busy || blocked} onClick={() => { void api<Job>(`/backups/operations/${job.id}`).then(value => { setSelected({ id: job.snapshot, cutoff: 0, observed: 0 }); setPrepared(value); setConfirmation(''); setAcknowledged(false); setPasswords({}); setRepeats({}); }).catch(failure => setError((failure as Error).message)); }}>Continue restoration</Button>}
      </Stack>] : []),
    ] }))}/>
    {jobs && <PaginationControls count={jobs.total} page={jobs.page - 1} size={operationSize} onPage={setOperationPage} onSize={value => { setOperationSize(value); setOperationPage(0); }}/>} 
    </Stack>}
    <Dialog open={!!selected} fullWidth maxWidth="sm" onClose={busy ? undefined : () => { setSelected(null); setPrepared(null); setPasswords({}); setRepeats({}); }}>
      <DialogTitle>Restore all mailbox data</DialogTitle><DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
        <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>Snapshot {selected?.id}</Typography>
        <Alert severity="info">Restore merges missing messages and folders into every mailbox in this backup. Newer mail and existing passwords are preserved. A verified safety backup is created before live changes.</Alert>
        {prepared && <Typography role="status">{prepared.state === 'complete' ? 'Integrity verified' : prepared.state.replaceAll('_', ' ')} · {prepared.phase.replaceAll('_', ' ')}</Typography>}
        {prepared?.error && <Alert severity="error">{errorMessage(prepared.error)}</Alert>}
        {prepared?.state === 'complete' && <>
          <Typography>{prepared.result.mailboxes?.length ?? 0} mailboxes · {missing.length} missing addresses to recreate</Typography>
          <Box sx={{ maxHeight: 160, overflow: 'auto' }}>{prepared.result.mailboxes?.map(email => <Typography variant="body2" key={email} sx={{ overflowWrap: 'anywhere' }}>{email}{missing.includes(email) ? ' · New password required' : ''}</Typography>)}</Box>
          {missing.map(email => <Stack spacing={1} key={email}><TextField type="password" autoComplete="new-password" label={`New password for ${email}`} value={passwords[email] ?? ''} onChange={event => setPasswords(current => ({ ...current, [email]: event.target.value }))} helperText="At least 16 characters; at most 72 UTF-8 bytes."/><TextField type="password" autoComplete="new-password" label={`Confirm password for ${email}`} value={repeats[email] ?? ''} onChange={event => setRepeats(current => ({ ...current, [email]: event.target.value }))}/></Stack>)}
          <TextField label="Type snapshot ID to confirm" value={confirmation} onChange={event => setConfirmation(event.target.value)}/>
          <FormControlLabel control={<Checkbox checked={acknowledged} onChange={(_, value) => setAcknowledged(value)}/>} label="Restore all mailboxes from this backup and recreate the listed missing addresses."/>
        </>}
      </Stack></DialogContent><DialogActions><Button disabled={busy} onClick={() => { setSelected(null); setPasswords({}); setRepeats({}); }}>Close</Button>
        {!prepared || prepared.state === 'failed' ? <Button disabled={busy} onClick={() => submit('preflight', selected!.id)}>Verify backup</Button> : <Button variant="contained" disabled={busy || prepared.state !== 'complete' || !valid} onClick={() => submit('restore', selected!.id, prepared.id)}>Restore mail data</Button>}
      </DialogActions>
    </Dialog>
  </Stack>;
}
