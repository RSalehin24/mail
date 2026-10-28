import { formatDateTime } from './format';
import { useEffect, useState } from 'react';
import { Alert, Box, Button, Paper, Stack, Tab, Tabs, TextField, Typography } from '@mui/material';
import { api, cached, type Authorize, type Notify, type RelaySettings } from './api';
import { SecuritySettings } from './SecuritySettings';

interface Props { authorize: Authorize; notify: Notify; onRevoked: (codes: string[]) => void }

import { BackupSettings } from './BackupSettings';
import { useDeliveryHistory } from './DeliveryHistoryPagination';
import { navigate } from './navigation';


export function SettingsView({ authorize, notify, onRevoked }: Props) {
  const names = ['security', 'outgoing', 'backups'];
  const [tab, setTab] = useState(() => { const value = new URLSearchParams(location.search).get('tab'); return value && names.includes(value) ? value : 'security'; });
  useEffect(() => { const legacy = new URLSearchParams(location.search).get('tab'); if (legacy === 'deletion' || legacy === 'protected') navigate(`/deleted?tab=${legacy === 'deletion' ? 'configure' : 'protected'}`); }, []);
  return <Stack spacing={3}>
    <Typography variant="h4" component="h1">Settings</Typography>
    <Tabs value={tab} onChange={(_, value: string) => { setTab(value); const url = new URL(location.href); url.searchParams.set('tab', value); history.replaceState(null, '', url); }} variant="scrollable" scrollButtons="auto" aria-label="Settings tabs">
      <Tab value="security" label="Security" id="settings-security" aria-controls="panel-security"/>
      <Tab value="outgoing" label="Outgoing mail provider" id="settings-outgoing" aria-controls="panel-outgoing"/>
      <Tab value="backups" label="Backup & Recovery" id="settings-backups" aria-controls="panel-backups"/>
    </Tabs>
    <Box role="tabpanel" id={`panel-${tab}`} aria-labelledby={`settings-${tab}`}>
      {tab === 'security' && <SecuritySettings authorize={authorize} onRevoked={onRevoked} onDeliverySettings={() => { setTab('outgoing'); const url = new URL(location.href); url.searchParams.set('tab', 'outgoing'); history.replaceState(null, '', url); }}/>}
      {tab === 'outgoing' && <OutgoingSettings authorize={authorize} notify={notify}/>}
      {tab === 'backups' && <BackupSettings authorize={authorize} notify={notify}/>}
    </Box>
  </Stack>;
}

