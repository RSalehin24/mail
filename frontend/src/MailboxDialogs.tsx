import { useEffect, useState } from 'react';
import { Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, Paper, Stack, Tab, Tabs, TextField, Typography } from '@mui/material';
import ContentCopyOutlined from '@mui/icons-material/ContentCopyOutlined';
import { ApiError, api, domain, generatePassword, validMailboxPassword, type Authorize, type Mailbox,
  type Notify, type Outcome } from './api';
import AddCircleOutlineRounded from '@mui/icons-material/AddCircleOutlineRounded';
import KeyRounded from '@mui/icons-material/KeyRounded';
import LoginRounded from '@mui/icons-material/LoginRounded';
import SendRounded from '@mui/icons-material/SendRounded';
import { CopyRow, Disclosure, NoteList, Section } from './ui';

interface Common { onClose: () => void; onChanged: () => void; notify: Notify; authorize: Authorize }

export function PasswordDialog({ mode, mailbox, onClose, onChanged, notify, authorize }:
  Common & { mode: 'create' | 'reset'; mailbox?: Mailbox }) {
  const [local, setLocal] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [notes, setNotes] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [identifier] = useState(() => crypto.randomUUID());
  const [uncertain, setUncertain] = useState(false);
  async function submit() {
    setBusy(true); setError('');
    try {
      const result = mode === 'create' ? await api<Outcome>('/accounts/create', {
        request_id: identifier, email: `${local}@${domain}`, password, display_name: displayName, notes,
      }) : await api<Outcome>(`/accounts/${encodeURIComponent(mailbox!.email)}/password`, { request_id: identifier, password });
      if (result.state !== 'complete') { setUncertain(true); setError(`${result.warning || 'Reconciliation required.'} Operation ${identifier}`); }
      else { notify(mode === 'create' ? 'Mailbox created. Authorize its sender in Brevo before sending.' : 'Password changed. Existing IMAP sessions may remain active.'); onChanged(); onClose(); }
    } catch (failure) { if (failure instanceof ApiError && ['recent_authentication_required', 'password_confirmation_required'].includes(failure.code)) throw failure; setError((failure as Error).message); setUncertain(true); }
    finally { setBusy(false); }
  }
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : onClose} aria-labelledby="mailbox-dialog-title">
    <DialogTitle id="mailbox-dialog-title">{mode === 'create' ? 'Create a mailbox' : 'Reset mailbox password'}</DialogTitle>
    <DialogContent><Stack spacing={2.5} sx={{ pt: 1 }}>
      {error && <Alert severity="warning">{error}</Alert>}
      {mode === 'reset' && <Alert severity="info">{mailbox?.email}<br/>This changes future IMAP and SMTP logins. Existing sessions may remain active. Ask the operator for immediate disconnection if needed.</Alert>}
      {mode === 'create' && <>
        <TextField label="Username prefix" value={local} onChange={event => setLocal(event.target.value.toLowerCase())} helperText={`@${domain}`} disabled={busy || uncertain}/>
        <TextField label="Display name" value={displayName} onChange={event => setDisplayName(event.target.value)} disabled={busy || uncertain}/>
        <TextField label="Notes / purpose" multiline minRows={2} value={notes} onChange={event => setNotes(event.target.value)} disabled={busy || uncertain}/>
      </>}
      <TextField label="Mailbox password" type={visible ? 'text' : 'password'} autoComplete="new-password" value={password}
        disabled={busy || uncertain} onChange={event => setPassword(event.target.value)}
        helperText="At least 16 characters, at most 72 UTF-8 bytes. Spaces are preserved."/>
      <Stack direction="row" spacing={1}><Button onClick={() => { setPassword(generatePassword()); setVisible(true); }} disabled={busy || uncertain}>Generate 24-character password</Button>
        <Button startIcon={<ContentCopyOutlined/>} disabled={!password} onClick={async () => { await navigator.clipboard.writeText(password); notify('Password copied'); }}>Copy</Button></Stack>
      <FormControlLabel control={<Checkbox checked={visible} onChange={event => setVisible(event.target.checked)}/>} label="Show password for this flow"/>
      {uncertain && <Typography variant="body2">Keep operation ID {identifier}. Reconcile the recorded outcome with the operator before trying another mutation.</Typography>}
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose} disabled={busy}>Close</Button><Button variant="contained"
      disabled={busy || uncertain || !validMailboxPassword(password) || (mode === 'create' && !local)}
      onClick={() => mode === 'reset' ? authorize(submit) : void submit()}>{mode === 'create' ? 'Create mailbox' : 'Reset password'}</Button></DialogActions>
  </Dialog>;
}

