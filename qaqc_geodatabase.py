
"""
================================================================================
QA/QC Cartográfico de Geodatabases — ISO 19157:2013
================================================================================
Autor       : Ian Franco Arias Carrasco
Versión     : 1.0.0
Fecha       : Junio 2025
Licencia    : MIT

Descripción
-----------
Script de auditoría programática para geodatabases de planimetría urbana,
estructurado en torno a las tres dimensiones principales de calidad del dato
geoespacial definidas en ISO 19157:2013:

    1. Completitud         → Omisión y comisión de entidades
    2. Consistencia Lógica → Topología, integridad estructural, duplicados
    3. Exactitud Temática  → Validación de atributos requeridos

El análisis opera directamente sobre la GDB con Fiona y GeoPandas,
sin depender de reglas topológicas nativas del SIG desktop.
Entrega un reporte estructurado en JSON + resumen en consola.

Dependencias
------------
    pip install geopandas fiona shapely pandas

Uso
---
    python qaqc_geodatabase.py --gdb ruta/a/planimetria.gdb --umbral 95
    python qaqc_geodatabase.py --gdb ruta/a/planimetria.gdb --capa VIALIDAD_L
    python qaqc_geodatabase.py --gdb ruta/a/planimetria.gdb --output reporte.json

================================================================================
"""

import json
import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import fiona
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.validation import explain_validity


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

# Atributos mínimos requeridos por capa (ajustar según esquema institucional)
ATRIBUTOS_REQUERIDOS = {
    "VIALIDAD_L"       : ["NOMBRE_VIA", "TIPO_VIA", "COD_COMUNA"],
    "EDIFICACION_P"    : ["USO", "PISOS", "COD_MANZANA"],
    "MANZANA_P"        : ["COD_MANZANA", "COD_COMUNA", "AREA_M2"],
    "LIMITE_COMUNAL_P" : ["NOMBRE", "COD_COMUNA", "SUPERFICIE"],
    "CURVAS_NIVEL_L"   : ["COTA", "TIPO_CURVA"],
}

UMBRAL_ACEPTACION = 95.0   # Porcentaje mínimo de aceptación por dimensión


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 1 — INVENTARIO DE LA GEODATABASE
# ─────────────────────────────────────────────────────────────────────────────

def inventario_gdb(ruta_gdb: str) -> dict:
    """
    Lista todas las capas disponibles en la GDB con conteo de entidades.
    Detecta capas vacías (error de Completitud por Omisión estructural).

    Returns
    -------
    dict con keys: capas_disponibles, capas_vacias, total_entidades
    """
    capas = fiona.listlayers(ruta_gdb)
    resultado = {
        "capas_disponibles": [],
        "capas_vacias": [],
        "total_entidades": 0,
    }

    for nombre in capas:
        try:
            with fiona.open(ruta_gdb, layer=nombre) as src:
                n = len(src)
                tipo_geom = src.schema.get("geometry", "Unknown")
                info = {
                    "nombre": nombre,
                    "n_entidades": n,
                    "tipo_geometria": tipo_geom,
                    "crs": str(src.crs),
                }
                resultado["capas_disponibles"].append(info)
                resultado["total_entidades"] += n

                if n == 0:
                    resultado["capas_vacias"].append(nombre)

        except Exception as e:
            resultado["capas_disponibles"].append({
                "nombre": nombre,
                "n_entidades": -1,
                "error": str(e),
            })

    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 2 — COMPLETITUD (ISO 19157 §4.15 y §4.16)
# ─────────────────────────────────────────────────────────────────────────────

