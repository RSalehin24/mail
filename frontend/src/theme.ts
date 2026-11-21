import { createTheme } from '@mui/material';

// One theme for both colour schemes; components read colours from CSS variables so a mode
// change never re-renders the tree and `styles.css` can use the same tokens.
export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'data' },
  defaultColorScheme: 'light',
  colorSchemes: {
    light: { palette: {
      primary: { main: '#2b6659' }, secondary: { main: '#267894' },
      background: { default: '#f7f9f8', paper: '#ffffff' }, divider: '#dfe6e3',
      success: { main: '#1b7f5c' }, warning: { main: '#9a6200' }, error: { main: '#b3261e' },
    } },
    dark: { palette: {
      primary: { main: '#8fcbb7' }, secondary: { main: '#87c8e0' },
      background: { default: '#101619', paper: '#182126' }, divider: '#2b383e',
      success: { main: '#6fd0a8' }, warning: { main: '#f0b45a' }, error: { main: '#ff8a80' },
    } },
  },
  breakpoints: { values: { xs: 0, sm: 600, md: 960, lg: 1280, xl: 1536 } },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    h4: { fontWeight: 650, fontSize: 'clamp(1.65rem, 3vw, 2.1rem)' },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: { borderRadius: 10 },
  components: {
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 40, '@media(max-width:599px)': { minHeight: 48 } } } },
    MuiIconButton: { styleOverrides: { root: { '@media(max-width:599px)': { minWidth: 48, minHeight: 48 } } } },
    MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
  },
});
