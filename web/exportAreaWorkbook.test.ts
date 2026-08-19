import assert from 'node:assert/strict';
import test from 'node:test';
import { toSpreadsheetMl } from '../web/exportAreaWorkbook.ts';

test('gera SpreadsheetML com abas, números e escape XML', () => {
  const xml = toSpreadsheetMl([
    { name: 'Polos', headers: ['Polo', 'Unidades'], rows: [['A & B', 12], ['<x>', 0]] },
    { name: 'Unidades', headers: ['Nome'], rows: [['São Paulo']] },
  ]);
  assert.match(xml, /ss:Name="Polos"/);
  assert.match(xml, /ss:Name="Unidades"/);
  assert.match(xml, /ss:Type="Number">12</);
  assert.match(xml, /A &amp; B/);
  assert.match(xml, /&lt;x&gt;/);
});
