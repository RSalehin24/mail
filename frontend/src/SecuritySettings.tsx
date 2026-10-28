import { useEffect, useState } from 'react';
import { Alert, Avatar, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Stack, Switch, FormControlLabel, TextField, Typography } from '@mui/material';
import LockOutlined from '@mui/icons-material/LockOutlined';
import ShieldOutlined from '@mui/icons-material/ShieldOutlined';
import KeyOutlined from '@mui/icons-material/KeyOutlined';
import { ApiError, api, type Authorize, type Enrollment } from './api';
import { CodeInput, EnrollmentMaterial } from './components';

export function SecuritySettings({ authorize, onRevoked, onDeliverySettings }: { onDeliverySettings: () => void; authorize: Authorize; onRevoked: (codes: string[]) => void }) {
  const [dialog, setDialog] = useState<'password' | 'authenticator' | 'recovery' | null>(null);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [material, setMaterial] = useState<Enrollment | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [emailAuth, setEmailAuth] = useState<{ enabled: boolean; delivery_verified: boolean; available: boolean; sender: string; recipient: string } | null>(null);
  const [emailError, setEmailError] = useState('');
  useEffect(() => { void api<NonNullable<typeof emailAuth>>('/security/email-authentication').then(setEmailAuth).catch(failure => setEmailError((failure as Error).message)); }, []);
  const [error, setError] = useState('');
  const validPassword = [...password].length >= 16 && new TextEncoder().encode(password).length <= 1024 && password === confirmation;
  function close() { if (busy) return; setDialog(null); setPassword(''); setConfirmation(''); setMaterial(null); setCode(''); setError(''); }
  function perform(operation: () => Promise<void>) {
    authorize(async () => {
      setBusy(true); setError('');
      try { await operation(); }
      catch (failure) {
        if (failure instanceof ApiError && ['recent_authentication_required', 'authentication_required'].includes(failure.code)) throw failure;
        setError((failure as Error).message);
      } finally { setBusy(false); }
    });
  }
  const cards = [
    { id: 'password' as const, title: 'Master password', icon: <LockOutlined/>, description: 'Manage the password you use to sign in to Mail Admin.', action: 'Change password', note: 'Signs out all sessions after the change.' },
    { id: 'authenticator' as const, title: 'Authenticator app', icon: <ShieldOutlined/>, description: 'Move two-step verification to a new phone or authenticator app.', action: 'Replace authenticator', note: 'Your current authenticator works until you confirm the new one.' },
    { id: 'recovery' as const, title: 'Recovery codes', icon: <KeyOutlined/>, description: 'Create backup codes for signing in when your authenticator is unavailable.', action: 'Replace recovery codes', note: 'Replaces all five codes and signs out all sessions.' },
  ];
  return <Box component="section" aria-labelledby="security-heading">
    <Typography variant="h5" component="h2" id="security-heading" sx={{ mb: .75, fontWeight: 700 }}>Security</Typography>
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' }, gap: 2 }}>
      {cards.map(card => <Paper variant="outlined" component="section" key={card.id} sx={{ p: 3, borderRadius: '16px', display: 'flex', flexDirection: 'column', alignItems: 'start' }}>
        <Avatar variant="rounded" sx={{ bgcolor: 'action.hover', color: 'primary.main', mb: 2 }}>{card.icon}</Avatar>
        <Typography component="h3" variant="h6" sx={{ fontWeight: 650 }}>{card.title}</Typography>
        <Typography color="text.secondary" variant="body2" sx={{ mt: 1, mb: 2, lineHeight: 1.7 }}>{card.description}</Typography>
        <Typography color="text.secondary" variant="caption" sx={{ mb: 2.5 }}>{card.note}</Typography>
        <Button variant="outlined" sx={{ mt: 'auto' }} onClick={() => { setError(''); setDialog(card.id); }}>{card.action}</Button>
      </Paper>)}
    </Box>
    <Paper variant="outlined" sx={{ mt: 3, p: 3, borderRadius: 3 }}><Stack spacing={2}>
      <Typography variant="h6" component="h3">Email verification codes</Typography>
      <Typography variant="body2" color="text.secondary">Use an email code as a second factor after your master password. Your authenticator and recovery codes remain available.</Typography>
      {emailError && <Alert severity="error">{emailError}</Alert>}
      {!emailAuth && !emailError && <Typography role="status">Loading email authentication settings…</Typography>}
      {emailAuth && <>
        <Typography variant="body2">Codes are sent from {emailAuth.sender} to {emailAuth.recipient}.</Typography>
        <Alert severity={emailAuth.delivery_verified ? 'success' : 'info'}>{emailAuth.delivery_verified ? 'Administrator email delivery is verified for the current provider settings.' : 'Send an administrator delivery test and confirm its receipt before enabling email codes.'}</Alert>
        <FormControlLabel label="Allow email verification codes" control={<Switch checked={emailAuth.enabled} disabled={busy || (!emailAuth.enabled && !emailAuth.delivery_verified)} onChange={(_, checked) => authorize(async () => {
          setBusy(true); setEmailError('');
          try { setEmailAuth(await api<NonNullable<typeof emailAuth>>('/security/email-authentication', { enabled: checked }, 'PUT')); }
          catch (failure) { if (failure instanceof ApiError && ['recent_authentication_required', 'authentication_required'].includes(failure.code)) throw failure; setEmailError((failure as Error).message); }
          finally { setBusy(false); }
        })}/>}/>
        {emailAuth.enabled && !emailAuth.available && <Typography color="warning.main" variant="body2">Email codes are unavailable until delivery is verified again.</Typography>}
      </>}
      <Button variant="outlined" sx={{ alignSelf: 'start' }} onClick={onDeliverySettings}>Verify administrator email delivery</Button>
    </Stack></Paper>
    <Dialog open={dialog !== null} onClose={busy ? undefined : close} fullWidth maxWidth="sm" aria-labelledby="security-dialog-title">
      <DialogTitle id="security-dialog-title">{dialog === 'password' ? 'Change master password' : dialog === 'authenticator' ? 'Replace authenticator app' : 'Replace recovery codes'}</DialogTitle>
      <DialogContent><Stack spacing={2.5} sx={{ pt: 1 }}>
        {error && <Alert severity="error">{error}</Alert>}
        {dialog === 'password' && <Box component="form" id="master-password-form" onSubmit={event => { event.preventDefault(); if (validPassword && !busy) perform(async () => { await api('/security/password', { password }); setPassword(''); setConfirmation(''); setDialog(null); onRevoked([]); }); }}>
          <Stack spacing={2.5}><Typography color="text.secondary">Choose a new password for your administrator account. Your current sessions will be signed out.</Typography>
            <TextField autoFocus label="New password" type="password" autoComplete="new-password" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} helperText="At least 16 characters; at most 1,024 UTF-8 bytes."/>
            <TextField label="Confirm new password" type="password" autoComplete="new-password" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} error={!!confirmation && password !== confirmation} helperText={confirmation && password !== confirmation ? 'Passwords do not match.' : 'Enter the same password again.'}/>
          </Stack></Box>}
        {dialog === 'authenticator' && (!material ? <>
          <Typography>First, verify your identity. Then scan a new setup code with your authenticator app.</Typography>
          <Alert severity="info">Your current authenticator stays active until you confirm the new code. Confirmation replaces your recovery codes and signs out all sessions.</Alert>
        </> : <>
          <Typography variant="subtitle1" sx={{ fontWeight: 650 }}>Set up your new authenticator</Typography>
          <Typography color="text.secondary">Scan this code, then enter the six-digit code from the new app.</Typography>
          <EnrollmentMaterial material={material}/><CodeInput value={code} onChange={setCode}/>
          <Typography variant="caption" color="text.secondary">Setup expires in ten minutes. Wait for a new code if you just used an authenticator code to verify your identity.</Typography>
        </>)}
        {dialog === 'recovery' && <>
          <Typography>Create five new recovery codes to keep somewhere private.</Typography>
          <Alert severity="warning">All existing recovery codes will stop working. The new codes are shown once, and you will be signed out.</Alert>
        </>}
      </Stack></DialogContent>
      <DialogActions sx={{ p: 3, pt: 1 }}><Button disabled={busy} onClick={close}>Cancel</Button>
        {dialog === 'password' && <Button variant="contained" type="submit" form="master-password-form" disabled={busy || !validPassword}>Save new password</Button>}
        {dialog === 'authenticator' && <Button variant="contained" disabled={busy || (!!material && code.replace(/\D/g, '').length !== 6)} onClick={() => perform(async () => {
          if (!material) setMaterial(await api<Enrollment>('/security/totp', {}));
          else { const result = await api<{ recovery_codes: string[] }>('/security/totp', { code }); setMaterial(null); setCode(''); setDialog(null); onRevoked(result.recovery_codes); }
        })}>{material ? 'Confirm new authenticator' : 'Verify and continue'}</Button>}
        {dialog === 'recovery' && <Button variant="contained" disabled={busy} onClick={() => perform(async () => { const result = await api<{ recovery_codes: string[] }>('/security/recovery-codes', {}); setDialog(null); onRevoked(result.recovery_codes); })}>Generate new codes</Button>}
      </DialogActions>
    </Dialog>
  </Box>;
}
