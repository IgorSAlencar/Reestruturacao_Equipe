import type { SheetModel } from '../shared/areaManagement';

function xmlEscape(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function sheetName(name: string) {
  return xmlEscape(name.replace(/[:\\/?*[\]]/g, ' ').slice(0, 31) || 'Aba');
}

function cellXml(value: string | number) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<Cell><Data ss:Type="Number">${value}</Data></Cell>`;
  }
  return `<Cell><Data ss:Type="String">${xmlEscape(String(value ?? ''))}</Data></Cell>`;
}

export function toSpreadsheetMl(sheets: SheetModel[]) {
  const worksheets = sheets.map(sheet => {
    const header = `<Row ss:StyleID="header">${sheet.headers.map(cellXml).join('')}</Row>`;
    const body = sheet.rows.map(row => `<Row>${row.map(cellXml).join('')}</Row>`).join('');
    return `<Worksheet ss:Name="${sheetName(sheet.name)}"><Table>${header}${body}</Table></Worksheet>`;
  }).join('');
  return `<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Styles>
<Style ss:ID="header"><Font ss:Bold="1" ss:Color="#EAF1FB"/><Interior ss:Color="#0D1727" ss:Pattern="Solid"/></Style>
</Styles>
${worksheets}
</Workbook>`;
}

export function downloadAreaWorkbook(filename: string, sheets: SheetModel[]) {
  const xml = `\uFEFF${toSpreadsheetMl(sheets)}`;
  const blob = new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.endsWith('.xls') ? filename : `${filename}.xls`;
  link.click();
  URL.revokeObjectURL(url);
}
