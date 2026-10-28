import { AdminTable } from './AdminTable';
import { usePagination } from './PaginationControls';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Stack, Switch, TextField, Typography, Checkbox } from '@mui/material';
import { api, type Authorize, type Notify } from './api';

export type Retention = '7_days' | '30_days' | 'forever' | 'none';
export const retentionLabels: Record<Retention, string> = { '7_days': 'Retain for 7 days', '30_days': 'Retain for 30 days', forever: 'Keep forever', none: 'Don’t Retain' };
export interface RecoveryPolicy { schema_version: 1; revision: number; enabled: boolean; permanent_deletion_enabled: boolean; default_retention: Retention; protected: Record<string, string> }
export function useRecoveryPolicy() {
  const [policy, setPolicy] = useState<RecoveryPolicy | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try { const result = await api<{ policy: RecoveryPolicy }>('/recovery/policy'); setPolicy(result.policy); setError(''); }
    catch (failure) { setError((failure as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return { policy, setPolicy, error, load };
}

export function RecoverySettings({ authorize, notify, section = 'deletion' }: { authorize: Authorize; notify: Notify; section?: 'deletion' | 'protected' }) {
  const protecting = section === 'protected';
  const { policy, setPolicy, error: loadError, load } = useRecoveryPolicy();
  const [original, setOriginal] = useState<RecoveryPolicy | null>(null);
  const [dirty, setDirty] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [email, setEmail] = useState('');
  const [reason, setReason] = useState('');
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removalAcknowledged, setRemovalAcknowledged] = useState(false);
  const form = useRef<HTMLDivElement>(null);
  const reasonInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (policy && !original) setOriginal(policy); }, [policy, original]);
  function update(value: Partial<RecoveryPolicy>) { if (policy) { setPolicy({ ...policy, ...value }); setDirty(true); setAcknowledged(false); } }
  const protectedPages = usePagination(Object.entries(policy?.protected ?? {}).sort(([a], [b]) => a.localeCompare(b)));
  const consequential = !!(policy && original && (Object.keys(original.protected).some(address => !(address in policy.protected)) || (policy.permanent_deletion_enabled && !original.permanent_deletion_enabled) || (policy.default_retention === 'none' && original.default_retention !== 'none')));
  function savePolicy(next: RecoveryPolicy, message: string, onSaved?: () => void) {
    authorize(async () => {
      setBusy(true); setError('');
      try {
        const result = await api<{ policy: RecoveryPolicy }>('/recovery/policy', { policy: next, acknowledged: true }, 'PUT');
        setPolicy(result.policy); setOriginal(result.policy); setDirty(false); setAcknowledged(false);
        onSaved?.(); notify(message);
      } catch (failure) { setError((failure as Error).message); throw failure; }
      finally { setBusy(false); }
    });
  }
  function resetForm() { setEmail(''); setReason(''); setEditing(false); }
  function saveProtection() {
    if (!policy) return;
    const address = email.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9._-]*@rsalehin24\.me$/.test(address)) { setError('Enter a mailbox address at rsalehin24.me.'); return; }
    if (!editing && address in policy.protected) { setError('This mailbox is already protected. Use Edit reason to change it.'); return; }
    savePolicy({ ...policy, protected: { ...policy.protected, [address]: reason.trim() } }, editing ? 'Protection reason saved.' : 'Mailbox protection added.', resetForm);
  }
  const saveButton = <Button variant="contained" sx={{ alignSelf: 'start' }} disabled={busy || !dirty || (consequential && !acknowledged)} onClick={() => { if (policy) savePolicy(policy, 'Deletion policy saved.'); }}>Save deletion policy</Button>;
  return <Paper variant="outlined" component="section" sx={{ p: { xs: 2, sm: 3 } }}><Stack spacing={2.5}>
    <Typography variant="h6">{protecting ? 'Protected Mailboxes' : 'Deletion and Recovery'}</Typography>
    {(error || loadError) && <Alert severity="error" action={loadError ? <Button onClick={() => { void load(); }}>Retry</Button> : undefined}>{error || loadError}</Alert>}
    {!policy && !loadError && <Typography role="status">Loading deletion policy…</Typography>}
    {policy && <>
      {!protecting && <>
      <FormControlLabel control={<Switch checked={policy.enabled} disabled={busy} onChange={(_, checked) => update({ enabled: checked })}/>} label="Enable mailbox deletion"/>
      <TextField select label="Default retention" value={policy.default_retention} disabled={busy} onChange={event => update({ default_retention: event.target.value as Retention })}>
        {Object.entries(retentionLabels).map(([value, label]) => <MenuItem value={value} key={value} disabled={value === 'none' && !policy.permanent_deletion_enabled}>{label}</MenuItem>)}
      </TextField>
      <FormControlLabel control={<Switch checked={policy.permanent_deletion_enabled} disabled={busy} onChange={(_, checked) => update({ permanent_deletion_enabled: checked, ...(!checked && policy.default_retention === 'none' ? { default_retention: '7_days' as const } : {}) })}/>} label="Allow permanent deletion"/>
      </>}
      {protecting && <>
      <Typography variant="body2" color="text.secondary">Protected mailboxes cannot be deleted. Add a mailbox or edit the reason for its protection.</Typography>
      <Stack ref={form} direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
        <TextField label="Mailbox to protect" value={email} disabled={busy || editing} onChange={event => setEmail(event.target.value)} size="small"/>
        <TextField inputRef={reasonInput} label="Protection reason" value={reason} disabled={busy} onChange={event => setReason(event.target.value)} size="small"/>
        <Button variant="contained" sx={{ whiteSpace: 'nowrap' }} disabled={busy || !email.trim() || !reason.trim() || (editing && reason.trim() === policy.protected[email])} onClick={saveProtection}>{editing ? 'Save reason' : 'Add protection'}</Button>
        {editing && <Button disabled={busy} sx={{ whiteSpace: 'nowrap' }} onClick={resetForm}>Cancel edit</Button>}
      </Stack>
      <Box>
        <AdminTable columns={['Mailbox', 'Reason', 'Actions']} empty="No mailboxes are protected." rows={protectedPages.rows.map(([address, explanation]) => ({ key: address, cells: [address, explanation, <Stack key="actions" direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ justifyContent: 'center', alignItems: 'stretch' }}><Button variant="outlined" sx={{ whiteSpace: 'nowrap' }} disabled={busy} onClick={() => { setEmail(address); setReason(explanation); setEditing(true); form.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); reasonInput.current?.focus({ preventScroll: true }); }}>Edit reason</Button><Button variant="outlined" color="error" sx={{ whiteSpace: 'nowrap' }} disabled={busy} aria-label={`Remove protection from ${address}`} onClick={() => { setRemoving(address); setRemovalAcknowledged(false); setError(''); }}>Remove protection</Button></Stack>] }))}/>
        {protectedPages.controls}
        {!Object.keys(policy.protected).length && <Typography variant="body2" color="text.secondary">No mailboxes are protected.</Typography>}
      </Box>
      <Dialog open={!!removing} onClose={busy ? undefined : () => setRemoving(null)} fullWidth maxWidth="sm" aria-labelledby="remove-protection-title">
        <DialogTitle id="remove-protection-title">Remove mailbox protection?</DialogTitle>
        <DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Typography sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{removing}</Typography>
          <Alert severity="warning">This removes only the deletion safeguard. The mailbox and its mail stay intact. The mailbox can then be deleted through its configuration.</Alert>
          <FormControlLabel control={<Checkbox checked={removalAcknowledged} disabled={busy} onChange={(_, checked) => setRemovalAcknowledged(checked)}/>} label="I understand the consequences of removing this protection."/>
        </Stack></DialogContent>
        <DialogActions><Button disabled={busy} onClick={() => setRemoving(null)}>Cancel</Button><Button color="error" variant="contained" disabled={busy || !removalAcknowledged} onClick={() => {
          if (!removing) return;
          const next = { ...policy.protected }; delete next[removing];
          savePolicy({ ...policy, protected: next }, 'Mailbox protection removed.', () => { if (email === removing) resetForm(); setRemoving(null); });
        }}>Remove protection</Button></DialogActions>
      </Dialog>
      </>}

      {!protecting && consequential && <Alert severity="warning"><FormControlLabel control={<Checkbox checked={acknowledged} onChange={(_, checked) => setAcknowledged(checked)}/>} label={protecting ? 'I understand that removing protection can stop operational emails and allow this mailbox to be deleted.' : 'I understand that permanent deletion cannot be restored from a new recovery copy.'}/></Alert>}
      {!protecting && saveButton}
    </>}
  </Stack></Paper>;
}