export function DeleteDialog({ mailbox, cutoff, ...common }: Common & { mailbox: Mailbox; cutoff?: string }) {
  const [confirmation, setConfirmation] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [identifier] = useState(() => crypto.randomUUID());
  const [attempted, setAttempted] = useState(false);
  return <Dialog open fullWidth maxWidth="sm" onClose={busy ? undefined : common.onClose} aria-labelledby="delete-title">
    <DialogTitle id="delete-title" className="keep-case">Permanently Delete {mailbox.email}</DialogTitle>
    <DialogContent><Stack spacing={2.5}>
      <Alert severity="warning">Stored messages and incoming credentials will be removed. Existing IMAP sessions may remain active if deletion fails. Brevo sender access and shared outgoing keys are managed separately.</Alert>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography>Trusted recovery cutoff: <strong>{cutoff || 'No qualifying backup available'}</strong></Typography>
      <FormControlLabel control={<Checkbox checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)}/>}
        label="I understand mail arriving after this cutoff is outside the backup."/>
      <TextField label="Type the exact email address" value={confirmation} autoComplete="off" onChange={event => setConfirmation(event.target.value)}/>
    </Stack></DialogContent>
    <DialogActions><Button onClick={common.onClose} disabled={busy}>Cancel</Button><Button color="error" variant="contained"
      disabled={busy || attempted || !cutoff || !acknowledged || confirmation !== mailbox.email} onClick={() => common.authorize(async () => {
        setBusy(true); setAttempted(true);
        try {
          const outcome = await api<Outcome>(`/accounts/${encodeURIComponent(mailbox.email)}`, { request_id: identifier, confirmation, acknowledged, cutoff }, 'DELETE');
          if (outcome.state !== 'complete') setError(`${outcome.warning} Operation ${identifier}`);
          else { common.notify('Mailbox deleted'); common.onChanged(); common.onClose(); }
        } catch (failure) { if (failure instanceof ApiError && ['recent_authentication_required', 'password_confirmation_required'].includes(failure.code)) { setAttempted(false); throw failure; } setError((failure as Error).message); }
        finally { setBusy(false); }
      })}>Delete permanently</Button></DialogActions>
  </Dialog>;
}

type Connection = { host: string; port: number; security: string; username: string; password: string };
const clients = ['Apple Mail', 'Gmail', 'Thunderbird / Outlook', 'Nextcloud', 'Raw details'];
const FIELD_LABELS: Record<string, string> = { host: 'Server', port: 'Port', security: 'Security', username: 'Username' };
const CLIENT_HINTS = [
  'In Mail settings, add an Other Mail Account. Enter the incoming and outgoing settings separately.',
  'Choose Other (IMAP) in the Gmail app. For Gmail web “Send As”, use the outgoing SMTP details and mailbox password. Gmail web saves its own sent copy in Gmail; use an IMAP client to save to this server’s Sent folder.',
  'Choose manual setup, IMAP for incoming mail, and the outgoing SMTP settings below.',
  'In Nextcloud Mail, use manual configuration and use the same mailbox credentials for IMAP and SMTP.',
  'Use these values in any IMAP client with separate incoming and outgoing authentication.',
];

export function ClientConfig({ mailbox, onClose, embedded = false }: Common & { mailbox: Mailbox; embedded?: boolean }) {
  const [configuration, setConfiguration] = useState<{ incoming: Connection; outgoing: Connection } | null>(null);
  const [tab, setTab] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void api<{ incoming: Connection; outgoing: Connection }>(`/accounts/${encodeURIComponent(mailbox.email)}/config`)
      .then(value => { if (active) setConfiguration(value); })
      .catch(failure => { if (active) setError((failure as Error).message); });
    return () => { active = false; };
  }, [mailbox.email]);
  const body = <Stack spacing={2.5}>
        {error && <Alert severity="error">{error}</Alert>}
        <Tabs value={tab} onChange={(_, value: number) => setTab(value)} variant="scrollable" scrollButtons="auto" aria-label="Mail client">{clients.map(client => <Tab label={client} key={client}/>)}</Tabs>
        <Paper variant="outlined" sx={{ p: 2, bgcolor: 'var(--surface-low)' }}><NoteList notes={[{ icon: <AddCircleOutlineRounded fontSize="small"/>, label: 'Setup', text: CLIENT_HINTS[tab] }]}/></Paper>
        {configuration && <Box className="client-pair">{(['incoming', 'outgoing'] as const).map(direction => <Section key={direction} title={direction === 'incoming' ? 'Incoming · IMAP' : 'Outgoing · SMTP'}>
          {Object.entries(configuration[direction]).map(([key, value]) => key === 'password'
            ? <CopyRow key={key} label="Password" value="Your mailbox password"/>
            : <CopyRow key={key} label={FIELD_LABELS[key] ?? key} value={String(value)} copyLabel={`Copy ${direction} ${key}`}/>)}
        </Section>)}</Box>}
        <Disclosure title="Good to know">
          <NoteList notes={[
            { icon: <LoginRounded fontSize="small"/>, label: 'Sign in', text: `Use ${mailbox.email} and your mailbox password for both IMAP and SMTP. The From address must match this mailbox.` },
            { icon: <KeyRounded fontSize="small"/>, label: 'Password', text: 'Stored password hashes cannot be recovered. Use your known password, or change it.' },
            { icon: <SendRounded fontSize="small"/>, label: 'Sent mail', text: 'Turn on saving sent messages to the server IMAP Sent folder; Maddy does not create a copy itself. “Sent” means accepted into Maddy’s queue, not delivered.' },
          ]}/>
        </Disclosure>
  </Stack>;
  return embedded ? body : <Dialog open fullWidth maxWidth="md" onClose={onClose} aria-labelledby="client-title">
    <DialogTitle id="client-title" className="keep-case">Connect {mailbox.email}</DialogTitle><DialogContent>{body}</DialogContent><DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
  </Dialog>;
}
