import { createTheme } from '@mui/material';

// Quiet, Linear-style system: near-white surfaces, hairline borders, one green accent,
// no shadows. Components read colours from CSS variables so a mode change never re-renders.
const FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const hairline = 'var(--mui-palette-divider)';

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'data' },
  defaultColorScheme: 'light',
  colorSchemes: {
    light: { palette: {
      primary: { main: '#2f7a66', contrastText: '#ffffff' }, secondary: { main: '#3b6f8f' },
      background: { default: '#fafaf9', paper: '#ffffff' }, divider: '#ebebe8',
      text: { primary: '#1c1d1f', secondary: '#6b6f76' },
      success: { main: '#1f8a5f' }, warning: { main: '#a86400' }, error: { main: '#c4352c' }, info: { main: '#3b6f8f' },
      action: { hover: '#f3f3f1', selected: '#eef3f1' },
    } },
    dark: { palette: {
      primary: { main: '#7fcdb2', contrastText: '#0b1a15' }, secondary: { main: '#8cc4e0' },
      background: { default: '#0f1112', paper: '#16181a' }, divider: '#26292c',
      text: { primary: '#ececec', secondary: '#9a9fa6' },
      success: { main: '#62cf9f' }, warning: { main: '#f0b45a' }, error: { main: '#ff8a80' }, info: { main: '#8cc4e0' },
      action: { hover: '#1d2022', selected: '#1b2723' },
    } },
  },
  breakpoints: { values: { xs: 0, sm: 600, md: 960, lg: 1280, xl: 1536 } },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: FONT, fontSize: 14,
    h4: { fontWeight: 600, fontSize: 'clamp(1.4rem, 2.4vw, 1.75rem)', letterSpacing: '-0.02em' },
    h5: { fontWeight: 600, letterSpacing: '-0.015em' },
    h6: { fontWeight: 600, fontSize: '1rem', letterSpacing: '-0.01em' },
    overline: { letterSpacing: '0.08em', fontWeight: 600, fontSize: '0.68rem' },
    button: { textTransform: 'none', fontWeight: 550 },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { minHeight: 36, borderRadius: 8, '@media(max-width:599px)': { minHeight: 48 } },
        outlined: { borderColor: hairline, color: 'inherit', '&:hover': { borderColor: hairline, backgroundColor: 'var(--mui-palette-action-hover)' } } },
    },
    MuiIconButton: { styleOverrides: { root: { '@media(max-width:599px)': { minWidth: 48, minHeight: 48 } } } },
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: 'none' }, outlined: { borderColor: hairline } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 14, border: `1px solid ${hairline}` } } },
    MuiDialogTitle: { styleOverrides: { root: { fontWeight: 600, fontSize: '1.1rem' } } },
    MuiTextField: { defaultProps: { size: 'small' } },
    MuiOutlinedInput: { styleOverrides: { root: { borderRadius: 8 }, notchedOutline: { borderColor: hairline } } },
    MuiChip: { styleOverrides: { root: { borderRadius: 6, fontWeight: 550 } } },
    MuiTab: { styleOverrides: { root: { minHeight: 44, fontWeight: 550 } } },
    MuiTableCell: { styleOverrides: { root: { borderColor: hairline }, head: { fontWeight: 600, color: 'var(--mui-palette-text-secondary)' } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 10 } } },
    MuiTooltip: { styleOverrides: { tooltip: { fontSize: 12 } } },
  },
});
