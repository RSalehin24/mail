import { useEffect, useState } from 'react';
import { Alert, Box, Button, Divider, Paper, Stack, TextField, Typography } from '@mui/material';
import { api, type Authorize, type Enrollment, type Notify, type RelaySettings } from './api';
import { CodeInput, EnrollmentMaterial } from './components';

interface Props { authorize: Authorize; notify: Notify; onRevoked: (codes: string[]) => void }

export function SettingsView({ authorize, notify, onRevoked }: Props) {
  const [relay, setRelay] = useState<RelaySettings | null>(null);
  const [username, setUsername] = useState('');
  const [port, setPort] = useState(587);
  const [credential, setCredential] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [master, setMaster] = useState('');
  const [material, setMaterial] = useState<Enrollment | null>(null);
  const [code, setCode] = useState('');
  const [quota, setQuota] = useState('Not observed');
  async function load() {
    try { const value = await api<RelaySettings>('/settings/relay'); setRelay(value); setUsername(value.username); setPort(value.port); }
    catch (failure) { setError((failure as Error).message); }
  }
  useEffect(() => { void load(); }, []);
  return <Stack spacing={3}>
    <Box><Typography variant="h4" component="h1">Settings</Typography><Typography color="text.secondary">Relay delivery and administrator security.</Typography></Box>
    {error && <Alert severity="error">{error}</Alert>}
    {!relay && error && <Button onClick={() => { setError(''); void load(); }}>Retry relay settings</Button>}
    <Paper component="fieldset" disabled={relay === null} variant="outlined" sx={{ m: 0, minWidth: 0, p: { xs: 2, md: 3 }, borderRadius: 3 }}><Stack spacing={2.5}>
      <Box><Typography variant="h6">Brevo relay</Typography><Typography variant="body2" color="text.secondary">{relay ? `Saved revision ${relay.revision} · ${relay.otp_ready ? 'Fixed-sender delivery confirmed' : 'Delivery unverified'}` : 'Loading saved relay settings…'}</Typography></Box>
      <TextField label="SMTP host" value="smtp-relay.brevo.com" disabled/>
      <Stack direction="row" spacing={1}><Button variant={port === 587 ? 'contained' : 'outlined'} onClick={() => { setPort(587); setDirty(true); }}>587 · STARTTLS</Button>
        <Button variant={port === 465 ? 'contained' : 'outlined'} onClick={() => { setPort(465); setDirty(true); }}>465 · TLS</Button></Stack>
      <TextField label="Brevo SMTP login" value={username} onChange={event => { setUsername(event.target.value); setDirty(true); }}/>
      <TextField label="Brevo SMTP key" type="password" autoComplete="new-password" value={credential} onChange={event => { setCredential(event.target.value); setDirty(true); }} helperText={relay?.configured ? 'Configured and masked. Leave blank to preserve it.' : 'Required for first save.'}/>
      <TextField label="Optional Brevo API key" type="password" autoComplete="new-password" value={apiKey} onChange={event => { setApiKey(event.target.value); setDirty(true); }} helperText="Encrypted separately; never returned by the reveal action."/>
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
      <Alert severity="info">All tests and email codes go only to rsalehin24@gmail.com. The fixed OTP sender is mail@rsalehin24.me; authorize it in Brevo first. Saving edits requires a new test and receipt confirmation.</Alert>
      {!!relay?.tests.length && <Typography variant="subtitle2">Tests for this saved revision</Typography>}
      {relay?.tests.map(test => <Box key={test.id} sx={{ p: 2, bgcolor: 'action.hover', borderRadius: 2 }}>
        <Typography variant="body2">From {test.sender} · {new Date(test.created * 1000).toLocaleString()}</Typography>
        <Typography variant="caption" sx={{ overflowWrap: 'anywhere' }}>Test {test.id}</Typography>
        <Typography variant="body2">{test.confirmed ? 'Receipt confirmed' : test.accepted ? 'SMTP accepted · receipt unconfirmed' : test.connection_ok ? 'Connection only · no email sent' : 'Failed'}</Typography>
        {!!test.accepted && !test.confirmed && <Button onClick={() => authorize(async () => {
          await api(`/relay-tests/${test.id}/confirm`, {}); await load(); notify('Matching delivery receipt recorded');
        })}>I received this test</Button>}
      </Box>)}
    </Stack></Paper>
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, borderRadius: 3 }}><Stack spacing={2.5}>
      <Typography variant="h6">Security</Typography>
      <TextField label="New master password" type="password" autoComplete="new-password" value={master} onChange={event => setMaster(event.target.value)} helperText="At least 16 characters; at most 1,024 UTF-8 bytes."/>
      <Button sx={{ alignSelf: 'start' }} variant="outlined" disabled={[...master].length < 16} onClick={() => authorize(async () => {
        await api('/security/password', { password: master }); setMaster(''); onRevoked([]);
      })}>Change master password</Button>
      <Divider/>
      <Typography variant="subtitle1">Replace authenticator</Typography>
      <Typography variant="body2" color="text.secondary">Verify an existing factor first. The old authenticator stays active until the new code is confirmed. Successful replacement signs out all sessions and replaces recovery codes.</Typography>
      {!material ? <Button sx={{ alignSelf: 'start' }} onClick={() => authorize(async () => setMaterial(await api<Enrollment>('/security/totp', {})))}>Begin replacement</Button> : <>
        <EnrollmentMaterial material={material}/><CodeInput value={code} onChange={setCode}/>
        <Typography variant="caption">Use a new time step after your last accepted authenticator code. Enrollment expires in ten minutes.</Typography>
        <Stack direction="row" spacing={1}><Button onClick={() => { setMaterial(null); setCode(''); }}>Cancel replacement</Button>
          <Button variant="contained" onClick={() => authorize(async () => {
            const result = await api<{ recovery_codes: string[] }>('/security/totp', { code }); setMaterial(null); setCode(''); onRevoked(result.recovery_codes);
          })}>Confirm new authenticator</Button></Stack>
      </>}
      <Divider/>
      <Button sx={{ alignSelf: 'start' }} onClick={() => authorize(async () => {
        const result = await api<{ recovery_codes: string[] }>('/security/recovery-codes', {}); onRevoked(result.recovery_codes);
      })}>Regenerate recovery codes</Button>
      <Typography variant="caption" color="text.secondary">Previous codes become invalid. Replacement codes are shown once; a fresh login is required.</Typography>
    </Stack></Paper>
  </Stack>;
}
