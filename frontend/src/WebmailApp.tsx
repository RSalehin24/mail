import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, IconButton, LinearProgress, List, ListItemButton, ListItemText, MenuItem, Pagination, Paper, Stack, TextField, Tooltip, Typography } from '@mui/material';
import AddOutlined from '@mui/icons-material/AddOutlined';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';
import SearchOutlined from '@mui/icons-material/SearchOutlined';
import EditOutlined from '@mui/icons-material/EditOutlined';
import { api, type Notify } from './api';
import { MailCompose } from './MailCompose';
import { MailProfile } from './MailProfile';
import { attachmentUrl, emptySearch, refKey, submissionStates, type ComposeStart, type MailFolder, type MailLabel, type MailMessage, type MailPage, type MailProfile as Profile, type MailRow, type MailSearch, type SendOperation } from './webmailTypes';

export function WebmailApp({ showProfile, onProfile, onTheme, onRevoked, notify }: { showProfile: boolean; onProfile: (value: boolean) => void; onTheme: (dark: boolean) => void; onRevoked: (codes: string[]) => void; notify: Notify }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [labels, setLabels] = useState<MailLabel[]>([]);
  const [folder, setFolder] = useState<string | null>('INBOX');
  const [label, setLabel] = useState('');
  const [search, setSearch] = useState<MailSearch>(emptySearch);
  const [searchDraft, setSearchDraft] = useState<MailSearch>(emptySearch);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLabel, setSearchLabel] = useState('');
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<25 | 50>(25);
  const [data, setData] = useState<MailPage | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<MailMessage | null>(null);
  const [compose, setCompose] = useState<ComposeStart | null>(null);
  const [operations, setOperations] = useState<SendOperation[]>([]);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  const [bulkReport, setBulkReport] = useState('');
  const [destination, setDestination] = useState('');
  const [actionLabel, setActionLabel] = useState('');
  const [labelDialog, setLabelDialog] = useState<MailLabel | null>(null);
  const [folderDialog, setFolderDialog] = useState<{ name: string; new_name: string; action: string } | null>(null);
  const requestVersion = useRef(0);
  const openVersion = useRef(0);
  const reload = useRef<() => void>(() => {});

  const applyProfile = useCallback((value: Profile) => { setProfile(value); onTheme(value.theme === 'dark' || value.theme === 'system' && window.matchMedia('(prefers-color-scheme:dark)').matches); }, [onTheme]);
  useEffect(() => { void api<Profile>('/profile').then(applyProfile).catch(failure => setError((failure as Error).message)); }, [applyProfile]);
  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setBusy(true); setError('');
    try {
      // Sequence these reads so a single browser leaves I/O capacity for others.
      const result = await api<MailPage>('/webmail/messages/search', { ...search, folder, label, page, size });
      if (version !== requestVersion.current) return;
      setData(result);
      if (page > 1 && result.total <= (page - 1) * size) { setPage(Math.max(1, Math.ceil(result.total / size))); return; }
      const nextFolders = await api<MailFolder[]>('/webmail/folders');
      if (version !== requestVersion.current) return;
      setFolders(nextFolders);
      setLabels(await api<MailLabel[]>('/webmail/labels'));
      setOperations(await api<SendOperation[]>('/webmail/sending'));
    } catch (failure) { if (version === requestVersion.current) setError((failure as Error).message); }
    finally { if (version === requestVersion.current) setBusy(false); }
  }, [folder, label, page, search, size]);
  reload.current = () => { void refresh(); };
  useEffect(() => { if (!showProfile) void refresh(); return () => { requestVersion.current++; }; }, [refresh, showProfile]);
  useEffect(() => {
    const poll = () => { if (!document.hidden && !showProfile) reload.current(); };
    const timer = setInterval(poll, 60000);
    document.addEventListener('visibilitychange', poll);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', poll); openVersion.current++; };
  }, [showProfile]);
  function changeView(name: string | null, labelId = '') {
    openVersion.current++; setOpening(false); setData(null); setFolder(name); setLabel(labelId); setPage(1); setMessage(null); setSelected(new Set()); setBulkReport('');
    setSearch(labelId ? { ...emptySearch, include_trash: true, include_junk: true } : emptySearch);
  }
  async function open(row: MailRow, remote = false) {
    const version = ++openVersion.current;
    setOpening(true); setError('');
    try {
      const result = await api<MailMessage>(`/webmail/messages/open${remote ? '?remote=true' : ''}`, row.ref);
      if (version !== openVersion.current) return;
      setMessage(result); setData(previous => previous ? { ...previous, messages: previous.messages.map(item => refKey(item.ref) === refKey(row.ref) ? { ...item, unread: false } : item) } : previous);
      if (row.unread) setFolders(values => values.map(item => item.name === row.ref.folder ? { ...item, unread: Math.max(0, item.unread - 1) } : item));
    } catch (failure) { if (version === openVersion.current) setError((failure as Error).message); }
    finally { if (version === openVersion.current) setOpening(false); }
  }
  async function bulk(action: string) {
    const rows = (data?.messages || []).filter(row => selected.has(refKey(row.ref)));
    if (!rows.length) return;
    if (action === 'delete' && !window.confirm(`Permanently delete ${rows.length} message(s)? This cannot be undone.`)) return;
    setBusy(true); setError(''); setBulkReport('');
    try {
      const result = await api<{ results: { complete: boolean; error?: string; warning?: string }[] }>('/webmail/messages/bulk', { messages: rows.map(row => row.ref), action, destination, label: actionLabel, confirmation: action === 'delete' ? 'DELETE PERMANENTLY' : '' });
      const failed = result.results.filter(item => !item.complete);
      if (result.results.some(item => item.warning === 'permanent_deletion_queued_for_hourly_maintenance')) notify('Permanent deletion is queued for the next hourly maintenance run.');
      setBulkReport(`${result.results.length - failed.length} completed. ${failed.length ? `${failed.length} failed: ${[...new Set(failed.map(item => item.error?.replaceAll('_', ' ')))].join(', ')}. Refresh and select any remaining messages before trying again.` : ''}`);
      setSelected(new Set()); setMessage(null); await refresh();
    } catch (failure) { setError(`${(failure as Error).message} Some actions may have completed. Refresh before selecting messages again.`); setSelected(new Set()); }
    finally { setBusy(false); }
  }
  async function saveLabel(remove = false) {
    if (!labelDialog) return;
    setBusy(true); setError('');
    try {
      if (remove) { if (!window.confirm('Delete this label? Messages will be preserved.')) return; await api(`/webmail/labels/${labelDialog.id}`, {}, 'DELETE'); if (label === labelDialog.id) changeView('INBOX'); }
      else await api(`/webmail/labels${labelDialog.id ? `/${labelDialog.id}` : ''}`, { name: labelDialog.name, color: labelDialog.color }, labelDialog.id ? 'PUT' : 'POST');
      setLabelDialog(null); await refresh();
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  const currentFolder = folders.find(item => item.name === folder);
  const isTrash = currentFolder?.special === 'trash' || folder?.toLowerCase() === 'trash';
  const currentName = label ? labels.find(item => item.id === label)?.name || 'Label' : folder || 'Search all folders';
  const allSelected = !!data?.messages.length && data.messages.every(row => selected.has(refKey(row.ref)));
  const activeSearch = JSON.stringify(search) !== JSON.stringify(emptySearch);
  if (showProfile && profile) return <MailProfile value={profile} onChanged={applyProfile} onRevoked={onRevoked} onBack={() => onProfile(false)} notify={notify}/>;
  return <>
    <Stack direction={{ xs: 'column', sm: 'row' }} sx={{ gap: 2, justifyContent: "space-between", mb: 3 }}><Box><Typography variant="h4" component="h1">Your mail</Typography><Typography color="text.secondary">{profile?.email}</Typography></Box><Stack direction="row" sx={{ gap: 1 }}><Button variant="contained" disabled={!profile} onClick={() => setCompose({ mode: 'new' })}>Compose</Button></Stack></Stack>
    {error && <Alert severity="error" sx={{ mb: 2 }} action={<Button onClick={() => { void api<Profile>('/profile').then(applyProfile).catch(failure => setError((failure as Error).message)); void refresh(); }}>Retry</Button>}>{error}</Alert>}
    {bulkReport && <Alert severity="info" onClose={() => setBulkReport('')} sx={{ mb: 2 }}>{bulkReport}</Alert>}
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', md: '210px minmax(0,1fr)' }, gap: 2 }}>
      <Paper variant="outlined" sx={{ p: 1, alignSelf: 'start' }}>
        <List dense aria-label="Mail folders" sx={{ display: { xs: 'flex', md: 'block' }, flexWrap: 'wrap' }}>{folders.map(item => <ListItemButton key={item.name} selected={folder === item.name && !label} onClick={() => changeView(item.name)} sx={{ flex: { xs: '0 1 auto', md: undefined }, borderRadius: 1 }}><ListItemText primary={item.name} slotProps={{ primary: { sx: { fontWeight: item.unread ? 700 : 400 } } }}/>{item.unread > 0 && <Chip size="small" label={item.unread} sx={{ ml: 1 }}/>}</ListItemButton>)}</List>
        <Button size="small" startIcon={<AddOutlined/>} onClick={() => setFolderDialog({ name: '', new_name: '', action: 'create' })}>New folder</Button>
        {currentFolder && !currentFolder.special && !['inbox', 'sent', 'drafts', 'trash', 'junk', 'archive'].includes(currentFolder.name.toLowerCase()) && <Button size="small" onClick={() => setFolderDialog({ name: currentFolder.name, new_name: currentFolder.name, action: 'rename' })}>Manage folder</Button>}
        <Divider sx={{ my: 1 }}/><Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}><Typography variant="overline" sx={{ px: 1 }}>Labels</Typography><IconButton size="small" aria-label="Create label" onClick={() => setLabelDialog({ id: '', name: '', color: '#2b6659' })}><AddOutlined/></IconButton></Stack>
        <Stack sx={{ gap: 0.5 }}>{labels.map(item => <Stack key={item.id} direction="row" sx={{ alignItems: "center" }}><Button fullWidth variant={label === item.id ? 'outlined' : 'text'} sx={{ justifyContent: 'flex-start', color: 'text.primary' }} onClick={() => changeView(null, item.id)}><Box component="span" sx={{ mr: 1, width: 10, height: 10, bgcolor: item.color, borderRadius: '50%', flexShrink: 0 }}/>{item.name}</Button><IconButton size="small" aria-label={`Edit ${item.name} label`} onClick={() => setLabelDialog(item)}><EditOutlined fontSize="small"/></IconButton></Stack>)}</Stack>
      </Paper>
      <Stack spacing={2} sx={{ minWidth: 0 }}>
        <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
          <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", px: 2, py: 1 }}><Typography variant="h6" component="h2">{currentName}</Typography><Stack direction="row"><Tooltip title="Search all folders"><IconButton aria-label="Search mail" onClick={() => { setSearchDraft(search); setSearchLabel(label); setSearchOpen(true); }}><SearchOutlined/></IconButton></Tooltip><IconButton aria-label="Refresh mail" disabled={busy} onClick={() => { void refresh(); }}><RefreshOutlined/></IconButton></Stack></Stack>
          {busy && <LinearProgress/>}
          {(activeSearch || label) && <Stack direction="row" sx={{ gap: 1, px: 2, pb: 1 }}><Chip label={label ? 'Messages with this label across folders' : 'Search filters applied'} onDelete={() => changeView('INBOX')}/></Stack>}
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1, alignItems: "center", p: 1 }}>
            <Checkbox aria-label="Select all messages on this page" checked={allSelected} indeterminate={selected.size > 0 && !allSelected} onChange={event => setSelected(event.target.checked ? new Set(data?.messages.map(row => refKey(row.ref))) : new Set())}/>
            <Typography variant="body2">{selected.size ? `${selected.size} selected` : 'Select messages'}</Typography>
            {selected.size > 0 && <><Button size="small" onClick={() => setSelected(new Set())}>Clear selection</Button><Button size="small" disabled={busy} onClick={() => { void bulk('read'); }}>Read</Button><Button size="small" disabled={busy} onClick={() => { void bulk('unread'); }}>Unread</Button>
              <TextField select size="small" label="Move to" value={destination} sx={{ minWidth: 110 }} onChange={event => setDestination(event.target.value)}>{folders.filter(item => item.name !== folder).map(item => <MenuItem key={item.name} value={item.name}>{item.name}</MenuItem>)}</TextField><Button size="small" disabled={busy || !destination} onClick={() => { void bulk('move'); }}>Move</Button>
              <TextField select size="small" label="Label" value={actionLabel} sx={{ minWidth: 110 }} onChange={event => setActionLabel(event.target.value)}>{labels.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}</TextField><Button size="small" disabled={busy || !actionLabel} onClick={() => { void bulk('label'); }}>Apply</Button><Button size="small" disabled={busy || !actionLabel} onClick={() => { void bulk('unlabel'); }}>Remove label</Button>
              {isTrash ? <><Button size="small" disabled={busy} onClick={() => { void bulk('restore'); }}>Restore</Button><Button size="small" color="error" disabled={busy} onClick={() => { void bulk('delete'); }}>Delete permanently</Button></> : <Button size="small" disabled={busy} onClick={() => { void bulk('trash'); }}>Trash</Button>}
            </>}
          </Stack><Divider/>
          {!data ? <Typography sx={{ p: 3 }}>{busy ? 'Loading mail…' : 'Mail could not be loaded. Use Refresh to try again.'}</Typography> : data.messages.length === 0 ? <Typography color="text.secondary" sx={{ p: 4 }}>No messages here.</Typography> : <List disablePadding aria-label="Messages">{data.messages.map(row => <Box key={refKey(row.ref)} sx={{ display: 'flex', borderBottom: '1px solid', borderColor: 'divider', bgcolor: row.unread ? 'action.hover' : undefined }}><Checkbox aria-label={`Select ${row.subject || 'message without subject'}`} checked={selected.has(refKey(row.ref))} onChange={event => setSelected(values => { const next = new Set(values); if (event.target.checked) next.add(refKey(row.ref)); else next.delete(refKey(row.ref)); return next; })}/><ListItemButton aria-label={`Open ${row.subject || 'message without subject'}`} selected={!!message && refKey(message.ref) === refKey(row.ref)} onClick={() => { void open(row); }} sx={{ px: 1, minWidth: 0 }}><Box sx={{ width: '100%', minWidth: 0 }}><Stack direction="row" sx={{ justifyContent: "space-between", gap: 1 }}><Typography noWrap sx={{ fontWeight: row.unread ? 750 : 400, minWidth: 0 }}>{row.from || '(unknown sender)'}</Typography><Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>{Number.isNaN(Date.parse(row.date)) ? '' : new Date(row.date).toLocaleDateString()}</Typography></Stack><Typography noWrap sx={{ fontWeight: row.unread ? 700 : 400 }}>{row.subject || '(no subject)'}</Typography><Stack direction="row" sx={{ gap: 0.5, flexWrap: "wrap" }}>{folder === null && <Chip size="small" label={row.ref.folder}/>} {row.labels.map(id => labels.find(item => item.id === id)).filter((item): item is MailLabel => !!item).map(item => <Chip size="small" key={item.id} label={item.name} sx={{ borderColor: item.color, borderLeft: `5px solid ${item.color}` }}/>)}</Stack></Box></ListItemButton></Box>)}</List>}
          <Stack direction={{ xs: 'column', sm: 'row' }} sx={{ alignItems: "center", justifyContent: "space-between", gap: 1, p: 2 }}><Stack direction="row" sx={{ gap: 1, alignItems: "center" }}><Typography variant="caption">{data?.total || 0} messages</Typography><TextField select size="small" label="Per page" value={size} onChange={event => { setSize(Number(event.target.value) as 25 | 50); setPage(1); setSelected(new Set()); }}><MenuItem value={25}>25</MenuItem><MenuItem value={50}>50</MenuItem></TextField></Stack><Pagination count={Math.max(1, Math.ceil((data?.total || 0) / size))} page={page} siblingCount={0} onChange={(_, value) => { setPage(value); setSelected(new Set()); setMessage(null); }}/></Stack>
        </Paper>
        {opening && <LinearProgress/>}
        {message && <Paper component="article" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, overflowWrap: 'anywhere' }}><Stack spacing={2}>
          <Stack direction="row" sx={{ justifyContent: "space-between", gap: 1 }}><Typography variant="h5" component="h2">{message.subject || '(no subject)'}</Typography><Button onClick={() => { openVersion.current++; setMessage(null); }}>Close</Button></Stack>
          <Typography variant="body2">From: {message.from}<br/>To: {message.to}{message.cc && <><br/>Cc: {message.cc}</>}<br/>{message.date}</Typography>
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>{(message.ref.folder.toLowerCase() === 'drafts' || folders.find(item => item.name === message.ref.folder)?.special === 'drafts') ? <Button onClick={() => setCompose({ mode: 'draft', message })}>Edit draft</Button> : <><Button onClick={() => setCompose({ mode: 'reply', message })}>Reply</Button><Button onClick={() => setCompose({ mode: 'reply-all', message })}>Reply all</Button><Button onClick={() => setCompose({ mode: 'forward', message })}>Forward</Button></>}</Stack>
          {message.blocked_images > 0 && <Alert severity="info" action={<Button onClick={() => { void open(message, true); }}>Load remote images</Button>}>Remote images are blocked. Loading them can reveal your IP address to the sender.</Alert>}
          {message.truncated && <Alert severity="warning">This message is large. The displayed body is limited to 1 MiB.</Alert>}
          {message.html ? <Box component="iframe" title="Email message content" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={message.html} sx={{ width: '100%', minHeight: 380, border: '1px solid', borderColor: 'divider', bgcolor: '#fff', borderRadius: 1 }}/> : <Typography component="pre" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', m: 0 }}>{message.text || '(empty message)'}</Typography>}
          {message.attachments.length > 0 && <><Divider/><Typography variant="subtitle2">Attachments</Typography><Stack direction="row" sx={{ gap: 1, flexWrap: "wrap" }}>{message.attachments.map(file => <Button key={file.part} component="a" href={attachmentUrl(message.ref, file.part)} download variant="outlined">{file.filename} ({Math.ceil(file.size / 1024)} KiB)</Button>)}</Stack></>}
        </Stack></Paper>}
        {operations.some(item => item.state !== 'complete') && <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="h6" component="h2">Sending status</Typography><Stack sx={{ gap: 1, mt: 1 }}>{operations.filter(item => item.state !== 'complete').map(item => <Alert key={item.id} severity="warning" action={['accepted', 'sent_save_failed', 'sent_save_uncertain', 'sent_saved', 'cleanup_failed'].includes(item.state) ? <Button disabled={busy} onClick={async () => { try { await api(`/webmail/sending/${item.id}/save-sent`, {}); await refresh(); } catch (failure) { notify((failure as Error).message); } }}>Retry Sent / cleanup</Button> : undefined}>{submissionStates[item.state] || item.state}</Alert>)}</Stack></Paper>}
        <Typography variant="caption" color="text.secondary">Visible mail views refresh every 60 seconds. Trash is retained for 30 days from entry. Submission to Maddy does not confirm recipient delivery.</Typography>
      </Stack>
    </Box>
    <Dialog open={searchOpen} onClose={() => setSearchOpen(false)} fullWidth><DialogTitle>Search mail</DialogTitle><DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
      <Typography variant="body2">Search covers all folders. Trash and Junk are excluded unless selected.</Typography>
      {(['sender', 'recipient', 'subject', 'body'] as const).map(key => <TextField key={key} label={{ sender: 'Sender', recipient: 'Recipient (To, Cc or Bcc)', subject: 'Subject', body: 'Message body' }[key]} value={searchDraft[key]} onChange={event => setSearchDraft({ ...searchDraft, [key]: event.target.value })}/>)}
      <Stack direction="row" sx={{ gap: 2 }}><TextField fullWidth type="date" label="On or after" slotProps={{ inputLabel: { shrink: true } }} value={searchDraft.after} onChange={event => setSearchDraft({ ...searchDraft, after: event.target.value })}/><TextField fullWidth type="date" label="Before" slotProps={{ inputLabel: { shrink: true } }} value={searchDraft.before} onChange={event => setSearchDraft({ ...searchDraft, before: event.target.value })}/></Stack>
      {(['unread', 'include_trash', 'include_junk'] as const).map(key => <FormControlLabel key={key} control={<Checkbox checked={searchDraft[key]} onChange={event => setSearchDraft({ ...searchDraft, [key]: event.target.checked })}/>} label={{ unread: 'Unread only', include_trash: 'Include Trash', include_junk: 'Include Junk' }[key]}/>)}
      <TextField select label="Label" value={searchLabel} onChange={event => setSearchLabel(event.target.value)}><MenuItem value="">Any label</MenuItem>{labels.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}</TextField>
    </Stack></DialogContent><DialogActions><Button onClick={() => setSearchOpen(false)}>Cancel</Button><Button variant="contained" onClick={() => { setFolder(null); setLabel(searchLabel); setData(null); setSearch(searchDraft); setPage(1); setSelected(new Set()); setMessage(null); setSearchOpen(false); }}>Search</Button></DialogActions></Dialog>
    <Dialog open={!!labelDialog} onClose={() => setLabelDialog(null)} fullWidth maxWidth="xs"><DialogTitle>{labelDialog?.id ? 'Edit label' : 'Create label'}</DialogTitle><DialogContent><Stack spacing={2} sx={{ pt: 1 }}><TextField label="Label name" value={labelDialog?.name || ''} onChange={event => setLabelDialog(value => value ? { ...value, name: event.target.value } : value)}/><TextField label="Color" type="color" value={labelDialog?.color || '#2b6659'} onChange={event => setLabelDialog(value => value ? { ...value, color: event.target.value } : value)}/></Stack></DialogContent><DialogActions>{labelDialog?.id && <Button color="error" disabled={busy} onClick={() => { void saveLabel(true); }}>Delete label</Button>}<Button onClick={() => setLabelDialog(null)}>Cancel</Button><Button disabled={busy || !labelDialog?.name.trim()} onClick={() => { void saveLabel(); }}>Save</Button></DialogActions></Dialog>
    <Dialog open={!!folderDialog} onClose={() => setFolderDialog(null)} fullWidth maxWidth="xs"><DialogTitle>{folderDialog?.action === 'create' ? 'Create folder' : 'Manage folder'}</DialogTitle><DialogContent><TextField fullWidth sx={{ mt: 1 }} label="Folder name" value={folderDialog?.action === 'create' ? folderDialog.name : folderDialog?.new_name || ''} onChange={event => setFolderDialog(value => value ? { ...value, [value.action === 'create' ? 'name' : 'new_name']: event.target.value } : value)}/></DialogContent><DialogActions><Button onClick={() => setFolderDialog(null)}>Cancel</Button>{folderDialog?.action === 'rename' && <Button color="error" disabled={busy} onClick={async () => { if (!window.confirm('Delete this empty folder?')) return; try { await api('/webmail/folders', { ...folderDialog, action: 'delete' }); setFolderDialog(null); changeView('INBOX'); } catch (failure) { notify((failure as Error).message); } }}>Delete empty folder</Button>}<Button disabled={busy} onClick={async () => { try { await api('/webmail/folders', folderDialog); setFolderDialog(null); if (folderDialog?.action === 'rename') changeView(folderDialog.new_name); else await refresh(); } catch (failure) { notify((failure as Error).message); } }}>Save</Button></DialogActions></Dialog>
    {compose && profile && <MailCompose key={`${compose.mode}-${compose.message ? refKey(compose.message.ref) : 'new'}`} start={compose} profile={profile} onClose={() => setCompose(null)} onChanged={() => { void refresh(); }}/>} 
  </>;
}
