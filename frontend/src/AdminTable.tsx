import type { ReactNode } from 'react';
import { Box, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';

// `wideFirst`: on phones the first cell (an address) gets its own full-width line and the rest sit side by side without labels.
export function AdminTable({ columns, rows, empty, footer, widths, wideFirst = false }: { columns: string[]; rows: { key: string; cells: ReactNode[] }[]; empty: string; footer?: ReactNode; widths?: string[]; wideFirst?: boolean }) {
  return <Paper variant="outlined" className="mailbox-surface">
    <TableContainer className="desktop-table"><Table className="mailbox-table" sx={widths && { tableLayout: 'fixed' }}>{widths && <colgroup>{widths.map(width => <col key={width + columns.length} style={{ width }}/>)}</colgroup>}<TableHead><TableRow>{columns.map(label => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
      <TableBody>{rows.map(row => <TableRow hover key={row.key}>{row.cells.map((cell, index) => <TableCell key={columns[index]} sx={{ overflowWrap: 'anywhere' }}>{cell}</TableCell>)}</TableRow>)}</TableBody>
    </Table></TableContainer>
    <Box className="mailbox-cards">{rows.map(row => wideFirst
      ? <Stack key={row.key} spacing={1.5} sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Box sx={{ overflowWrap: 'anywhere', fontWeight: 500 }}>{row.cells[0]}</Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 1 }}>{row.cells.slice(1).map((cell, index) => <Box key={columns[index + 1]}>{cell}</Box>)}</Stack>
      </Stack>
      : <Box key={row.key} sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider', display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 2 }}>
        {row.cells.map((cell, index) => <Box key={columns[index]} sx={{ overflowWrap: 'anywhere', '&:last-child:nth-child(odd)': { gridColumn: '1 / -1' } }}><Typography variant="caption" color="text.secondary">{columns[index]}</Typography><Box>{cell}</Box></Box>)}
      </Box>)}</Box>
    {!rows.length && <Typography sx={{ p: 4, textAlign: 'center' }}>{empty}</Typography>}
    {footer && <Box sx={{ borderTop: '1px solid', borderColor: 'divider' }}>{footer}</Box>}
  </Paper>;
}
