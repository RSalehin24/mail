import { useState } from 'react';
import { Alert, Box, Button, Divider, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material';
import { api, ApiError, type Enrollment } from './api';
import { CodeInput, EnrollmentMaterial } from './components';
import { type MailProfile as Profile } from './webmailTypes';

export function MailProfile({ value, onChanged, onRevoked, onBack, notify }: { value: Profile; onChanged: (profile: Profile) => void; onRevoked: (codes: string[]) => void; onBack: () => void; notify: (message: string) => void }) {
  const [profile, setProfile] = useState(value);
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [factor, setFactor] = useState('totp');
  const [code, setCode] = useState('');
  const [newCode, setNewCode] = useState('');
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await action(); } catch (failure) { if (failure instanceof ApiError && failure.revoked) { notify(failure.message); onRevoked([]); } else setError((failure as Error).message); } finally { setBusy(false); } }
  const proof = () => ({ password, factor, code });
  async function changeFactor(action: string) {
    const result = await api<Enrollment & { recovery_codes: string[]; revoked: boolean }>('/profile/authenticator', { ...proof(), action, new_code: newCode });
    setCode('');
    if (action === 'begin') { setEnrollment(result); setNotice('Scan the code, then confirm with a code from the new authenticator. If two-step login is already enabled, also enter a fresh code from the current authenticator.'); }
    else { setPassword(''); setNewCode(''); onRevoked(result.recovery_codes || []); }
  }
  return <Stack spacing={3} sx={{ maxWidth: 800, mx: 'auto' }}>
    <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}><Typography variant="h4" component="h1">Your profile</Typography><Button onClick={onBack}>Back to mail</Button></Stack>
    {error && <Alert severity="error">{error}</Alert>}{notice && <Alert severity="success">{notice}</Alert>}
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}><Stack component="form" spacing={2} onSubmit={event => { event.preventDefault(); void run(async () => {
      const result = await api<Profile>('/profile', { display_name: profile.display_name, reply_to: profile.reply_to, signature: profile.signature, theme: profile.theme }, 'PUT'); setProfile(result); onChanged(result); setNotice('Profile saved.');
    }); }}>
      <TextField label="Mailbox address" value={profile.email} slotProps={{ input: { readOnly: true } }} helperText="Contact your administrator if you need a different address."/>
      <TextField label="Display name" value={profile.display_name} onChange={event => setProfile({ ...profile, display_name: event.target.value })} slotProps={{ htmlInput: { maxLength: 256 } }}/>
      <TextField label="Reply-to address" value={profile.reply_to} onChange={event => setProfile({ ...profile, reply_to: event.target.value })} helperText="Optional. Where recipients should send their replies."/>
      <TextField label="Plain-text signature" multiline minRows={3} value={profile.signature} onChange={event => setProfile({ ...profile, signature: event.target.value })} slotProps={{ htmlInput: { maxLength: 8192 } }}/>
      <TextField select label="Theme" value={profile.theme} onChange={event => setProfile({ ...profile, theme: event.target.value as Profile['theme'] })}><MenuItem value="system">Use system setting</MenuItem><MenuItem value="light">Light</MenuItem><MenuItem value="dark">Dark</MenuItem></TextField>
      <Button type="submit" variant="contained" disabled={busy}>Save profile</Button>
    </Stack></Paper>
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}><Stack spacing={2}>
      <Typography variant="h6" component="h2">Mail app connection instructions</Typography>
      <Typography>Use your full mailbox address and mailbox password in your mail app.</Typography>
      <Typography>Incoming: {profile.connections.host}, IMAP port {profile.connections.imap_port}, TLS.</Typography>
      <Typography>Outgoing: {profile.connections.host}, SMTP port {profile.connections.smtp_port}, STARTTLS, authentication required.</Typography>
      <Typography variant="body2" color="text.secondary">Enable saving sent messages in the server’s Sent folder. Optional webmail two-step login applies to this website; IMAP and SMTP apps continue to use your mailbox password.</Typography>
    </Stack></Paper>
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}><Stack spacing={2}>
      <Typography variant="h6" component="h2">Password and sign-in security</Typography>
      <Typography>Authenticator: {profile.two_step_enabled ? 'Enabled' : 'Not enabled'}. {profile.two_step_enabled && `${profile.recovery_codes_remaining} recovery codes remaining.`}</Typography>
      <Typography variant="body2" color="text.secondary">Enter your current password and, when enabled, an authenticator code or recovery code for each security change. Completed security changes sign out your web sessions. For a forgotten password, contact your administrator.</Typography>
      <TextField label="Current mailbox password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}/>
      {profile.two_step_enabled && <><TextField select label="Verification method" value={factor} onChange={event => { setFactor(event.target.value); setCode(''); }}><MenuItem value="totp">Authenticator</MenuItem><MenuItem value="recovery">Recovery code</MenuItem></TextField>
        {factor === 'totp' ? <CodeInput value={code} onChange={setCode}/> : <TextField label="Unused recovery code" value={code} onChange={event => setCode(event.target.value)} autoComplete="off"/>}</>}
      <Divider/>
      <TextField label="New mailbox password" type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" helperText="At least 16 characters and at most 72 UTF-8 bytes."/>
      <TextField label="Confirm new password" type="password" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password"/>
      <Button variant="outlined" disabled={busy || !password || !newPassword || newPassword !== confirmation} onClick={() => { void run(async () => { const result = await api<{ state: string }>('/profile/password', { ...proof(), new_password: newPassword }); setPassword(''); setNewPassword(''); setConfirmation(''); if (result.state !== 'complete') notify('The password change needs administrator reconciliation. Sign in again or contact your administrator.'); onRevoked([]); }); }}>Change password and sign out</Button>
      <Divider/>
      {enrollment ? <Box><EnrollmentMaterial material={enrollment}/><Typography sx={{ my: 2 }}>Code from the new authenticator</Typography><CodeInput value={newCode} onChange={setNewCode}/><Button disabled={busy || !password || newCode.length !== 6} onClick={() => { void run(() => changeFactor('confirm')); }}>Confirm authenticator and sign out</Button></Box>
        : <Button variant="outlined" disabled={busy || !password} onClick={() => { void run(() => changeFactor('begin')); }}>{profile.two_step_enabled ? 'Replace authenticator' : 'Enable authenticator'}</Button>}
      {profile.two_step_enabled && <Stack direction={{ xs: 'column', sm: 'row' }} sx={{ gap: 1 }}><Button disabled={busy || !password} onClick={() => { void run(() => changeFactor('recovery')); }}>Replace recovery codes</Button><Button color="error" disabled={busy || !password} onClick={() => { if (window.confirm('Disable authenticator-based webmail login and invalidate your recovery codes?')) void run(() => changeFactor('disable')); }}>Disable authenticator</Button></Stack>}
    </Stack></Paper>
  </Stack>;
}
