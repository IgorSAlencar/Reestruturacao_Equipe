from __future__ import annotations

import os
import re
import shutil
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from zipfile import ZipFile

import geopandas as gpd
import pandas as pd


# ============================================================
# 1. CONFIGURAÇÕES
# ============================================================

BASE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()

PASTA = BASE / "distritos_brasil"
PASTA_DOWNLOADS = PASTA / "downloads"
PASTA_EXTRAIDOS = PASTA / "extraidos"
PASTA_SAIDAS = PASTA / "saidas"

SAIDA_POP_CSV = PASTA_SAIDAS / "populacao_distritos_brasil_2022.csv"
SAIDA_GEO_CSV = PASTA_SAIDAS / "distritos_brasil_pop2022_latlon.csv"
SAIDA_GPKG = PASTA_SAIDAS / "distritos_brasil_pop2022.gpkg"

# False: baixa e gera apenas o CSV populacional por distrito.
# True: também baixa a malha nacional, junta população e gera GeoPackage.
GERAR_GEOMETRIA = True

# True: baixa novamente mesmo quando o arquivo já existir.
FORCAR_DOWNLOAD = False

# Tenta corrigir geometrias inválidas antes de consolidar os distritos.
CORRIGIR_GEOMETRIAS_INVALIDAS = True

# CRS métrico nacional para cálculo de área e ponto representativo.
CRS_METRICO_BRASIL = "EPSG:5880"

DIR_POP_IBGE = (
    "https://ftp.ibge.gov.br/"
    "Censos/Censo_Demografico_2022/"
    "Agregados_por_Setores_Censitarios/"
    "Agregados_por_Distrito_csv/"
)

URL_MALHA_IBGE = (
    "https://ftp.ibge.gov.br/"
    "Censos/Censo_Demografico_2022/"
    "Agregados_por_Setores_Censitarios/"
    "malha_com_atributos/"
    "distritos/gpkg/BR/"
    "BR_distritos_CD2022.gpkg"
)

PADRAO_ARQUIVO_POP = re.compile(
    r"^Agregados_por_distritos_basico_BR(?:_(\d{8}))?\.zip$",
    flags=re.IGNORECASE,
)


# ============================================================
# 2. PASTAS E DOWNLOADS
# ============================================================


def criar_pastas() -> None:
    for pasta in (PASTA, PASTA_DOWNLOADS, PASTA_EXTRAIDOS, PASTA_SAIDAS):
        pasta.mkdir(parents=True, exist_ok=True)


def criar_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Encoding": "identity",
        },
    )


def descobrir_url_populacao() -> str:
    """Localiza automaticamente o ZIP básico mais recente por distrito."""
    print("Consultando o diretório oficial do IBGE...")

    with urllib.request.urlopen(criar_request(DIR_POP_IBGE), timeout=180) as resposta:
        html = resposta.read().decode("utf-8", errors="ignore")

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    candidatos: list[tuple[str, str]] = []

    for href in hrefs:
        nome = unquote(Path(urlparse(href).path).name)
        match = PADRAO_ARQUIVO_POP.match(nome)
        if not match:
            continue

        data_revisao = match.group(1) or "00000000"
        candidatos.append((data_revisao, urljoin(DIR_POP_IBGE, href)))

    if not candidatos:
        raise RuntimeError(
            "Nenhum arquivo básico de agregados por distrito foi encontrado em:\n"
            f"{DIR_POP_IBGE}"
        )

    data_revisao, url = max(candidatos, key=lambda item: item[0])
    print(f"Arquivo populacional encontrado: {Path(urlparse(url).path).name}")
    if data_revisao != "00000000":
        print(f"Revisão identificada: {data_revisao}")

    return url


