import { useState } from 'react';
import MoreHorizRounded from '@mui/icons-material/MoreHorizRounded';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Menu, MenuItem, Stack, Switch, TextField, Tooltip, Typography } from '@mui/material';
import { LoadingState } from './PageState';
import { AdminTable } from './AdminTable';
import { api, errorMessage, type Authorize, type Notify } from './api';
import { usePagination } from './PaginationControls';
import { formatDateTime } from './format';
import { PageHeader, StateDot } from './ui';
import { useForwarding, type Listing, type Rule } from './useForwarding';

// Short label for the header, full explanation on hover; only a failed sync is loud enough for an alert.
const CONNECTION_STATUS: Record<string, { label: string; hint: string }> = {
  not_connected: { label: 'Cloudflare not connected', hint: 'Saved rules do not change your existing Cloudflare routes until forwarding is connected.' },
  pending: { label: 'Syncing with Cloudflare', hint: 'Changes take effect after synchronization.' },
  verification_pending: { label: 'Verification pending', hint: 'Destination verification or local delivery setup is pending. Existing routes are kept until local delivery is verified.' },
  stale: { label: 'Connection check overdue', hint: 'The Cloudflare connection has not been verified recently. Refresh or contact the operator.' },
};
const SYNC_FAILED_MESSAGE = 'Cloudflare synchronization failed. Some changes may still be pending. Contact the operator to restore the connection.';

