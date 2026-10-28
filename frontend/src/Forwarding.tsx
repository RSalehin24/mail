import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Stack, Switch, Tab, Tabs, TextField, Typography } from '@mui/material';
import { AdminTable } from './AdminTable';
import { api, errorMessage, type Authorize, type Mailbox, type Notify } from './api';
import { usePagination } from './PaginationControls';
import { formatDateTime } from './format';

interface Rule { email: string; destination: string; enabled: boolean; keep_local: true; pending: number; attention: number; destination_verified?: boolean; local_delivery_verified?: boolean; synchronized?: boolean; verification_state?: string; last_result: {state: string; error: string; updated: number} | null }
interface Listing { connection?: {configured: boolean; state: string; observed_at?: number; reason?: string; mailboxes?: Record<string, {local_delivery_verified: boolean}>; connected_mailboxes?: string[]}; rules: Rule[]; revision: number; mode: string; keep_local: true; worker?: { observed_at: number; outcome: string; error: string } | null }

export function Forwarding({ authorize, notify, mailbox }: { authorize: Authorize; notify: Notify; mailbox?: string }) {
  const [tab, setTab] = useState(() => new URLSearchParams(window.location.search).get('tab') === 'incoming' ? 'incoming' : 'rules');
  const [result, setResult] = useState<Listing | null>(null);
  const [accounts, setAccounts] = useState<Mailbox[]>([]);
  const [email, setEmail] = useState(mailbox ?? '');
  const [target, setTarget] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState<{rule: Rule; mode: 'remove' | 'retry'} | null>(null);
  const load = useCallback(async () => {
    try {
      const [rules, mailboxes] = await Promise.all([api<Listing>('/forwarding'), api<Mailbox[]>('/accounts')]);
      setResult(rules); setAccounts(mailboxes); setError('');
    } catch (failure) { setError((failure as Error).message); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => { void load(); }, 15000); return () => window.clearInterval(timer); }, [load]);
  const selected = result?.rules.find(rule => rule.email === mailbox);
  useEffect(() => { if (mailbox && result && !dirty) { setTarget(selected?.destination ?? ''); setEnabled(selected?.enabled ?? true); setEditing(!!selected); } }, [mailbox, result, selected, dirty]);
  const pages = usePagination((result?.rules ?? []).filter(rule => !mailbox || rule.email === mailbox));
  const incomingPages = usePagination(accounts.filter(account => !mailbox || account.email === mailbox));
  const connection = result?.connection;
  const failed = connection?.state === 'sync_failed';
  const unsettled = !connection || ['pending', 'stale', 'not_connected', 'sync_failed'].includes(connection.state);
  function perform(operation: () => Promise<Listing>, message: string) {
    authorize(async () => {
      setBusy(true); setError('');
      try {
        setResult(await operation()); setDirty(false); setConfirm(null); notify(message);
        if (!mailbox) { setEmail(''); setTarget(''); setEnabled(true); setEditing(false); }
      } catch (failure) { setError((failure as Error).message); throw failure; }
      finally { setBusy(false); }
    });
  }
  return <Stack spacing={3}>
    {!mailbox && <Typography variant="h4" component="h1">Forwarding</Typography>}
    <Tabs value={tab} onChange={(_, value: string) => { setTab(value); if (!mailbox) { const url = new URL(window.location.href); url.searchParams.set('tab', value); window.history.replaceState(null, '', url); } }} aria-label="Forwarding sections">
      <Tab value="rules" label="Forwarding Rules" id="forwarding-rules-tab" aria-controls="forwarding-rules-panel"/>
      <Tab value="incoming" label="Incoming Delivery" id="forwarding-incoming-tab" aria-controls="forwarding-incoming-panel"/>
    </Tabs>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Refresh</Button>}>{error}</Alert>}
    <Stack hidden={tab !== 'rules'} role="tabpanel" id="forwarding-rules-panel" aria-labelledby="forwarding-rules-tab" spacing={3} sx={{display: tab === 'rules' ? 'flex' : 'none'}}>
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}><Stack spacing={2}>
      <Stack direction="row" sx={{justifyContent:'space-between',gap:1}}><Typography variant="h6" component="h2">{mailbox ? 'Forwarding' : editing ? 'Edit forwarding' : 'Add mailbox forwarding'}</Typography><Button disabled={busy} onClick={() => { void load(); }}>Refresh forwarding</Button></Stack>
      <Typography variant="body2" color="text.secondary">Keep incoming messages in the mailbox and forward a copy to an external address. Only new inbox messages are forwarded; existing messages stay untouched.</Typography>
      <Alert severity="info">Forwarding preserves the original sender, subject, and message body. A transparent forwarding connection must be verified before delivery can start.</Alert>
      {result?.connection && <Alert severity={result.connection.state === 'connected' ? 'success' : result.connection.state === 'sync_failed' ? 'error' : 'warning'}>{result.connection.state === 'connected' ? 'Cloudflare delivery and forwarding are synchronized. Local copies are always retained.' : result.connection.state === 'pending' ? 'Applying forwarding changes to Cloudflare. Changes take effect after synchronization.' : result.connection.state === 'verification_pending' ? 'Destination verification or local delivery setup is pending. Existing routes are preserved until local delivery is verified.' : result.connection.state === 'sync_failed' ? 'Cloudflare synchronization failed. Some changes may still be pending. Contact the operator to restore the connection.' : result.connection.state === 'stale' ? 'The Cloudflare connection has not been verified recently. Refresh or contact the operator.' : 'Forwarding delivery is not connected. Saved rules do not change your existing Cloudflare routes.'}</Alert>}
      {!result && !error && <Typography role="status">Loading forwarding settings…</Typography>}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        {!mailbox && <TextField select label="Mailbox" value={email} disabled={busy || editing} onChange={event => { const value = event.target.value; const rule = result?.rules.find(row => row.email === value); setEmail(value); setTarget(rule?.destination ?? ''); setEnabled(rule?.enabled ?? true); setEditing(!!rule); }} sx={{ flex: 1, minWidth: 0 }}>{accounts.map(account => <MenuItem key={account.email} value={account.email}>{account.email}</MenuItem>)}</TextField>}
        <TextField label="Forward to" type="email" value={target} disabled={busy} onChange={event => { setDirty(true); setTarget(event.target.value); }} placeholder="recipient@example.com" sx={{ flex: 1, minWidth: 0 }}/>
      </Stack>
      <FormControlLabel label="Enable forwarding" control={<Switch checked={enabled} disabled={busy} onChange={(_, value) => { setDirty(true); setEnabled(value); }}/>}/>
      <Typography variant="caption" color="text.secondary">A local copy is always kept. Disabling or removing forwarding does not delete mailbox messages.</Typography>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}><Button variant="contained" disabled={busy || !result || !email || !target.trim()} onClick={() => perform(() => api<Listing>('/forwarding', {email, destination: target.trim(), enabled, revision: result!.revision}, 'PUT'), 'Forwarding rule saved. Cloudflare must synchronize it and verify the destination before forwarding starts.')}>{editing ? 'Save forwarding' : 'Add forwarding'}</Button>
      {editing && !mailbox && <Button disabled={busy} onClick={() => { setEditing(false); setEmail(''); setTarget(''); setEnabled(true); }}>Cancel edit</Button>}
      {selected && <Button color="error" variant="outlined" disabled={busy} onClick={() => setConfirm({rule: selected, mode: 'remove'})}>Remove forwarding</Button>}</Stack>
    </Stack></Paper>
    <AdminTable columns={['Mailbox', 'Forward To', 'Local Copy', 'Status', 'Actions']} empty="No forwarding configured." rows={pages.rows.map(rule => ({key: rule.email, cells: [rule.email, rule.destination, 'Always kept',
      <Stack key="status" spacing={0.75}><Chip size="small" variant="outlined" color={!rule.enabled ? 'default' : failed || rule.last_result?.state === 'failed' ? 'error' : unsettled || rule.attention || !rule.synchronized || !rule.local_delivery_verified || !rule.destination_verified ? 'warning' : 'success'} label={!rule.enabled ? 'Disabled' : failed ? 'Synchronization failed' : rule.last_result?.state === 'failed' ? 'Forwarding failed' : connection?.state === 'stale' ? 'Connection check overdue' : !rule.synchronized ? 'Awaiting synchronization' : !rule.local_delivery_verified ? 'Local delivery pending' : !rule.destination_verified ? 'Verify destination' : 'Connected'}/>{rule.last_result && <Typography variant="caption">{rule.last_result.state === 'accepted' ? 'SMTP accepted' : rule.last_result.state.replaceAll('_', ' ')} · {formatDateTime(rule.last_result.updated)}</Typography>}{rule.last_result?.error && <Typography variant="caption" color="error">{errorMessage(rule.last_result.error)}</Typography>}{rule.pending > 0 && <Typography variant="caption">{rule.pending} queued</Typography>}</Stack>,
      <Stack key="actions" spacing={1} direction={{xs: 'column', md: 'row'}} sx={{justifyContent: 'center'}}>
        {!rule.destination_verified && <Button variant="outlined" disabled={busy || ['queued', 'requesting'].includes(rule.verification_state ?? '')} onClick={() => perform(() => api<Listing>('/forwarding/verify', { email: rule.email }), 'Verification check queued. Check that address for Cloudflare’s verification email and click its link.')}>{rule.verification_state === 'sent' ? 'Check verification' : 'Verify destination'}</Button>}
        {!mailbox && <Button variant="outlined" disabled={busy} onClick={() => { setEmail(rule.email); setTarget(rule.destination); setEnabled(rule.enabled); setEditing(true); window.scrollTo({top: 0, behavior: 'smooth'}); }}>Edit</Button>}
        <Button variant="outlined" disabled={busy} onClick={() => perform(() => api<Listing>('/forwarding', {email:rule.email,destination:rule.destination,enabled:!rule.enabled,revision:result!.revision},'PUT'), rule.enabled ? 'Forwarding disabled.' : 'Forwarding enabled.')}>{rule.enabled ? 'Disable' : 'Enable'}</Button>
        {rule.attention > 0 && rule.enabled && <Button variant="outlined" disabled={busy} onClick={() => setConfirm({rule,mode:'retry'})}>Retry failed copies</Button>}
        {!mailbox && <Button color="error" variant="outlined" disabled={busy} onClick={() => setConfirm({rule,mode:'remove'})}>Remove</Button>}
      </Stack>,
    ]}))} footer={!mailbox && pages.controls}/>
    </Stack>
    <Paper hidden={tab !== 'incoming'} role="tabpanel" id="forwarding-incoming-panel" aria-labelledby="forwarding-incoming-tab" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, display: tab === 'incoming' ? 'block' : 'none' }}><Stack spacing={2}>
      <Stack direction="row" sx={{justifyContent:'space-between',gap:1}}><Typography variant="h6" component="h2">Incoming delivery</Typography><Button disabled={busy} onClick={() => { void load(); }}>Refresh delivery</Button></Stack>
      <Typography variant="body2" color="text.secondary">New mailboxes receive a Cloudflare verification email directly on this server. Open it in your IMAP client and click its verification link to activate incoming routing. Verify delivery checks the status and retries synchronization; it does not replace that confirmation or resend an existing email.</Typography>
      <AdminTable columns={['Mailbox', 'Local delivery', 'Actions']} empty="No mailbox delivery connections." rows={incomingPages.rows.map(account => {
        const verified = connection?.mailboxes?.[account.email]?.local_delivery_verified;
        const connected = !unsettled && verified && connection?.connected_mailboxes?.includes(account.email);
        return {key: account.email, cells: [account.email,
          <Chip key="status" size="small" variant="outlined" color={failed ? 'error' : connected ? 'success' : 'warning'} label={failed ? 'Synchronization failed' : connected ? 'Connected' : verified ? 'Awaiting routing synchronization' : 'Verification required'}/>,
          !connected && <Button key="verify" variant="outlined" disabled={busy || !result} aria-label={`Verify delivery for ${account.email}`} onClick={() => perform(() => api<Listing>('/forwarding/verify-local', {email: account.email}), 'Delivery verification check queued. If unverified, open Cloudflare’s verification email in this mailbox and click its link. Status updates automatically.')}>Verify delivery</Button>,
        ]};
      })} footer={incomingPages.controls}/>
    </Stack></Paper>
    <Dialog open={!!confirm} onClose={busy ? undefined : () => setConfirm(null)} maxWidth="sm" fullWidth>
      <DialogTitle>{confirm?.mode === 'retry' ? 'Retry forwarding?' : 'Remove forwarding?'}</DialogTitle>
      <DialogContent><Typography sx={{overflowWrap:'anywhere'}}>{confirm?.rule.email} → {confirm?.rule.destination}</Typography><Typography sx={{mt:2}}>{confirm?.mode === 'retry' ? 'Copies with an uncertain SMTP outcome may already have arrived. Retrying may send a duplicate.' : 'This stops future forwarded copies. Mailbox messages are preserved.'}</Typography>{error && <Alert severity="error">{error}</Alert>}</DialogContent>
      <DialogActions><Button disabled={busy} onClick={() => setConfirm(null)}>Cancel</Button><Button variant="contained" disabled={busy} onClick={() => { if (!confirm || !result) return; const action = confirm; perform(() => api<Listing>(`/forwarding/${action.mode}`, action.mode === 'retry' ? {email:action.rule.email,acknowledged:true} : {email:action.rule.email,revision:result.revision}), action.mode === 'retry' ? 'Forwarding copies queued for retry.' : 'Forwarding removed.'); }}>{confirm?.mode === 'retry' ? 'Retry copies' : 'Remove forwarding'}</Button></DialogActions>
    </Dialog>
  </Stack>;
}
