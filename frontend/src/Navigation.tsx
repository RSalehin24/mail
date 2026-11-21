import { Button, Stack } from '@mui/material';
import type { ReactElement } from 'react';
import MailOutline from '@mui/icons-material/MailOutlineOutlined';
import ForwardToInboxOutlined from '@mui/icons-material/ForwardToInboxOutlined';
import DeleteOutline from '@mui/icons-material/DeleteOutlined';
import DnsOutlined from '@mui/icons-material/DnsOutlined';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import { navigate } from './navigation';

export type View = 'mailboxes' | 'forwarding' | 'deleted' | 'server' | 'settings';
interface Item { view: View; label: string; path: string; icon: ReactElement }

// One list drives the desktop rail and the mobile drawer.
export const NAVIGATION_ITEMS: Item[] = [
  { view: 'mailboxes', label: 'Mailboxes', path: '/', icon: <MailOutline/> },
  { view: 'forwarding', label: 'Forwarding', path: '/forwarding', icon: <ForwardToInboxOutlined/> },
  { view: 'deleted', label: 'Deleted', path: '/deleted', icon: <DeleteOutline/> },
  { view: 'server', label: 'Server', path: '/server', icon: <DnsOutlined/> },
  { view: 'settings', label: 'Settings', path: '/settings', icon: <SettingsOutlined/> },
];

export function NavigationList({ current, onNavigate }: { current: View; onNavigate?: () => void }) {
  return <Stack component="nav" aria-label="Main" spacing={0.5}>
    {NAVIGATION_ITEMS.map(item => <Button key={item.view} startIcon={item.icon} color={item.view === current ? 'primary' : 'inherit'}
      aria-current={item.view === current ? 'page' : undefined}
      sx={{ justifyContent: 'flex-start', px: 1.5, bgcolor: item.view === current ? 'action.selected' : 'transparent' }}
      onClick={() => { navigate(item.path); onNavigate?.(); }}>{item.label}</Button>)}
  </Stack>;
}
