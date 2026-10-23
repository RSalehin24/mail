import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppBar, Box, Button, Container, CssBaseline, Drawer, IconButton, Snackbar, Stack,
  ThemeProvider, Toolbar, Typography, createTheme } from '@mui/material';
import MailOutline from '@mui/icons-material/MailOutlineOutlined';
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined';
import LogoutOutlined from '@mui/icons-material/LogoutOutlined';
import MenuOutlined from '@mui/icons-material/MenuOutlined';
import { ApiError, api } from './api';
import { AuthView } from './AuthView';
import { Dashboard } from './Dashboard';
import { SettingsView } from './SettingsView';
import { Reauthenticate, RecoveryCodes } from './components';
import './styles.css';

export function App() {
  const [dark, setDark] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<'mailboxes' | 'settings'>('mailboxes');
  const [message, setMessage] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [reauth, setReauth] = useState(false);
  const [navigation, setNavigation] = useState(false);
  const grantUntil = useRef(0);
  const pending = useRef<(() => Promise<void>) | null>(null);
  const theme = createTheme({
    breakpoints: { values: { xs: 0, sm: 600, md: 960, lg: 1280, xl: 1536 } },
    palette: { mode: dark ? 'dark' : 'light', primary: { main: dark ? '#8fcbb7' : '#2b6659' },
      background: { default: dark ? '#141b20' : '#f7f9f8', paper: dark ? '#1b252c' : '#ffffff' } },
    typography: { fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', h4: { fontWeight: 650, fontSize: 'clamp(1.65rem, 3vw, 2.1rem)' }, button: { textTransform: 'none', fontWeight: 600 } },
    shape: { borderRadius: 10 },
    components: { MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 40, '@media(max-width:599px)': { minHeight: 48 } } } }, MuiIconButton: { styleOverrides: { root: { '@media(max-width:599px)': { minWidth: 48, minHeight: 48 } } } } },
  });
  useEffect(() => { void api('/diagnostics').then(() => setAuthenticated(true)).catch(() => setAuthenticated(false)).finally(() => setReady(true)); }, []);
  useEffect(() => {
    const expired = () => { setAuthenticated(false); setCodes([]); grantUntil.current = 0; pending.current = null; setReauth(false); };
    const recentExpired = () => { grantUntil.current = 0; };
    window.addEventListener('mail-admin-session-expired', expired);
    window.addEventListener('mail-admin-reauth-expired', recentExpired);
    return () => { window.removeEventListener('mail-admin-session-expired', expired); window.removeEventListener('mail-admin-reauth-expired', recentExpired); };
  }, []);
  function revoke(values: string[]) { setAuthenticated(false); setCodes(values); grantUntil.current = 0; pending.current = null; setReauth(false); }
  const execute = useCallback(async (operation: () => Promise<void>) => {
    try { await operation(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.code === 'recent_authentication_required') {
        grantUntil.current = 0; pending.current = operation; setReauth(true); return;
      }
      if (failure instanceof ApiError && failure.status === 401) setAuthenticated(false);
      setMessage((failure as Error).message);
    }
  }, []);
  function authorize(operation: () => Promise<void>) {
    if (Date.now() < grantUntil.current) { void execute(operation); return; }
    pending.current = operation; setReauth(true);
  }
  return <ThemeProvider theme={theme}><CssBaseline/>
    <AppBar position="sticky" color="inherit" elevation={0} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
      <Container maxWidth="lg"><Toolbar disableGutters sx={{ minHeight: 78, flexWrap: 'wrap', py: 1 }}>
        <Stack direction="row" spacing={1.5} sx={{ flexGrow: 1, alignItems: 'center' }}><Box className="brand-mark"><MailOutline/></Box><Box><Typography sx={{ fontWeight: 700 }}>Mail Admin</Typography><Typography variant="caption" color="text.secondary">rsalehin24.me</Typography></Box></Stack>
        {authenticated && <Stack direction="row" sx={{ display: { xs: 'none', sm: 'flex' } }}><Button color={view === 'mailboxes' ? 'primary' : 'inherit'} onClick={() => setView('mailboxes')}>Mailboxes</Button><Button color={view === 'settings' ? 'primary' : 'inherit'} onClick={() => setView('settings')}>Settings</Button></Stack>}
        {authenticated && <IconButton aria-label="Open navigation" sx={{ display: { xs: 'inline-flex', sm: 'none' } }} onClick={() => setNavigation(true)}><MenuOutlined/></IconButton>}
        <IconButton aria-label="Toggle dark mode" onClick={() => setDark(!dark)}><DarkModeOutlined/></IconButton>
        {authenticated && <IconButton aria-label="Sign out" onClick={() => { void execute(async () => { await api('/logout', {}); revoke([]); }); }}><LogoutOutlined/></IconButton>}
      </Toolbar></Container>
    </AppBar>
    <Drawer open={navigation && authenticated} onClose={() => setNavigation(false)}>
      <Stack spacing={1} sx={{ width: 260, p: 3 }}><Typography variant="h6">Mail Admin</Typography>
        <Button onClick={() => { setView('mailboxes'); setNavigation(false); }}>Mailboxes</Button>
        <Button onClick={() => { setView('settings'); setNavigation(false); }}>Settings</Button>
      </Stack>
    </Drawer>
    {!ready ? <Box className="page"><Typography>Checking your session…</Typography></Box> : !authenticated ? <AuthView onLogin={() => { setAuthenticated(true); setView('mailboxes'); }} onCodes={setCodes}/>
      : <Box className="page">{view === 'mailboxes' ? <Dashboard authorize={authorize} notify={setMessage}/> : <SettingsView authorize={authorize} notify={setMessage} onRevoked={revoke}/>}</Box>}
    {reauth && <Reauthenticate onCancel={() => { pending.current = null; setReauth(false); }} onVerified={() => {
      grantUntil.current = Date.now() + 300000; setReauth(false); const operation = pending.current; pending.current = null; if (operation) void execute(operation);
    }}/>}
    {codes.length > 0 && <RecoveryCodes codes={codes} onSaved={() => setCodes([])}/>}
    <Snackbar open={!!message} autoHideDuration={9000} onClose={() => setMessage('')} anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}><Alert severity="info" onClose={() => setMessage('')}>{message}</Alert></Snackbar>
  </ThemeProvider>;
}
