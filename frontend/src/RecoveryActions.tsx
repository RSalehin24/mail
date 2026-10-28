import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { api, errorMessage, validMailboxPassword, type Authorize, type Notify } from './api';
import { retentionLabels, useRecoveryPolicy, type Retention } from './RecoverySettings';

export interface DeletedMailbox { id: string; email: string; display_name: string; state: string; retention: Retention; deleted_at: number; deadline: number | null; recovery_available: boolean; error?: string; phase?: string; deletion_pending?: boolean }
interface Job { request_id: string; state: string; error: string; phase?: string }
export function RecoveryAction({ email, mode, record, authorize, notify, onClose, onChanged }: {
  email: string; mode: 'delete' | 'restore' | 'remove' | 'reconcile'; record?: DeletedMailbox;
  authorize: Authorize; notify: Notify; onClose: () => void; onChanged: () => void;
}) {
  const { policy, error: policyError } = useRecoveryPolicy();
  const [retention, setRetention] = useState<Retention | ''>('');
  const [confirmation, setConfirmation] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [identifier] = useState(() => crypto.randomUUID());
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.state)) return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<Job>(`/recovery/operations/${identifier}`);
        if (!active) return;
        setJob(result);
        if (result.state === 'complete') { notify(mode === 'restore' ? 'Mailbox restored' : mode === 'delete' ? 'Mailbox deleted' : mode === 'reconcile' ? 'Operation reconciled' : 'Recovery copy removed'); onChanged(); onClose(); return; }
        if (['failed', 'reconciliation_required'].includes(result.state)) { setError(`${errorMessage(result.error)} Review this operation before retrying.`); return; }
      } catch (failure) { if (active) setError((failure as Error).message); }
      if (active) timer = setTimeout(() => { void poll(); }, 2000);
    }
    timer = setTimeout(() => { void poll(); }, 1000);
    return () => { active = false; clearTimeout(timer); };
  }, [job?.state, identifier, mode, notify, onChanged, onClose]);
  const selectedRetention = retention || policy?.default_retention || '7_days';
  const permanent = mode === 'delete' && selectedRetention === 'none';
  const protectedReason = mode === 'delete' ? policy?.protected[email] : undefined;
  const allowed = !!policy && !protectedReason && (mode !== 'delete' || policy.enabled) && (!(permanent || mode === 'remove') || policy.permanent_deletion_enabled);
  const valid = confirmation === email && acknowledged && (mode !== 'restore' || (validMailboxPassword(password) && password === repeat));
  const waiting = !!job && ['queued', 'running'].includes(job.state);
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose}>
    <DialogTitle>{mode === 'delete' ? 'Delete Mailbox' : mode === 'restore' ? 'Restore Mailbox' : mode === 'reconcile' ? 'Reconcile Operation' : 'Permanently Remove Recovery Copy'}</DialogTitle>
    <DialogContent><Stack spacing={2.5} sx={{ pt: 1 }}>
      <Typography sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{email}</Typography>
      {(error || policyError) && <Alert severity="error">{error || policyError}</Alert>}
      {protectedReason && <Alert severity="warning">Protected: {protectedReason}. Remove its protection in Settings before deleting it.</Alert>}
      {mode === 'delete' && policy && !policy.enabled && <Alert severity="info">Mailbox deletion is disabled in Settings.</Alert>}
      {waiting && <Alert severity="info" role="status">{job?.phase ? job.phase.replaceAll('_', ' ') : job?.state === 'queued' ? 'Queued' : 'Processing'} · You can close this dialog. The operation will continue.</Alert>}
      {mode === 'delete' && <>
        <TextField select label="Recovery retention" value={selectedRetention} disabled={busy || waiting} onChange={event => setRetention(event.target.value as Retention)}>
          {Object.entries(retentionLabels).map(([value, label]) => <MenuItem key={value} value={value} disabled={value === 'none' && !policy?.permanent_deletion_enabled}>{label}</MenuItem>)}
        </TextField>
        {!permanent && <Alert severity="info">Messages, folders, flags, name, and notes will be retained {selectedRetention === 'forever' ? 'without an expiry date' : `for ${selectedRetention === '7_days' ? '7' : '30'} days`}. The mail service briefly pauses while a verified encrypted recovery copy is created.</Alert>}
      </>}
      {(permanent || mode === 'remove') && <Alert severity="warning">This action cannot be undone. Separately retained backups may still contain historical data until their retention expires.</Alert>}
      {mode === 'reconcile' && <Alert severity="info">Check the recorded outcome and resume a paused mail service. Existing addresses will not be overwritten, and uncertain imports will not be repeated.</Alert>}
      {mode === 'restore' && <>
        <TextField autoComplete="new-password" type="password" label="New mailbox password" value={password} disabled={busy || waiting} onChange={event => setPassword(event.target.value)} helperText="At least 16 characters; at most 72 UTF-8 bytes."/>
        <TextField autoComplete="new-password" type="password" label="Confirm new mailbox password" value={repeat} disabled={busy || waiting} onChange={event => setRepeat(event.target.value)} error={!!repeat && password !== repeat}/>
        <Alert severity="info">Restore preserves the mailbox contents and requires this new password. An existing address will not be overwritten or merged.</Alert>
      </>}
      <TextField label="Type the mailbox address to confirm" value={confirmation} disabled={busy || waiting} onChange={event => setConfirmation(event.target.value)}/>
      <FormControlLabel control={<Checkbox checked={acknowledged} disabled={busy || waiting} onChange={(_, checked) => setAcknowledged(checked)}/>} label={mode === 'restore' ? 'I understand that mail clients will need the new password.' : 'I understand the selected deletion and retention settings.'}/>
    </Stack></DialogContent>
    <DialogActions>{error && !job && <Button onClick={() => { void api<Job>(`/recovery/operations/${identifier}`).then(result => { setJob(result); if (result.state === 'complete') { onChanged(); onClose(); } }).catch(failure => setError((failure as Error).message)); }}>Check operation</Button>}<Button onClick={onClose} disabled={busy}>{waiting ? 'Close' : 'Cancel'}</Button>
      <Button variant="contained" color={mode === 'restore' ? 'primary' : 'error'} disabled={busy || !!job || !valid || !allowed} onClick={() => authorize(async () => {
        setBusy(true); setError('');
        try {
          const values = { request_id: identifier, confirmation, acknowledged };
          const result = mode === 'delete' ? await api<Job>('/recovery/delete', { ...values, email, retention: selectedRetention, permanent, policy_revision: policy!.revision })
            : await api<Job>(`/recovery/mailboxes/${record!.id}/${mode}`, { ...values, ...(mode === 'restore' ? { password } : {}) });
          setJob(result); setPassword(''); setRepeat('');
          if (result.state === 'complete') { notify('Operation completed'); onChanged(); onClose(); }
        } catch (failure) { setError((failure as Error).message); throw failure; }
        finally { setBusy(false); }
      })}>{mode === 'restore' ? 'Restore mailbox' : mode === 'delete' ? 'Delete mailbox' : mode === 'reconcile' ? 'Reconcile operation' : 'Permanently remove'}</Button>
    </DialogActions>
  </Dialog>;
}
