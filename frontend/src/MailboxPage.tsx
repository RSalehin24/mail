import { Forwarding } from './Forwarding';
import { useCallback, useEffect, useState } from 'react';
import { LoadingState } from './PageState';
import { Alert, Breadcrumbs, Button, Paper, Stack, Tab, Tabs, Typography } from '@mui/material';
import { api, cached, type Authorize, type Mailbox, type Notify } from './api';
import { MailboxDetails } from './MailboxDetails';
import { ClientConfig, PasswordDialog } from './MailboxDialogs';
import { SenderVerification } from './SenderVerification';
import { RecoveryAction } from './RecoveryActions';
import { SendingHistory } from './SendingHistory';
import { formatDate } from './format';
import { mailboxPath, navigate } from './navigation';
import { InfoList, PageHeader, Section, StatusDot } from './ui';

const NOT_SET = <Typography component="span" variant="body2" color="text.secondary">Not set</Typography>;

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
  const ready = mailbox?.status === 'ready';
  return <Stack spacing={3}>
    <Breadcrumbs aria-label="Breadcrumb" sx={{ ml: -1 }}><Button href="/mailboxes" onClick={event => { event.preventDefault(); navigate('/mailboxes'); }}>Mailboxes</Button><Typography>{mailbox?.email ?? 'Mailbox'}</Typography></Breadcrumbs>
    {error && <Alert severity="error" action={<Button onClick={() => { void load(); }}>Retry</Button>}>{error}</Alert>}
    {!mailbox && !error && <LoadingState label="Loading mailbox…"/>}
    {mailbox && <>
      <PageHeader title={mailbox.email} subtitle={mailbox.display_name || undefined} action={<StatusDot tone={ready ? 'success' : 'warning'} label={ready ? 'Ready' : 'Needs review'}/>}/>
      <Tabs value={tab} onChange={(_, value: string) => navigate(mailboxPath(mailbox.email, value))} variant="scrollable" allowScrollButtonsMobile aria-label="Mailbox pages">
        <Tab value="details" label="Details"/><Tab value="configuration" label="Configuration"/><Tab value="connections" label="Connection details"/>
      </Tabs>
      {tab === 'details' && <Stack spacing={3}>
        <Section title="Identity" action={<Button variant="outlined" size="small" onClick={() => navigate(mailboxPath(mailbox.email, 'configuration'))}>Edit</Button>}>
          <InfoList items={[
            { label: 'Display name', value: mailbox.display_name || NOT_SET },
            { label: 'Created', value: mailbox.created ? formatDate(mailbox.created) : NOT_SET },
            { label: 'Notes', value: mailbox.notes || NOT_SET },
            { label: 'Incoming mail', value: <StatusDot tone={ready ? 'success' : 'warning'} label={ready ? 'Ready' : 'Needs review'}/> },
          ]}/>
          {!ready && <Alert severity="warning" sx={{ mt: 2 }}>Ask the operator to reconcile the recorded operation before making further changes.</Alert>}
        </Section>
        <SendingHistory mailbox={mailbox} authorize={authorize} notify={notify} onChanged={load} onSend={() => setDialog('verify')}/>
      </Stack>}
      {tab === 'configuration' && <Stack spacing={3}>
        <MailboxDetails key={mailbox.updated ?? mailbox.display_name + mailbox.notes} mailbox={mailbox} {...common} embedded/>
        <Section title="Password" action={<Button variant="outlined" onClick={() => setDialog('password')}>Change password</Button>}/>
        <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 } }}><Forwarding mailbox={mailbox.email} authorize={authorize} notify={notify}/></Paper>
        <Section title="Delete mailbox" danger action={<Button color="error" variant="outlined" onClick={() => setDialog('delete')}>Delete mailbox</Button>}/>
      </Stack>}
      {tab === 'connections' && <ClientConfig mailbox={mailbox} {...common} embedded/>}
      {dialog === 'password' && <PasswordDialog mailbox={mailbox} mode="reset" {...common}/>}
      {dialog === 'delete' && <RecoveryAction email={mailbox.email} {...common} onChanged={() => navigate('/mailboxes')}/>}
      {dialog === 'verify' && <SenderVerification emails={[mailbox.email]} mailboxes={[mailbox]} {...common} onChanged={load}/>}
    </>}
  </Stack>;
}
