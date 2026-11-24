import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppBar, Box, Button, CssBaseline, Drawer, IconButton, Snackbar,
  ThemeProvider, Toolbar, Typography } from '@mui/material';
import MailOutline from '@mui/icons-material/MailOutlineOutlined';
import LogoutOutlined from '@mui/icons-material/LogoutOutlined';
import MenuOutlined from '@mui/icons-material/MenuOutlined';
import { ApiError, api, clearSessionData } from './api';
import { AuthView } from './AuthView';
import { Dashboard } from './Dashboard';
import { Overview } from './Overview';
import { SettingsView } from './SettingsView';
import { ConfirmPassword, Reauthenticate, RecoveryCodes } from './components';
import './styles.css';
import { theme } from './theme';
import { ThemeToggle } from './ThemeToggle';
import { navigate, usePath } from './navigation';
import { MailboxPage } from './MailboxPage';
import { DeletedMailboxes } from './DeletedMailboxes';
import { ServerPage } from './ServerPage';
import { Forwarding } from './Forwarding';
import { OutgoingDelivery } from './OutgoingDelivery';
import { BackupSettings } from './BackupSettings';
import { IncomingDelivery } from './IncomingDelivery';
import { NavigationList } from './Navigation';

export function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [ready, setReady] = useState(false);
  const [sessionError, setSessionError] = useState('');
  const path = usePath();
  const view = path === '/' ? 'overview' : path === '/forwarding' ? 'forwarding' : path === '/settings' ? 'settings' : path === '/server' ? 'server' : path === '/deleted' ? 'deleted' : path === '/outgoing' ? 'outgoing' : path === '/incoming' ? 'incoming' : path === '/backups' ? 'backups' : 'mailboxes';
  const match = /^\/mailboxes\/([^/]+)\/(details|configuration|connections)$/.exec(path);
  const [toast, setToast] = useState<{ text: string; severity: 'success' | 'error' } | null>(null);
  const notify = useCallback((text: string) => setToast(text ? { text, severity: 'success' } : null), []);
  const [codes, setCodes] = useState<string[]>([]);
  const [reauth, setReauth] = useState(false);
  const [confirmation, setConfirmation] = useState(false);
  const [navigation, setNavigation] = useState(false);
  const pending = useRef<(() => Promise<void>) | null>(null);
  const checkSession = useCallback(async () => {
    setSessionError(''); setReady(false);
    try { await api('/session'); setAuthenticated(true); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 401 && failure.code === 'authentication_required') setAuthenticated(false);
      else setSessionError((failure as Error).message);
    } finally { setReady(true); }
  }, []);
  useEffect(() => { void checkSession(); }, [checkSession]);
  useEffect(() => {
    const expired = () => { setAuthenticated(false); setCodes([]); pending.current = null; setReauth(false); setConfirmation(false); };
    window.addEventListener('mail-admin-session-expired', expired);
    return () => { window.removeEventListener('mail-admin-session-expired', expired); };
  }, []);
  useEffect(() => {
    if (authenticated) for (const endpoint of ['/telemetry', '/accounts', '/diagnostics']) void api(endpoint).catch(() => {});
  }, [authenticated]);
  function revoke(values: string[]) { clearSessionData(); setAuthenticated(false); setCodes(values); pending.current = null; setReauth(false); }
  const execute = useCallback(async (operation: () => Promise<void>) => {
    try { await operation(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.code === 'recent_authentication_required') {
        pending.current = operation; setReauth(true); return;
      }
      if (failure instanceof ApiError && failure.code === 'password_confirmation_required') {
        pending.current = operation; setConfirmation(true); return;
      }
      if (failure instanceof ApiError && failure.status === 401 && failure.code === 'authentication_required') setAuthenticated(false);
      setToast({ text: (failure as Error).message, severity: 'error' });
    }
  }, []);
  function authorize(operation: () => Promise<void>) {
    void execute(operation);
  }
  const content = !ready ? <Typography color="text.secondary">Checking your session…</Typography>
    : sessionError ? <Alert severity="error" action={<Button onClick={() => { void checkSession(); }}>Retry</Button>}>Session check unavailable: {sessionError}</Alert>
    : match ? <MailboxPage key={match[1]} encodedEmail={match[1]} tab={match[2]} authorize={authorize} notify={notify}/>
    : view === 'deleted' ? <DeletedMailboxes authorize={authorize} notify={notify}/>
    : view === 'forwarding' ? <Forwarding authorize={authorize} notify={notify}/>
    : view === 'incoming' ? <IncomingDelivery authorize={authorize} notify={notify}/>
    : view === 'backups' ? <BackupSettings notify={notify}/>
    : view === 'outgoing' ? <OutgoingDelivery authorize={authorize} notify={notify}/>
    : view === 'server' ? <ServerPage/>
    : view === 'settings' ? <SettingsView authorize={authorize} notify={notify} onRevoked={revoke}/>
    : view === 'overview' ? <Overview/>
    : path === '/mailboxes' ? <Dashboard authorize={authorize} notify={notify}/>
    : <Alert severity="warning">Page not found. <Button onClick={() => navigate('/')}>Go to overview</Button></Alert>;
  const home = <Box component="a" href="/" onClick={event => { if (authenticated) { event.preventDefault(); navigate('/'); setNavigation(false); } }} aria-label="RSalehin24.ME home" className="brand-link">
    <Typography component="span" sx={{ fontWeight: 500, fontSize: '1rem' }}>RSalehin24.ME</Typography></Box>;
  const topBar = <AppBar position="sticky" color="inherit" elevation={0} className={authenticated ? 'top-bar signed-in' : 'top-bar'}>
    <Toolbar sx={{ minHeight: 64, gap: { xs: 0.5, sm: 1.5 }, px: { xs: 2, md: 3 } }}>
      {authenticated && <IconButton aria-label="Open navigation" sx={{ display: { xs: 'inline-flex', md: 'none' } }} onClick={() => setNavigation(true)}><MenuOutlined/></IconButton>}
      {home}<Box sx={{ flexGrow: 1 }}/>
      <ThemeToggle/>
      {authenticated && <IconButton aria-label="Sign out" onClick={() => { void execute(async () => { await api('/logout', {}); revoke([]); }); }}><LogoutOutlined/></IconButton>}
    </Toolbar>
  </AppBar>;
  const signedIn = authenticated || !ready || !!sessionError;
  return <ThemeProvider theme={theme}><CssBaseline/>
    <Drawer open={navigation && authenticated} onClose={() => setNavigation(false)} slotProps={{ paper: { className: 'nav-drawer' } }}>
      <NavigationList current={view} onNavigate={() => setNavigation(false)}/>
    </Drawer>
    <Box className="shell">
      {authenticated && <Box component="aside" className="rail"><Box className="rail-mark" aria-hidden><MailOutline/></Box><NavigationList current={view}/></Box>}
      <Box className="main">
        {topBar}
        {!authenticated && ready && !sessionError ? <AuthView onLogin={() => { setAuthenticated(true); navigate('/'); }} onCodes={setCodes}/>
          : signedIn && <Box component="main" className="page">{content}</Box>}
      </Box>
    </Box>
    {confirmation && <ConfirmPassword onCancel={() => { pending.current = null; setConfirmation(false); }} onVerified={() => {
      setConfirmation(false); const operation = pending.current; pending.current = null; if (operation) void execute(operation);
    }}/>}
    {reauth && <Reauthenticate onCancel={() => { pending.current = null; setReauth(false); }} onVerified={() => {
      setReauth(false); const operation = pending.current; pending.current = null; if (operation) void execute(operation);
    }}/>}
    {codes.length > 0 && <RecoveryCodes codes={codes} onSaved={() => setCodes([])}/>}
    <Snackbar open={!!toast} autoHideDuration={toast?.severity === 'error' ? 15000 : 9000} onClose={() => setToast(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}><Alert severity={toast?.severity ?? 'success'} variant="filled" onClose={() => setToast(null)}>{toast?.text}</Alert></Snackbar>
  </ThemeProvider>;
}
