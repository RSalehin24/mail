import { useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField } from '@mui/material';
import { api, type Authorize, type Mailbox, type Notify } from './api';
import { Section } from './ui';

export function MailboxDetails({ mailbox, authorize, notify, onClose, onChanged, embedded = false }: {
  embedded?: boolean; mailbox: Mailbox; authorize: Authorize; notify: Notify; onClose: () => void; onChanged: () => void;
}) {
  const [name, setName] = useState(mailbox.display_name);
  const [notes, setNotes] = useState(mailbox.notes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fields = <Stack spacing={2} sx={{ pt: embedded ? 0 : 1 }}>
    {error && <Alert severity="error">{error}</Alert>}
    {!embedded && <TextField label="Mailbox" value={mailbox.email} disabled/>}
    <TextField autoFocus={!embedded} label="Display name" placeholder="Shown only in this dashboard" value={name} onChange={event => setName(event.target.value)} disabled={busy}/>
    <TextField label="Notes" value={notes} multiline minRows={2} onChange={event => setNotes(event.target.value)} disabled={busy}/>
  </Stack>;
  const save = <Button variant="contained" disabled={busy || (name === mailbox.display_name && notes === mailbox.notes)}
      onClick={() => authorize(async () => {
        setBusy(true); setError('');
        try {
          await api(`/accounts/${encodeURIComponent(mailbox.email)}/metadata`, { display_name: name, notes }, 'PATCH');
          onChanged(); notify('Mailbox details saved'); onClose();
        } catch (failure) { setError((failure as Error).message); throw failure; }
        finally { setBusy(false); }
      })}>Save details</Button>;
  if (embedded) return <Section title="Details">{fields}<Stack direction="row" sx={{ mt: 2 }}>{save}</Stack></Section>;
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose}>
    <DialogTitle>Edit mailbox details</DialogTitle><DialogContent>{fields}</DialogContent>
    <DialogActions><Button onClick={onClose} disabled={busy}>Cancel</Button>{save}</DialogActions>
  </Dialog>;
}
