import { formatDate, formatTime } from './format';
import { useEffect, useState } from 'react';
import { Alert, Box, Button, Paper, Stack, Tab, Tabs, TextField, Tooltip, Typography } from '@mui/material';
import { api, cached, type Authorize, type Notify, type RelaySettings } from './api';
import { SecuritySettings } from './SecuritySettings';
import { AdminTable } from './AdminTable';
import { InfoList, Section, StatusBadge, StatusDot } from './ui';

interface Props { authorize: Authorize; notify: Notify; onRevoked: (codes: string[]) => void }

import { useDeliveryHistory } from './DeliveryHistoryPagination';
import { navigate } from './navigation';


const RECENT_TESTS = 3;

export function SettingsView({ authorize, notify, onRevoked }: Props) {
  const names = ['security', 'outgoing'];
  const [tab, setTab] = useState(() => { const value = new URLSearchParams(location.search).get('tab'); return value && names.includes(value) ? value : 'security'; });
  useEffect(() => { const legacy = new URLSearchParams(location.search).get('tab'); if (legacy === 'deletion' || legacy === 'protected') navigate(`/deleted?tab=${legacy === 'deletion' ? 'configure' : 'protected'}`);
    if (legacy === 'backups') navigate('/backups'); }, []);
  return <Stack spacing={3}>
    <Typography variant="h4" component="h1">Settings</Typography>
    <Tabs value={tab} onChange={(_, value: string) => { setTab(value); const url = new URL(location.href); url.searchParams.set('tab', value); history.replaceState(null, '', url); }} variant="scrollable" scrollButtons="auto" aria-label="Settings tabs">
      <Tab value="security" label="Security" id="settings-security" aria-controls="panel-security"/>
      <Tab value="outgoing" label="Outgoing mail provider" id="settings-outgoing" aria-controls="panel-outgoing"/>
    </Tabs>
    <Box role="tabpanel" id={`panel-${tab}`} aria-labelledby={`settings-${tab}`}>
      {tab === 'security' && <SecuritySettings authorize={authorize} onRevoked={onRevoked} onDeliverySettings={() => { setTab('outgoing'); const url = new URL(location.href); url.searchParams.set('tab', 'outgoing'); history.replaceState(null, '', url); }}/>}
      {tab === 'outgoing' && <OutgoingSettings authorize={authorize} notify={notify}/>}
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
  const [quota, setQuota] = useState('');
  async function load() {
    try { const value = await api<RelaySettings>('/settings/relay'); setRelay(value); setUsername(value.username); }
    catch (failure) { setError((failure as Error).message); }
  }
  useEffect(() => { void load(); }, []);
  const delivery = useDeliveryHistory('mail@rsalehin24.me', JSON.stringify(relay?.tests));
  const recent = delivery.rows.slice(0, RECENT_TESTS);
  return <Stack spacing={3}>
    {error && <Alert severity="error">{error}</Alert>}
    {!relay && error && <Button onClick={() => { setError(''); void load(); }}>Retry relay settings</Button>}
    <Paper component="fieldset" disabled={relay === null} variant="outlined" sx={{ m: 0, minWidth: 0, p: { xs: 2, md: 3 }, borderRadius: 1.5 }}><Stack spacing={2.5}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="h6">Outgoing mail provider</Typography>
        {relay ? <StatusDot tone={relay.otp_ready ? 'success' : 'warning'} label={relay.otp_ready ? 'Administrator email delivery verified' : 'Administrator email delivery needs verification'}/> : <Typography variant="body2" color="text.secondary">Loading saved relay settings…</Typography>}
      </Stack>
      <InfoList items={[
        { label: 'Provider', value: 'Brevo' }, { label: 'SMTP host', value: 'smtp-relay.brevo.com' },
        { label: 'Security', value: '587 · STARTTLS' }, { label: 'Saved revision', value: relay ? relay.revision : '—' },
      ]}/>
      <TextField label="Brevo SMTP login" value={username} onChange={event => { setUsername(event.target.value); setDirty(true); }}/>
      <TextField label="Brevo SMTP key" type="password" autoComplete="new-password" value={credential} onChange={event => { setCredential(event.target.value); setDirty(true); }}/>
      <TextField label="Optional Brevo API key" type="password" autoComplete="new-password" value={apiKey} onChange={event => { setApiKey(event.target.value); setDirty(true); }} />
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' } }}>
        <Button variant="outlined" disabled={dirty || !relay?.configured} onClick={async () => {
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
        <Button variant="outlined" disabled={dirty || !relay?.configured} onClick={() => authorize(async () => {
          const result = await api<{ connection_ok: boolean }>('/settings/relay/test', { mode: 'connection' });
          notify(result.connection_ok ? 'Verified TLS and SMTP authentication passed; delivery remains unconfirmed.' : 'Relay connection or authentication failed.'); await load();
        })}>Test connection</Button>
        <Button variant="outlined" disabled={dirty || !relay?.configured} onClick={() => authorize(async () => {
          const result = await api<{ smtp_accepted: boolean }>('/settings/relay/test', { mode: 'delivery' });
          notify(result.smtp_accepted ? 'Test accepted by SMTP. Confirm the matching message below after receipt.' : 'SMTP delivery test failed.'); await load();
        })}>Send explicit delivery test</Button>
      </Stack>
    </Stack></Paper>
    {delivery.error && <Alert severity="error">{delivery.error}</Alert>}
    <Section title="Delivery tests">
      <AdminTable widths={['22%', '22%', '56%']} columns={['Date', 'Time', 'Result']} empty="No tests yet." rows={recent.map(test => {
        const outcome = test.confirmed ? { tone: 'success' as const, label: 'Receipt confirmed', hint: '' } : test.accepted ? { tone: 'warning' as const, label: 'Awaiting receipt', hint: '' }
          : test.connection_ok ? { tone: 'neutral' as const, label: 'Connection only', hint: 'No email was sent.' } : { tone: 'error' as const, label: 'Failed', hint: '' };
        return { key: test.id, cells: [formatDate(test.created), formatTime(test.created),
          <Stack key="result" direction="row" spacing={1.5} sx={{ alignItems: 'center' }}>
            <Tooltip title={[outcome.hint, `Test ${test.id}`].filter(Boolean).join(' ')} placement="bottom"><Box tabIndex={0} sx={{ cursor: 'help' }}><StatusBadge tone={outcome.tone} label={outcome.label}/></Box></Tooltip>
            {!!test.accepted && !test.confirmed && test.revision === relay?.revision && <Button size="small" variant="outlined" onClick={() => authorize(async () => {
              await api(`/relay-tests/${test.id}/confirm`, {}); await load(); notify('Matching delivery receipt recorded');
            })}>I received this test</Button>}
          </Stack>] };
      })}/>
    </Section>
  </Stack>;
}