export function Forwarding({ authorize, notify, mailbox }: { authorize: Authorize; notify: Notify; mailbox?: string }) {
  const { result, accounts, busy, error, load, perform } = useForwarding(authorize, notify);
  const [form, setForm] = useState<{ email: string; target: string; enabled: boolean; editing: boolean } | null>(null);
  const [menu, setMenu] = useState<{ anchor: HTMLElement; rule: Rule } | null>(null);
  const [confirm, setConfirm] = useState<{rule: Rule; mode: 'remove' | 'retry'} | null>(null);
  const pages = usePagination((result?.rules ?? []).filter(rule => !mailbox || rule.email === mailbox));
  const connection = result?.connection;
  const failed = connection?.state === 'sync_failed';
  const unsettled = !connection || ['pending', 'stale', 'not_connected', 'sync_failed'].includes(connection.state);
  const selected = result?.rules.find(rule => rule.email === mailbox);
  function openForm(rule?: Rule) {
    setForm(rule ? { email: rule.email, target: rule.destination, enabled: rule.enabled, editing: true } : { email: mailbox ?? '', target: '', enabled: true, editing: false });
  }
  function save() {
    if (!form || !result) return;
    perform(() => api<Listing>('/forwarding', { email: form.email, destination: form.target.trim(), enabled: form.enabled, revision: result.revision }, 'PUT'),
      'Forwarding rule saved. Cloudflare must synchronize it and verify the destination before forwarding starts.', () => setForm(null));
  }
  const dot = connection && <StateDot tone={connection.state === 'connected' ? 'success' : connection.state === 'pending' || connection.state === 'verification_pending' ? 'warning' : 'error'}
    label={connection.state === 'connected' ? 'Connected to Cloudflare' : failed ? 'Cloudflare synchronization failed' : `${(CONNECTION_STATUS[connection.state] ?? CONNECTION_STATUS.not_connected).label}. ${(CONNECTION_STATUS[connection.state] ?? CONNECTION_STATUS.not_connected).hint}`}/>;
  const openButton = <Button variant="contained" disabled={busy || !result} onClick={() => openForm(selected)}>{selected ? 'Edit forwarding' : 'Add forwarding'}</Button>;
  return <Stack spacing={3}>
    {mailbox ? <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', gap: 1 }}><Typography variant="h6" component="h2" sx={{ position: 'relative', display: 'inline-block', pr: 2.5 }}>Forwarding{dot && <Box sx={{ position: 'absolute', top: 4, right: 0 }}>{dot}</Box>}</Typography>{openButton}</Stack>
      : <PageHeader title="Forwarding" badge={dot} action={openButton}/>}
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Refresh</Button>}>{error}</Alert>}
    {failed && <Alert severity="error">{SYNC_FAILED_MESSAGE}</Alert>}
    {!result && !error && <LoadingState label="Loading forwarding settings…"/>}
    <AdminTable columns={['Mailbox', 'Forward To', 'Local Copy', 'Status', 'Actions']} empty="No forwarding configured." rows={pages.rows.map(rule => ({key: rule.email, cells: [rule.email, rule.destination, 'Always kept',
      <Stack key="status" spacing={0.75}><Chip size="small" variant="outlined" color={!rule.enabled ? 'default' : failed || rule.last_result?.state === 'failed' ? 'error' : unsettled || rule.attention || !rule.synchronized || !rule.local_delivery_verified || !rule.destination_verified ? 'warning' : 'success'} label={!rule.enabled ? 'Disabled' : failed ? 'Synchronization failed' : rule.last_result?.state === 'failed' ? 'Forwarding failed' : connection?.state === 'stale' ? 'Connection check overdue' : !rule.synchronized ? 'Awaiting synchronization' : !rule.local_delivery_verified ? 'Local delivery pending' : !rule.destination_verified ? 'Verify destination' : 'Connected'}/>{rule.last_result && <Typography variant="caption">{rule.last_result.state === 'accepted' ? 'SMTP accepted' : rule.last_result.state.replaceAll('_', ' ')} · {formatDateTime(rule.last_result.updated)}</Typography>}{rule.last_result?.error && <Typography variant="caption" color="error">{errorMessage(rule.last_result.error)}</Typography>}{rule.pending > 0 && <Typography variant="caption">{rule.pending} queued</Typography>}</Stack>,
      <Tooltip key="actions" title="Rule actions"><IconButton aria-label={`Actions for ${rule.email}`} disabled={busy} onClick={event => setMenu({ anchor: event.currentTarget, rule })}><MoreHorizRounded/></IconButton></Tooltip>,
    ]}))} footer={!mailbox && pages.controls}/>
    <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)} slotProps={{ paper: { sx: { minWidth: 220, borderRadius: 2, mt: 0.5 } } }}>
      {menu && !menu.rule.destination_verified && <MenuItem component="button" className="mailbox-menu-item" disabled={['queued', 'requesting'].includes(menu.rule.verification_state ?? '')} onClick={() => { const rule = menu.rule; setMenu(null); perform(() => api<Listing>('/forwarding/verify', { email: rule.email }), 'Verification check queued. Check that address for Cloudflare’s verification email and click its link.'); }}>{menu.rule.verification_state === 'sent' ? 'Check verification' : 'Verify destination'}</MenuItem>}
      {menu && <MenuItem component="button" className="mailbox-menu-item" onClick={() => { const rule = menu.rule; setMenu(null); openForm(rule); }}>Edit</MenuItem>}
      {menu && <MenuItem component="button" className="mailbox-menu-item" onClick={() => { const rule = menu.rule; setMenu(null); perform(() => api<Listing>('/forwarding', { email: rule.email, destination: rule.destination, enabled: !rule.enabled, revision: result!.revision }, 'PUT'), rule.enabled ? 'Forwarding disabled.' : 'Forwarding enabled.'); }}>{menu.rule.enabled ? 'Disable' : 'Enable'}</MenuItem>}
      {menu && menu.rule.attention > 0 && menu.rule.enabled && <MenuItem component="button" className="mailbox-menu-item" onClick={() => { const rule = menu.rule; setMenu(null); setConfirm({ rule, mode: 'retry' }); }}>Retry failed copies</MenuItem>}
      {menu && <MenuItem component="button" className="mailbox-menu-item" sx={{ color: 'error.main' }} onClick={() => { const rule = menu.rule; setMenu(null); setConfirm({ rule, mode: 'remove' }); }}>Remove</MenuItem>}
    </Menu>
    <Dialog open={!!form} onClose={busy ? undefined : () => setForm(null)} maxWidth="sm" fullWidth>
      <DialogTitle>{form?.editing ? 'Edit forwarding' : 'Add forwarding'}</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
        {error && <Alert severity="error">{error}</Alert>}
        <TextField select label="Mailbox" value={form?.email ?? ''} disabled={busy || !!form?.editing || !!mailbox} onChange={event => { const value = event.target.value; const rule = result?.rules.find(row => row.email === value); setForm(current => current && { ...current, email: value, target: rule?.destination ?? '', enabled: rule?.enabled ?? true, editing: !!rule }); }}>{accounts.map(account => <MenuItem key={account.email} value={account.email}>{account.email}</MenuItem>)}</TextField>
        <TextField label="Forward to" type="email" value={form?.target ?? ''} disabled={busy} onChange={event => setForm(current => current && { ...current, target: event.target.value })} placeholder="recipient@example.com"/>
        <FormControlLabel label="Enable forwarding" control={<Switch checked={form?.enabled ?? true} disabled={busy} onChange={(_, value) => setForm(current => current && { ...current, enabled: value })}/>}/>
      </Stack></DialogContent>
      <DialogActions><Button disabled={busy} onClick={() => setForm(null)}>Cancel</Button><Button variant="contained" disabled={busy || !form?.email || !form.target.trim()} onClick={save}>Save forwarding</Button></DialogActions>
    </Dialog>
    <Dialog open={!!confirm} onClose={busy ? undefined : () => setConfirm(null)} maxWidth="sm" fullWidth>
      <DialogTitle>{confirm?.mode === 'retry' ? 'Retry forwarding?' : 'Remove forwarding?'}</DialogTitle>
      <DialogContent><Typography sx={{overflowWrap:'anywhere'}}>{confirm?.rule.email} → {confirm?.rule.destination}</Typography><Typography sx={{mt:2}}>{confirm?.mode === 'retry' ? 'Copies with an uncertain SMTP outcome may already have arrived. Retrying may send a duplicate.' : 'This stops future forwarded copies. Mailbox messages are preserved.'}</Typography>{error && <Alert severity="error">{error}</Alert>}</DialogContent>
      <DialogActions><Button disabled={busy} onClick={() => setConfirm(null)}>Cancel</Button><Button variant="contained" disabled={busy} onClick={() => { if (!confirm || !result) return; const action = confirm; perform(() => api<Listing>(`/forwarding/${action.mode}`, action.mode === 'retry' ? {email:action.rule.email,acknowledged:true} : {email:action.rule.email,revision:result.revision}), action.mode === 'retry' ? 'Forwarding copies queued for retry.' : 'Forwarding removed.', () => setConfirm(null)); }}>{confirm?.mode === 'retry' ? 'Retry copies' : 'Remove forwarding'}</Button></DialogActions>
    </Dialog>
  </Stack>;
}
