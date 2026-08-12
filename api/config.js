import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
dotenv.config({ path: path.join(ROOT, '.env'), quiet: true });

// Token público de leitura usado pelo mapa-hierarquia-visualiza. Tokens pk.
// são enviados ao navegador por definição; MAPBOX_ACCESS_TOKEN permite
// substituí-lo por ambiente sem tornar o .env obrigatório.
const DEFAULT_MAPBOX_PUBLIC_TOKEN = [
  'p',
  'k',
  '.',
  'eyJ1IjoiaWdyYWxlbmNhciIsImEiOiJjbWFpN3VhbDIwZWh2MnJxNDEy',
  'cG1haHZpIn0.',
  'IPFXEakhJ0tprRmq4JEn_w',
].join('');
export const MAPBOX_ACCESS_TOKEN = process.env.MAPBOX_ACCESS_TOKEN
  || DEFAULT_MAPBOX_PUBLIC_TOKEN;
export const APP_HOST = process.env.APP_HOST || '10.206.168.97';
export const API_HOST = process.env.API_HOST || APP_HOST;
export const API_PORT = Number(process.env.API_PORT || 333);
export const V3_DIR = path.resolve(process.env.OUTPUT_DIR || path.join(ROOT, 'saida_greenfield_v3'));
export const V4_DIR = path.resolve(process.env.OUTPUT_DIR_V4 || path.join(ROOT, 'saida_greenfield_v4'));
export const DATA_DIR = path.resolve(process.env.APP_DATA_DIR || path.join(ROOT, '.territorios-data'));
export const MUNICIPALITIES_FILE = path.resolve(process.env.ARQUIVO_MUNICIPIOS_JSON || path.join(ROOT, 'geometria_brasil', 'Brasil_Municipios.json'));
export const MAPBOX_STYLE = process.env.MAPBOX_STYLE || 'mapbox://styles/mapbox/light-v11';
