"""
Baixa todos os GeoJSON de municípios do repositório geodata-br
e unifica em um único arquivo Brasil_Municipios.json.
Fonte: https://github.com/tbrugz/geodata-br/tree/master/geojson
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_API_CONTENTS = (
    "https://api.github.com/repos/tbrugz/geodata-br/contents/geojson"
)
RAW_BASE = "https://raw.githubusercontent.com/tbrugz/geodata-br/master/geojson"
OUTPUT_FILE = Path(__file__).resolve().parent / "Brasil_Municipios.json"


def _get_json(url: str) -> dict | list:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Reestruturacao_Equipe/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def listar_arquivos_geojson() -> list[str]:
    """Lista os .json da pasta geojson via API do GitHub."""
    conteudo = _get_json(GITHUB_API_CONTENTS)
    if not isinstance(conteudo, list):
        raise RuntimeError(f"Resposta inesperada da API do GitHub: {type(conteudo)}")

    arquivos = sorted(
        item["name"]
        for item in conteudo
        if item.get("type") == "file" and str(item.get("name", "")).endswith(".json")
    )
    if not arquivos:
        raise RuntimeError("Nenhum arquivo .json encontrado em geojson/")
    return arquivos


def baixar_geojson(nome_arquivo: str) -> dict:
    url = f"{RAW_BASE}/{nome_arquivo}"
    print(f"  Baixando {nome_arquivo}...")
    data = _get_json(url)
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ValueError(f"{nome_arquivo} não é um FeatureCollection válido")
    return data


def unificar_features(arquivos: list[str]) -> dict:
    features: list[dict] = []
    vistos: set[str] = set()

    for nome in arquivos:
        geojson = baixar_geojson(nome)
        for feature in geojson.get("features", []):
            props = feature.get("properties") or {}
            feature_id = str(props.get("id") or props.get("name") or "")
            # evita duplicar municípios se algum arquivo se sobrepor
            if feature_id and feature_id in vistos:
                continue
            if feature_id:
                vistos.add(feature_id)
            features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def main() -> int:
    try:
        print("Listando arquivos em tbrugz/geodata-br/geojson...")
        arquivos = listar_arquivos_geojson()
        print(f"Encontrados {len(arquivos)} arquivos.")

        unificado = unificar_features(arquivos)

        OUTPUT_FILE.write_text(
            json.dumps(unificado, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

        print(
            f"\nPronto: {OUTPUT_FILE.name} "
            f"com {len(unificado['features'])} municípios."
        )
        return 0
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"Erro de rede ao baixar os GeoJSON: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
