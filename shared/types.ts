import type { FeatureCollection, Geometry } from 'geojson';

export type ScenarioKind = 'current' | 'v3' | 'v4' | 'v5' | 'draft';
export interface ScenarioSummary { id: string; name: string; kind: ScenarioKind; version: string; createdAt?: string; path?: string; poleCount: number; areaCounts: Record<string, number>; warnings: string[]; }
export interface Pole { id: string; name: string; longitude: number; latitude: number; area: string; regional?: string; uf?: string; municipalityCode?: string; municipalityName?: string; source: ScenarioKind; }
export interface TerritoryUnit { id: string; type: 'MUNICIPIO'|'DISTRITO'; municipalityCode: string; districtCode?: string; municipalityName?: string; uf?: string; poleId: string|null; population: number; stores: number; latitude: number; longitude: number; distanceKm: number; }
export interface ScenarioData { summary: ScenarioSummary; poles: Pole[]; units: TerritoryUnit[]; territories: FeatureCollection<Geometry>; refreshedAt?: string; }
export interface DraftData { id: string; name: string; baseScenarioId: string; revision: number; createdAt: string; updatedAt: string; data: ScenarioData; }
export interface PoleMetrics { municipalities: number; units: number; districts: number; stores: number; population: number; minKm: number; meanKm: number; maxKm: number; }
export interface RegionalOffice { id: string; name: string; longitude: number; latitude: number; address: string; agencies: number; }
