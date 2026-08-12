import 'dotenv/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const API_PORT = Number(process.env.API_PORT || 3333);
export const V3_DIR = path.resolve(process.env.OUTPUT_DIR || path.join(ROOT, 'saida_greenfield_v3'));
export const V4_DIR = path.resolve(process.env.OUTPUT_DIR_V4 || path.join(ROOT, 'saida_greenfield_v4'));
export const DATA_DIR = path.resolve(process.env.APP_DATA_DIR || path.join(ROOT, '.territorios-data'));
export const MUNICIPALITIES_FILE = path.resolve(process.env.ARQUIVO_MUNICIPIOS_JSON || path.join(ROOT, 'geometria_brasil', 'Brasil_Municipios.json'));
export const MAPBOX_STYLE = process.env.MAPBOX_STYLE || 'mapbox://styles/mapbox/light-v11';
