import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Chip, Stack } from '@mui/material';
import { AdminTable } from './AdminTable';
import { api, cached, type Authorize, type Mailbox, type Notify } from './api';
import { LoadingState } from './PageState';
import { usePagination } from './PaginationControls';
import { SenderVerification } from './SenderVerification';
import { sendingState } from './sendingState';
import { PageHeader } from './ui';

type Chosen = string[] | null;
// Same shape as Incoming delivery: every mailbox, its sending state, and the action that moves it forward.
export function OutgoingDelivery({ authorize, notify }: { authorize: Authorize; notify: Notify }) {
  const [mailboxes, setMailboxes] = useState<Mailbox[] | null>(() => cached<Mailbox[]>('/accounts') ?? null);
  const [error, setError] = useState('');
  const [verifying, setVerifying] = useState<Chosen>(null);
  const load = useCallback(async () => {
    try { setMailboxes(await api<Mailbox[]>('/accounts')); setError(''); }
    catch (failure) { setError((failure as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const pages = usePagination(mailboxes ?? []);
  const unverified = (mailboxes ?? []).filter(mailbox => mailbox.outbound_readiness !== 'confirmed').map(mailbox => mailbox.email);
  return <>
    <PageHeader title="Outgoing delivery"
      action={<Stack direction="row" spacing={1}><Button variant="outlined" onClick={() => { void load(); }}>Refresh delivery</Button><Button variant="contained" disabled={unverified.length === 0} onClick={() => setVerifying(unverified)}>Verify all</Button></Stack>}/>
    {error && <Alert severity="error" sx={{ mb: 2 }} action={<Button color="inherit" size="small" onClick={() => { void load(); }}>Retry</Button>}>{error}</Alert>}
    {!mailboxes && !error && <LoadingState label="Loading outgoing delivery"/>}
    {mailboxes && <AdminTable wideFirst widths={['42%', '30%', '28%']} columns={['Mailbox', 'Sending', 'Actions']} empty="No mailboxes." footer={pages.controls} rows={pages.rows.map(mailbox => {
      const state = sendingState(mailbox.outbound_readiness);
      return { key: mailbox.email, cells: [mailbox.email,
        <Chip key="state" size="small" variant="outlined" color={state.color} label={state.label}/>,
        state.action && <Button key="action" variant="outlined" aria-label={`${state.action} for ${mailbox.email}`} onClick={() => setVerifying([mailbox.email])}>{state.action}</Button>] };
    })}/>}
    {verifying && mailboxes && <SenderVerification emails={verifying} mailboxes={mailboxes} authorize={authorize} notify={notify} onChanged={load} onClose={() => setVerifying(null)}/>}
  </>;
}
