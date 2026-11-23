import { useState } from 'react';
import { Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { ApiError, api, type Authorize, type Notify } from './api';
import { retentionLabels, useRecoveryPolicy, type Retention } from './RecoverySettings';

export interface DeletedMailbox { id: string; email: string; display_name: string; state: string; retention: Retention; deleted_at: number; deadline: number | null; recovery_available: boolean; error?: string; phase?: string; deletion_pending?: boolean }
interface Job { request_id: string; state: string; error: string }
export function RecoveryAction({ email, authorize, notify, onClose, onChanged }: {
  email: string; authorize: Authorize; notify: Notify; onClose: () => void; onChanged: () => void;
}) {
  const { policy, error: policyError } = useRecoveryPolicy();
  const [retention, setRetention] = useState<Retention | ''>('');
  const [confirmation, setConfirmation] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [identifier] = useState(() => crypto.randomUUID());
  const selectedRetention = retention || policy?.default_retention || '7_days';
  const permanent = selectedRetention === 'none';
  const protectedReason = policy?.protected[email];
  const allowed = !!policy && !protectedReason && policy.enabled && (!permanent || policy.permanent_deletion_enabled);
  const valid = confirmation === email && acknowledged;
  const needsLogin = (failure: unknown) => failure instanceof ApiError && ['recent_authentication_required', 'password_confirmation_required'].includes(failure.code);
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose}>
    <DialogTitle>Delete Mailbox</DialogTitle>
    <DialogContent><Stack spacing={2.5} sx={{ pt: 1 }}>
      <Typography sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{email}</Typography>
      {(error || policyError) && <Alert severity="error">{error || policyError}</Alert>}
      {protectedReason && <Alert severity="warning">Protected: {protectedReason}. Remove its protection in Settings before deleting it.</Alert>}
      {policy && !policy.enabled && <Alert severity="info">Mailbox deletion is disabled in Settings.</Alert>}
      <TextField select label="Recovery retention" value={selectedRetention} disabled={busy} onChange={event => setRetention(event.target.value as Retention)}>
        {Object.entries(retentionLabels).map(([value, label]) => <MenuItem key={value} value={value} disabled={value === 'none' && !policy?.permanent_deletion_enabled}>{label}</MenuItem>)}
      </TextField>
      {permanent ? <Alert severity="warning">This action cannot be undone. Separately retained backups may still contain historical data until their retention expires.</Alert>
        : <Alert severity="info">Messages, folders, flags, name, and notes are kept {selectedRetention === 'forever' ? 'without an expiry date' : `for ${selectedRetention === '7_days' ? '7' : '30'} days`}. Mail keeps flowing during the delete.</Alert>}
      <TextField label="Type the mailbox address to confirm" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)}/>
      <FormControlLabel control={<Checkbox checked={acknowledged} disabled={busy} onChange={(_, checked) => setAcknowledged(checked)}/>} label="I understand the selected deletion and retention settings."/>
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose} disabled={busy}>Cancel</Button>
      <Button variant="contained" color="error" disabled={busy || !valid || !allowed} onClick={() => authorize(async () => {
        setBusy(true); setError('');
        try {
          await api<Job>('/recovery/delete', { request_id: identifier, confirmation, acknowledged, email, retention: selectedRetention, permanent, policy_revision: policy!.revision });
          notify('Mailbox deleted'); onChanged(); onClose();
        } catch (failure) { if (!needsLogin(failure)) setError((failure as Error).message); throw failure; }
        finally { setBusy(false); }
      })}>Delete mailbox</Button>
    </DialogActions>
  </Dialog>;
}
