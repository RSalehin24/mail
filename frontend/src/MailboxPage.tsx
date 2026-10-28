import { Forwarding } from './Forwarding';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Breadcrumbs, Button, Chip, Paper, Stack, Tab, Tabs, Typography } from '@mui/material';
import { api, cached, type Authorize, type Mailbox, type Notify } from './api';
import { MailboxDetails } from './MailboxDetails';
import { ClientConfig, PasswordDialog } from './MailboxDialogs';
import { SenderVerification } from './SenderVerification';
import { RecoveryAction } from './RecoveryActions';
import { SendingHistory } from './SendingHistory';
import { mailboxPath, navigate } from './navigation';

export function MailboxPage({ encodedEmail, tab, authorize, notify }: {
  encodedEmail: string; tab: string; authorize: Authorize; notify: Notify;
}) {
  const [mailbox, setMailbox] = useState<Mailbox | null>(() => cached<Mailbox>(`/accounts/${encodedEmail}`) ?? null);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState('');
  const load = useCallback(async () => {
    try {
      const account = await api<Mailbox>(`/accounts/${encodedEmail}`);
      setMailbox(account); setError('');
    } catch (failure) { setError((failure as Error).message); }
  }, [encodedEmail]);
  useEffect(() => { void load(); }, [load]);
  const common = { authorize, notify, onChanged: () => { void load(); }, onClose: () => setDialog('') };
  return <Stack spacing={3}>
    <Breadcrumbs aria-label="Breadcrumb"><Button href="/" onClick={event => { event.preventDefault(); navigate('/'); }}>Mailboxes</Button><Typography>{mailbox?.email ?? 'Mailbox'}</Typography></Breadcrumbs>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Retry</Button>}>{error}</Alert>}
    {!mailbox && !error && <Typography role="status">Loading mailbox…</Typography>}
    {mailbox && <>
      <Box><Typography variant="h4" component="h1" sx={{ overflowWrap: 'anywhere' }}>{mailbox.email}</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>{mailbox.display_name || 'No display name'}</Typography></Box>
      <Tabs value={tab} onChange={(_, value: string) => navigate(mailboxPath(mailbox.email, value))} variant="scrollable" allowScrollButtonsMobile aria-label="Mailbox pages">
        <Tab value="details" label="Details"/><Tab value="configuration" label="Configuration"/><Tab value="connections" label="Connection details"/>
      </Tabs>
      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
        {tab === 'details' && <Stack spacing={3}>
          <Box component="section" sx={{ p: { xs: 2, sm: 3 }, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
            <Stack spacing={2}><Typography variant="h6" component="h2" sx={{ fontWeight: 650 }}>Identity</Typography>
              <Box><Typography variant="caption" color="text.secondary">Display name</Typography><Typography>{mailbox.display_name || 'Not set'}</Typography></Box>
              <Box><Typography variant="caption" color="text.secondary">Notes</Typography><Typography sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{mailbox.notes || 'Not set'}</Typography></Box>
              <Button variant="outlined" sx={{ alignSelf: 'start' }} onClick={() => navigate(mailboxPath(mailbox.email, 'configuration'))}>Edit details</Button>
              <Typography variant="caption" color="text.secondary">The display name is a dashboard label. Set your outgoing From name in your mail client.</Typography>
            </Stack>
          </Box>
          <Box component="section" sx={{ p: { xs: 2, sm: 3 }, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
            <Stack spacing={2}><Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}><Typography variant="h6" component="h2" sx={{ fontWeight: 650 }}>Incoming mail</Typography><Chip variant="outlined" label={mailbox.status === 'ready' ? 'Ready' : 'Needs review'} color={mailbox.status === 'ready' ? 'success' : 'warning'}/></Stack>
              <Typography variant="body2" color="text.secondary">{mailbox.status === 'ready' ? 'Incoming credentials and mailbox storage are present.' : 'Ask the operator to reconcile the recorded operation before making further changes.'}</Typography>
              <Button sx={{ alignSelf: 'start' }} onClick={() => navigate(mailboxPath(mailbox.email, 'connections'))}>View connection details</Button>
            </Stack>
          </Box>
          <Box component="section" sx={{ p: { xs: 2, sm: 3 }, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
            <SendingHistory mailbox={mailbox} authorize={authorize} notify={notify} onChanged={load} onSend={() => setDialog('verify')}/>
          </Box>
        </Stack>}
        {tab === 'configuration' && <Stack spacing={3}>
          <MailboxDetails key={mailbox.updated ?? mailbox.display_name + mailbox.notes} mailbox={mailbox} {...common} embedded/>
          <Button variant="outlined" sx={{ alignSelf: 'start' }} onClick={() => setDialog('password')}>Change incoming password</Button>
          <Forwarding mailbox={mailbox.email} authorize={authorize} notify={notify}/>
          <Box component="section" sx={{ borderTop: '1px solid', borderColor: 'divider', pt: 3 }}><Typography variant="h6" sx={{ mb: 2 }}>Delete Mailbox</Typography><Button color="error" variant="outlined" sx={{ alignSelf: 'start' }} onClick={() => setDialog('delete')}>Delete mailbox</Button></Box>
        </Stack>}
        {tab === 'connections' && <ClientConfig mailbox={mailbox} {...common} embedded/>}
      </Paper>
      {dialog === 'password' && <PasswordDialog mailbox={mailbox} mode="reset" {...common}/>}
      {dialog === 'delete' && <RecoveryAction email={mailbox.email} mode="delete" {...common} onChanged={() => navigate('/')}/>}
      {dialog === 'verify' && <SenderVerification emails={[mailbox.email]} mailboxes={[mailbox]} {...common} onChanged={load}/>}
    </>}
  </Stack>;
}
