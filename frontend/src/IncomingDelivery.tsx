import { Alert, Button, Chip, Stack } from '@mui/material';
import { AdminTable } from './AdminTable';
import { api, type Authorize, type Notify } from './api';
import { LoadingState } from './PageState';
import { usePagination } from './PaginationControls';
import { PageHeader } from './ui';
import { useForwarding, type Listing } from './useForwarding';

// Whether Cloudflare routes each mailbox's incoming mail to this server; setup, so it has its own page.
export function IncomingDelivery({ authorize, notify }: { authorize: Authorize; notify: Notify }) {
  const { result, accounts, busy, error, load, perform } = useForwarding(authorize, notify);
  const pages = usePagination(accounts);
  const connection = result?.connection;
  const failed = connection?.state === 'sync_failed';
  const unsettled = !connection || ['pending', 'stale', 'not_connected', 'sync_failed'].includes(connection.state);
  const unverified = accounts.filter(account => !(!unsettled && connection?.mailboxes?.[account.email]?.local_delivery_verified && connection.connected_mailboxes?.includes(account.email)));
  function verifyAll() {
    perform(async () => {
      let listing: Listing | undefined;
      for (const account of unverified) listing = await api<Listing>('/forwarding/verify-local', { email: account.email });
      return listing ?? result!;
    }, `Delivery verification check queued for ${unverified.length} ${unverified.length === 1 ? 'mailbox' : 'mailboxes'}. Status updates automatically.`);
  }
  return <>
    <PageHeader title="Incoming delivery"
      action={<Stack direction="row" spacing={1}><Button variant="outlined" disabled={busy} onClick={() => { void load(); }}>Refresh delivery</Button><Button variant="contained" disabled={busy || !result || unverified.length === 0} onClick={verifyAll}>Verify all</Button></Stack>}/>
    {error && <Alert severity="error" sx={{ mb: 2 }} action={<Button onClick={() => { void load(); }}>Refresh</Button>}>{error}</Alert>}
    {!result && !error && <LoadingState label="Loading incoming delivery…"/>}
    {result && <AdminTable wideFirst widths={['42%', '30%', '28%']} columns={['Mailbox', 'Local delivery', 'Actions']} empty="No mailbox delivery connections." footer={pages.controls} rows={pages.rows.map(account => {
      const verified = connection?.mailboxes?.[account.email]?.local_delivery_verified;
      const connected = !unsettled && verified && connection?.connected_mailboxes?.includes(account.email);
      return { key: account.email, cells: [account.email,
        <Chip key="status" size="small" variant="outlined" color={failed ? 'error' : connected ? 'success' : 'warning'} label={failed ? 'Synchronization failed' : connected ? 'Connected' : verified ? 'Awaiting routing synchronization' : 'Verification required'}/>,
        !connected && <Button key="verify" variant="outlined" disabled={busy || !result} aria-label={`Verify delivery for ${account.email}`} onClick={() => perform(() => api<Listing>('/forwarding/verify-local', { email: account.email }), 'Delivery verification check queued. If unverified, open Cloudflare’s verification email in this mailbox and click its link. Status updates automatically.')}>Verify delivery</Button>,
      ] };
    })}/>}
  </>;
}