def baixar_arquivo(
    url: str,
    destino: Path,
    forcar: bool = False,
    tentativas: int = 3,
) -> Path:
    """Baixa um arquivo em blocos, com tentativas e arquivo temporário."""
    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists() and destino.stat().st_size > 0 and not forcar:
        print(f"Usando arquivo existente: {destino}")
        return destino

    temporario = destino.with_suffix(destino.suffix + ".part")

    for tentativa in range(1, tentativas + 1):
        temporario.unlink(missing_ok=True)

        try:
            print(f"Baixando ({tentativa}/{tentativas}): {url}")

            with urllib.request.urlopen(criar_request(url), timeout=600) as resposta:
                total = int(resposta.headers.get("Content-Length") or 0)
                baixado = 0
                bloco = 1024 * 1024

                with temporario.open("wb") as arquivo:
                    while True:
                        dados = resposta.read(bloco)
                        if not dados:
                            break

                        arquivo.write(dados)
                        baixado += len(dados)

                        if total:
                            percentual = baixado / total * 100
                            print(
                                f"\r  {baixado / 1024**2:,.1f} MB / "
                                f"{total / 1024**2:,.1f} MB ({percentual:5.1f}%)",
                                end="",
                                flush=True,
                            )
                        else:
                            print(
                                f"\r  {baixado / 1024**2:,.1f} MB",
                                end="",
                                flush=True,
                            )

            print()
            os.replace(temporario, destino)
            print(f"Download concluído: {destino}")
            return destino

        except Exception as erro:
            print(f"Falha no download: {erro}")
            temporario.unlink(missing_ok=True)

            if tentativa == tentativas:
                raise

            time.sleep(2 * tentativa)

    raise RuntimeError("Falha inesperada no download.")


# ============================================================
# 3. POPULAÇÃO OFICIAL POR DISTRITO
# ============================================================


def extrair_csv_basico(zip_path: Path) -> Path:
    """Extrai o CSV básico de população contido no ZIP do IBGE."""
    with ZipFile(zip_path) as arquivo_zip:
        membros_csv = [
            nome for nome in arquivo_zip.namelist() if nome.lower().endswith(".csv")
        ]

        if not membros_csv:
            raise FileNotFoundError(f"Nenhum CSV encontrado dentro de {zip_path}")

        preferidos = [
            nome
            for nome in membros_csv
            if "basico" in Path(nome).name.casefold()
        ]

        membro = preferidos[0] if preferidos else membros_csv[0]
        destino = PASTA_EXTRAIDOS / Path(membro).name

        if destino.exists() and destino.stat().st_size > 0:
            print(f"Usando CSV já extraído: {destino}")
            return destino

        print(f"Extraindo: {membro}")
        with arquivo_zip.open(membro) as origem, destino.open("wb") as saida:
            shutil.copyfileobj(origem, saida)

    return destino


def detectar_separador(csv_path: Path, encoding: str) -> str:
    with csv_path.open("r", encoding=encoding, errors="strict") as arquivo:
        primeira_linha = arquivo.readline()

    candidatos = [";", ",", "|", "\t"]
    separador = max(candidatos, key=primeira_linha.count)

    if primeira_linha.count(separador) == 0:
        raise ValueError(f"Não foi possível detectar o separador de {csv_path.name}")

    return separador


def ler_csv_robusto(csv_path: Path) -> pd.DataFrame:
    ultimo_erro: Exception | None = None

    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            separador = detectar_separador(csv_path, encoding)
            print(
                f"Lendo população: {csv_path.name} | "
                f"encoding={encoding} | separador={separador!r}"
            )

            return pd.read_csv(
                csv_path,
                sep=separador,
                encoding=encoding,
                dtype=str,
                low_memory=False,
            )

        except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError) as erro:
            ultimo_erro = erro

    raise RuntimeError(
        f"Não foi possível ler {csv_path}. Último erro: {ultimo_erro}"
    )


