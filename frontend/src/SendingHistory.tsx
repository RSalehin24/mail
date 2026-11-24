import { useState } from 'react';
import { Alert, Button, Stack, Tooltip, Typography } from '@mui/material';
import { AdminTable } from './AdminTable';
import { api, ApiError, type Authorize, type Mailbox, type Notify } from './api';
import { useDeliveryHistory } from './DeliveryHistoryPagination';
import { formatDate, formatTime } from './format';
import { Section, StatusDot, type Tone } from './ui';

const AUTHENTICATION_CODES = ['recent_authentication_required', 'password_confirmation_required', 'authentication_required'];
const OVERALL: Record<string, { tone: Tone; label: string }> = {
  confirmed: { tone: 'success', label: 'Verified' }, 'awaiting receipt': { tone: 'warning', label: 'Awaiting receipt' }, failed: { tone: 'error', label: 'Test failed' },
};

export function SendingHistory({ mailbox, authorize, notify, onChanged, onSend }: { mailbox: Mailbox; authorize: Authorize; notify: Notify; onChanged: () => Promise<void>; onSend: () => void }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const pages = useDeliveryHistory(mailbox.email, JSON.stringify(mailbox.sender_history ?? mailbox.sender_tests ?? []));
  const overall = OVERALL[mailbox.outbound_readiness] ?? { tone: 'neutral' as Tone, label: 'Not tested' };
  function confirm(id: string) {
    authorize(async () => {
      setBusy(id); setError('');
      try { await api(`/relay-tests/${id}/confirm`, {}); await onChanged(); notify(`Sending verified for ${mailbox.email}`); }
      catch (failure) { if (failure instanceof ApiError && AUTHENTICATION_CODES.includes(failure.code)) throw failure; setError((failure as Error).message); }
      finally { setBusy(''); }
    });
  }
  return <Section title="Sending" action={<Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}><StatusDot {...overall}/><Button variant="contained" size="small" onClick={onSend}>Send test email</Button></Stack>}>
    <Stack spacing={2}>
      {(error || pages.error) && <Alert severity="error">{error || pages.error}</Alert>}
      <AdminTable columns={['Date', 'Time', 'Result', 'Action']} empty="No tests yet." footer={pages.controls} rows={pages.rows.map(test => {
        const current = !('revision' in test) || test.revision === mailbox.current_relay_revision;
        return { key: test.id, cells: [formatDate(test.created), formatTime(test.created),
          <Stack key="result"><StatusDot tone={test.confirmed ? 'success' : test.accepted ? 'warning' : 'error'} label={test.confirmed ? 'Delivered' : test.accepted ? 'Awaiting receipt' : 'Failed'}/>
            {!current && <Tooltip title="Sent with an earlier outgoing mail setup"><Typography variant="caption" color="text.secondary">Earlier setup</Typography></Tooltip>}</Stack>,
          !!test.accepted && !test.confirmed && current ? <Button key="confirm" variant="outlined" size="small" disabled={!!busy} onClick={() => confirm(test.id)}>Confirm receipt</Button> : '—'] };
      })}/>
    </Stack>
  </Section>;
}
