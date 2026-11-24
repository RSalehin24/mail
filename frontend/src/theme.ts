import { createTheme } from '@mui/material';

// Quiet green system (owner: primary colours are shades of green) on the Figma mockup's layout: tinted canvas, white panels, hairline borders,
// one green accent, no shadows. Components read colours from CSS variables so a mode change never re-renders.
const FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const hairline = 'var(--mui-palette-divider)';
// Controls need a stronger edge than layout dividers, in both colour schemes.
const controlLine = 'var(--mui-palette-text-disabled)';
const controlLineHover = 'var(--mui-palette-text-secondary)';

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'data' },
  defaultColorScheme: 'light',
  colorSchemes: {
    light: { palette: {
      primary: { main: '#15803d', contrastText: '#ffffff' }, secondary: { main: '#526174' },
      background: { default: '#f1f4f8', paper: '#ffffff' }, divider: '#dde3ec',
      text: { primary: '#17212f', secondary: '#526174', disabled: '#8290a3' },
      success: { main: '#067647' }, warning: { main: '#925a00' }, error: { main: '#b42318' }, info: { main: '#175cd3' },
      action: { hover: '#eef1f5', selected: '#f0fdf4' },
    } },
    dark: { palette: {
      primary: { main: '#86efac', contrastText: '#052e16' }, secondary: { main: '#a6b9ae' },
      background: { default: '#0c1612', paper: '#13211b' }, divider: '#26382f',
      text: { primary: '#e8f0ec', secondary: '#a6b9ae', disabled: '#6f857a' },
      success: { main: '#75e0a7' }, warning: { main: '#fec84b' }, error: { main: '#fda29b' }, info: { main: '#84adff' },
      action: { hover: '#1c2e25', selected: '#16342a' },
    } },
  },
  breakpoints: { values: { xs: 0, sm: 600, md: 960, lg: 1280, xl: 1536 } },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: FONT, fontSize: 14,
    h4: { fontWeight: 500, fontSize: 'clamp(1.5rem, 3vw, 2rem)', lineHeight: 1.25, letterSpacing: '-0.01em' },
    h5: { fontWeight: 500, fontSize: '1.5rem', lineHeight: 1.33, fontVariantNumeric: 'tabular-nums' },
    h6: { fontWeight: 500, fontSize: '1.25rem', lineHeight: 1.4 },
    overline: { letterSpacing: '0.08em', fontWeight: 600, fontSize: '0.68rem' },
    button: { textTransform: 'none', fontWeight: 500 },
  },
  components: {
    MuiLink: { defaultProps: { underline: 'none' }, styleOverrides: { root: { fontWeight: 500 } } },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { minHeight: 36, borderRadius: 8, textTransform: 'capitalize', '@media(max-width:599px)': { minHeight: 48 } },
        outlined: { borderColor: controlLine, color: 'var(--mui-palette-text-primary)', '&:hover': { borderColor: controlLineHover, backgroundColor: 'var(--mui-palette-action-hover)' } } },
    },
    MuiIconButton: { styleOverrides: { root: { '@media(max-width:599px)': { minWidth: 48, minHeight: 48 } } } },
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: 'none' }, outlined: { borderColor: hairline, borderRadius: 12 } } },
    // Dialogs open from the bottom as sheets on phones.
    MuiDialog: { styleOverrides: {
      container: { '@media(max-width:599px)': { alignItems: 'flex-end' } },
      paper: { borderRadius: 12, border: `1px solid ${hairline}`, '@media(max-width:599px)': { margin: 0, width: '100%', maxWidth: '100%', maxHeight: '92%', borderRadius: '16px 16px 0 0', borderBottom: 0 } },
    } },
    MuiDialogTitle: { styleOverrides: { root: { fontWeight: 600, fontSize: '1.1rem' } } },
    MuiTextField: { defaultProps: { size: 'small' } },
    MuiOutlinedInput: { styleOverrides: { root: { borderRadius: 8, '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: controlLineHover } }, notchedOutline: { borderColor: controlLine } } },
    MuiInputLabel: { styleOverrides: { root: { color: 'var(--mui-palette-text-secondary)' } } },
    MuiSwitch: { styleOverrides: { track: { backgroundColor: controlLine, opacity: 1 } } },
    // Chip labels are centred by cap height (text-box trim); the padding keeps tall and hanging letters (d, f, y, g) inside the label's clipping box, with 0.5 px extra above.
    MuiChip: { styleOverrides: { root: { borderRadius: 6, fontWeight: 500, alignItems: 'center' }, label: { display: 'block', paddingTop: '4.5px', paddingBottom: '4px', textBox: 'trim-both cap alphabetic' }, outlined: { borderColor: controlLine } } },
    MuiTab: { styleOverrides: { root: { minHeight: 44, fontWeight: 550 } } },
    MuiTableCell: { styleOverrides: { root: { borderColor: hairline }, head: { fontWeight: 600, color: 'var(--mui-palette-text-secondary)' } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 8 } } },
    MuiTooltip: { defaultProps: { arrow: true }, styleOverrides: { tooltip: { fontSize: 12 } } },
  },
});
