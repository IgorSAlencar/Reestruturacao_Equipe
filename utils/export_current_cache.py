"""Atualiza o cache da visão Atual usado pela API TypeScript."""
from __future__ import annotations
import json, os, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import Estudo_GreenField_V3_COMPLETO as v3

def clean(v):
    if pd.isna(v): return None
    if hasattr(v,"item"): v=v.item()
    return v

def main():
    cfg=v3.ModelConfig(); engine=v3.create_sql_engine(v3.SQLConfig()); raw=v3.load_raw_data(engine,cfg)
    municipal=v3.prepare_municipal_reference(raw["municipalities"],raw["population"],v3.load_municipal_geometry(cfg))
    stores=v3.prepare_stores(raw["stores"],municipal)
    hierarchy=v3.prepare_current_hierarchy(raw["current_hierarchy"])
    poles=v3.attach_current_hierarchy(v3.prepare_current_poles(raw["current_poles"],cfg),hierarchy)
    groups=stores.groupby(["CHAVE_SUPERVISAO","COD_IBGE"],as_index=False).agg(QTD_LOJAS=("CHAVE_LOJA","nunique"))
    groups=groups.merge(municipal[["COD_IBGE","NM_MUN","UF","POPULACAO_MUNICIPIO","LATITUDE_MUNICIPIO","LONGITUDE_MUNICIPIO"]],on="COD_IBGE",how="left")
    pole_rows=[]
    for r in poles.itertuples(index=False):
        pole_rows.append({"id":str(r.CHAVE_SUPERVISAO),"name":str(getattr(r,"DESC_SUPERVISAO",r.CHAVE_SUPERVISAO)),"longitude":float(r.LONGITUDE_ATUAL),"latitude":float(r.LATITUDE_ATUAL),"area":str(r.DESC_GERENCIA_AREA_ATUAL),"source":"current"})
    units=[]
    for r in groups.itertuples(index=False):
        units.append({"id":"MUN-"+str(r.COD_IBGE)+"-"+str(r.CHAVE_SUPERVISAO),"type":"MUNICIPIO","municipalityCode":str(r.COD_IBGE),"municipalityName":clean(r.NM_MUN),"uf":clean(r.UF),"poleId":str(r.CHAVE_SUPERVISAO),"population":float(clean(r.POPULACAO_MUNICIPIO) or 0),"stores":int(r.QTD_LOJAS),"latitude":float(r.LATITUDE_MUNICIPIO),"longitude":float(r.LONGITUDE_MUNICIPIO),"distanceKm":0})
    areas={}
    for p in pole_rows: areas[p["area"]]=areas.get(p["area"],0)+1
    now=datetime.now().isoformat(); payload={"summary":{"id":"current","name":"Atual — lojas ativas","kind":"current","version":f"PERIODO_{cfg.periodo_lojas}","createdAt":now,"poleCount":len(pole_rows),"areaCounts":areas,"warnings":[]},"poles":pole_rows,"units":units,"territories":{"type":"FeatureCollection","features":[]},"refreshedAt":now}
    target=Path(os.getenv("APP_DATA_DIR",ROOT/".territorios-data"));target.mkdir(parents=True,exist_ok=True)
    temp=target/"current.json.tmp";temp.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8");temp.replace(target/"current.json")
    print(json.dumps({"polos":len(pole_rows),"unidades":len(units),"lojas":sum(x["stores"] for x in units)},ensure_ascii=False))

if __name__=="__main__": main()
