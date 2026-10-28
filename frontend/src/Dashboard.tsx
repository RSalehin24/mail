import { formatDate, formatTime } from './format';
import { PaginationControls } from './PaginationControls';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Avatar, Box, Button, Card, Checkbox, Chip, Divider, IconButton, InputAdornment, Menu, MenuItem, Paper, Stack, Skeleton,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import AddRounded from '@mui/icons-material/AddRounded';
import RefreshRounded from '@mui/icons-material/RefreshRounded';
import SearchRounded from '@mui/icons-material/SearchRounded';
import MoreHorizRounded from '@mui/icons-material/MoreHorizRounded';
import EditOutlined from '@mui/icons-material/EditOutlined';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import SendOutlined from '@mui/icons-material/SendOutlined';
import ArrowDownwardRounded from '@mui/icons-material/ArrowDownwardRounded';
import { api, cached, type Authorize, type Diagnostics, type Mailbox, type Notify } from './api';
import { PasswordDialog } from './MailboxDialogs';
import { SenderVerification } from './SenderVerification';
import { navigate, mailboxPath } from './navigation';

export function Dashboard({ authorize, notify }: { authorize: Authorize; notify: Notify }) {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>(() => cached<Mailbox[]>('/accounts') ?? []);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(() => cached<Diagnostics>('/diagnostics') ?? null);
  const [loading, setLoading] = useState(!cached('/accounts'));
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [reverse, setReverse] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [verification, setVerification] = useState<string[] | null>(null);
  const [menu, setMenu] = useState<{ anchor: HTMLElement; mailbox: Mailbox } | null>(null);
  const [dialog, setDialog] = useState<{ mode: 'create' } | null>(null);
  const load = useCallback(async () => {
    try {
      const accounts = await api<Mailbox[]>('/accounts');
      setMailboxes(accounts); setError('');
    } catch (failure) { setError((failure as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void api<Diagnostics>('/diagnostics').then(setDiagnostics).catch(() => {}); void load(); const timer = setInterval(() => { void load(); }, 15000); return () => clearInterval(timer); }, [load]);
  const filtered = mailboxes.filter(mailbox => `${mailbox.email} ${mailbox.display_name} ${mailbox.notes}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => ((b.created ?? 0) - (a.created ?? 0)) * (reverse ? -1 : 1) || a.email.localeCompare(b.email));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / rowsPerPage) - 1));
  const rows = filtered.slice(currentPage * rowsPerPage, (currentPage + 1) * rowsPerPage);
  const common = { authorize, notify, onClose: () => setDialog(null), onChanged: () => { void load(); } };
  const pending = mailboxes.filter(mailbox => mailbox.sender_tests?.some(test => test.accepted && !test.confirmed));
  function toggle(email: string) { setSelected(current => current.includes(email) ? current.filter(value => value !== email) : [...current, email]); }
  function selectVisible(checked: boolean) {
    setSelected(current => checked ? [...new Set([...current, ...rows.map(mailbox => mailbox.email)])] : current.filter(email => !rows.some(mailbox => mailbox.email === email)));
  }
  function creationDate(mailbox: Mailbox) {
    return mailbox.created ? formatDate(mailbox.created) : 'Unavailable';
  }
  function sending(mailbox: Mailbox) {
    const confirmed = mailbox.outbound_readiness === 'confirmed';
    const waiting = mailbox.outbound_readiness === 'awaiting receipt';
    return <Chip size="small" className="status-chip" color={confirmed ? 'success' : waiting ? 'warning' : 'default'} variant="outlined"
      label={confirmed ? 'Verified' : waiting ? 'Awaiting receipt' : mailbox.outbound_readiness === 'failed' ? 'Test failed' : 'Not tested'} onClick={() => setVerification([mailbox.email])}/>;
  }
  function rowActions(mailbox: Mailbox) {
    return <Tooltip title="Mailbox actions"><IconButton aria-label={`Actions for ${mailbox.email}`} onClick={event => setMenu({ anchor: event.currentTarget, mailbox })}><MoreHorizRounded/></IconButton></Tooltip>;
  }
  function choose(mode: 'config') {
    if (menu) navigate(mailboxPath(menu.mailbox.email, mode === 'config' ? 'connections' : 'configuration')); setMenu(null);
  }
  return <>
    <Stack className="page-heading" direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
      <Box><Typography variant="h4" component="h1">Mailboxes</Typography></Box>
      <Button variant="contained" startIcon={<AddRounded/>} onClick={() => setDialog({ mode: 'create' })}>New mailbox</Button>
    </Stack>
    {error && <Alert severity="warning" sx={{ mb: 2 }}>{error} Previously loaded accounts may be out of date.</Alert>}
    {!!diagnostics?.host.unresolved?.length && <Alert severity="warning" sx={{ mb: 2 }}>A mailbox operation needs administrator review before further changes.</Alert>}
    {pending.length > 0 && <Alert severity="info" className="receipt-notice" action={<Button color="inherit" size="small" onClick={() => setVerification(pending.map(mailbox => mailbox.email))}>Review receipts</Button>}>Test emails are waiting for confirmation for {pending.length} {pending.length === 1 ? 'mailbox' : 'mailboxes'}.</Alert>}
    <Paper variant="outlined" className="mailbox-surface" sx={{ mt: 3 }}>
      {loading && <Box role="status" aria-label="Loading mailboxes" sx={{ p: 3 }}><Skeleton/><Skeleton/><Skeleton/></Box>}
      <Stack className="table-toolbar" direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
        <Stack direction="row" spacing={1.2} sx={{ alignItems: 'center' }}><Typography variant="h6" sx={{ fontSize: '1rem' }}>All mailboxes</Typography><Chip size="small" label={mailboxes.length}/></Stack>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <TextField label="Search mailboxes" placeholder="Search name or address" size="small" value={search}
            onChange={event => { setSearch(event.target.value); setPage(0); }} slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchRounded fontSize="small"/></InputAdornment> } }}/>
          <Tooltip title="Refresh mailboxes"><IconButton aria-label="Refresh accounts" onClick={() => { void load(); }}><RefreshRounded/></IconButton></Tooltip>
        </Stack>
      </Stack>
      {selected.length > 0 && <Stack className="selection-toolbar" direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{selected.length} selected</Typography>
        <Button size="small" startIcon={<SendOutlined/>} onClick={() => setVerification(selected)}>Verify selected senders</Button>
        <Button size="small" color="inherit" onClick={() => setSelected([])}>Clear selection</Button>
      </Stack>}
      <Divider/>
      <TableContainer className="desktop-table"><Table className="mailbox-table" aria-label="Mailboxes"><TableHead><TableRow>
        <TableCell padding="checkbox" align="center"><Checkbox checked={rows.length > 0 && rows.every(mailbox => selected.includes(mailbox.email))}
          indeterminate={rows.some(mailbox => selected.includes(mailbox.email)) && !rows.every(mailbox => selected.includes(mailbox.email))}
          slotProps={{ input: { 'aria-label': 'Select visible mailboxes' } }} onChange={(_, checked) => selectVisible(checked)}/></TableCell>
        <TableCell align="center">Name</TableCell>
        <TableCell align="center">Mailbox</TableCell>
        <TableCell align="center" sortDirection={reverse ? 'asc' : 'desc'}><Button color="inherit" className="column-sort" size="small" aria-label={reverse ? 'Creation Date, oldest first. Sort newest first' : 'Creation Date, newest first. Sort oldest first'} endIcon={<ArrowDownwardRounded sx={{ fontSize: '14px !important', transform: reverse ? 'rotate(180deg)' : undefined }}/>} onClick={() => { setReverse(!reverse); setPage(0); }}>Creation Date</Button></TableCell>
        <TableCell align="center">Inbox</TableCell><TableCell align="center">Sending</TableCell><TableCell align="center" sx={{ width: 88 }}>Actions</TableCell>
      </TableRow></TableHead><TableBody>{rows.map(mailbox => <TableRow hover selected={selected.includes(mailbox.email)} key={mailbox.email}>
        <TableCell padding="checkbox" align="center"><Checkbox checked={selected.includes(mailbox.email)} onChange={() => toggle(mailbox.email)} slotProps={{ input: { 'aria-label': `Select ${mailbox.email}` } }}/></TableCell>
        <TableCell align="center"><Button size="small" className="name-button" onClick={() => navigate(mailboxPath(mailbox.email, 'configuration'))}>{mailbox.display_name || 'Add name'}</Button></TableCell>
        <TableCell><Stack direction="row" spacing={1.5} sx={{ alignItems: 'center' }}><Avatar className="mailbox-avatar">{mailbox.email[0].toUpperCase()}</Avatar><Box sx={{ minWidth: 0 }}><Typography component="a" href={mailboxPath(mailbox.email)} onClick={event => { event.preventDefault(); navigate(mailboxPath(mailbox.email)); }} variant="body2" sx={{ color: 'primary.main', fontWeight: 600, overflowWrap: 'anywhere' }}>{mailbox.email}</Typography>{mailbox.notes && <Typography variant="caption" color="text.secondary">{mailbox.notes}</Typography>}</Box></Stack></TableCell>

        <TableCell align="center" sx={{ whiteSpace: 'nowrap' }}><Typography variant="body2" component="time" dateTime={mailbox.created ? new Date(mailbox.created * 1000).toISOString() : undefined}>{creationDate(mailbox)}</Typography></TableCell>
        <TableCell align="center"><Chip size="small" className="status-chip" color={mailbox.status === 'ready' ? 'success' : 'warning'} variant="outlined" label={mailbox.status === 'ready' ? 'Ready' : 'Needs review'}/></TableCell>
        <TableCell align="center">{sending(mailbox)}</TableCell><TableCell align="center">{rowActions(mailbox)}</TableCell>
      </TableRow>)}</TableBody></Table></TableContainer>
      <Box className="mailbox-cards">{rows.map(mailbox => <Card variant="outlined" className="mailbox-mobile-card" key={mailbox.email}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}><Checkbox checked={selected.includes(mailbox.email)} onChange={() => toggle(mailbox.email)} slotProps={{ input: { 'aria-label': `Select ${mailbox.email}` } }}/><Box sx={{ minWidth: 0, flex: 1 }}><Button size="small" sx={{ display: 'block' }} onClick={() => navigate(mailboxPath(mailbox.email, 'configuration'))}>{mailbox.display_name || 'Add name'}</Button><Typography component="a" href={mailboxPath(mailbox.email)} onClick={event => { event.preventDefault(); navigate(mailboxPath(mailbox.email)); }} variant="body2" sx={{ color: 'primary.main', fontWeight: 650, overflowWrap: 'anywhere' }}>{mailbox.email}</Typography></Box>{rowActions(mailbox)}</Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>Creation Date: {creationDate(mailbox)}</Typography>
        <Divider sx={{ my: 1.5 }}/><Stack direction="row" sx={{ justifyContent: 'space-between', gap: 2 }}>
          <Box><Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.7 }}>Inbox</Typography><Chip size="small" variant="outlined" color={mailbox.status === 'ready' ? 'success' : 'warning'} label={mailbox.status === 'ready' ? 'Ready' : 'Needs review'}/></Box>
          <Box sx={{ textAlign: 'right' }}><Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.7 }}>Sending</Typography>{sending(mailbox)}</Box>
        </Stack>{mailbox.notes && <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>{mailbox.notes}</Typography>}
      </Card>)}</Box>
      {!loading && !error && !filtered.length && <Box sx={{ py: 8, textAlign: 'center' }}><Typography variant="h6">{search ? 'No matching mailboxes' : 'No mailboxes'}</Typography><Typography color="text.secondary" variant="body2" sx={{ mt: 1 }}>{search ? 'Try another address or name.' : 'Create a mailbox.'}</Typography></Box>}
      <PaginationControls count={filtered.length} page={currentPage} size={rowsPerPage} onPage={setPage} onSize={value => { setRowsPerPage(value); setPage(0); }}/>
    </Paper>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between', mt: 2, px: 0.5 }}>
      <Typography variant="caption" color="text.secondary">{diagnostics?.host.summary ? `Last checked ${formatTime(diagnostics.host.summary.observed_at)}` : 'Waiting for server status'}</Typography>

    </Stack>
    <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)} slotProps={{ paper: { sx: { minWidth: 225, borderRadius: 2, mt: 0.5 } } }}>
      <MenuItem component="button" className="mailbox-menu-item" onClick={() => { if (menu) navigate(mailboxPath(menu.mailbox.email)); setMenu(null); }}><EditOutlined fontSize="small"/>Open mailbox</MenuItem>
      <MenuItem component="button" className="mailbox-menu-item" onClick={() => choose('config')}><SettingsOutlined fontSize="small"/>Mailbox settings</MenuItem>
    </Menu>
    {dialog && <PasswordDialog {...common} mode="create"/>}
    {verification && <SenderVerification emails={verification} mailboxes={mailboxes} authorize={authorize} notify={notify} onChanged={load} onClose={() => setVerification(null)}/>}
  </>;
}
