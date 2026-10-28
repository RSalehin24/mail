import { formatDateTime } from './format';
import { useRef, useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Stack, Typography } from '@mui/material';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import { api, ApiError, type Authorize, type Mailbox, type Notify } from './api';

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
            if (failure instanceof ApiError && ['recent_authentication_required', 'authentication_required'].includes(failure.code)) throw failure;
          }
        }
        setErrors(failures); await onChanged();
        if (completed.length) notify(`${completed.length} test ${completed.length === 1 ? 'email sent' : 'emails sent'}. Confirm each matching receipt here.`);
      } finally { setCurrentSender(''); setErrors(failures); setBusy(false); }
    });
  }
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose} aria-labelledby="sender-verification-title">
    <DialogTitle id="sender-verification-title" sx={{ pb: 1 }}>Verify outgoing email</DialogTitle>
    <DialogContent>
      <Typography color="text.secondary" sx={{ mb: 3 }}>Send a test from each selected address to rsalehin24@gmail.com. Match the test ID in the email, then confirm receipt below.</Typography>
      {currentSender && <Typography role="status" sx={{ mb: 2 }}>Sending test from {currentSender}…</Typography>}
      <Stack spacing={2}>{emails.map(email => {
        const mailbox = mailboxes.find(value => value.email === email);
        const pending = mailbox?.sender_tests?.filter(test => test.accepted && !test.confirmed) ?? [];
        const verified = mailbox?.outbound_readiness === 'confirmed';
        return <Box key={email} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 650, overflowWrap: 'anywhere' }}>{email}</Typography>
            {verified && <Chip size="small" color="success" variant="outlined" icon={<CheckCircleOutlineRounded/>} label="Verified"/>}
          </Stack>
          {errors[email] && <Alert severity="error" sx={{ mt: 1 }}>{errors[email]}</Alert>}
          {!pending.length && !verified && <Typography variant="body2" color="text.secondary">Ready to send a test email.</Typography>}
          {pending.map(test => <Box key={test.id} sx={{ mt: 2 }}>
            <Typography variant="caption" color="text.secondary">Sent {formatDateTime(test.created)}</Typography>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere', mt: 0.5 }}>{test.id}</Typography>
            <Button sx={{ mt: 1 }} variant="outlined" size="small" disabled={busy} onClick={() => authorize(async () => {
              setBusy(true);
              try { await api(`/relay-tests/${test.id}/confirm`, {}); await onChanged(); notify(`Sending verified for ${email}`); }
              finally { setBusy(false); }
            })}>I received this email</Button>
          </Box>)}
        </Box>;
      })}</Stack>
      <Divider sx={{ my: 2 }}/>
      <Typography variant="caption" color="text.secondary">Your sender address or domain must be authorized in Brevo. SMTP acceptance alone does not confirm that an email reached your inbox.</Typography>
    </DialogContent>
    <DialogActions sx={{ px: 3, pb: 2.5 }}><Button onClick={onClose} disabled={busy}>Done</Button>
      <Button variant="contained" onClick={send} disabled={busy || emails.every(email => sent.includes(email) || mailboxes.find(mailbox => mailbox.email === email)?.sender_tests?.some(test => test.accepted))}>
        {busy ? 'Working…' : `Send ${emails.length === 1 ? 'test email' : `${emails.length} test emails`}`}
      </Button>
    </DialogActions>
  </Dialog>;
}
