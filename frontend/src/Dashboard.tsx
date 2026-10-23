import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Card, CardContent, Chip, Collapse, Divider, Paper, Stack,
  Table, TableBody, TableCell, TableHead, TablePagination, TableRow, TextField, Typography } from '@mui/material';
import AddOutlined from '@mui/icons-material/AddOutlined';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';
import SearchOutlined from '@mui/icons-material/SearchOutlined';
import { api, type Authorize, type Diagnostics, type Mailbox, type Notify } from './api';
import { ClientConfig, DeleteDialog, PasswordDialog } from './MailboxDialogs';

export function Dashboard({ authorize, notify }: { authorize: Authorize; notify: Notify }) {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [reverse, setReverse] = useState(false);
  const [expanded, setExpanded] = useState('');
  const [dialog, setDialog] = useState<{ mode: 'create' | 'reset' | 'delete' | 'config'; mailbox?: Mailbox } | null>(null);
  const load = useCallback(async () => {
    setError('');
    try { const [accounts, health] = await Promise.all([api<Mailbox[]>('/accounts'), api<Diagnostics>('/diagnostics')]); setMailboxes(accounts); setDiagnostics(health); }
    catch (failure) { setError((failure as Error).message); }
  }, []);
  useEffect(() => { void load(); const timer = setInterval(() => {
    void api<Diagnostics>('/diagnostics').then(setDiagnostics).catch(() => setDiagnostics(null));
  }, 30000); return () => clearInterval(timer); }, [load]);
  const filtered = mailboxes.filter(mailbox => `${mailbox.email} ${mailbox.display_name} ${mailbox.notes}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => a.email.localeCompare(b.email) * (reverse ? -1 : 1));
  const rows = filtered.slice(page * 20, page * 20 + 20);
  const common = { authorize, notify, onClose: () => setDialog(null), onChanged: () => { void load(); } };
  function actions(mailbox: Mailbox) {
    return <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap' }}>
      <Button size="small" onClick={() => setDialog({ mode: 'config', mailbox })}>Configure</Button>
      <Button size="small" onClick={() => setDialog({ mode: 'reset', mailbox })}>Reset password</Button>
      <Button size="small" onClick={() => authorize(async () => {
        const result = await api<{ test_id: string; smtp_accepted: boolean }>(`/accounts/${encodeURIComponent(mailbox.email)}/test-sender`, {});
        notify(result.smtp_accepted ? `Sender test accepted: ${result.test_id}. Confirm receipt in Settings.` : 'Sender authorization or delivery failed.');
      })}>Test sender</Button>
      <Button size="small" color="error" disabled={!diagnostics?.capabilities.permanent_deletion} onClick={() => setDialog({ mode: 'delete', mailbox })}>Delete</Button>
    </Stack>;
  }
  function status(mailbox: Mailbox) {
    return <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}><Chip size="small" variant="outlined"
      color={mailbox.status === 'ready' ? 'success' : 'warning'} label={mailbox.status === 'ready' ? 'Incoming ready' : 'Reconciliation required'}/>
      <Chip size="small" variant="outlined" label={`Outgoing ${mailbox.outbound_readiness}`}/></Stack>;
  }
  return <>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
      <Box><Typography variant="overline" color="text.secondary">YOUR MAIL, IN ORDER</Typography><Typography variant="h4" component="h1">Mailboxes</Typography><Typography color="text.secondary">Manage incoming accounts and outgoing readiness.</Typography></Box>
      <Button variant="contained" startIcon={<AddOutlined/>} onClick={() => setDialog({ mode: 'create' })}>New mailbox</Button>
    </Stack>
    <Box className="stat-row">{[
      ['Server health', diagnostics?.host.summary ? 'Host telemetry observed' : 'Unknown / unavailable'],
      ['Brevo relay', diagnostics?.relay.state.replaceAll('_', ' ') || 'Unverified'],
      ['Inbound routing', diagnostics?.dns_mx.state === 'verified' ? diagnostics.dns_mx.records?.join(', ') || 'Verified' : 'MX observation unverified · Cloudflare retained'],
    ].map(([label, value]) => <Paper elevation={0} className="stat-card" key={label}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="body1" sx={{ mt: 0.8, fontWeight: 600 }}>{value}</Typography></Paper>)}</Box>
    {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}<br/>Previously displayed rows may be stale; refresh after reconciliation.</Alert>}
    {!!diagnostics?.host.unresolved?.length && <Alert severity="warning" sx={{ mb: 2 }}>Unresolved helper operations require operator reconciliation. Some messages may already have been removed after a failed deletion.</Alert>}
    {diagnostics?.host.summary?.certificates && <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Certificate: {diagnostics.host.summary.certificates.expiry ? `expires ${new Date(diagnostics.host.summary.certificates.expiry * 1000).toLocaleDateString()}` : 'expiry unknown'} · Renewal {diagnostics.host.summary.certificates.outcome || 'unverified'}</Typography>}
    <Paper variant="outlined" sx={{ borderRadius: 3, overflow: 'hidden' }}>
      <Stack direction="row" spacing={2} sx={{ p: 2.5, alignItems: 'center', justifyContent: 'space-between' }}>
        <TextField label="Search mailboxes" size="small" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} slotProps={{ input: { startAdornment: <SearchOutlined sx={{ mr: 1, color: 'text.secondary' }}/> } }}/>
        <Button aria-label="Refresh accounts" onClick={() => { void load(); }} startIcon={<RefreshOutlined/>}>Refresh</Button>
      </Stack><Divider/>
      <Box className="desktop-table"><Table aria-label="Mailboxes"><TableHead><TableRow>
        <TableCell><Button onClick={() => setReverse(!reverse)}>Mailbox {reverse ? '↓' : '↑'}</Button></TableCell><TableCell>Provisioning</TableCell><TableCell>Actions</TableCell>
      </TableRow></TableHead><TableBody>{rows.map(mailbox => <TableRow key={mailbox.email}>
        <TableCell><Typography sx={{ fontWeight: 600 }}>{mailbox.email}</Typography><Typography variant="body2" color="text.secondary">{mailbox.display_name || '—'}</Typography><Typography variant="caption" color="text.secondary">{mailbox.notes}</Typography></TableCell>
        <TableCell>{status(mailbox)}</TableCell><TableCell>{actions(mailbox)}</TableCell>
      </TableRow>)}</TableBody></Table></Box>
      <Box className="mailbox-cards" sx={{ p: 2 }}>{rows.map(mailbox => <Card variant="outlined" key={mailbox.email} sx={{ borderRadius: 2 }}><CardContent>
        <Button sx={{ textTransform: 'none', maxWidth: '100%', overflowWrap: 'anywhere' }} onClick={() => setExpanded(expanded === mailbox.email ? '' : mailbox.email)}>{mailbox.email}</Button>
        {status(mailbox)}<Collapse in={expanded === mailbox.email}><Typography sx={{ mt: 2 }}>{mailbox.display_name}</Typography><Typography variant="body2" color="text.secondary">{mailbox.notes}</Typography></Collapse>
        <Box sx={{ mt: 2 }}>{actions(mailbox)}</Box>
      </CardContent></Card>)}</Box>
      {!filtered.length && <Box sx={{ py: 7, textAlign: 'center' }}><Typography color="text.secondary">{search ? 'No matching mailboxes' : 'No accounts observed yet'}</Typography></Box>}
      <TablePagination component="div" count={filtered.length} page={page} rowsPerPage={20} rowsPerPageOptions={[20]} onPageChange={(_, next) => setPage(next)}/>
    </Paper>
    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>Permanent deletion {diagnostics?.capabilities.permanent_deletion ? 'requires a qualifying backup and recipient-policy checks' : 'is disabled by deployment policy'}. Outgoing access is managed separately in Brevo.</Typography>
    {dialog && ['create', 'reset'].includes(dialog.mode) && <PasswordDialog key={dialog.mode + (dialog.mailbox?.email ?? '')} {...common} mode={dialog.mode as 'create' | 'reset'} mailbox={dialog.mailbox}/>}
    {dialog?.mode === 'delete' && dialog.mailbox && <DeleteDialog {...common} mailbox={dialog.mailbox} cutoff={diagnostics?.host.summary?.backup?.cutoff}/>}
    {dialog?.mode === 'config' && dialog.mailbox && <ClientConfig {...common} mailbox={dialog.mailbox}/>}
  </>;
}
