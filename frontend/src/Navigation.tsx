import { Button, Stack } from '@mui/material';
import type { ReactElement } from 'react';
import GridViewOutlined from '@mui/icons-material/GridViewOutlined';
import MailOutline from '@mui/icons-material/MailOutlineOutlined';
import ForwardToInboxOutlined from '@mui/icons-material/ForwardToInboxOutlined';
import DeleteOutline from '@mui/icons-material/DeleteOutlined';
import DnsOutlined from '@mui/icons-material/DnsOutlined';
import BackupOutlined from '@mui/icons-material/BackupOutlined';
import MoveToInboxOutlined from '@mui/icons-material/MoveToInboxOutlined';
import OutboxOutlined from '@mui/icons-material/OutboxOutlined';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import { navigate } from './navigation';

export type View = 'overview' | 'mailboxes' | 'forwarding' | 'deleted' | 'incoming' | 'backups' | 'outgoing' | 'server' | 'settings';
interface Item { view: View; label: string; path: string; icon: ReactElement }

// One list drives the desktop rail and the mobile drawer.
export const NAVIGATION_ITEMS: Item[] = [
  { view: 'overview', label: 'Overview', path: '/', icon: <GridViewOutlined/> },
  { view: 'mailboxes', label: 'Mailboxes', path: '/mailboxes', icon: <MailOutline/> },
  { view: 'forwarding', label: 'Forwarding', path: '/forwarding', icon: <ForwardToInboxOutlined/> },
  { view: 'incoming', label: 'Incoming', path: '/incoming', icon: <MoveToInboxOutlined/> },
  { view: 'outgoing', label: 'Outgoing', path: '/outgoing', icon: <OutboxOutlined/> },
  { view: 'deleted', label: 'Deleted', path: '/deleted', icon: <DeleteOutline/> },
  { view: 'backups', label: 'Backups', path: '/backups', icon: <BackupOutlined/> },
  { view: 'server', label: 'Server', path: '/server', icon: <DnsOutlined/> },
  { view: 'settings', label: 'Settings', path: '/settings', icon: <SettingsOutlined/> },
];

export function NavigationList({ current, onNavigate }: { current: View; onNavigate?: () => void }) {
  return <Stack component="nav" aria-label="Main" className="nav-list">
    {NAVIGATION_ITEMS.map(item => <Button key={item.view} className="nav-item" aria-current={item.view === current ? 'page' : undefined}
      onClick={() => { navigate(item.path); onNavigate?.(); }}>{item.icon}<span>{item.label}</span></Button>)}
  </Stack>;
}
