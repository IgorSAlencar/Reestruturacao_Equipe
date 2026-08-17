"""A malha municipal agora sai do shapefile oficial do IBGE, não do geodata-br.

Use:
    python scripts/generate_municipal_mesh.py
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_municipal_mesh.py"


def main() -> int:
    print(
        "Aviso: utils/extrair_geojson.py foi substituído por "
        "scripts/generate_municipal_mesh.py (shapefile IBGE 2025)."
    )
    if not SCRIPT.exists():
        print(f"Script não encontrado: {SCRIPT}", file=sys.stderr)
        return 1
    runpy.run_path(str(SCRIPT), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
