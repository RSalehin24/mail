import { useState } from 'react';
import { IconButton, ListItemIcon, Menu, MenuItem, Tooltip, useColorScheme } from '@mui/material';
import BrightnessAutoOutlined from '@mui/icons-material/BrightnessAutoOutlined';
import CheckRounded from '@mui/icons-material/CheckRounded';
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined';
import LightModeOutlined from '@mui/icons-material/LightModeOutlined';

type Mode = 'system' | 'light' | 'dark';
const OPTIONS: { mode: Mode; label: string; icon: React.ReactElement }[] = [
  { mode: 'system', label: 'System', icon: <BrightnessAutoOutlined fontSize="small"/> },
  { mode: 'light', label: 'Light', icon: <LightModeOutlined fontSize="small"/> },
  { mode: 'dark', label: 'Dark', icon: <DarkModeOutlined fontSize="small"/> },
];

// A dropdown so the user picks the theme directly; MUI remembers the choice in localStorage.
export function ThemeToggle() {
  const { mode, setMode } = useColorScheme();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  if (!mode) return null;
  const current = OPTIONS.find(option => option.mode === mode) ?? OPTIONS[0];
  return <>
    <Tooltip title={`Theme: ${current.label}`}>
      <IconButton aria-label="Theme" aria-haspopup="menu" aria-expanded={!!anchor} onClick={event => setAnchor(event.currentTarget)}>{current.icon}</IconButton>
    </Tooltip>
    <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)} slotProps={{ paper: { sx: { minWidth: 160 } } }}>
      {OPTIONS.map(option => <MenuItem key={option.mode} component="button" selected={option.mode === mode} sx={{ display: 'flex', width: '100%', gap: 1 }}
        onClick={() => { setMode(option.mode); setAnchor(null); }}>
        <ListItemIcon>{option.icon}</ListItemIcon>{option.label}{option.mode === mode && <CheckRounded fontSize="small" sx={{ ml: 'auto' }}/>}
      </MenuItem>)}
    </Menu>
  </>;
}