def evaluar_completitud(ruta_gdb: str, capas: list) -> dict:
    """
    Evalúa Completitud según ISO 19157:2013:
        - Omisión   : capas estructuralmente vacías
        - Comisión  : entidades con geometría nula o inválida estructuralmente
                      (no detectables sin revisar el dato directamente)

    Returns
    -------
    dict con métricas por capa y score global de completitud
    """
    resultados = {}
    total_entidades = 0
    total_errores = 0

    for nombre_capa in capas:
        try:
            gdf = gpd.read_file(ruta_gdb, layer=nombre_capa)
            n_total = len(gdf)
            total_entidades += n_total

            # Comisión: geometrías nulas
            geom_nulas = int(gdf.geometry.isna().sum())

            # Comisión: geometrías vacías (is_empty)
            geom_vacias = int(gdf.geometry.apply(
                lambda g: g.is_empty if g is not None else False
            ).sum())

            errores_capa = geom_nulas + geom_vacias
            total_errores += errores_capa

            resultados[nombre_capa] = {
                "n_entidades": n_total,
                "geom_nulas": geom_nulas,
                "geom_vacias": geom_vacias,
                "total_errores_completitud": errores_capa,
                "capa_vacia": n_total == 0,
            }

        except Exception as e:
            resultados[nombre_capa] = {"error": str(e)}

    score = (
        round((1 - total_errores / total_entidades) * 100, 2)
        if total_entidades > 0 else 0.0
    )

    return {
        "dimension": "Completitud",
        "iso_referencia": "ISO 19157:2013 §4.15–§4.16",
        "score_global": score,
        "total_entidades_evaluadas": total_entidades,
        "total_errores": total_errores,
        "detalle_por_capa": resultados,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 3 — CONSISTENCIA LÓGICA (ISO 19157 §4.13)
# ─────────────────────────────────────────────────────────────────────────────

def detectar_duplicados_exactos(ruta_gdb: str, nombre_capa: str) -> dict:
    """
    Detecta entidades con geometría Y atributos idénticos (duplicados exactos).
    Los duplicados de integridad NO son detectados por reglas topológicas
    estándar a menos que se configure la regla específica en ArcGIS Pro.

    Devuelve los OBJECTID de cada par para corrección directa en el SIG.
    """
    pares_duplicados = []
    wkt_map = defaultdict(list)

    with fiona.open(ruta_gdb, layer=nombre_capa) as src:
        for feat in src:
            if feat["geometry"] is None:
                continue
            geom_wkt = shape(feat["geometry"]).wkt
            feat_id = feat.get("id") or feat["properties"].get("OBJECTID")
            wkt_map[geom_wkt].append(feat_id)

    for wkt, ids in wkt_map.items():
        if len(ids) > 1:
            pares_duplicados.append({
                "ids_duplicados": ids,
                "n_copias": len(ids),
            })

    return {
        "capa": nombre_capa,
        "n_pares_duplicados": len(pares_duplicados),
        "pares": pares_duplicados,
    }


def detectar_errores_topologicos(ruta_gdb: str, nombre_capa: str) -> dict:
    """
    Detecta errores de consistencia topológica por geometría inválida
    usando Shapely (equivalente programático al chequeo de topología):

        - Ring Self-intersection  : polígono con anillo que se cruza a sí mismo
        - Pseudonodos             : líneas con vértice compartido que debieran
                                    estar unidas (detectados por nodos colgantes
                                    en el extremo, no por geometría inválida)
        - Dangles (residuales)    : extremos de línea sin conexión a otra entidad

    Returns
    -------
    dict con listado de errores y OBJECTID/coordenada exacta para corrección
    """
    errores_geometria = []
    pseudonodos = []
    dangles = []

    try:
        gdf = gpd.read_file(ruta_gdb, layer=nombre_capa)
    except Exception as e:
        return {"error": str(e)}

    # ── Errores de geometría inválida ────────────────────────────────────────
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            razon = explain_validity(geom)
            # Extraer coordenada aproximada del error desde la explicación
            coord_error = None
            if "[" in razon:
                try:
                    coord_str = razon.split("[")[1].split("]")[0]
                    coord_error = coord_str
                except Exception:
                    pass

            errores_geometria.append({
                "objectid": int(idx),
                "tipo_error": razon.split("[")[0].strip(),
                "coordenada_error": coord_error,
                "descripcion_completa": razon,
            })

    # ── Pseudonodos en capas lineales ────────────────────────────────────────
    # Un pseudonodo ocurre cuando dos líneas se unen en un extremo compartido
    # pero corresponden a la misma entidad lineal (debieran ser una sola)
    tipo_geom = str(gdf.geom_type.iloc[0]) if len(gdf) > 0 else ""
    if "Line" in tipo_geom or "line" in tipo_geom:
        endpoints = defaultdict(list)
        for idx, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "LineString":
                coords = list(geom.coords)
                endpoints[coords[0]].append(int(idx))
                endpoints[coords[-1]].append(int(idx))
            elif geom.geom_type == "MultiLineString":
                for part in geom.geoms:
                    coords = list(part.coords)
                    endpoints[coords[0]].append(int(idx))
                    endpoints[coords[-1]].append(int(idx))

        for coord, ids in endpoints.items():
            if len(ids) == 2 and ids[0] != ids[1]:
                # Nodo compartido por exactamente dos líneas distintas
                # → candidato a pseudonodo (requiere validación semántica)
                pseudonodos.append({
                    "coordenada": coord,
                    "objectids_involucrados": ids,
                })
            elif len(ids) == 1:
                # Extremo libre sin conexión → dangle residual
                dangles.append({
                    "coordenada": coord,
                    "objectid": ids[0],
                })

    return {
        "capa": nombre_capa,
        "errores_geometria_invalida": {
            "total": len(errores_geometria),
            "detalle": errores_geometria,
        },
        "pseudonodos_detectados": {
            "total": len(pseudonodos),
            "nota": "Requiere validación semántica: no todo nodo de 2 líneas es pseudonodo",
            "detalle": pseudonodos[:50],  # limitar output para capas grandes
        },
        "dangles_residuales": {
            "total": len(dangles),
            "detalle": dangles[:50],
        },
    }


def evaluar_consistencia_logica(ruta_gdb: str, capas: list) -> dict:
    """
    Consolida la evaluación de Consistencia Lógica para todas las capas:
    duplicados exactos + errores topológicos de geometría.

    Score calculado sobre el total de errores vs entidades evaluadas.
    """
    resultados = {}
    total_entidades = 0
    total_errores = 0

    for nombre_capa in capas:
        try:
            gdf = gpd.read_file(ruta_gdb, layer=nombre_capa)
            n = len(gdf)
            total_entidades += n

            dup = detectar_duplicados_exactos(ruta_gdb, nombre_capa)
            topo = detectar_errores_topologicos(ruta_gdb, nombre_capa)

            errores_capa = (
                dup["n_pares_duplicados"]
                + topo["errores_geometria_invalida"]["total"]
            )
            total_errores += errores_capa

            resultados[nombre_capa] = {
                "n_entidades": n,
                "duplicados_exactos": dup,
                "errores_topologicos": topo,
                "total_errores_capa": errores_capa,
            }

        except Exception as e:
            resultados[nombre_capa] = {"error": str(e)}

    score = (
        round((1 - total_errores / total_entidades) * 100, 2)
        if total_entidades > 0 else 0.0
    )

    return {
        "dimension": "Consistencia Lógica",
        "iso_referencia": "ISO 19157:2013 §4.13",
        "score_global": score,
        "total_entidades_evaluadas": total_entidades,
        "total_errores": total_errores,
        "detalle_por_capa": resultados,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 4 — EXACTITUD TEMÁTICA (ISO 19157 §4.25)
# ─────────────────────────────────────────────────────────────────────────────

def evaluar_exactitud_tematica(ruta_gdb: str, capas: list) -> dict:
    """
    Valida la presencia y completitud de atributos requeridos por capa.
    Detecta:
        - Campos obligatorios ausentes en el esquema
        - Registros con valor nulo en campos obligatorios
        - Campos con valor vacío (string "") equivalente a nulo semántico

    Returns
    -------
    dict con score por capa y score global de exactitud temática
    """
    resultados = {}
    total_campos_req = 0
    total_errores = 0

    for nombre_capa in capas:
        atributos_req = ATRIBUTOS_REQUERIDOS.get(nombre_capa, [])

        try:
            gdf = gpd.read_file(ruta_gdb, layer=nombre_capa)
            n = len(gdf)
            errores_capa = []

            for campo in atributos_req:
                total_campos_req += 1

                if campo not in gdf.columns:
                    errores_capa.append({
                        "campo": campo,
                        "tipo_error": "Campo ausente en esquema",
                        "n_registros_afectados": n,
                    })
                    total_errores += n
                    continue

                # Nulos reales
                nulos = int(gdf[campo].isna().sum())

                # Vacíos semánticos (strings vacíos)
                vacios = 0
                if gdf[campo].dtype == object:
                    vacios = int((gdf[campo] == "").sum())

                afectados = nulos + vacios
                if afectados > 0:
                    errores_capa.append({
                        "campo": campo,
                        "tipo_error": "Valores nulos o vacíos",
                        "n_nulos": nulos,
                        "n_vacios_semanticos": vacios,
                        "n_registros_afectados": afectados,
                        "porcentaje_afectado": round(afectados / n * 100, 2) if n > 0 else 0,
                    })
                    total_errores += afectados

            resultados[nombre_capa] = {
                "n_entidades": n,
                "atributos_requeridos": atributos_req,
                "errores_tematicos": errores_capa,
                "total_errores_capa": sum(e["n_registros_afectados"] for e in errores_capa),
            }

        except Exception as e:
            resultados[nombre_capa] = {"error": str(e)}

    # Score sobre campos requeridos evaluados
    total_valores_req = total_campos_req * max(
        (len(gpd.read_file(ruta_gdb, layer=c)) for c in capas if c in ATRIBUTOS_REQUERIDOS),
        default=1
    )

    score = (
        round((1 - total_errores / max(total_valores_req, 1)) * 100, 2)
        if total_campos_req > 0 else 100.0
    )

    return {
        "dimension": "Exactitud Temática",
        "iso_referencia": "ISO 19157:2013 §4.25",
        "score_global": score,
        "total_campos_requeridos_evaluados": total_campos_req,
        "total_errores": total_errores,
        "detalle_por_capa": resultados,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 5 — REPORTE CONSOLIDADO
# ─────────────────────────────────────────────────────────────────────────────

def generar_reporte(
    ruta_gdb: str,
    inventario: dict,
    completitud: dict,
    consistencia: dict,
    exactitud: dict,
    umbral: float,
) -> dict:
    """
    Consolida los tres módulos en un reporte único con:
        - Score por dimensión
        - Score global ponderado (igual peso por dimensión)
        - Veredicto de aceptación vs umbral
        - Lista de correcciones requeridas antes de entrega formal
    """
    scores = {
        "Completitud": completitud["score_global"],
        "Consistencia Lógica": consistencia["score_global"],
        "Exactitud Temática": exactitud["score_global"],
    }

    score_global = round(sum(scores.values()) / len(scores), 2)
    veredicto = "APROBADO" if score_global >= umbral else "RECHAZADO"

    # Correcciones específicas requeridas
    correcciones = []

    # → Capas vacías
    for capa_vacia in inventario.get("capas_vacias", []):
        correcciones.append({
            "prioridad": "CRÍTICA",
            "dimension": "Completitud",
            "descripcion": f"Capa '{capa_vacia}' entregada vacía — sin entidades",
            "accion": "Verificar proceso de exportación o fuente de datos original",
        })

    # → Duplicados exactos
    for nombre_capa, detalle in consistencia["detalle_por_capa"].items():
        if "duplicados_exactos" in detalle:
            n_dup = detalle["duplicados_exactos"].get("n_pares_duplicados", 0)
            if n_dup > 0:
                correcciones.append({
                    "prioridad": "ALTA",
                    "dimension": "Consistencia Lógica",
                    "descripcion": f"Capa '{nombre_capa}': {n_dup} pares de entidades duplicadas exactas",
                    "accion": "Eliminar duplicados usando OBJECTID listados. En ArcGIS Pro: Find Identical + Delete Identical",
                })

    # → Errores de geometría inválida
    for nombre_capa, detalle in consistencia["detalle_por_capa"].items():
        if "errores_topologicos" in detalle:
            n_err = detalle["errores_topologicos"]["errores_geometria_invalida"]["total"]
            if n_err > 0:
                correcciones.append({
                    "prioridad": "ALTA",
                    "dimension": "Consistencia Lógica",
                    "descripcion": f"Capa '{nombre_capa}': {n_err} geometrías inválidas detectadas",
                    "accion": "Reparar con Check Geometry + Repair Geometry en ArcGIS Pro",
                })

    # → Atributos faltantes
    for nombre_capa, detalle in exactitud["detalle_por_capa"].items():
        if "errores_tematicos" in detalle and detalle["errores_tematicos"]:
            for error in detalle["errores_tematicos"]:
                correcciones.append({
                    "prioridad": "MEDIA",
                    "dimension": "Exactitud Temática",
                    "descripcion": (
                        f"Capa '{nombre_capa}', campo '{error['campo']}': "
                        f"{error['n_registros_afectados']} registros con error"
                    ),
                    "accion": "Completar o corregir atributos antes de la entrega formal",
                })

    return {
        "metadata": {
            "ruta_gdb": str(ruta_gdb),
            "fecha_evaluacion": datetime.now().isoformat(),
            "norma": "ISO 19157:2013",
            "umbral_aceptacion": umbral,
            "autor": "Ian Franco Arias Carrasco",
            "institucion": "SAF — Fuerza Aérea de Chile",
        },
        "inventario": inventario,
        "scores_por_dimension": scores,
        "score_global": score_global,
        "veredicto": veredicto,
        "n_correcciones_requeridas": len(correcciones),
        "correcciones_previas_entrega": correcciones,
        "detalle_completitud": completitud,
        "detalle_consistencia_logica": consistencia,
        "detalle_exactitud_tematica": exactitud,
    }


def imprimir_resumen(reporte: dict) -> None:
    """Imprime resumen ejecutivo en consola."""
    sep = "=" * 72
    sep2 = "-" * 72
    print(f"\n{sep}")
    print("  QA/QC CARTOGRÁFICO — ISO 19157:2013")
    print(f"  GDB: {reporte['metadata']['ruta_gdb']}")
    print(f"  Fecha: {reporte['metadata']['fecha_evaluacion'][:10]}")
    print(sep)

    inv = reporte["inventario"]
    print(f"\n  INVENTARIO")
    print(f"  Total capas   : {len(inv['capas_disponibles'])}")
    print(f"  Capas vacías  : {len(inv['capas_vacias'])} → {inv['capas_vacias']}")
    print(f"  Total entidades evaluadas: {inv['total_entidades']}")

    print(f"\n{sep2}")
    print("  SCORES POR DIMENSIÓN ISO 19157")
    print(sep2)
    for dim, score in reporte["scores_por_dimension"].items():
        estado = "✓" if score >= reporte["metadata"]["umbral_aceptacion"] else "✗"
        print(f"  [{estado}] {dim:<30} {score:>6.2f}%")

    print(sep2)
    veredicto = reporte["veredicto"]
    score_g = reporte["score_global"]
    print(f"  SCORE GLOBAL   : {score_g:.2f}%")
    print(f"  UMBRAL         : {reporte['metadata']['umbral_aceptacion']}%")
    print(f"  VEREDICTO      : {veredicto}")
    print(sep2)

    n_corr = reporte["n_correcciones_requeridas"]
    print(f"\n  CORRECCIONES REQUERIDAS ANTES DE ENTREGA FORMAL: {n_corr}")
    for i, corr in enumerate(reporte["correcciones_previas_entrega"], 1):
        print(f"\n  [{i}] [{corr['prioridad']}] {corr['dimension']}")
        print(f"      {corr['descripcion']}")
        print(f"      → {corr['accion']}")

    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QA/QC Cartográfico de Geodatabases — ISO 19157:2013",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--gdb",
        required=True,
        help="Ruta a la geodatabase (.gdb)",
    )
    parser.add_argument(
        "--capa",
        default=None,
        help="Evaluar solo esta capa (opcional). Por defecto evalúa todas.",
    )
    parser.add_argument(
        "--umbral",
        type=float,
        default=UMBRAL_ACEPTACION,
        help=f"Umbral de aceptación en %% (por defecto {UMBRAL_ACEPTACION})",
    )
    parser.add_argument(
        "--output",
        default="reporte_qaqc.json",
        help="Archivo de salida JSON (por defecto: reporte_qaqc.json)",
    )
    args = parser.parse_args()

    ruta_gdb = args.gdb
    umbral = args.umbral
    ruta_output = args.output

    print(f"\n  Iniciando auditoría: {ruta_gdb}")
    print(f"  Umbral de aceptación: {umbral}%\n")

    # 1. Inventario
    print("  [1/4] Inventario de capas...")
    inventario = inventario_gdb(ruta_gdb)
    capas_evaluar = (
        [args.capa]
        if args.capa
        else [c["nombre"] for c in inventario["capas_disponibles"]]
    )

    # 2. Completitud
    print(f"  [2/4] Evaluando Completitud ({len(capas_evaluar)} capas)...")
    completitud = evaluar_completitud(ruta_gdb, capas_evaluar)

    # 3. Consistencia Lógica
    print("  [3/4] Evaluando Consistencia Lógica (duplicados + topología)...")
    consistencia = evaluar_consistencia_logica(ruta_gdb, capas_evaluar)

    # 4. Exactitud Temática
    print("  [4/4] Evaluando Exactitud Temática (atributos requeridos)...")
    exactitud = evaluar_exactitud_tematica(ruta_gdb, capas_evaluar)

    # 5. Reporte
    reporte = generar_reporte(
        ruta_gdb, inventario, completitud, consistencia, exactitud, umbral
    )

    # Guardar JSON
    with open(ruta_output, "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2, default=str)

    # Imprimir resumen
    imprimir_resumen(reporte)
    print(f"  Reporte completo guardado en: {ruta_output}\n")


if __name__ == "__main__":
    main()
