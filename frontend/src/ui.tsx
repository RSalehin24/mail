import { useId, useState, type MouseEvent, type ReactNode } from 'react';
import { Box, Button, Collapse, IconButton, LinearProgress, Paper, Stack, Tooltip, Typography } from '@mui/material';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import ErrorOutlineRounded from '@mui/icons-material/ErrorOutlineRounded';
import ScheduleRounded from '@mui/icons-material/ScheduleRounded';
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded';
import CheckRounded from '@mui/icons-material/CheckRounded';
import ContentCopyRounded from '@mui/icons-material/ContentCopyRounded';
import ExpandLessRounded from '@mui/icons-material/ExpandLessRounded';
import ExpandMoreRounded from '@mui/icons-material/ExpandMoreRounded';
import { navigate } from './navigation';

export type Tone = 'success' | 'warning' | 'error' | 'neutral';
const TONE_COLOR: Record<Tone, string> = {
  success: 'var(--mui-palette-success-main)', warning: 'var(--mui-palette-warning-main)',
  error: 'var(--mui-palette-error-main)', neutral: 'var(--mui-palette-text-secondary)',
};

// A coloured dot plus a text label: status without heavy chips.
export function StatusDot({ tone, label }: { tone: Tone; label: string }) {
  return <Stack direction="row" spacing={0.9} sx={{ alignItems: 'center', display: 'inline-flex' }}>
    <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: TONE_COLOR[tone], flexShrink: 0 }}/>
    <Typography variant="body2" component="span">{label}</Typography>
  </Stack>;
}

const SEVERITY_ICON = { warning: WarningAmberRounded, error: ErrorOutlineRounded } as const;

// A number with its meaning: severity is a coloured edge plus an icon (never colour alone), `meter` draws a usage bar.
export function StatCard({ label, value, detail, tone = 'neutral', to, meter }: { label: string; value: ReactNode; detail?: ReactNode; tone?: Tone; to?: string; meter?: number }) {
  const link = to ? { component: 'a', href: to, onClick: (event: MouseEvent) => { event.preventDefault(); navigate(to); } } : {};
  const Severity = tone === 'warning' || tone === 'error' ? SEVERITY_ICON[tone] : undefined;
  return <Paper variant="outlined" {...link} sx={{ p: 2, minWidth: 0, display: 'block', color: 'inherit', textDecoration: 'none', borderLeft: Severity ? '4px solid' : undefined, borderLeftColor: Severity ? TONE_COLOR[tone] : undefined, ...(to && { '&:hover': { borderColor: 'primary.main' } }) }}>
    <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>{label}</Typography>
      {Severity && <Severity fontSize="small" sx={{ color: TONE_COLOR[tone] }}/>}
    </Stack>
    <Typography variant="h5" component="p" sx={{ mt: 0.5, color: tone === 'neutral' ? 'text.primary' : TONE_COLOR[tone] }}>{value}</Typography>
    {meter !== undefined && <LinearProgress variant="determinate" value={Math.min(100, Math.max(0, meter))} aria-label={`${label} usage`} sx={{ mt: 1, height: 6, borderRadius: 3, bgcolor: 'action.hover', '& .MuiLinearProgress-bar': { borderRadius: 3, bgcolor: tone === 'neutral' || tone === 'success' ? 'primary.main' : TONE_COLOR[tone] } }}/>}
    {detail && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>{detail}</Typography>}
  </Paper>;
}

export function StatGrid({ children, columns = 4 }: { children: ReactNode; columns?: number }) {
  return <Box sx={{ display: 'grid', gap: 2, mb: 3, gridTemplateColumns: { xs: '1fr 1fr', md: `repeat(${columns}, 1fr)` } }}>{children}</Box>;
}

