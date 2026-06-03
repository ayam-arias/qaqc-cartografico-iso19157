QA/QC Cartográfico de Geodatabases — ISO 19157:2013
Script de auditoría programática para geodatabases de planimetría urbana, estructurado sobre las tres dimensiones de calidad del dato geoespacial definidas en ISO 19157:2013.
Desarrollado en el contexto del Servicio Aerofotogramétrico (SAF) de la Fuerza Aérea de Chile, como complemento a los controles de calidad nativos de ArcGIS Pro.
---
Motivación
Las reglas topológicas estándar del SIG desktop no detectan todos los tipos de errores relevantes en una geodatabase. En particular:
Duplicados exactos de integridad (misma geometría + mismos atributos) pasan inadvertidos sin la regla específica configurada.
Capas estructuralmente vacías aparecen en el inventario pero no generan alerta automática.
Nulos semánticos (strings vacíos equivalentes a ausencia de dato) no son capturados por validaciones básicas de atributos.
Este script opera directamente sobre el dato con Python, entregando OBJECTID exactos y coordenadas de error para corrección directa en el SIG.
---
Dimensiones evaluadas (ISO 19157:2013)
Dimensión	Elementos evaluados
Completitud	Capas vacías, geometrías nulas, geometrías vacías
Consistencia Lógica	Duplicados exactos, Ring Self-intersection, pseudonodos, dangles residuales
Exactitud Temática	Campos requeridos ausentes, valores nulos, vacíos semánticos
---
Instalación
```bash
pip install geopandas fiona shapely pandas
```
Python 3.9+ recomendado. Probado con:
`geopandas` 0.14+
`fiona` 1.9+
`shapely` 2.0+
---
Uso
```bash
# Evaluar toda la GDB con umbral por defecto (95%)
python src/qaqc_geodatabase.py --gdb ruta/a/planimetria.gdb

# Evaluar solo una capa
python src/qaqc_geodatabase.py --gdb planimetria.gdb --capa VIALIDAD_L

# Cambiar umbral de aceptación y nombre del reporte
python src/qaqc_geodatabase.py --gdb planimetria.gdb --umbral 98 --output reporte_junio.json
```
---
Output
El script genera dos salidas:
1. Resumen en consola
```
========================================================================
  QA/QC CARTOGRÁFICO — ISO 19157:2013
  GDB: planimetria.gdb
  Fecha: 2025-06-02
========================================================================

  INVENTARIO
  Total capas   : 21
  Capas vacías  : 1 → ['TOPONIMIA_P']
  Total entidades evaluadas: 4.832

------------------------------------------------------------------------
  SCORES POR DIMENSIÓN ISO 19157
------------------------------------------------------------------------
  [✓] Completitud                    97.12%
  [✓] Consistencia Lógica            95.43%
  [✓] Exactitud Temática             98.20%
------------------------------------------------------------------------
  SCORE GLOBAL   : 96.92%
  UMBRAL         : 95.0%
  VEREDICTO      : APROBADO
------------------------------------------------------------------------

  CORRECCIONES REQUERIDAS ANTES DE ENTREGA FORMAL: 4
  [1] [CRÍTICA] Completitud
      Capa 'TOPONIMIA_P' entregada vacía — sin entidades
      → Verificar proceso de exportación o fuente de datos original
  ...
```
2. Reporte JSON estructurado (`reporte_qaqc.json`)
Contiene el detalle completo por capa, con OBJECTID de cada error para corrección directa en ArcGIS Pro.
---
Configuración de atributos requeridos
Editar el diccionario `ATRIBUTOS_REQUERIDOS` en el script para adaptar al esquema institucional:
```python
ATRIBUTOS_REQUERIDOS = {
    "VIALIDAD_L"    : ["NOMBRE_VIA", "TIPO_VIA", "COD_COMUNA"],
    "EDIFICACION_P" : ["USO", "PISOS", "COD_MANZANA"],
    # agregar capas según esquema del proyecto
}
```
---
Arquitectura del script
```
qaqc_geodatabase.py
│
├── inventario_gdb()              → Lista capas y detecta vacías
│
├── evaluar_completitud()         → Módulo ISO 19157 §4.15–§4.16
│
├── evaluar_consistencia_logica() → Módulo ISO 19157 §4.13
│   ├── detectar_duplicados_exactos()
│   └── detectar_errores_topologicos()
│
├── evaluar_exactitud_tematica()  → Módulo ISO 19157 §4.25
│
└── generar_reporte()             → Consolidación + veredicto + correcciones
```
---
Referencia normativa
ISO 19157:2013 — Geographic information — Data quality
ASPRS 2015 — Accuracy Standards for Digital Geospatial Data
IGM Chile — Especificaciones técnicas para productos cartográficos nacionales
---
Autor
Ian Franco Arias Carrasco  
Analista Geoespacial — Sección IDE  
Servicio Aerofotogramétrico (SAF), Fuerza Aérea de Chile  
linkedin.com/in/ian-arias
---
Licencia
MIT — Libre uso y distribución con atribución.
