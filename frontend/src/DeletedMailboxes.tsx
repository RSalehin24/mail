import { formatDateTime } from './format';
import { AdminTable } from './AdminTable';
import { LoadingState } from './PageState';
import { PaginationControls } from './PaginationControls';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Chip, Stack, Tab, Tabs, Typography } from '@mui/material';
import { api, errorMessage, type Authorize, type Notify } from './api';
import { type DeletedMailbox } from './RecoveryActions';
import { RecoverySettings } from './RecoverySettings';

interface RecoveryList { mailboxes: DeletedMailbox[]; next_cursor: string | null; total: number; page: number; page_size: number }
const date = formatDateTime;
export function DeletedMailboxes({ authorize, notify, embedded = false }: { authorize: Authorize; notify: Notify; embedded?: boolean }) {
  const [tab, setTab] = useState(() => { const value = new URLSearchParams(location.search).get('tab'); return value === 'configure' || value === 'protected' ? value : 'deleted'; });
  const [result, setResult] = useState<RecoveryList | null>(null);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const load = useCallback(async () => {
    try { const value = await api<RecoveryList>(`/recovery/mailboxes?page=${page + 1}&page_size=${size}`); setResult(value); if (page !== value.page - 1) setPage(value.page - 1); setError(''); }
    catch (failure) { setError((failure as Error).message); }
  }, [page, size]);
  useEffect(() => { void load(); }, [load]);
  return <Stack spacing={3}>
    <Stack direction="row" sx={{ justifyContent: 'space-between', gap: 2 }}><Typography variant={embedded ? 'h5' : 'h4'} component={embedded ? 'h2' : 'h1'}>Deleted Mailboxes</Typography><Button onClick={() => { void load(); }}>Refresh</Button></Stack>
    {error && <Alert severity="error">{error}</Alert>}
    {!result && !error && <LoadingState label="Loading deleted mailboxes…"/>}
    <Tabs value={tab} onChange={(_, value: string) => { setTab(value); const url = new URL(location.href); url.searchParams.set('tab', value); history.replaceState(null, '', url); }} variant="scrollable" aria-label="Deleted mailbox sections">
      <Tab value="configure" label="Configure"/><Tab value="protected" label="Protected Mailboxes"/><Tab value="deleted" label="Deleted Mailboxes"/>
    </Tabs>
    {tab === 'configure' && <RecoverySettings authorize={authorize} notify={notify}/>}
    {tab === 'protected' && <RecoverySettings section="protected" authorize={authorize} notify={notify}/>}
    {tab === 'deleted' && <AdminTable columns={['Name', 'Mailbox', 'Deleted At', 'Recovery Deadline', 'Status', 'Restore (run on the VM)']} empty="No deleted mailboxes." rows={(result?.mailboxes ?? []).map(record => ({ key: record.id, cells: [
      record.display_name || '—', record.email, date(record.deleted_at), record.deletion_pending ? 'Waiting for deletion' : record.retention === 'none' ? 'Not retained' : record.deadline ? date(record.deadline) : 'No expiry',
      <Stack key="status"><Chip variant="outlined" label={record.state.replaceAll('_', ' ')}/>{record.error && <Typography color="error" variant="caption">{errorMessage(record.error)}</Typography>}</Stack>,
      record.state === 'deleted' && record.recovery_available ? <Typography key="restore" variant="caption" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>sudo python3 -m host.restore_mailbox {record.id}</Typography> : '—',
    ] }))}/>}
    {tab === 'deleted' && result && <PaginationControls count={result.total} page={result.page - 1} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/>}
  </Stack>;
}
