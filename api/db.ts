import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { DATA_DIR } from './config.ts';
import type { DraftData, ScenarioData } from '../shared/types.ts';

fs.mkdirSync(DATA_DIR, { recursive: true });
const db = new DatabaseSync(path.join(DATA_DIR, 'territorios.sqlite'));
db.exec(`CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_scenario_id TEXT NOT NULL, revision INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data_json TEXT NOT NULL
); CREATE INDEX IF NOT EXISTS idx_drafts_updated_at ON drafts(updated_at DESC);`);
db.exec('PRAGMA optimize');

export function listDrafts(): Omit<DraftData,'data'>[] {
  return db.prepare('SELECT id,name,base_scenario_id,revision,created_at,updated_at FROM drafts ORDER BY updated_at DESC').all().map((r:any)=>({
    id:r.id,name:r.name,baseScenarioId:r.base_scenario_id,revision:r.revision,createdAt:r.created_at,updatedAt:r.updated_at
  }));
}
export function getDraft(id:string):DraftData|null {
  const r:any=db.prepare('SELECT * FROM drafts WHERE id=?').get(id); if(!r)return null;
  return {id:r.id,name:r.name,baseScenarioId:r.base_scenario_id,revision:r.revision,createdAt:r.created_at,updatedAt:r.updated_at,data:JSON.parse(r.data_json)};
}
export function createDraft(id:string,name:string,baseScenarioId:string,data:ScenarioData):DraftData {
  const now=new Date().toISOString();
  db.prepare('INSERT INTO drafts(id,name,base_scenario_id,revision,created_at,updated_at,data_json) VALUES(?,?,?,?,?,?,?)').run(id,name,baseScenarioId,1,now,now,JSON.stringify(data));
  return {id,name,baseScenarioId,revision:1,createdAt:now,updatedAt:now,data};
}
export function updateDraft(id:string,name:string,revision:number,data:ScenarioData):DraftData|null|'conflict' {
  const current=getDraft(id); if(!current)return null; if(current.revision!==revision)return 'conflict';
  const updatedAt=new Date().toISOString(), next=revision+1;
  db.prepare('UPDATE drafts SET name=?,revision=?,updated_at=?,data_json=? WHERE id=? AND revision=?').run(name,next,updatedAt,JSON.stringify(data),id,revision);
  return {...current,name,revision:next,updatedAt,data};
}
export function deleteDraft(id:string){ return db.prepare('DELETE FROM drafts WHERE id=?').run(id).changes>0; }
