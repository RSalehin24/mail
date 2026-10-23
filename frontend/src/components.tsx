import { useRef, useState } from 'react';
import { Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, Stack, TextField, Typography } from '@mui/material';
import { api, type Enrollment } from './api';

export function CodeInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  function paste(text: string) {
    const digits = text.replace(/\D/g, '').slice(0, 6);
    onChange(digits); inputs.current[Math.min(digits.length, 5)]?.focus();
  }
  return <Box className="code-input" aria-label="Six digit verification code">
    {Array.from({ length: 6 }, (_, index) => <input key={index} ref={element => { inputs.current[index] = element; }}
      aria-label={`Digit ${index + 1} of 6`} inputMode="numeric" autoComplete={index === 0 ? 'one-time-code' : 'off'}
      autoFocus={index === 0} value={(value[index] ?? '').trim()} maxLength={6}
      onPaste={event => { event.preventDefault(); paste(event.clipboardData.getData('text')); }}
      onChange={event => {
        const digits = event.target.value.replace(/\D/g, '');
        if (digits.length > 1) { paste(digits); return; }
        const next = value.padEnd(6, ' ').split(''); next[index] = digits || ' '; onChange(next.join(''));
        if (digits) inputs.current[index + 1]?.focus();
      }} onKeyDown={event => { if (event.key === 'Backspace' && !value[index]?.trim()) inputs.current[index - 1]?.focus(); }}/>) }
  </Box>;
}

export function EnrollmentMaterial({ material }: { material: Enrollment }) {
  return <Stack spacing={2} sx={{ alignItems: 'center' }}>
    <img className="qr" src={material.qr} alt="Scan this QR code with your authenticator"/>
    <Typography variant="body2">Or enter this secret in your authenticator:</Typography>
    <Typography component="code" className="secret-text">{material.secret}</Typography>
  </Stack>;
}

export function RecoveryCodes({ codes, onSaved }: { codes: string[]; onSaved: () => void }) {
  const [saved, setSaved] = useState(false);
  return <Dialog open fullWidth maxWidth="sm" aria-labelledby="recovery-title">
    <DialogTitle id="recovery-title">Save your recovery codes</DialogTitle>
    <DialogContent><Stack spacing={2}>
      <Alert severity="warning">These five codes are shown once. Keep them somewhere private. Each code works once.</Alert>
      {codes.map(code => <Typography component="code" className="secret-text" key={code}>{code}</Typography>)}
      <FormControlLabel control={<Checkbox checked={saved} onChange={event => setSaved(event.target.checked)}/>}
        label="I saved all five recovery codes"/>
    </Stack></DialogContent>
    <DialogActions><Button disabled={!saved} onClick={onSaved} variant="contained">Continue to login</Button></DialogActions>
  </Dialog>;
}

export function Reauthenticate({ onVerified, onCancel }: { onVerified: () => void; onCancel: () => void }) {
  const [password, setPassword] = useState('');
  const [challenge, setChallenge] = useState('');
  const [factor, setFactor] = useState<'totp' | 'recovery' | 'email'>('totp');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function verify() {
    setBusy(true); setError('');
    try {
      if (!challenge) {
        const result = await api<{ challenge: string }>('/security/reauth', { email: 'rsalehin24@gmail.com', password });
        setChallenge(result.challenge); setPassword('');
      } else {
        await api('/security/reauth/verify', { challenge, factor, code });
        setCode(''); onVerified();
      }
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  return <Dialog open fullWidth maxWidth="xs" onClose={busy ? undefined : onCancel} aria-labelledby="reauth-title">
    <DialogTitle id="reauth-title">Verify it’s you</DialogTitle>
    <DialogContent><Stack spacing={2} sx={{ pt: 1 }}>
      <Typography color="text.secondary">Sensitive actions need your password and an existing second factor. Verification lasts five minutes.</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {!challenge ? <TextField label="Master password" type="password" value={password} autoFocus autoComplete="current-password"
        onChange={event => setPassword(event.target.value)}/> : <>
        <Stack direction="row" spacing={1}><Button onClick={() => { setFactor('totp'); setCode(''); }}>Authenticator</Button>
          <Button onClick={() => { setFactor('recovery'); setCode(''); }}>Recovery code</Button></Stack>
        {factor === 'recovery' ? <TextField label="Recovery code" value={code} autoComplete="off" onChange={event => setCode(event.target.value)}/>
          : <CodeInput value={code} onChange={setCode}/>}
        <Button onClick={async () => {
          try { await api('/2fa/send-otp', { challenge, purpose: 'reauth' }); setFactor('email'); setCode(''); }
          catch (failure) { setError((failure as Error).message); }
        }}>Send email code if configured</Button>
      </>}
    </Stack></DialogContent>
    <DialogActions><Button onClick={onCancel} disabled={busy}>Cancel</Button><Button variant="contained" onClick={verify}
      disabled={busy || (!challenge ? !password : !code)}>{challenge ? 'Verify second factor' : 'Verify password'}</Button></DialogActions>
  </Dialog>;
}
