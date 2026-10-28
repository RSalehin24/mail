import { formatDateTime } from './format';
import { useDeliveryHistory } from './DeliveryHistoryPagination';
import { useState } from 'react';
import { Alert, Box, Button, Chip, Divider, Stack, Typography } from '@mui/material';
import { api, ApiError, type Authorize, type Mailbox, type Notify } from './api';

export function SendingHistory({ mailbox, authorize, notify, onChanged, onSend }: { mailbox: Mailbox; authorize: Authorize; notify: Notify; onChanged: () => Promise<void>; onSend: () => void }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const tests = mailbox.sender_history ?? mailbox.sender_tests ?? [];
  const pages = useDeliveryHistory(mailbox.email, JSON.stringify(tests));
  return <Stack spacing={2.5}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'start' } }}>
      <Box><Typography variant="h6" component="h2" sx={{ fontWeight: 650 }}>Sending verification</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .75 }}>Check delivery from this mailbox and confirm the matching receipt.</Typography></Box>
      <Chip variant="outlined" color={mailbox.outbound_readiness === 'confirmed' ? 'success' : 'default'} label={mailbox.outbound_readiness === 'confirmed' ? 'Verified' : mailbox.outbound_readiness === 'awaiting receipt' ? 'Awaiting receipt' : mailbox.outbound_readiness === 'failed' ? 'Test failed' : 'Not tested'}/>
    </Stack>
    <Button variant="contained" sx={{ alignSelf: 'start' }} onClick={onSend}>Send test email</Button>
    <Divider/>
    <Typography variant="subtitle2" component="h3">Delivery history</Typography>
    {(error || pages.error) && <Alert severity="error">{error || pages.error}</Alert>}
    {!tests.length && <Typography variant="body2" color="text.secondary">No tests yet. Send a test email to start verifying this address.</Typography>}
    {pages.rows.map(test => {
      const current = !('revision' in test) || test.revision === mailbox.current_relay_revision;
      return <Box key={test.id} sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}><Typography variant="body2" sx={{ fontWeight: 600 }}>{formatDateTime(test.created)}</Typography><Chip size="small" variant="outlined" color={test.confirmed && current ? 'success' : 'default'} label={test.confirmed ? 'Receipt confirmed' : test.accepted ? 'Awaiting receipt' : 'Test failed'}/></Stack>
        <Typography variant="caption" component="p" color="text.secondary" sx={{ mt: 1, overflowWrap: 'anywhere' }}>Test ID · {test.id}</Typography>
        {!current && <Typography variant="caption" color="text.secondary">Previous relay configuration · retained for reference</Typography>}
        {!!test.accepted && !test.confirmed && current && <Button sx={{ mt: 1.5 }} variant="outlined" size="small" disabled={!!busy} onClick={() => authorize(async () => {
          setBusy(test.id); setError('');
          try { await api(`/relay-tests/${test.id}/confirm`, {}); await onChanged(); notify(`Sending verified for ${mailbox.email}`); }
          catch (failure) { if (failure instanceof ApiError && ['recent_authentication_required', 'authentication_required'].includes(failure.code)) throw failure; setError((failure as Error).message); }
          finally { setBusy(''); }
        })}>Confirm receipt</Button>}
      </Box>;
    })}
    {pages.controls}
  </Stack>;
}
