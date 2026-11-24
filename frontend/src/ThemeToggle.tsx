import { ToggleButton, ToggleButtonGroup, useColorScheme } from '@mui/material';

type Mode = 'system' | 'light' | 'dark';
const OPTIONS: { mode: Mode; label: string }[] = [
  { mode: 'system', label: 'System' },
  { mode: 'light', label: 'Light' },
  { mode: 'dark', label: 'Dark' },
];

// A segmented control so the current theme is always visible; MUI remembers the choice in localStorage.
export function ThemeToggle() {
  const { mode, setMode } = useColorScheme();
  if (!mode) return null;
  return <ToggleButtonGroup exclusive size="small" value={mode} aria-label="Theme" className="theme-toggle"
    onChange={(_, next: Mode | null) => { if (next) setMode(next); }}>
    {OPTIONS.map(option => <ToggleButton key={option.mode} value={option.mode}>{option.label}</ToggleButton>)}
  </ToggleButtonGroup>;
}
