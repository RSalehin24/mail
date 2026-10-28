import type { ReactNode } from 'react';
import { Box, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';

export function AdminTable({ columns, rows, empty, footer }: { columns: string[]; rows: { key: string; cells: ReactNode[] }[]; empty: string; footer?: ReactNode }) {
  return <Paper variant="outlined" className="mailbox-surface">
    <TableContainer className="desktop-table"><Table className="mailbox-table"><TableHead><TableRow>{columns.map(label => <TableCell align="center" key={label}>{label}</TableCell>)}</TableRow></TableHead>
      <TableBody>{rows.map(row => <TableRow hover key={row.key}>{row.cells.map((cell, index) => <TableCell align="center" key={columns[index]} sx={{ overflowWrap: 'anywhere' }}>{cell}</TableCell>)}</TableRow>)}</TableBody>
    </Table></TableContainer>
    <Box className="mailbox-cards">{rows.map(row => <Box key={row.key} sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}><Stack spacing={1.5}>{row.cells.map((cell, index) => <Box key={columns[index]} sx={{ overflowWrap: 'anywhere' }}><Typography variant="caption" color="text.secondary">{columns[index]}</Typography><Box>{cell}</Box></Box>)}</Stack></Box>)}</Box>
    {!rows.length && <Typography sx={{ p: 4, textAlign: 'center' }}>{empty}</Typography>}
    {footer && <Box sx={{ borderTop: '1px solid', borderColor: 'divider' }}>{footer}</Box>}
  </Paper>;
}