export function PageHeader({ title, subtitle, action, badge, titleHint }: { title: string; subtitle?: string; action?: ReactNode; badge?: ReactNode; titleHint?: string }) {
  return <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'flex-start' }, mb: 3 }}>
    <Box>{titleHint ? <Tooltip title={titleHint} placement="right"><Typography variant="h4" component="h1" sx={{ overflowWrap: 'anywhere', display: 'inline-block', cursor: 'help' }}>{title}</Typography></Tooltip>
      : <Typography variant="h4" component="h1" sx={{ overflowWrap: 'anywhere', position: 'relative', display: 'inline-block', pr: badge ? 2.5 : 0 }}>{title}{badge && <Box sx={{ position: 'absolute', top: 6, right: 0 }}>{badge}</Box>}</Typography>}
      {subtitle && <Typography color="text.secondary" sx={{ mt: 0.5 }}>{subtitle}</Typography>}</Box>
    {action}
  </Stack>;
}

const BADGE_ICON = { success: CheckCircleOutlineRounded, error: ErrorOutlineRounded, warning: WarningAmberRounded, neutral: ScheduleRounded } as const;

// An icon and a one-word state; the detail (an error, an ID) belongs in the tooltip the caller wraps around it.
export function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  const Icon = BADGE_ICON[tone];
  return <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center', display: 'inline-flex' }}><Icon sx={{ fontSize: 18, color: TONE_COLOR[tone] }}/><Typography variant="body2" component="span">{label}</Typography></Stack>;
}

// A bare coloured dot whose meaning is in its hover text and its accessible name, so it is never colour alone.
export function StateDot({ tone, label }: { tone: Tone; label: string }) {
  return <Tooltip title={label} placement="right"><Box role="img" aria-label={label} tabIndex={0} sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: TONE_COLOR[tone], cursor: 'help' }}/></Tooltip>;
}

