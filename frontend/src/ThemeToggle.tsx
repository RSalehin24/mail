import { IconButton, Tooltip, useColorScheme } from '@mui/material';
import BrightnessAutoOutlined from '@mui/icons-material/BrightnessAutoOutlined';
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined';
import LightModeOutlined from '@mui/icons-material/LightModeOutlined';

type Mode = 'system' | 'dark' | 'light';
const NEXT_MODE: Record<Mode, Mode> = { system: 'dark', dark: 'light', light: 'system' };
const ICONS = { system: <BrightnessAutoOutlined/>, dark: <DarkModeOutlined/>, light: <LightModeOutlined/> };

// Cycles system -> dark -> light; MUI remembers the choice in localStorage.
export function ThemeToggle() {
  const { mode, setMode } = useColorScheme();
  if (!mode) return null;
  return <Tooltip title={`Theme: ${mode} (click to change)`}>
    <IconButton aria-label="Toggle dark mode" onClick={() => setMode(NEXT_MODE[mode])}>{ICONS[mode]}</IconButton>
  </Tooltip>;
}