def normalizar_codigo(
    serie: pd.Series,
    tamanho: int | None = None,
) -> pd.Series:
    resultado = (
        serie.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    if tamanho:
        resultado = resultado.str.zfill(tamanho)

    return resultado


def preparar_populacao(csv_path: Path) -> pd.DataFrame:
    """Gera uma linha por distrito com V0001 convertido para POP_2022."""
    df = ler_csv_robusto(csv_path)
    df.columns = [str(coluna).strip().upper() for coluna in df.columns]

    obrigatorias = {"CD_DIST", "V0001"}
    faltantes = obrigatorias - set(df.columns)

    if faltantes:
        raise ValueError(
            f"Colunas obrigatórias ausentes: {sorted(faltantes)}\n"
            f"Colunas disponíveis: {df.columns.tolist()}"
        )

    if "CD_UF" in df.columns:
        df["CD_UF"] = normalizar_codigo(df["CD_UF"], 2)

    if "CD_MUN" in df.columns:
        df["CD_MUN"] = normalizar_codigo(df["CD_MUN"], 7)

    df["CD_DIST"] = normalizar_codigo(df["CD_DIST"], 9)

    df["POP_2022"] = pd.to_numeric(
        df["V0001"].astype("string").str.strip(),
        errors="coerce",
    ).astype("Int64")

    colunas_desejadas = [
        "CD_REGIAO",
        "NM_REGIAO",
        "CD_UF",
        "NM_UF",
        "CD_MUN",
        "NM_MUN",
        "CD_DIST",
        "NM_DIST",
        "POP_2022",
    ]

    colunas_existentes = [
        coluna for coluna in colunas_desejadas if coluna in df.columns
    ]

    pop = df[colunas_existentes].copy()

    duplicados = pop.loc[pop["CD_DIST"].duplicated(keep=False)]
    if not duplicados.empty:
        raise ValueError(
            "A base populacional possui CD_DIST duplicado. Exemplos:\n"
            + duplicados.head(20).to_string(index=False)
        )

    pop = pop.sort_values("CD_DIST").reset_index(drop=True)

    pop.to_csv(SAIDA_POP_CSV, index=False, encoding="utf-8-sig")

    print("\nResumo da população por distrito")
    print(f"  Distritos: {len(pop):,}".replace(",", "."))

    if "CD_MUN" in pop.columns:
        print(f"  Municípios: {pop['CD_MUN'].nunique():,}".replace(",", "."))

    print(
        f"  Distritos sem população: {pop['POP_2022'].isna().sum():,}".replace(
            ",", "."
        )
    )
    print(
        f"  Soma da população: {pop['POP_2022'].sum():,.0f}".replace(",", ".")
    )
    print(f"  CSV salvo em: {SAIDA_POP_CSV}")

    return pop


# ============================================================
# 4. LEITURA E CONSOLIDAÇÃO DA MALHA
# ============================================================


def normalizar_colunas_geograficas(
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError("A malha carregada não é um GeoDataFrame.")

    nome_geometria = gdf.geometry.name

    mapa_colunas = {
        coluna: (
            "geometry"
            if coluna == nome_geometria
            else str(coluna).strip().upper()
        )
        for coluna in gdf.columns
    }

    gdf = gdf.rename(columns=mapa_colunas)
    gdf = gdf.set_geometry("geometry")

    if gdf.crs is None:
        raise ValueError("A malha de distritos não possui CRS definido.")

    return gdf


def corrigir_geometrias(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Tenta corrigir geometrias inválidas sem alterar o CRS."""
    gdf = gdf.copy()
    invalidas = ~gdf.geometry.is_valid
    quantidade = int(invalidas.sum())

    if quantidade == 0:
        return gdf

    print(f"Geometrias inválidas encontradas: {quantidade:,}".replace(",", "."))

    try:
        from shapely import make_valid

        gdf.loc[invalidas, "geometry"] = gdf.loc[
            invalidas, "geometry"
        ].apply(make_valid)

    except (ImportError, AttributeError):
        print("shapely.make_valid indisponível; usando buffer(0).")
        gdf.loc[invalidas, "geometry"] = gdf.loc[
            invalidas, "geometry"
        ].buffer(0)

    ainda_invalidas = int((~gdf.geometry.is_valid).sum())
    print(
        f"Geometrias ainda inválidas após correção: {ainda_invalidas:,}".replace(
            ",", "."
        )
    )

    return gdf


def consolidar_por_distrito(
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Garante uma única linha por CD_DIST.

    - remove linhas sem código ou geometria;
    - remove duplicidades geométricas exatamente iguais;
    - dissolve partes diferentes pertencentes ao mesmo distrito.
    """
    if "CD_DIST" not in gdf.columns:
        raise ValueError("A coluna CD_DIST não existe na malha.")

    if gdf.crs is None:
        raise ValueError("A malha não possui CRS definido.")

    crs_original = gdf.crs
    gdf = gdf.copy()

    sem_codigo = gdf["CD_DIST"].isna() | gdf["CD_DIST"].eq("")
    if sem_codigo.any():
        print(
            f"Removendo registros sem CD_DIST: {int(sem_codigo.sum()):,}".replace(
                ",", "."
            )
        )
        gdf = gdf.loc[~sem_codigo].copy()

    sem_geometria = gdf.geometry.isna() | gdf.geometry.is_empty
    if sem_geometria.any():
        print(
            "Removendo registros sem geometria: "
            f"{int(sem_geometria.sum()):,}".replace(",", ".")
        )
        gdf = gdf.loc[~sem_geometria].copy()

    if CORRIGIR_GEOMETRIAS_INVALIDAS:
        gdf = corrigir_geometrias(gdf)

    # Remove apenas geometrias exatamente iguais do mesmo distrito.
    gdf["_GEOMETRIA_WKB"] = gdf.geometry.to_wkb(hex=True)
    duplicidade_exata = gdf.duplicated(
        subset=["CD_DIST", "_GEOMETRIA_WKB"],
        keep="first",
    )

    if duplicidade_exata.any():
        print(
            "Removendo feições exatamente repetidas: "
            f"{int(duplicidade_exata.sum()):,}".replace(",", ".")
        )
        gdf = gdf.loc[~duplicidade_exata].copy()

    gdf = gdf.drop(columns="_GEOMETRIA_WKB")

    quantidade_codigos_repetidos = gdf.loc[
        gdf["CD_DIST"].duplicated(keep=False), "CD_DIST"
    ].nunique()

    if quantidade_codigos_repetidos:
        print(
            "Distritos com múltiplas feições após remover cópias exatas: "
            f"{quantidade_codigos_repetidos:,}".replace(",", ".")
        )

        colunas_atributos = [
            coluna
            for coluna in gdf.columns
            if coluna not in {"CD_DIST", "geometry"}
        ]

        agregacoes = {coluna: "first" for coluna in colunas_atributos}

        gdf = gdf.dissolve(
            by="CD_DIST",
            as_index=False,
            aggfunc=agregacoes,
        )

    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=crs_original)

    duplicados_finais = int(gdf["CD_DIST"].duplicated().sum())
    if duplicados_finais:
        raise ValueError(
            f"Ainda existem {duplicados_finais} CD_DIST duplicados após o dissolve."
        )

    print(
        f"Malha consolidada: {len(gdf):,} distritos únicos".replace(",", ".")
    )

    return gdf


def ler_malha_nacional(gpkg_path: Path) -> gpd.GeoDataFrame:
    print(f"\nLendo malha nacional: {gpkg_path}")

    try:
        gdf = gpd.read_file(gpkg_path, engine="pyogrio")
    except (ImportError, ModuleNotFoundError):
        print("pyogrio não instalado; usando o mecanismo padrão do GeoPandas.")
        gdf = gpd.read_file(gpkg_path)

    gdf = normalizar_colunas_geograficas(gdf)

    faltantes = {"CD_DIST", "geometry"} - set(gdf.columns)
    if faltantes:
        raise ValueError(
            f"Colunas ausentes na malha: {sorted(faltantes)}\n"
            f"Colunas disponíveis: {gdf.columns.tolist()}"
        )

    if "CD_UF" in gdf.columns:
        gdf["CD_UF"] = normalizar_codigo(gdf["CD_UF"], 2)

    if "CD_MUN" in gdf.columns:
        gdf["CD_MUN"] = normalizar_codigo(gdf["CD_MUN"], 7)

    gdf["CD_DIST"] = normalizar_codigo(gdf["CD_DIST"], 9)

    print(f"CRS original: {gdf.crs}")
    print(f"Feições antes da consolidação: {len(gdf):,}".replace(",", "."))

    gdf = consolidar_por_distrito(gdf)

    # Recalcula a área depois de unir todas as partes de cada distrito.
    gdf_metrico = gdf.to_crs(CRS_METRICO_BRASIL)
    gdf["AREA_KM2_CALCULADA"] = gdf_metrico.geometry.area / 1_000_000

    return gdf


# ============================================================
# 5. JUNÇÃO POPULAÇÃO + GEOMETRIA
# ============================================================


def juntar_populacao_geometria(
    malha: gpd.GeoDataFrame,
    pop: pd.DataFrame,
) -> gpd.GeoDataFrame:
    duplicados_malha = int(malha["CD_DIST"].duplicated().sum())
    duplicados_pop = int(pop["CD_DIST"].duplicated().sum())

    if duplicados_malha:
        raise ValueError(
            f"A malha ainda possui {duplicados_malha} CD_DIST duplicados."
        )

    if duplicados_pop:
        raise ValueError(
            f"A população ainda possui {duplicados_pop} CD_DIST duplicados."
        )

    # Evita colunas populacionais duplicadas vindas da malha com atributos.
    colunas_remover = [
        coluna
        for coluna in malha.columns
        if re.fullmatch(r"V\d+", str(coluna), flags=re.IGNORECASE)
    ]

    if colunas_remover:
        malha = malha.drop(columns=colunas_remover)

    resultado = malha.merge(
        pop[["CD_DIST", "POP_2022"]],
        on="CD_DIST",
        how="left",
        validate="one_to_one",
    )

    resultado = gpd.GeoDataFrame(
        resultado,
        geometry="geometry",
        crs=malha.crs,
    )

    sem_pop = resultado["POP_2022"].isna()

    print(
        f"\nCruzamento concluído: {len(resultado):,} distritos".replace(",", ".")
    )
    print(
        f"Distritos sem população: {int(sem_pop.sum()):,}".replace(",", ".")
    )

    if sem_pop.any():
        colunas_problemas = [
            coluna
            for coluna in (
                "CD_UF",
                "NM_UF",
                "CD_MUN",
                "NM_MUN",
                "CD_DIST",
                "NM_DIST",
            )
            if coluna in resultado.columns
        ]

        print("\nExemplos de distritos sem correspondência populacional:")
        print(
            resultado.loc[sem_pop, colunas_problemas]
            .head(30)
            .to_string(index=False)
        )

    # Ponto representativo interno ao polígono.
    resultado_metrico = resultado.to_crs(CRS_METRICO_BRASIL)
    pontos_metricos = resultado_metrico.geometry.representative_point()

    pontos_wgs84 = gpd.GeoSeries(
        pontos_metricos,
        index=resultado.index,
        crs=resultado_metrico.crs,
    ).to_crs(epsg=4326)

    resultado["LON"] = pontos_wgs84.x
    resultado["LAT"] = pontos_wgs84.y

    print("\nGravando GeoPackage nacional...")

    # Remove arquivo anterior para evitar conflitos de camada/esquema.
    SAIDA_GPKG.unlink(missing_ok=True)

    try:
        resultado.to_file(
            SAIDA_GPKG,
            layer="distritos_pop_2022",
            driver="GPKG",
            engine="pyogrio",
        )
    except (ImportError, ModuleNotFoundError):
        resultado.to_file(
            SAIDA_GPKG,
            layer="distritos_pop_2022",
            driver="GPKG",
        )

    colunas_csv = [
        coluna
        for coluna in (
            "CD_REGIAO",
            "NM_REGIAO",
            "CD_UF",
            "NM_UF",
            "CD_MUN",
            "NM_MUN",
            "CD_DIST",
            "NM_DIST",
            "POP_2022",
            "AREA_KM2_CALCULADA",
            "LAT",
            "LON",
        )
        if coluna in resultado.columns
    ]

    resultado[colunas_csv].to_csv(
        SAIDA_GEO_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"GeoPackage salvo em: {SAIDA_GPKG}")
    print(f"CSV com latitude/longitude salvo em: {SAIDA_GEO_CSV}")

    return resultado


# ============================================================
# 6. VALIDAÇÕES E AMOSTRAS
# ============================================================


def validar_resultado_populacional(pop: pd.DataFrame) -> None:
    print("\nValidação do resultado populacional")

    if pop.empty:
        raise ValueError("A base populacional ficou vazia.")

    if pop["CD_DIST"].duplicated().any():
        raise ValueError("Existem CD_DIST duplicados na base populacional final.")

    if pop["POP_2022"].isna().all():
        raise ValueError("Nenhum valor de POP_2022 foi carregado.")

    if "CD_MUN" in pop.columns:
        sp = pop.loc[pop["CD_MUN"] == "3550308"].copy()

        print(f"  Distritos do município de São Paulo: {len(sp)}")
        print(
            "  Soma populacional dos distritos de São Paulo: "
            f"{sp['POP_2022'].sum():,.0f}".replace(",", ".")
        )

        if "NM_DIST" in sp.columns:
            nomes_interesse = {
                "TUCURUVI",
                "GRAJAÚ",
                "JAÇANÃ",
                "TATUAPÉ",
            }

            amostra = sp.loc[
                sp["NM_DIST"].fillna("").str.upper().isin(nomes_interesse)
            ]

            if not amostra.empty:
                print("\n  Amostra de distritos paulistanos:")
                print(
                    amostra[["CD_DIST", "NM_DIST", "POP_2022"]]
                    .sort_values("NM_DIST")
                    .to_string(index=False)
                )


def validar_resultado_geografico(resultado: gpd.GeoDataFrame) -> None:
    print("\nValidação do resultado geográfico")

    if resultado.empty:
        raise ValueError("O resultado geográfico ficou vazio.")

    duplicados = int(resultado["CD_DIST"].duplicated().sum())
    sem_geometria = int(
        (resultado.geometry.isna() | resultado.geometry.is_empty).sum()
    )

    print(f"  Linhas finais: {len(resultado):,}".replace(",", "."))
    print(f"  CD_DIST duplicados: {duplicados:,}".replace(",", "."))
    print(f"  Geometrias ausentes/vazias: {sem_geometria:,}".replace(",", "."))
    print(
        f"  População ausente: {resultado['POP_2022'].isna().sum():,}".replace(
            ",", "."
        )
    )

    if duplicados:
        raise ValueError("O resultado final ainda possui CD_DIST duplicado.")


# ============================================================
# 7. EXECUÇÃO PRINCIPAL
# ============================================================


def main() -> tuple[pd.DataFrame, gpd.GeoDataFrame | None]:
    criar_pastas()

    # --------------------------------------------------------
    # População nacional por distrito
    # --------------------------------------------------------
    url_pop = descobrir_url_populacao()
    nome_zip_pop = Path(urlparse(url_pop).path).name

    zip_pop = baixar_arquivo(
        url_pop,
        PASTA_DOWNLOADS / nome_zip_pop,
        forcar=FORCAR_DOWNLOAD,
    )

    csv_pop = extrair_csv_basico(zip_pop)
    pop = preparar_populacao(csv_pop)
    validar_resultado_populacional(pop)

    # --------------------------------------------------------
    # Geometria nacional, opcional
    # --------------------------------------------------------
    resultado_geo: gpd.GeoDataFrame | None = None

    if GERAR_GEOMETRIA:
        gpkg_malha = baixar_arquivo(
            URL_MALHA_IBGE,
            PASTA_DOWNLOADS / "BR_distritos_CD2022.gpkg",
            forcar=FORCAR_DOWNLOAD,
        )

        malha = ler_malha_nacional(gpkg_malha)
        resultado_geo = juntar_populacao_geometria(malha, pop)
        validar_resultado_geografico(resultado_geo)

    else:
        print(
            "\nGERAR_GEOMETRIA=False: apenas o CSV populacional foi produzido."
        )

    print("\nProcessamento concluído.")
    print(f"População por distrito: {SAIDA_POP_CSV}")

    if resultado_geo is not None:
        print(f"CSV geográfico: {SAIDA_GEO_CSV}")
        print(f"GeoPackage: {SAIDA_GPKG}")

    return pop, resultado_geo


if __name__ == "__main__":
    populacao_distritos, distritos_geo = main()
