import { useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField } from '@mui/material';
import { api, type Authorize, type Mailbox, type Notify } from './api';

export function MailboxDetails({ mailbox, authorize, notify, onClose, onChanged, embedded = false }: {
  embedded?: boolean; mailbox: Mailbox; authorize: Authorize; notify: Notify; onClose: () => void; onChanged: () => void;
}) {
  const [name, setName] = useState(mailbox.display_name);
  const [notes, setNotes] = useState(mailbox.notes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const content = <>
    <DialogTitle>Edit mailbox details</DialogTitle>
    <DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
      {error && <Alert severity="error">{error}</Alert>}
      <TextField label="Mailbox" value={mailbox.email} disabled/>
      <TextField autoFocus label="Display name" value={name} onChange={event => setName(event.target.value)} disabled={busy}
        helperText="A label in this dashboard. Set your outgoing From name separately in your mail client."/>
      <TextField label="Notes / purpose" value={notes} multiline minRows={2} onChange={event => setNotes(event.target.value)} disabled={busy}/>
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose} disabled={busy}>Cancel</Button><Button variant="contained" disabled={busy}
      onClick={() => authorize(async () => {
        setBusy(true); setError('');
        try {
          await api(`/accounts/${encodeURIComponent(mailbox.email)}/metadata`, { display_name: name, notes }, 'PATCH');
          onChanged(); notify('Mailbox details saved'); onClose();
        } catch (failure) { setError((failure as Error).message); throw failure; }
        finally { setBusy(false); }
      })}>Save details</Button></DialogActions>
  </>;
  return embedded ? content : <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose}>{content}</Dialog>;
}