// Label above value pairs for facts such as a schedule or a timestamp; replaces loose "a · b · c" lines.
export function InfoList({ items, centered = false }: { items: { label: string; value: ReactNode }[]; centered?: boolean }) {
  return <Box component="dl" sx={{ m: 0, display: 'grid', gap: 2, gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', textAlign: centered ? 'center' : 'left' }}>
    {items.map(item => <Box key={item.label} sx={{ minWidth: 0 }}>
      <Typography component="dt" variant="caption" color="text.secondary" sx={centered ? { fontSize: 11 } : undefined}>{item.label}</Typography>
      <Typography component="dd" variant="body2" sx={{ m: 0, mt: centered ? 0.75 : 0.25, overflowWrap: 'anywhere' }}>{item.value}</Typography>
    </Box>)}
  </Box>;
}

// One checked thing per row: name and detail on the left, state and age on the right.
export function StatusRow({ title, hint, tone, status, detail, meta }: { title: string; hint?: string; tone: Tone; status: string; detail?: ReactNode; meta?: string }) {
  const state = <StatusDot tone={tone} label={status}/>;
  return <Box sx={{ display: 'grid', gap: 0.5, columnGap: 2, py: 1.5, gridTemplateColumns: 'minmax(0, 1fr) auto', alignItems: 'center', borderTop: '1px solid', borderColor: 'divider', '&:first-of-type': { borderTop: 0, pt: 0 } }}>
    <Box sx={{ minWidth: 0 }}>
      {hint ? <Tooltip title={hint} placement="right"><Typography variant="body2" tabIndex={0} sx={{ fontWeight: 500, width: 'fit-content', cursor: 'help' }}>{title}</Typography></Tooltip>
        : <Typography variant="body2" sx={{ fontWeight: 500 }}>{title}</Typography>}
      {detail && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{detail}</Typography>}
    </Box>
    {meta ? <Tooltip title={meta} placement="left"><Box tabIndex={0} sx={{ cursor: 'help' }}>{state}</Box></Tooltip> : state}
  </Box>;
}

// A shell command with a copy button; the tooltip confirms the copy so no toast is needed.
export function CommandBlock({ label, command, showLabel = true }: { label: string; command: string; showLabel?: boolean }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  function copy() {
    void navigator.clipboard.writeText(command).then(() => setState('copied')).catch(() => setState('failed'));
    window.setTimeout(() => setState('idle'), 2500);
  }
  return <Box>
    {showLabel && <Typography variant="caption" color="text.secondary">{label}</Typography>}
    <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: showLabel ? 0.5 : 0, pl: 1.5, pr: 0.5, py: 0.5, borderRadius: 1, bgcolor: 'var(--surface-low)', border: '1px solid', borderColor: 'divider' }}>
      <Typography component="code" variant="body2" sx={{ flex: 1, minWidth: 0, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', overflowWrap: 'anywhere' }}>{command}</Typography>
      <Tooltip title={state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy command'}>
        <IconButton size="small" aria-label={`Copy command: ${label}`} onClick={copy}>{state === 'copied' ? <CheckRounded fontSize="small"/> : <ContentCopyRounded fontSize="small"/>}</IconButton>
      </Tooltip>
    </Stack>
  </Box>;
}

// Content the reader can hide once they know it; the heading stays so it can be shown again.
export function Disclosure({ title, summary, children }: { title: string; summary?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <Paper variant="outlined" component="section" sx={{ p: 2 }}>
    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
      <Box sx={{ minWidth: 0 }}>
        <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 500 }}>{title}</Typography>
        {summary && <Typography variant="caption" color="text.secondary">{summary}</Typography>}
      </Box>
      <Button size="small" aria-label={`${open ? 'Hide' : 'Show'} ${title}`} aria-expanded={open} aria-controls={id} endIcon={open ? <ExpandLessRounded/> : <ExpandMoreRounded/>} onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'}</Button>
    </Stack>
    <Collapse in={open} unmountOnExit><Stack id={id} spacing={2} sx={{ pt: 2 }}>{children}</Stack></Collapse>
  </Paper>;
}

// A titled panel with an optional action on the right; the one container every page section uses.
export function Section({ title, action, danger = false, children }: { title: string; action?: ReactNode; danger?: boolean; children?: ReactNode }) {
  return <Paper variant="outlined" component="section" sx={{ p: { xs: 2, sm: 2.5 }, minWidth: 0, borderColor: danger ? 'error.main' : undefined }}>
    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap', mb: children ? 2 : 0 }}>
      <Typography component="h2" variant="h6" sx={{ fontSize: '1.0625rem' }}>{title}</Typography>{action}
    </Stack>
    {children}
  </Paper>;
}

// A fact on the left and its value on the right, optionally copyable; rows are separated by hairlines.
export function CopyRow({ label, value, copyLabel }: { label: string; value: string; copyLabel?: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    void navigator.clipboard.writeText(value).then(() => setCopied(true)).catch(() => setCopied(false));
    window.setTimeout(() => setCopied(false), 2500);
  }
  return <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 1, minHeight: 48, borderTop: '1px solid', borderColor: 'divider', '&:first-of-type': { borderTop: 0 } }}>
    <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0 }}>{label}</Typography>
    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', minWidth: 0 }}>
      <Typography variant="body2" sx={{ overflowWrap: 'anywhere', textAlign: 'right' }}>{value}</Typography>
      {copyLabel && <Tooltip title={copied ? 'Copied' : 'Copy'}><IconButton size="small" aria-label={copyLabel} onClick={copy}>{copied ? <CheckRounded fontSize="small"/> : <ContentCopyRounded fontSize="small"/>}</IconButton></Tooltip>}
    </Stack>
  </Stack>;
}

// Short labelled facts in a list: an icon, a bold lead-in and one or two lines. Replaces paragraphs of instructions.
export function NoteList({ notes }: { notes: { icon: ReactNode; label: string; text: string }[] }) {
  return <Stack>{notes.map(note => <Stack key={note.label} direction="row" spacing={1.5} sx={{ alignItems: 'center', py: 1.25, borderTop: '1px solid', borderColor: 'divider', '&:first-of-type': { borderTop: 0, pt: 0 }, '&:last-of-type': { pb: 0 } }}>
    <Box sx={{ color: 'text.secondary', display: 'flex' }}>{note.icon}</Box>
    <Box sx={{ minWidth: 0 }}><Typography variant="body2" sx={{ fontWeight: 500 }}>{note.label}</Typography><Typography variant="body2" color="text.secondary">{note.text}</Typography></Box>
  </Stack>)}</Stack>;
}
