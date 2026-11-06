import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error('Usage: node extract_operational_workbook.mjs <input.xlsx> <output.json>');
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const value = (cell) => cell === null || cell === undefined ? '' : String(cell).trim();
const rows = (sheetName, range) => workbook.worksheets.getItem(sheetName).getRange(range).values;

const people = rows('P0-03（人员信息）', 'A3:D24')
  .filter((row) => value(row[0]))
  .map((row) => ({
    name: value(row[0]),
    phone: value(row[1]),
    roles: [value(row[2]), value(row[3])].filter(Boolean),
  }));

const sites = rows('P1-01（站点清单）', 'A3:D39')
  .filter((row) => value(row[1]))
  .map((row) => ({
    sequence: Number(row[0]),
    name: value(row[1]),
    type: value(row[2]),
    owner: value(row[3]),
  }));

const pilotSites = rows('P0-01（测试站点）', 'A2:D5')
  .filter((row) => value(row[1]))
  .map((row) => ({
    sequence: Number(row[0]),
    name: value(row[1]),
    owner: value(row[2]),
    frequency: value(row[3]),
  }));

const equipmentRows = rows('P1-02（站点设备信息）', 'A1:H634');
const markerLabels = new Set(['站点名称', '站点情况说明']);
const equipmentBlocks = [];
for (let index = 0; index < equipmentRows.length; index += 1) {
  if (!markerLabels.has(value(equipmentRows[index][0]))) continue;
  let end = index + 1;
  while (end < equipmentRows.length && !markerLabels.has(value(equipmentRows[end][0]))) end += 1;
  const siteName = equipmentRows[index].slice(1).map(value).find(Boolean) || '';
  const devices = equipmentRows.slice(index + 2, end)
    .map((row, offset) => ({
      source_row: index + offset + 3,
      name: value(row[0]),
      condition: value(row[1]),
      model: value(row[2]),
      manufacturer: ['外接', '外接设备'].includes(value(row[3])) ? '外接设备' : value(row[3]),
      reagent_name: value(row[4]),
      unit: value(row[5]),
      remaining_days: typeof row[6] === 'number' ? row[6] : (value(row[6]) || null),
      reagent_batch: value(row[7]),
    }))
    .filter((device) => device.name && device.name !== '设备名称');
  equipmentBlocks.push({ site: siteName, devices });
}

const excelDate = (serial) => {
  const date = new Date(Date.UTC(1899, 11, 30) + Number(serial) * 86400000);
  return date.toISOString().slice(0, 10);
};
const normalizeInspectionDate = (raw) => {
  if (typeof raw === 'number') return excelDate(raw);
  const text = value(raw);
  if (!text) return '';
  const yearMonth = text.match(/(20\d{2})[.年/-](\d{1,2})/);
  if (yearMonth) return `${yearMonth[1]}-${String(yearMonth[2]).padStart(2, '0')}`;
  const monthOnly = text.match(/^(\d{1,2})月份?$/);
  if (monthOnly) return `${new Date().getFullYear()}-${String(monthOnly[1]).padStart(2, '0')}`;
  return text;
};
const parseVehicle = (row) => {
  const rawPlate = value(row[1]).replace(/\s+/g, '');
  const match = rawPlate.match(/^(.*?)(赣[A-Z0-9]+)$/i);
  return {
    sequence: Number(row[0]),
    vehicle_name: match ? match[1].replace(/[（）()]/g, '') : '',
    plate_no: match ? match[2].toUpperCase() : rawPlate,
    energy_type: value(row[2]),
    seats: Number(row[3]) || null,
    current_mileage: typeof row[4] === 'number' ? row[4] : null,
    next_maintenance_mileage: typeof row[5] === 'number' ? row[5] : null,
    annual_inspection_expiry: normalizeInspectionDate(row[6]),
    source_insurance_inspection: value(row[6]) || (typeof row[6] === 'number' ? excelDate(row[6]) : ''),
  };
};
const vehicles = rows('P1-05（车辆信息）', 'A3:G6')
  .filter((row) => value(row[1]))
  .map(parseVehicle);

const inspectionRows = rows('P1-06（站点检查项）', 'A1:C17');
const inspectionItems = [];
let frequency = 'weekly';
for (const row of inspectionRows) {
  const name = value(row[0]);
  if (!name) continue;
  if (name.includes('每月')) {
    frequency = 'monthly';
    if (!/^\d/.test(name)) continue;
  }
  if (!/^\d/.test(name)) continue;
  const cleanedName = name.replace(/^\d+[、.．]\s*/, '')
    .replace(/仪器校准/g, '仪器质控');
  const photoMatch = value(row[1]).match(/\d+/);
  inspectionItems.push({
    frequency,
    name: cleanedName,
    photo_count: photoMatch ? Number(photoMatch[0]) : 0,
    need_review: value(row[2]).includes('需审核'),
  });
}

const siteNames = new Set(sites.map((site) => site.name));
const peopleNames = new Set(people.map((person) => person.name));
const equipmentSiteNames = new Set(equipmentBlocks.map((block) => block.site));
const output = {
  source: path.resolve(inputPath),
  extracted_at: new Date().toISOString(),
  people,
  sites,
  pilot_sites: pilotSites,
  equipment_blocks: equipmentBlocks,
  vehicles,
  inspection_items: inspectionItems,
  quality: {
    owners_not_in_people: [...new Set(sites.filter((site) => !peopleNames.has(site.owner)).map((site) => site.owner))],
    equipment_blocks_not_in_sites: equipmentBlocks.filter((block) => !siteNames.has(block.site)).map((block) => block.site),
    sites_without_equipment_blocks: sites.filter((site) => !equipmentSiteNames.has(site.name)).map((site) => site.name),
    equipment_count: equipmentBlocks.reduce((sum, block) => sum + block.devices.length, 0),
  },
};

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, JSON.stringify(output, null, 2), 'utf8');
console.log(JSON.stringify({
  people: people.length,
  sites: sites.length,
  pilot_sites: pilotSites.length,
  equipment_blocks: equipmentBlocks.length,
  equipment: output.quality.equipment_count,
  vehicles: vehicles.length,
  inspection_items: inspectionItems.length,
  quality: output.quality,
}, null, 2));
