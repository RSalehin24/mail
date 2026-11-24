import { useRef, useState } from 'react';
import { Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from '@mui/material';
import { AdminTable } from './AdminTable';
import { sendingState } from './sendingState';
import { api, ApiError, type Authorize, type Mailbox, type Notify } from './api';

// Announces progress to screen readers; the visible feedback is each row's status chip.
const SCREEN_READER_ONLY = { position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' } as const;

export function SenderVerification({ emails, mailboxes, authorize, notify, onClose, onChanged }: {
  emails: string[]; mailboxes: Mailbox[]; authorize: Authorize; notify: Notify; onClose: () => void; onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<string[]>([]);
  const completedSenders = useRef(new Set<string>());
  const [currentSender, setCurrentSender] = useState('');
  function send() {
    authorize(async () => {
      setBusy(true); setErrors({});
      const failures: Record<string, string> = {};
      const completed: string[] = [];
      try {
        for (const email of emails) {
          if (completedSenders.current.has(email) || mailboxes.find(mailbox => mailbox.email === email)?.sender_tests?.some(test => test.accepted)) continue;
          setCurrentSender(email);
          try {
            const result = await api<{ smtp_accepted: boolean }>(`/accounts/${encodeURIComponent(email)}/test-sender`, {});
            if (result.smtp_accepted) { completedSenders.current.add(email); completed.push(email); setSent(current => [...current, email]); }
            else failures[email] = 'Delivery was rejected. Check this sender in Brevo, then retry.';
          } catch (failure) {
            failures[email] = (failure as Error).message;
            if (failure instanceof ApiError && ['recent_authentication_required', 'password_confirmation_required', 'authentication_required'].includes(failure.code)) throw failure;
          }
        }
        setErrors(failures); await onChanged();
        if (completed.length) notify(`${completed.length} test ${completed.length === 1 ? 'email sent' : 'emails sent'}. Confirm each matching receipt here.`);
      } finally { setCurrentSender(''); setErrors(failures); setBusy(false); }
    });
  }
  // A test can only be confirmed once it was sent, so the Actions column appears only when some row has one.
  const pendingTests = (email: string) => mailboxes.find(value => value.email === email)?.sender_tests?.filter(test => test.accepted && !test.confirmed) ?? [];
  const showActions = emails.some(email => pendingTests(email).length > 0);
  return <Dialog open fullWidth maxWidth="md" onClose={busy ? undefined : onClose} aria-labelledby="sender-verification-title">
    <DialogTitle id="sender-verification-title" sx={{ pb: 1 }}>Verify outgoing email</DialogTitle>
    <DialogContent>
      <Box role="status" sx={SCREEN_READER_ONLY}>{currentSender && `Sending test from ${currentSender}`}</Box>
      <AdminTable wideFirst widths={showActions ? ['42%', '30%', '28%'] : ['60%', '40%']} columns={showActions ? ['Mailbox', 'Sending', 'Actions'] : ['Mailbox', 'Sending']} empty="No mailboxes." rows={emails.map(email => {
        const state = errors[email] ? { color: 'error' as const, label: 'Test failed' } : email === currentSender ? { color: 'warning' as const, label: 'Sending…' } : sendingState(mailboxes.find(value => value.email === email)?.outbound_readiness ?? '');
        const pending = pendingTests(email);
        const cells = [email, <Stack key="state" spacing={0.5}><Chip size="small" variant="outlined" color={state.color} label={state.label}/>{errors[email] && <Typography variant="caption" color="error">{errors[email]}</Typography>}</Stack>];
        if (showActions) cells.push(pending.length ? <Stack key="actions" spacing={1}>{pending.map(test => <Button key={test.id} variant="outlined" disabled={busy} onClick={() => authorize(async () => {
          setBusy(true);
          try { await api(`/relay-tests/${test.id}/confirm`, {}); await onChanged(); notify(`Sending verified for ${email}`); }
          finally { setBusy(false); }
        })}>I received this email</Button>)}</Stack> : <span key="actions"/>);
        return { key: email, cells };
      })}/>
    </DialogContent>
    <DialogActions sx={{ px: 3, pb: 2.5 }}><Button onClick={onClose} disabled={busy}>Done</Button>
      <Button variant="contained" onClick={send} disabled={busy || emails.every(email => sent.includes(email) || mailboxes.find(mailbox => mailbox.email === email)?.sender_tests?.some(test => test.accepted))}>
        {busy ? 'Working…' : `Send ${emails.length === 1 ? 'test email' : `${emails.length} test emails`}`}
      </Button>
    </DialogActions>
  </Dialog>;
}
