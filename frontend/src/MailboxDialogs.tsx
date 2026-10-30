import { useEffect, useState } from 'react';
import { Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, Stack, Tab, Tabs, TextField, Tooltip, Typography, useMediaQuery } from '@mui/material';
import ContentCopyOutlined from '@mui/icons-material/ContentCopyOutlined';
import { ApiError, api, domain, generatePassword, validMailboxPassword, type Authorize, type Mailbox,
  type Notify, type Outcome } from './api';

interface Common { onClose: () => void; onChanged: () => void; notify: Notify; authorize: Authorize }

export function PasswordDialog({ mode, mailbox, onClose, onChanged, notify, authorize }:
  Common & { mode: 'create' | 'reset'; mailbox?: Mailbox }) {
  const fullScreen = useMediaQuery('(max-width:599px)');
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
      else { notify(mode === 'create' ? 'Mailbox created. Authorize its sender in your SMTP provider before sending.' : 'Password changed. Existing IMAP sessions may remain active.'); onChanged(); onClose(); }
    } catch (failure) { if (failure instanceof ApiError && failure.code === 'recent_authentication_required') throw failure; setError((failure as Error).message); setUncertain(true); }
    finally { setBusy(false); }
  }
  return <Dialog open fullScreen={fullScreen} fullWidth maxWidth="sm" onClose={busy ? undefined : onClose} aria-labelledby="mailbox-dialog-title">
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
      {mode === 'create' && <Typography variant="body2" color="text.secondary">After creation, add and verify this sender in your SMTP provider’s Senders & Domains settings. Then use “Test sender” and confirm receipt. Incoming provisioning does not authorize outgoing mail.</Typography>}
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
  return <Dialog open fullScreen={useMediaQuery('(max-width:599px)')} fullWidth maxWidth="sm" onClose={busy ? undefined : common.onClose} aria-labelledby="delete-title">
    <DialogTitle id="delete-title">Permanently delete {mailbox.email}</DialogTitle>
    <DialogContent><Stack spacing={2.5}>
      <Alert severity="warning">Stored messages and incoming credentials will be removed. Existing IMAP sessions may remain active if deletion fails. your SMTP provider sender access and shared outgoing keys are managed separately.</Alert>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography>Trusted recovery cutoff: <strong>{cutoff || 'No qualifying backup available'}</strong></Typography>
      <FormControlLabel control={<Checkbox checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)}/>}
        label="I understand mail arriving after this cutoff is outside the backup."/>
      <TextField label="Type the exact email address" value={confirmation} autoComplete="off" onChange={event => setConfirmation(event.target.value)}/>
      <Typography variant="body2" color="text.secondary">The helper rechecks archive availability, account coverage, incarnation and protected recipient policy before any deletion.</Typography>
    </Stack></DialogContent>
    <DialogActions><Button onClick={common.onClose} disabled={busy}>Cancel</Button><Button color="error" variant="contained"
      disabled={busy || attempted || !cutoff || !acknowledged || confirmation !== mailbox.email} onClick={() => common.authorize(async () => {
        setBusy(true); setAttempted(true);
        try {
          const outcome = await api<Outcome>(`/accounts/${encodeURIComponent(mailbox.email)}`, { request_id: identifier, confirmation, acknowledged, cutoff }, 'DELETE');
          if (outcome.state !== 'complete') setError(`${outcome.warning} Operation ${identifier}`);
          else { common.notify('Mailbox deleted'); common.onChanged(); common.onClose(); }
        } catch (failure) { if (failure instanceof ApiError && failure.code === 'recent_authentication_required') { setAttempted(false); throw failure; } setError((failure as Error).message); }
        finally { setBusy(false); }
      })}>Delete permanently</Button></DialogActions>
  </Dialog>;
}

type Connection = { host: string; port: number; security: string; username: string; password: string };
const clients = ['Apple Mail', 'Gmail', 'Thunderbird / Outlook', 'Nextcloud', 'Raw details'];

export function ClientConfig({ mailbox, onClose, notify, embedded = false }: Common & { mailbox: Mailbox; embedded?: boolean }) {
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
  const fullScreen = useMediaQuery('(max-width:599px)');
  const content = <>
    <DialogTitle id="client-title">Connect {mailbox.email}</DialogTitle>
    <DialogContent>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography sx={{ mb: 2 }}>Use {mailbox.email} and your mailbox password for both IMAP and SMTP. Your From address must match this mailbox.</Typography>
      <Tabs value={tab} onChange={(_, value: number) => setTab(value)} variant="scrollable" scrollButtons="auto" aria-label="Mail client">{clients.map(client => <Tab label={client} key={client}/>)}</Tabs>
      <Typography sx={{ mt: 2 }} variant="body2">{tab === 0 ? 'In Mail settings, add an Other Mail Account. Enter the incoming and outgoing settings separately.' : tab === 1 ? 'Choose Other (IMAP) in the Gmail app. For Gmail web “Send As”, use the outgoing SMTP details and mailbox password. Gmail web saves its own sent copy in Gmail; use an IMAP client to save to this server’s Sent folder.' : tab === 2 ? 'Choose manual setup, IMAP for incoming mail, and the outgoing SMTP settings below.' : tab === 3 ? 'In Nextcloud Mail, use manual configuration and use the same mailbox credentials for IMAP and SMTP.' : 'Use these values in any IMAP client with separate incoming and outgoing authentication.'}</Typography>
      {configuration && <Box className="client-pair">{(['incoming', 'outgoing'] as const).map(direction => <Box key={direction}>
        <Typography variant="h6">{direction === 'incoming' ? 'Incoming · IMAP' : 'Outgoing · SMTP'}</Typography>
        {Object.entries(configuration[direction]).map(([key, value]) => <Box key={key} className="connection-field">
          <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}><Box sx={{ minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">{key === 'password' ? 'Mailbox password' : key}</Typography>
            <Typography sx={{ overflowWrap: 'anywhere' }}>{String(value)}</Typography>
          </Box>{key !== 'password' && <Tooltip title={`Copy ${direction} ${key}`}><Button size="small" aria-label={`Copy ${direction} ${key}`} onClick={async () => { await navigator.clipboard.writeText(String(value)); notify('Copied'); }}><ContentCopyOutlined fontSize="small"/></Button></Tooltip>}</Stack>
        </Box>)}
      </Box>)}</Box>}
      <Alert severity="info" sx={{ mt: 3 }}>Use your known mailbox password or reset it; stored password hashes cannot be recovered. Enable saving sent messages to this account’s server IMAP Sent folder. Maddy does not create an automatic Sent copy. Sent means successful submission to Maddy’s queue, not confirmed recipient delivery. Clients that disable saving sent messages will not retain a sent copy.</Alert>
    </DialogContent>{!embedded && <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>}
  </>;
  return embedded ? content : <Dialog open fullScreen={fullScreen} fullWidth maxWidth="md" onClose={onClose} aria-labelledby="client-title">{content}</Dialog>;
}
