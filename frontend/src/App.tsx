import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppBar, Box, Button, Container, CssBaseline, Drawer, IconButton, Snackbar, Stack,
  ThemeProvider, Toolbar, Typography, createTheme } from '@mui/material';
import MailOutline from '@mui/icons-material/MailOutlineOutlined';
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined';
import LogoutOutlined from '@mui/icons-material/LogoutOutlined';
import MenuOutlined from '@mui/icons-material/MenuOutlined';
import { ApiError, api, clearSessionData, loadInstallation, domain, type Installation } from './api';
import { AuthView } from './AuthView';
import { Dashboard } from './Dashboard';
import { SettingsView } from './SettingsView';
import { Reauthenticate, RecoveryCodes } from './components';
import './styles.css';
import { navigate, usePath } from './navigation';
import { MailboxPage } from './MailboxPage';
import { DeletedMailboxes } from './DeletedMailboxes';
import { ServerPage } from './ServerPage';
import { Forwarding } from './Forwarding';
import { InstallationSetup } from './InstallationSetup';
import { InstallationAdmin } from './InstallationAdmin';
import { WebmailApp } from './WebmailApp';

export function App() {
  const [installation, setInstallation] = useState<Installation | null>(null);
  const [installationError, setInstallationError] = useState('');
  const [dark, setDark] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<'administrator' | 'mailbox'>('administrator');
  const [ready, setReady] = useState(false);
  const [sessionError, setSessionError] = useState('');
  const path = usePath();
  const legacy = installation?.kind === 'legacy';
  const view = path === '/forwarding' ? 'forwarding' : path === '/settings' ? 'settings' : path === '/server' ? 'server' : path === '/deleted' ? 'deleted' : 'mailboxes';
  const match = /^\/mailboxes\/([^/]+)\/(details|configuration|connections)$/.exec(path);
  const [message, setMessage] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [reauth, setReauth] = useState(false);
  const [navigation, setNavigation] = useState(false);
  const pending = useRef<(() => Promise<void>) | null>(null);
  const theme = createTheme({
    breakpoints: { values: { xs: 0, sm: 600, md: 960, lg: 1280, xl: 1536 } },
    palette: { mode: dark ? 'dark' : 'light', primary: { main: dark ? '#8fcbb7' : '#2b6659' },
      background: { default: dark ? '#141b20' : '#f7f9f8', paper: dark ? '#1b252c' : '#ffffff' } },
    typography: { fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', h4: { fontWeight: 650, fontSize: 'clamp(1.65rem, 3vw, 2.1rem)' }, button: { textTransform: 'none', fontWeight: 600 } },
    shape: { borderRadius: 10 },
    components: { MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 40, '@media(max-width:599px)': { minHeight: 48 } } } }, MuiIconButton: { styleOverrides: { root: { '@media(max-width:599px)': { minWidth: 48, minHeight: 48 } } } } },
  });
  const checkSession = useCallback(async () => {
    setSessionError(''); setReady(false);
    try { const result = await api<{ role: 'administrator' | 'mailbox' }>('/session'); setRole(result.role); setAuthenticated(true); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 401 && failure.code === 'authentication_required') setAuthenticated(false);
      else setSessionError((failure as Error).message);
    } finally { setReady(true); }
  }, []);
  useEffect(() => { void loadInstallation().then(value => { setInstallation(value); void checkSession(); }).catch(failure => { setInstallationError((failure as Error).message); setReady(true); }); }, [checkSession]);
  useEffect(() => {
    const expired = () => { setAuthenticated(false); setCodes([]); pending.current = null; setReauth(false); };
    window.addEventListener('mail-admin-session-expired', expired);
    return () => { window.removeEventListener('mail-admin-session-expired', expired); };
  }, []);
  useEffect(() => {
    if (authenticated && role === 'administrator' && installation?.kind !== 'webmail') for (const endpoint of ['/telemetry', '/accounts', '/diagnostics']) void api(endpoint).catch(() => {});
  }, [authenticated, role, installation]);
  function revoke(values: string[]) { clearSessionData(); setAuthenticated(false); setCodes(values); pending.current = null; setReauth(false); }
  const execute = useCallback(async (operation: () => Promise<void>) => {
    try { await operation(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.code === 'recent_authentication_required') {
        pending.current = operation; setReauth(true); return;
      }
      if (failure instanceof ApiError && failure.status === 401 && failure.code === 'authentication_required') setAuthenticated(false);
      setMessage((failure as Error).message);
    }
  }, []);
  function authorize(operation: () => Promise<void>) {
    void execute(operation);
  }
  return <ThemeProvider theme={theme}><CssBaseline/>
    <AppBar position="sticky" color="inherit" elevation={0} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
      <Container maxWidth="lg"><Toolbar disableGutters sx={{ minHeight: 78, flexWrap: 'wrap', py: 1 }}>
        <Box component="a" href="/" onClick={event => { if (authenticated) { event.preventDefault(); navigate('/'); setNavigation(false); } }} aria-label="Mail home" sx={{ display: 'flex', flexGrow: 1, alignItems: 'center', gap: 1.5, color: 'inherit', textDecoration: 'none', borderRadius: 1, '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 4 } }}><Box className="brand-mark"><MailOutline/></Box><Box><Typography sx={{ fontWeight: 700 }}>{installation?.kind === 'webmail' ? 'Webmail' : 'Mail Management'}</Typography><Typography variant="caption" color="text.secondary">{domain}</Typography></Box></Box>
        {authenticated && role === 'administrator' && installation?.kind !== 'webmail' && <Stack direction="row" sx={{ display: { xs: 'none', md: 'flex' } }}><Button color={view === 'mailboxes' ? 'primary' : 'inherit'} onClick={() => navigate('/')}>Mailboxes</Button>{legacy && <><Button color={view === 'deleted' ? 'primary' : 'inherit'} onClick={() => navigate('/deleted')}>Deleted</Button><Button color={view === 'forwarding' ? 'primary' : 'inherit'} onClick={() => navigate('/forwarding')}>Forwarding</Button></>}<Button color={view === 'settings' ? 'primary' : 'inherit'} onClick={() => navigate('/settings')}>Settings</Button>{!legacy && <Button onClick={() => navigate('/installation')}>Installation</Button>}<Button color={view === 'server' ? 'primary' : 'inherit'} onClick={() => navigate('/server')}>Server</Button></Stack>}
        {authenticated && role === 'mailbox' && <Stack direction="row"><Button onClick={() => navigate('/')}>Mail</Button><Button onClick={() => navigate('/profile')}>Profile</Button></Stack>}
        {authenticated && role === 'administrator' && <IconButton aria-label="Open navigation" sx={{ display: { xs: 'inline-flex', md: 'none' } }} onClick={() => setNavigation(true)}><MenuOutlined/></IconButton>}
        <IconButton aria-label="Toggle dark mode" onClick={() => setDark(!dark)}><DarkModeOutlined/></IconButton>
        {authenticated && <IconButton aria-label="Sign out" onClick={() => { void execute(async () => { await api('/logout', {}); revoke([]); }); }}><LogoutOutlined/></IconButton>}
      </Toolbar></Container>
    </AppBar>
    <Drawer open={navigation && authenticated && role === 'administrator'} onClose={() => setNavigation(false)}>
      <Stack spacing={1} sx={{ width: 260, p: 3 }}><Typography variant="h6">Mail Management</Typography>
        {installation?.kind !== 'webmail' && <Button onClick={() => { navigate('/'); setNavigation(false); }}>Mailboxes</Button>}
        {legacy && <><Button onClick={() => { navigate('/deleted'); setNavigation(false); }}>Deleted mailboxes</Button>
        <Button onClick={() => { navigate('/forwarding'); setNavigation(false); }}>Forwarding</Button></>}
        <Button onClick={() => { navigate('/settings'); setNavigation(false); }}>Settings</Button>{installation?.kind !== 'webmail' && <Button onClick={() => { navigate('/server'); setNavigation(false); }}>Server</Button>}
      </Stack>
    </Drawer>
    {installationError ? <Box className="page"><Alert severity="error">Installation unavailable: {installationError}</Alert></Box> : installation && !installation.configured ? <InstallationSetup installation={installation}/> : !ready ? <Box className="page"><Typography>Checking your session…</Typography></Box> : sessionError ? <Box className="page"><Alert severity="error" action={<Button onClick={() => { void checkSession(); }}>Retry</Button>}>Session check unavailable: {sessionError}</Alert></Box> : !authenticated ? <AuthView onLogin={() => { void checkSession(); }} onCodes={setCodes}/>
      : <Box className="page">{role === 'mailbox' ? <WebmailApp showProfile={path === '/profile'} onProfile={value => navigate(value ? '/profile' : '/')} onTheme={setDark} onRevoked={revoke} notify={setMessage}/> : installation && (installation.kind === 'webmail' || path === '/installation' || installation.kind === 'management' && view === 'settings') ? <InstallationAdmin installation={installation} authorize={authorize} onRevoked={revoke}/> : match ? <MailboxPage key={match[1]} encodedEmail={match[1]} tab={match[2]} authorize={authorize} notify={setMessage}/> : legacy && view === 'deleted' ? <DeletedMailboxes authorize={authorize} notify={setMessage}/> : legacy && view === 'forwarding' ? <Forwarding authorize={authorize} notify={setMessage}/> : view === 'server' ? <ServerPage/> : view === 'settings' ? <SettingsView authorize={authorize} notify={setMessage} onRevoked={revoke}/> : path === '/' ? <Dashboard authorize={authorize} notify={setMessage}/> : <Alert severity="warning">Page not found. <Button onClick={() => navigate('/')}>Go to mailboxes</Button></Alert>}</Box>}
    {reauth && <Reauthenticate onCancel={() => { pending.current = null; setReauth(false); }} onVerified={() => {
      setReauth(false); const operation = pending.current; pending.current = null; if (operation) void execute(operation);
    }}/>}
    {codes.length > 0 && <RecoveryCodes codes={codes} onSaved={() => setCodes([])}/>}
    <Snackbar open={!!message} autoHideDuration={9000} onClose={() => setMessage('')} anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}><Alert severity="info" onClose={() => setMessage('')}>{message}</Alert></Snackbar>
  </ThemeProvider>;
}
