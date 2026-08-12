import type { DraftData, RegionalOffice, ScenarioData, ScenarioSummary } from '../shared/types';

export type PopulationResponse={source:string;censusYear:number|null;count:number;values:Record<string,number>;cachedAt:string;stale:boolean};
export type RegionalOfficesResponse={source:string;count:number;points:RegionalOffice[];cachedAt:string;stale:boolean};

async function request<T>(url:string,init?:RequestInit):Promise<T>{
  const r=await fetch(url,{...init,headers:{'content-type':'application/json',...(init?.headers||{})}});
  if(!r.ok)throw new Error((await r.json().catch(()=>({message:r.statusText}))).message); return r.json();
}
export const api={
  config:()=>request<{mapboxToken:string;mapboxStyle:string}>('/api/config'),
  scenarios:()=>request<ScenarioSummary[]>('/api/scenarios'),
  drafts:()=>request<Omit<DraftData,'data'>[]>('/api/drafts'),
  scenario:(id:string)=>request<ScenarioData>(`/api/scenarios/${id}`),
  current:()=>request<ScenarioData>('/api/scenarios/current'),
  draft:(id:string)=>request<DraftData>(`/api/drafts/${id}`),
  createDraft:(name:string,baseScenarioId:string,data:ScenarioData)=>request<DraftData>('/api/drafts',{method:'POST',body:JSON.stringify({name,baseScenarioId,data})}),
  saveDraft:(draft:DraftData)=>request<DraftData>(`/api/drafts/${draft.id}`,{method:'PUT',body:JSON.stringify({name:draft.name,revision:draft.revision,data:draft.data})}),
  refresh:()=>request<{message:string}>('/api/current-cache/refresh',{method:'POST'}),
  cacheStatus:()=>request<{available:boolean;refreshing:boolean;lastError:string|null}>('/api/current-cache/status'),
  population:()=>request<PopulationResponse>('/api/population'),
  regionalOffices:()=>request<RegionalOfficesResponse>('/api/regional-offices'),
};
