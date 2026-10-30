import { useEffect, useState } from 'react';
import { Alert, Box, Button, Divider, Paper, Stack, TextField, Typography } from '@mui/material';
import ShieldOutlined from '@mui/icons-material/ShieldOutlined';
import { adminEmail, installationKind, api, type Enrollment } from './api';
import { CodeInput, EnrollmentMaterial } from './components';

export function AuthView({ onLogin, onCodes }: { onLogin: () => void; onCodes: (codes: string[]) => void }) {
  const [setup, setSetup] = useState(window.location.pathname === '/setup');
  const [administrator, setAdministrator] = useState(installationKind !== 'webmail');
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState(installationKind === 'webmail' ? '' : adminEmail);
  const [mailboxFactor, setMailboxFactor] = useState(false);
  const [challenge, setChallenge] = useState('');
  const [material, setMaterial] = useState<Enrollment | null>(null);
  const [factor, setFactor] = useState<'totp' | 'email' | 'recovery'>('totp');
  const [code, setCode] = useState('');
  const [otpAvailable, setOtpAvailable] = useState(false);
  const [resendAt, setResendAt] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => setSeconds(Math.max(0, Math.ceil((resendAt - Date.now()) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [resendAt]);
  function clear() { setPassword(''); setCode(''); setChallenge(''); setMaterial(null); setToken(''); setError(''); setFactor('totp'); }
  async function submit() {
    setBusy(true); setError('');
    try {
      if (setup && !material) setMaterial(await api<Enrollment>('/setup', { token }));
      else if (setup) {
        const result = await api<{ recovery_codes: string[] }>('/setup', { token, password, code });
        clear(); setSetup(false); onCodes(result.recovery_codes);
      } else if (!challenge) {
        const result = await api<{ authenticated?: boolean; role?: string; challenge: string; email_otp_available: boolean }>(administrator ? '/login' : '/mailbox/login', { email, password });
        if (result.authenticated) { clear(); onLogin(); }
        else { setChallenge(result.challenge); setMailboxFactor(result.role === 'mailbox'); setOtpAvailable(result.email_otp_available); setPassword(''); }
      } else { await api('/2fa/verify', { challenge, factor, code }); clear(); onLogin(); }
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  return <Box className="auth-stage"><Paper className="auth-card" elevation={0}>
    <Box className="auth-icon"><ShieldOutlined/></Box>
    <Typography variant="overline" color="primary">PRIVATE MAIL</Typography>
    <Typography variant="h4" component="h1">{setup ? 'Set up your dashboard' : challenge ? 'One more step' : 'Welcome back'}</Typography>
    <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>{setup ? 'Use the private token provided by your operator.' : 'Manage your mail with a little peace of mind.'}</Typography>
    <Stack component="form" spacing={2.5} onSubmit={event => { event.preventDefault(); void submit(); }}>
      {!setup && installationKind === 'webmail' && !challenge && <Button onClick={() => { setAdministrator(!administrator); setEmail(administrator ? '' : adminEmail); }}>{administrator ? 'Switch to mailbox sign in' : 'Administrator sign in'}</Button>}
      {error && <Alert severity="error">{error}</Alert>}
      {setup && !material && <TextField label="Private bootstrap token" type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)}/>}
      {material && <EnrollmentMaterial material={material}/>}
      {(!setup && !challenge) && <TextField label="Email address" type="email" autoComplete="username" value={email} onChange={event => setEmail(event.target.value)} slotProps={{ htmlInput: { 'aria-label': 'Email address' } }} required/>}
      {((!setup && !challenge) || material) && <TextField label={setup ? 'New master password' : 'Password'} type="password"
        autoComplete={setup ? 'new-password' : 'current-password'} value={password} onChange={event => setPassword(event.target.value)}
        helperText={setup ? 'At least 16 characters; spaces and Unicode are welcome.' : undefined}/>}
      {(challenge || material) && <>
        <Typography variant="body2">{factor === 'recovery' ? 'Enter one unused recovery code.' : 'Enter your six-digit verification code.'}</Typography>
        {factor === 'recovery' && !setup ? <TextField label="Recovery code" autoComplete="off" value={code} onChange={event => setCode(event.target.value)}/>
          : <CodeInput value={code} onChange={setCode}/>}
      </>}
      <Button type="submit" variant="contained" size="large" disabled={busy}>{busy ? 'Verifying…' : setup ? material ? 'Finish setup' : 'Start protected setup' : challenge ? 'Verify and sign in' : 'Continue securely'}</Button>
      {challenge && <>
        <Divider/>
        <Button onClick={() => { setFactor(factor === 'recovery' ? 'totp' : 'recovery'); setCode(''); }}>{factor === 'recovery' ? 'Use authenticator' : 'Use a recovery code'}</Button>
        {!mailboxFactor && <><Button disabled={!otpAvailable || seconds > 0 || busy} onClick={async () => {
          setBusy(true);
          try { await api('/2fa/send-otp', { challenge, purpose: 'login' }); setFactor('email'); setCode(''); setResendAt(Date.now() + 60000); setSeconds(60); }
          catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
        }}>{seconds ? `Send again in ${seconds}s` : `Email a code to ${adminEmail}`}</Button>
        {!otpAvailable && <Typography variant="caption" color="text.secondary">Email codes become available after the current relay has confirmed delivery. Use your authenticator or a recovery code.</Typography>}</>}
      </>}
    </Stack>
    <Divider sx={{ my: 3 }}/><Button fullWidth onClick={() => { if (challenge) { clear(); return; } clear(); setSetup(!setup); }}>{setup ? 'Return to sign in' : challenge ? 'Start over' : 'First time? Protected setup'}</Button>
    <Typography variant="caption" color="text.secondary" className="auth-footer">Mailbox users sign in with their mailbox address. For a forgotten password, contact your administrator.</Typography>
  </Paper></Box>;
}