function OutgoingSettings({ authorize, notify }: Pick<Props, 'authorize' | 'notify'>) {
  const [relay, setRelay] = useState<RelaySettings | null>(() => cached<RelaySettings>('/settings/relay') ?? null);
  const [username, setUsername] = useState(() => cached<RelaySettings>('/settings/relay')?.username ?? '');
  const port = 587;
  const [credential, setCredential] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [quota, setQuota] = useState('Add a Brevo API key to view remaining credits. SMTP sending does not require it.');
  async function load() {
    try { const value = await api<RelaySettings>('/settings/relay'); setRelay(value); setUsername(value.username); }
    catch (failure) { setError((failure as Error).message); }
  }
  useEffect(() => { void load(); }, []);
  const delivery = useDeliveryHistory('mail@rsalehin24.me', JSON.stringify(relay?.tests));
  return <Stack spacing={3}>
    {error && <Alert severity="error">{error}</Alert>}
    {!relay && error && <Button onClick={() => { setError(''); void load(); }}>Retry relay settings</Button>}
    <Paper component="fieldset" disabled={relay === null} variant="outlined" sx={{ m: 0, minWidth: 0, p: { xs: 2, md: 3 }, borderRadius: 3 }}><Stack spacing={2.5}>
      <Box><Typography variant="h6">Outgoing mail provider</Typography><Typography variant="body2" color="text.secondary">{relay ? `${relay.otp_ready ? 'Administrator email delivery verified' : 'Administrator email delivery needs verification'}` : 'Loading saved relay settings…'}</Typography></Box>
      <Typography variant="caption" color="text.secondary">{relay ? `Saved revision ${relay.revision}` : ''}</Typography>
      <TextField label="SMTP host" value="smtp-relay.brevo.com" disabled/>
      <TextField label="SMTP security" value="587 · STARTTLS" disabled/>
      <TextField label="Brevo SMTP login" value={username} onChange={event => { setUsername(event.target.value); setDirty(true); }}/>
      <TextField label="Brevo SMTP key" type="password" autoComplete="new-password" value={credential} onChange={event => { setCredential(event.target.value); setDirty(true); }} helperText={relay?.configured ? 'Configured and masked. Leave blank to preserve it.' : 'Required for first save.'}/>
      <TextField label="Optional Brevo API key" type="password" autoComplete="new-password" value={apiKey} onChange={event => { setApiKey(event.target.value); setDirty(true); }} helperText="Encrypted separately and kept on the server."/>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' } }}>
        <Button disabled={dirty || !relay?.configured} onClick={async () => {
          try { const result = await api<{ state: string; plans: { plan: string; credits: number; credits_type: string }[] }>('/settings/relay/quota');
            setQuota(result.state === 'not_configured' ? 'No API key configured' : result.plans.map(plan => `${plan.plan}: ${plan.credits} credits (${plan.credits_type})`).join(' · ') || 'Provider did not report an email credit count');
          } catch (failure) { setQuota((failure as Error).message); }
        }}>Refresh provider credits</Button><Typography variant="body2" color="text.secondary">{quota}</Typography>
      </Stack>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
        <Button variant="contained" disabled={!dirty || !username || (!relay?.configured && !credential)} onClick={() => authorize(async () => {
          await api('/settings/relay', { host: 'smtp-relay.brevo.com', port, tls_mode: port === 587 ? 'starttls' : 'tls', username,
            ...(credential ? { credential } : {}), ...(apiKey ? { api_key: apiKey } : {}) });
          setCredential(''); setApiKey(''); setDirty(false); await load(); notify('Relay saved. Retest this revision before enabling email codes.');
        })}>Save relay</Button>
        <Button disabled={dirty || !relay?.configured} onClick={() => authorize(async () => {
          const result = await api<{ connection_ok: boolean }>('/settings/relay/test', { mode: 'connection' });
          notify(result.connection_ok ? 'Verified TLS and SMTP authentication passed; delivery remains unconfirmed.' : 'Relay connection or authentication failed.'); await load();
        })}>Test connection</Button>
        <Button disabled={dirty || !relay?.configured} onClick={() => authorize(async () => {
          const result = await api<{ smtp_accepted: boolean }>('/settings/relay/test', { mode: 'delivery' });
          notify(result.smtp_accepted ? 'Test accepted by SMTP. Confirm the matching message below after receipt.' : 'SMTP delivery test failed.'); await load();
        })}>Send explicit delivery test</Button>
      </Stack>
      <Alert severity="info"><Typography variant="subtitle2">Administrator email delivery</Typography><Typography variant="body2">Tests from mail@rsalehin24.me go to rsalehin24@gmail.com. Each mailbox has its own sending verification and history on its Details page. Saving provider credentials requires a new delivery test.</Typography></Alert>
      {!!relay?.tests.filter(test => test.sender === 'mail@rsalehin24.me').length && <Typography variant="subtitle2">Administrator delivery history</Typography>}
      {delivery.rows.map(test => <Box key={test.id} sx={{ p: 2, bgcolor: 'action.hover', borderRadius: 2 }}>
        <Typography variant="body2">From {test.sender} · {formatDateTime(test.created)}</Typography>
        <Typography variant="caption" sx={{ overflowWrap: 'anywhere' }}>Test {test.id}</Typography>
        <Typography variant="body2">{test.confirmed ? 'Receipt confirmed' : test.accepted ? 'SMTP accepted · receipt unconfirmed' : test.connection_ok ? 'Connection only · no email sent' : 'Failed'}</Typography>
        {!!test.accepted && !test.confirmed && test.revision === relay?.revision && <Button onClick={() => authorize(async () => {
          await api(`/relay-tests/${test.id}/confirm`, {}); await load(); notify('Matching delivery receipt recorded');
        })}>I received this test</Button>}
      </Box>)}
      {delivery.error && <Alert severity="error">{delivery.error}</Alert>}
      {delivery.controls}
    </Stack></Paper>
  </Stack>;
}
