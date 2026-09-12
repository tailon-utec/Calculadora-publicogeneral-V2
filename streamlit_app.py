"""
Herramienta Computacional para la Cuantificación de Emisiones de Gases de
Efecto Invernadero en Operaciones Unimodales y Cadenas Multimodales de Transporte de Cargas en Uruguay

Arquitectura metodológica
ISO 14083:2023
Marco GLEC para la Contabilización y el Reporte de Emisiones Logísticas v3.2
Directrices IPCC 2006 como referencia complementaria para los niveles metodológicos

Frontera del sistema
Se cuantifican exclusivamente los segmentos de transporte. Puertos, aeropuertos,
terminales ferroviarias y otros nodos logísticos se registran como puntos de conexión,
pero sus consumos energéticos y emisiones quedan fuera de la frontera cuantitativa.

Jerarquía de factores
1. Datos primarios de consumo cuando estén disponibles.
2. Factores nacionales de Uruguay cuando sean compatibles con la frontera seleccionada.
3. Intensidades y factores internacionales por defecto del Marco GLEC v3.2 cuando no existan datos primarios o nacionales suficientes.
4. Factores documentados cargados por el usuario o ingresados manualmente.

Nota científica
Los perfiles nacionales integrados no representan una intensidad universal por tkm.
Se calculan a partir del consumo energético y de factores de combustión documentados.
Los perfiles GLEC se identifican explícitamente como referencia internacional y no se
presentan como factores nacionales uruguayos. Esta versión cuantifica exclusivamente
emisiones operativas del transporte. El suministro previo de la energía queda fuera de la frontera cuantitativa.
"""

from __future__ import annotations

import io
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    import requests
    REQUESTS_DISPONIBLE = True
except ModuleNotFoundError:
    requests = None
    REQUESTS_DISPONIBLE = False

try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    MATPLOTLIB_DISPONIBLE = True
except ModuleNotFoundError:
    plt = None
    PdfPages = None
    MATPLOTLIB_DISPONIBLE = False


# =============================================================================
# CONFIGURACIÓN GENERAL
# =============================================================================

APP_TITLE = (
    "Herramienta Computacional para la Cuantificación de Emisiones de GEI "
    "en Operaciones Unimodales y Cadenas Multimodales de Transporte de Cargas en Uruguay"
)
APP_SHORT = "Emisiones GEI del transporte de cargas"
METODOLOGIA = "Referencias: ISO 14083:2023 | Marco GLEC v3.2 | IPCC 2006 | Frontera operacional"
VERSION = "0.9.0"

MODOS = ["Carretero", "Ferroviario", "Marítimo", "Fluvial", "Aéreo"]
METODOS_CALCULO = ["Intensidad de emisión", "Consumo energético"]
CLASES_DATO = ["Primario", "Modelado", "Predeterminado"]
TIERS = ["No aplica", "Tier 1", "Tier 2", "Tier 3"]
ETIQUETAS_NIVEL_IPCC = {
    "No aplica": "No corresponde",
    "Tier 1": "Nivel 1",
    "Tier 2": "Nivel 2",
    "Tier 3": "Nivel 3",
}
ETIQUETAS_CLASE_DATO = {
    "Primario": "Primario",
    "Modelado": "Modelado",
    "Predeterminado": "Por defecto",
}
METODOS_DISTANCIA = [
    "Real u operacional — recorrido efectivamente realizado",
    "SFD — menor distancia factible",
    "GCD — distancia geodésica entre origen y destino",
    "Modelada — distancia estimada mediante un modelo",
]
MODOS_INGRESO_DISTANCIA = ["Calcular automáticamente", "Ingresar manualmente"]
UNIDADES_DISTANCIA = {
    "km — kilómetros": 1.0,
    "mi — millas terrestres": 1.609344,
    "nmi — millas náuticas": 1.852,
}
FORMAS_CONSUMO_LIQUIDO = [
    "Litros totales consumidos",
    "Consumo medio en L/100 km",
    "Rendimiento en km/L",
]
FORMAS_CONSUMO_GENERAL = [
    "Consumo total del tramo",
    "Consumo por 100 km",
    "Consumo por km",
]
UNIDADES_ENERGIA = ["L", "kg", "kWh", "MJ", "GJ"]
UNIDADES_CARGA = [
    "Carga suelta o granel",
    "Palé",
    "Contenedor ISO",
    "ULD aéreo",
    "Otra unidad logística",
]

TIPOS_CONTENEDOR_TEU = {
    "20 pies estándar o High Cube": 1.0,
    "40 pies estándar": 2.0,
    "40 pies High Cube": 2.25,
}


# =============================================================================
# FACTORES INTEGRADOS Y PARÁMETROS DE CONVERSIÓN
# =============================================================================
# Los perfiles "Uruguay" representan emisiones operacionales TTW.
# Se construyen con factores utilizados por Uruguay en el inventario del sector
# Transporte y con poderes caloríficos inferiores nacionales.
#
# Fórmula de conversión a kg CO2e por litro:
# EF_TTW,L = PCI [tep/m3] * 41.868 [MJ/L por tep/m3]
#            * (FE_CO2 + FE_CH4*GWP_CH4 + FE_N2O*GWP_N2O) / 1e6
#
# Los perfiles GLEC v3.2 se utilizan únicamente en su componente operacional TTW.
# El componente de suministro de energía no se cuantifica en esta versión.

TEP_A_GJ = 41.868
GWP_CH4_AR5 = 28.0
GWP_N2O_AR5 = 265.0


def _ef_ttw_kgco2e_litro(
    pci_tep_m3: float,
    ef_co2_kg_tj: float,
    ef_ch4_kg_tj: float,
    ef_n2o_kg_tj: float,
) -> float:
    energia_mj_l = pci_tep_m3 * TEP_A_GJ
    fe_co2e_kg_tj = (
        ef_co2_kg_tj
        + ef_ch4_kg_tj * GWP_CH4_AR5
        + ef_n2o_kg_tj * GWP_N2O_AR5
    )
    return energia_mj_l * fe_co2e_kg_tj / 1_000_000.0


PCI_GASOIL_10S_2022 = 0.8521
PCI_GASOIL_MARINO_2022 = 0.8724
PCI_FUELOIL_PESADO_2022 = 0.9257
PCI_TURBOCOMBUSTIBLE_2022 = 0.8304

EF_UY_CARRETERO_GASOIL_TTW_L = _ef_ttw_kgco2e_litro(
    PCI_GASOIL_10S_2022, 74100.0, 4.0, 4.0
)
EF_UY_FERROVIARIO_GASOIL_TTW_L = _ef_ttw_kgco2e_litro(
    PCI_GASOIL_10S_2022, 74100.0, 4.0, 29.0
)
EF_UY_NAVEGACION_GASOIL_TTW_L = _ef_ttw_kgco2e_litro(
    PCI_GASOIL_MARINO_2022, 74100.0, 7.0, 2.0
)
EF_UY_NAVEGACION_FUELOIL_TTW_L = _ef_ttw_kgco2e_litro(
    PCI_FUELOIL_PESADO_2022, 77400.0, 7.0, 2.0
)
EF_UY_AEREO_JET_TTW_L = _ef_ttw_kgco2e_litro(
    PCI_TURBOCOMBUSTIBLE_2022, 71500.0, 1.0, 2.0
)

FUENTE_UY = (
    "Uruguay. MIEM, Emisiones de Gases de Efecto Invernadero del sector Energía "
    "2019 y metodología IPCC 2006; MIEM/DNE e INE, factores de conversión PCI "
    "nacionales, valores 2022."
)
FUENTE_GLEC = (
    "Smart Freight Centre. GLEC Framework for Logistics Emissions Accounting "
    "and Reporting, versión 3.2, octubre de 2025."
)

FACTORES_INTEGRADOS = pd.DataFrame(
    [
        {
            "perfil": "Uruguay | Carretero | Gasoil | TTW",
            "modo": "Carretero",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_CARRETERO_GASOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW. FE: CO2 74100, CH4 4 y N2O 4 kg/TJ; "
                "PCI gasoil 10S 2022 = 0,8521 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Ferroviario | Gasoil | TTW",
            "modo": "Ferroviario",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_FERROVIARIO_GASOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW. FE: CO2 74100, CH4 4 y N2O 29 kg/TJ; "
                "PCI gasoil 10S 2022 = 0,8521 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Marítimo | Gasoil marino | TTW",
            "modo": "Marítimo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_NAVEGACION_GASOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW para navegación. FE: CO2 74100, CH4 7 y "
                "N2O 2 kg/TJ; PCI gasoil marino 2022 = 0,8724 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Marítimo | Fueloil pesado | TTW",
            "modo": "Marítimo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_NAVEGACION_FUELOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW para navegación. FE: CO2 77400, CH4 7 y "
                "N2O 2 kg/TJ; PCI fueloil pesado 2022 = 0,9257 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Fluvial | Gasoil marino | TTW",
            "modo": "Fluvial",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_NAVEGACION_GASOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW para navegación fluvial. FE: CO2 74100, "
                "CH4 7 y N2O 2 kg/TJ; PCI gasoil marino 2022 = 0,8724 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Fluvial | Fueloil pesado | TTW",
            "modo": "Fluvial",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_NAVEGACION_FUELOIL_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW para navegación fluvial. FE: CO2 77400, "
                "CH4 7 y N2O 2 kg/TJ; PCI fueloil pesado 2022 = 0,9257 tep/m3."
            ),
        },
        {
            "perfil": "Uruguay | Aéreo | Turbocombustible Jet A1 | TTW",
            "modo": "Aéreo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "L",
            "ef_wtt_kgco2e_unidad": 0.0,
            "ef_ttw_kgco2e_unidad": EF_UY_AEREO_JET_TTW_L,
            "fuente": FUENTE_UY,
            "anio": 2022,
            "clase_dato": "Predeterminado",
            "tier": "Tier 1",
            "base_gwp": "IPCC AR5 GWP100",
            "observacion": (
                "Perfil nacional operacional TTW. FE turbocombustible: CO2 71500, CH4 1 "
                "y N2O 2 kg/TJ; PCI Jet A1 2022 = 0,8304 tep/m3."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo contenedor | Promedio industria | Seco | Emisiones operativas",
            "modo": "Marítimo",
            "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "intensidad_wtt_g_teukm": 12.7,
            "intensidad_ttw_g_teukm": 59.0,
            "intensidad_wtw_g_teukm": 71.7,
            "unidad_energia": None,
            "ef_wtt_kgco2e_unidad": None,
            "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Tabla 18, transporte marítimo de contenedores.",
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Promedio de la industria para contenedores secos cuando el par origen-destino o la ruta comercial no se conoce. "
                "Factor de usuario final WTW expresado por TEU-km."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo contenedor | Promedio industria | Reefer | Emisiones operativas",
            "modo": "Marítimo",
            "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "intensidad_wtt_g_teukm": 25.3,
            "intensidad_ttw_g_teukm": 117.0,
            "intensidad_wtw_g_teukm": 142.3,
            "unidad_energia": None,
            "ef_wtt_kgco2e_unidad": None,
            "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Tabla 18, transporte marítimo de contenedores.",
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Promedio de la industria para contenedores refrigerados cuando el par origen-destino o la ruta comercial no se conoce. "
                "Factor de usuario final WTW expresado por TEU-km."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Seco | Emisiones operativas",
            "modo": "Marítimo",
            "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "intensidad_wtt_g_teukm": 11.8,
            "intensidad_ttw_g_teukm": 55.9,
            "intensidad_wtw_g_teukm": 67.7,
            "unidad_energia": None,
            "ef_wtt_kgco2e_unidad": None,
            "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Tabla 18, ruta Asia a/desde Sudamérica incluyendo Centroamérica.",
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Intensidad de usuario final WTW para contenedor seco en la ruta comercial Asia a/desde Sudamérica. "
                "Aplicable cuando la operación corresponde a esa agrupación de rutas."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Reefer | Emisiones operativas",
            "modo": "Marítimo",
            "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "intensidad_wtt_g_teukm": 23.9,
            "intensidad_ttw_g_teukm": 112.2,
            "intensidad_wtw_g_teukm": 136.1,
            "unidad_energia": None,
            "ef_wtt_kgco2e_unidad": None,
            "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Tabla 18, ruta Asia a/desde Sudamérica incluyendo Centroamérica.",
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Intensidad de usuario final WTW para contenedor refrigerado en la ruta comercial Asia a/desde Sudamérica. "
                "Aplicable cuando la operación corresponde a esa agrupación de rutas."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Carretero | Diésel 100% | WTW",
            "modo": "Carretero",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.97,
            "ef_ttw_kgco2e_unidad": 3.22,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional WTW. Diésel 100%: WTT 0,97 y TTW 3,22 kg CO2e/kg. "
                "No constituye un factor nacional del Uruguay."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Ferroviario | Diésel 100% | WTW",
            "modo": "Ferroviario",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.97,
            "ef_ttw_kgco2e_unidad": 3.22,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional del portador energético diésel. No constituye "
                "una intensidad ferroviaria nacional ni un factor nacional del Uruguay."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo | MDO/MGO VLSFO | WTW",
            "modo": "Marítimo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.61,
            "ef_ttw_kgco2e_unidad": 3.26,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional WTW para MDO/MGO VLSFO: WTT 0,61 y TTW "
                "3,26 kg CO2e/kg. No constituye un factor nacional del Uruguay."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Marítimo | HFO VLSFO | WTW",
            "modo": "Marítimo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.68,
            "ef_ttw_kgco2e_unidad": 3.16,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional WTW para HFO VLSFO: WTT 0,68 y TTW "
                "3,16 kg CO2e/kg. No constituye un factor nacional del Uruguay."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | MDO/MGO VLSFO | WTW",
            "modo": "Fluvial",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.61,
            "ef_ttw_kgco2e_unidad": 3.26,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional del combustible MDO/MGO VLSFO. Utilizar solo "
                "cuando corresponda al combustible real de la embarcación."
            ),
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Jet A1/A | WTW",
            "modo": "Aéreo",
            "metodo": "Consumo energético",
            "intensidad_wtt_g_tkm": None,
            "intensidad_ttw_g_tkm": None,
            "intensidad_wtw_g_tkm": None,
            "unidad_energia": "kg",
            "ef_wtt_kgco2e_unidad": 0.66,
            "ef_ttw_kgco2e_unidad": 3.18,
            "fuente": FUENTE_GLEC,
            "anio": 2025,
            "clase_dato": "Predeterminado",
            "tier": "No aplica",
            "base_gwp": "IPCC AR6 GWP100",
            "observacion": (
                "Referencia internacional WTW para Jet Kerosene: WTT 0,66 y TTW "
                "3,18 kg CO2e/kg. No constituye un factor nacional del Uruguay."
            ),
        },
    ]
)


# -----------------------------------------------------------------------------
# Intensidades internacionales por defecto para cerrar brechas de datos
# -----------------------------------------------------------------------------
# Estos perfiles se utilizan como fallback cuando el usuario no conoce el consumo
# energético del tramo. Todos corresponden a valores WTW publicados por GLEC v3.2
# y se identifican explícitamente como referencias internacionales, no como factores
# nacionales del Uruguay.
FACTORES_GLEC_INTENSIDAD_FALLBACK = pd.DataFrame(
    [
        # Carretero. GLEC v3.2, Module 2, Europe and South America.
        {
            "perfil": "GLEC v3.2 | Carretero | Furgón <3,5 t | Sudamérica/Europa | Intensidad estándar",
            "modo": "Carretero", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 840.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte carretero, Europa y Sudamérica; valor inicial para van <3,5 t.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW de referencia para vehículo sin control de temperatura. Incluye la corrección de distancia indicada por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Carretero | Camión urbano 3,5–7,5 t | Sudamérica/Europa | Intensidad estándar",
            "modo": "Carretero", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 335.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte carretero, Europa y Sudamérica; valor inicial para camión urbano 3,5–7,5 t GVW.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW de referencia para vehículo sin control de temperatura.",
        },
        {
            "perfil": "GLEC v3.2 | Carretero | Camión medio 7,5–20 t | Sudamérica/Europa | Intensidad estándar",
            "modo": "Carretero", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 210.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte carretero, Europa y Sudamérica; valor inicial para MGV 7,5–20 t GVW.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW de referencia para vehículo sin control de temperatura.",
        },
        {
            "perfil": "GLEC v3.2 | Carretero | Camión pesado >20 t | Sudamérica/Europa | Intensidad estándar",
            "modo": "Carretero", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 125.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte carretero, Europa y Sudamérica; valor inicial para HGV >20 t GVW.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW de referencia para vehículo sin control de temperatura. Se propone como fallback para carga pesada cuando no se dispone de consumo real.",
        },
        # Ferroviario. GLEC v3.2, Module 2, referencias europeas.
        {
            "perfil": "GLEC v3.2 | Ferroviario | Tracción no identificada | Referencia internacional",
            "modo": "Ferroviario", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 18.4,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte ferroviario; promedio UE cuando se desconoce el tipo de energía de tracción.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Fallback internacional WTW. No representa específicamente la red ferroviaria uruguaya.",
        },
        {
            "perfil": "GLEC v3.2 | Ferroviario | Tracción diésel | Referencia internacional",
            "modo": "Ferroviario", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 31.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte ferroviario; promedio UE para tracción diésel.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia internacional WTW para tracción diésel.",
        },
        {
            "perfil": "GLEC v3.2 | Ferroviario | Tracción eléctrica | Referencia internacional",
            "modo": "Ferroviario", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 10.8,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, transporte ferroviario; promedio UE para tracción eléctrica con mezcla eléctrica de referencia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia internacional WTW. La intensidad real depende de la mezcla eléctrica utilizada.",
        },
        # Fluvial / inland waterways. GLEC v3.2, Table 2.
        {
            "perfil": "GLEC v3.2 | Fluvial | Buque motor <50 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 77.1,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; buque motor <50 m (<650 t).",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC; la base de datos es predominantemente europea.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Buque motor 50–80 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 33.9,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; buque motor 50–80 m (650–1000 t).",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC; la base de datos es predominantemente europea.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Buque motor 85–110 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 21.4,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; buque motor 85–110 m (1000–2000 t).",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC; se utiliza como fallback sugerido para embarcación motorizada de porte medio.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Buque motor 135 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 21.8,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; buque motor 135 m (2000–3000 t).",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Buque tanque | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 24.7,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; tanker vessels.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Portacontenedores 110 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 29.3,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; container vessels 110 m.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Fluvial | Portacontenedores 135 m | Intensidad estándar",
            "modo": "Fluvial", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 22.6,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 2, vías navegables interiores; container vessels 135 m.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Valor WTW global propuesto por GLEC.",
        },
        # Aéreo. GLEC v3.2, Table 1. Los valores incorporan la corrección de distancia de +95 km definida por GLEC.
        {
            "perfil": "GLEC v3.2 | Aéreo | Configuración desconocida | Corta distancia <1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 1363.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; configuración desconocida, corta distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. Mezcla ponderada 55% belly freight y 45% freighter. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Configuración desconocida | Larga distancia >1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 788.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; configuración desconocida, larga distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. Mezcla ponderada 55% belly freight y 45% freighter. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Avión carguero | Corta distancia <1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 1516.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; freighter, corta distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Avión carguero | Larga distancia >1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 608.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; freighter, larga distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Carga en bodega de avión de pasajeros | Corta distancia <1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 1239.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; belly freight, corta distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        {
            "perfil": "GLEC v3.2 | Aéreo | Carga en bodega de avión de pasajeros | Larga distancia >1500 km | Intensidad estándar",
            "modo": "Aéreo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 936.0,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 1, transporte aéreo; belly freight, larga distancia.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "WTW. El valor GLEC ya incorpora la conversión de distancia de +95 km.",
        },
        # Marítimo no contenerizado. Perfiles de referencia con MDO y columna WTW con DAF del 15%.
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Bulk carrier 10.000–34.999 dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 9.6,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 14; bulk carrier 10.000–34.999 dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm para carga a granel. El valor integrado incluye el DAF de 15% publicado por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Carga general 5.000–9.999 dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 23.9,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 15; general cargo 5.000–9.999 dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm para carga general. El valor integrado incluye el DAF de 15% publicado por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Petrolero 20.000–59.999 dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 20.3,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 16; oil tanker 20.000–59.999 dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm para petrolero. El valor integrado incluye el DAF de 15% publicado por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Quimiquero 20.000–39.999 dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 12.5,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 15; chemical tanker 20.000–39.999 dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm para quimiquero. El valor integrado incluye el DAF de 15% publicado por GLEC.",
        },
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Ro-Ro 15.000+ dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 21.8,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 17; Ro-Ro 15.000+ dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm. En Ro-Ro la metodología GLEC requiere atención a la asignación entre vehículo y carga.",
        },
        {
            "perfil": "GLEC v3.2 | Marítimo no contenerizado | Granel refrigerado 6.000–9.999 dwt | Intensidad estándar",
            "modo": "Marítimo", "metodo": "Intensidad de emisión",
            "intensidad_wtt_g_tkm": None, "intensidad_ttw_g_tkm": None, "intensidad_wtw_g_tkm": 64.6,
            "unidad_energia": None, "ef_wtt_kgco2e_unidad": None, "ef_ttw_kgco2e_unidad": None,
            "fuente": FUENTE_GLEC + " Módulo 2, Tabla 16; refrigerated bulk 6.000–9.999 dwt, MDO, WTW con DAF 15%.",
            "anio": 2025, "clase_dato": "Predeterminado", "tier": "No aplica", "base_gwp": "IPCC AR6 GWP100",
            "observacion": "Referencia WTW por tkm para granel refrigerado. El valor integrado incluye el DAF de 15% publicado por GLEC.",
        },
    ]
)

FACTORES_INTEGRADOS = pd.concat(
    [FACTORES_INTEGRADOS, FACTORES_GLEC_INTENSIDAD_FALLBACK],
    ignore_index=True,
    sort=False,
)

# Etiquetas más comprensibles para usuarios no especializados.
_RENOMBRE_PERFILES = {
    "GLEC v3.2 | Marítimo contenedor | Promedio industria | Seco | WTW": "GLEC v3.2 | Marítimo contenedor | Promedio industria | Seco | Emisiones operativas",
    "GLEC v3.2 | Marítimo contenedor | Promedio industria | Reefer | WTW": "GLEC v3.2 | Marítimo contenedor | Promedio industria | Reefer | Emisiones operativas",
    "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Seco | WTW": "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Seco | Emisiones operativas",
    "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Reefer | WTW": "GLEC v3.2 | Marítimo contenedor | Asia ↔ Sudamérica | Reefer | Emisiones operativas",
    "Uruguay | Carretero | Gasoil | TTW": "Uruguay | Carretero | Gasoil | Emisiones operativas",
    "Uruguay | Ferroviario | Gasoil | TTW": "Uruguay | Ferroviario | Gasoil | Emisiones operativas",
    "Uruguay | Marítimo | Gasoil marino | TTW": "Uruguay | Marítimo | Gasoil marino | Emisiones operativas",
    "Uruguay | Marítimo | Fueloil pesado | TTW": "Uruguay | Marítimo | Fueloil pesado | Emisiones operativas",
    "Uruguay | Fluvial | Gasoil marino | TTW": "Uruguay | Fluvial | Gasoil marino | Emisiones operativas",
    "Uruguay | Fluvial | Fueloil pesado | TTW": "Uruguay | Fluvial | Fueloil pesado | Emisiones operativas",
    "Uruguay | Aéreo | Turbocombustible Jet A1 | TTW": "Uruguay | Aéreo | Turbocombustible Jet A1 | Emisiones operativas",
    "GLEC v3.2 | Carretero | Diésel 100% | WTW": "GLEC v3.2 | Carretero | Diésel | Emisiones operativas",
    "GLEC v3.2 | Ferroviario | Diésel 100% | WTW": "GLEC v3.2 | Ferroviario | Diésel | Emisiones operativas",
    "GLEC v3.2 | Marítimo | MDO/MGO VLSFO | WTW": "GLEC v3.2 | Marítimo | MDO/MGO VLSFO | Emisiones operativas",
    "GLEC v3.2 | Marítimo | HFO VLSFO | WTW": "GLEC v3.2 | Marítimo | HFO VLSFO | Emisiones operativas",
    "GLEC v3.2 | Fluvial | MDO/MGO VLSFO | WTW": "GLEC v3.2 | Fluvial | MDO/MGO VLSFO | Emisiones operativas",
    "GLEC v3.2 | Aéreo | Jet A1/A | WTW": "GLEC v3.2 | Aéreo | Jet A1/A | Emisiones operativas",
}
FACTORES_INTEGRADOS["perfil"] = FACTORES_INTEGRADOS["perfil"].replace(_RENOMBRE_PERFILES)

# -----------------------------------------------------------------------------
# FRONTERA ÚNICA DE LA HERRAMIENTA: EMISIONES OPERATIVAS DEL TRANSPORTE
# -----------------------------------------------------------------------------
# La interfaz y el núcleo de cálculo trabajan exclusivamente con TTW, es decir,
# con las emisiones generadas durante la operación del vehículo, tren, buque o
# aeronave. Los componentes WTT y WTW se conservan como columnas de compatibilidad
# de la base, pero se anulan y nunca participan en el cálculo.

# Valores TTW publicados por GLEC v3.2 para los perfiles por actividad utilizados
# como fallback cuando el usuario no conoce el consumo real.
_TTW_INTENSIDADES_GLEC = {
    # Carretero. Módulo 2, Tablas 7 y 8, Europa y Sudamérica, diésel.
    "GLEC v3.2 | Carretero | Furgón <3,5 t | Sudamérica/Europa | Intensidad estándar": 647.0,
    "GLEC v3.2 | Carretero | Camión urbano 3,5–7,5 t | Sudamérica/Europa | Intensidad estándar": 258.0,
    "GLEC v3.2 | Carretero | Camión medio 7,5–20 t | Sudamérica/Europa | Intensidad estándar": 147.0,
    "GLEC v3.2 | Carretero | Camión pesado >20 t | Sudamérica/Europa | Intensidad estándar": 78.0,
    # Ferroviario. Módulo 2, Tabla 4, tracción diésel, carga promedio/mixta.
    "GLEC v3.2 | Ferroviario | Tracción diésel | Referencia internacional": 23.6,
    # Fluvial. Módulo 2, Tabla 2.
    "GLEC v3.2 | Fluvial | Buque motor <50 m | Intensidad estándar": 59.2,
    "GLEC v3.2 | Fluvial | Buque motor 50–80 m | Intensidad estándar": 26.1,
    "GLEC v3.2 | Fluvial | Buque motor 85–110 m | Intensidad estándar": 16.4,
    "GLEC v3.2 | Fluvial | Buque motor 135 m | Intensidad estándar": 16.7,
    "GLEC v3.2 | Fluvial | Buque tanque | Intensidad estándar": 19.0,
    "GLEC v3.2 | Fluvial | Portacontenedores 110 m | Intensidad estándar": 22.5,
    "GLEC v3.2 | Fluvial | Portacontenedores 135 m | Intensidad estándar": 17.4,
    # Aéreo. Módulo 2, Tabla 1. Los factores ya incorporan la corrección de distancia de +95 km.
    "GLEC v3.2 | Aéreo | Configuración desconocida | Corta distancia <1500 km | Intensidad estándar": 1129.0,
    "GLEC v3.2 | Aéreo | Configuración desconocida | Larga distancia >1500 km | Intensidad estándar": 653.0,
    "GLEC v3.2 | Aéreo | Avión carguero | Corta distancia <1500 km | Intensidad estándar": 1255.0,
    "GLEC v3.2 | Aéreo | Avión carguero | Larga distancia >1500 km | Intensidad estándar": 503.0,
    "GLEC v3.2 | Aéreo | Carga en bodega de avión de pasajeros | Corta distancia <1500 km | Intensidad estándar": 1026.0,
    "GLEC v3.2 | Aéreo | Carga en bodega de avión de pasajeros | Larga distancia >1500 km | Intensidad estándar": 775.0,
    # Marítimo no contenerizado. Módulo 2, Tablas 14 a 17, combustible MDO.
    # Se usa la columna TTW sin incorporar el componente WTT. La distancia operacional
    # debe preferirse cuando esté disponible.
    "GLEC v3.2 | Marítimo no contenerizado | Bulk carrier 10.000–34.999 dwt | Intensidad estándar": 7.0,
    "GLEC v3.2 | Marítimo no contenerizado | Carga general 5.000–9.999 dwt | Intensidad estándar": 17.5,
    "GLEC v3.2 | Marítimo no contenerizado | Petrolero 20.000–59.999 dwt | Intensidad estándar": 14.9,
    "GLEC v3.2 | Marítimo no contenerizado | Quimiquero 20.000–39.999 dwt | Intensidad estándar": 9.2,
    "GLEC v3.2 | Marítimo no contenerizado | Ro-Ro 15.000+ dwt | Intensidad estándar": 16.0,
    "GLEC v3.2 | Marítimo no contenerizado | Granel refrigerado 6.000–9.999 dwt | Intensidad estándar": 47.3,
}

for _perfil, _valor_ttw in _TTW_INTENSIDADES_GLEC.items():
    _mask = FACTORES_INTEGRADOS["perfil"].eq(_perfil)
    FACTORES_INTEGRADOS.loc[_mask, "intensidad_wtt_g_tkm"] = None
    FACTORES_INTEGRADOS.loc[_mask, "intensidad_ttw_g_tkm"] = _valor_ttw
    FACTORES_INTEGRADOS.loc[_mask, "intensidad_wtw_g_tkm"] = None

# Contenedores marítimos: GLEC publica WTT, TTW y WTW por TEU-km. Esta herramienta
# utiliza exclusivamente la columna TTW ya cargada en los perfiles integrados.
for _col in ("intensidad_wtt_g_teukm", "intensidad_wtw_g_teukm"):
    if _col in FACTORES_INTEGRADOS.columns:
        FACTORES_INTEGRADOS[_col] = None

# Factores por consumo: se conserva únicamente el componente operacional TTW.
FACTORES_INTEGRADOS["ef_wtt_kgco2e_unidad"] = None
if "ef_wtw_kgco2e_unidad" in FACTORES_INTEGRADOS.columns:
    FACTORES_INTEGRADOS["ef_wtw_kgco2e_unidad"] = None

# No se ofrece el promedio ferroviario de tracción desconocida porque GLEC lo
# publica como WTW y no permite separar de forma documentada su componente TTW.
# Tampoco se ofrece el promedio eléctrico en este módulo TTW: la generación de
# electricidad queda fuera de la frontera y no debe confundirse con emisiones directas.
FACTORES_INTEGRADOS = FACTORES_INTEGRADOS[
    ~FACTORES_INTEGRADOS["perfil"].isin(
        [
            "GLEC v3.2 | Ferroviario | Tracción no identificada | Referencia internacional",
            "GLEC v3.2 | Ferroviario | Tracción eléctrica | Referencia internacional",
        ]
    )
].reset_index(drop=True)

# Etiquetas más precisas para los dos perfiles carreteros representativos usados
# en la tabla detallada de GLEC.
FACTORES_INTEGRADOS["perfil"] = FACTORES_INTEGRADOS["perfil"].replace(
    {
        "GLEC v3.2 | Carretero | Camión medio 7,5–20 t | Sudamérica/Europa | Intensidad estándar":
            "GLEC v3.2 | Carretero | Camión rígido 12–20 t | Sudamérica/Europa | Intensidad operacional",
        "GLEC v3.2 | Carretero | Camión pesado >20 t | Sudamérica/Europa | Intensidad estándar":
            "GLEC v3.2 | Carretero | Camión articulado 34–40 t | Sudamérica/Europa | Intensidad operacional",
    }
)

# Limpieza de textos de trazabilidad. La fuente original puede publicar también
# valores WTW, pero el valor usado por esta herramienta es siempre el operacional TTW.
FACTORES_INTEGRADOS["observacion"] = FACTORES_INTEGRADOS["observacion"].fillna("").astype(str)
FACTORES_INTEGRADOS["observacion"] = FACTORES_INTEGRADOS["observacion"].str.replace(
    "Valor WTW", "Valor TTW operacional", regex=False
).str.replace(
    "Referencia WTW", "Referencia TTW operacional", regex=False
).str.replace(
    "Fallback internacional WTW", "Fallback internacional TTW operacional", regex=False
).str.replace(
    "WTW.", "TTW operacional.", regex=False
)

# Textos de trazabilidad coherentes con la frontera operacional única.
# La fuente se conserva, pero la descripción indica con claridad qué columna se usa.
_mask_contenedor = FACTORES_INTEGRADOS["perfil"].str.contains("Marítimo contenedor", na=False)
FACTORES_INTEGRADOS.loc[_mask_contenedor, "observacion"] = FACTORES_INTEGRADOS.loc[_mask_contenedor, "observacion"].str.replace(
    "Factor de usuario final WTW", "Intensidad operacional TTW publicada por GLEC", regex=False
).str.replace(
    "Intensidad de usuario final WTW", "Intensidad operacional TTW publicada por GLEC", regex=False
)

_mask_glec_consumo = FACTORES_INTEGRADOS["perfil"].str.startswith("GLEC v3.2", na=False) & FACTORES_INTEGRADOS["metodo"].eq("Consumo energético")
FACTORES_INTEGRADOS.loc[_mask_glec_consumo, "observacion"] = FACTORES_INTEGRADOS.loc[_mask_glec_consumo, "observacion"].str.replace(
    r"WTT [0-9,.]+ y TTW ", "TTW operacional ", regex=True
).str.replace(
    "Referencia internacional WTW", "Referencia internacional operacional", regex=False
)

_mask_mar_no_cont = FACTORES_INTEGRADOS["perfil"].str.contains("Marítimo no contenerizado", na=False)
FACTORES_INTEGRADOS.loc[_mask_mar_no_cont, "fuente"] = FACTORES_INTEGRADOS.loc[_mask_mar_no_cont, "fuente"].str.replace(
    ", WTW con DAF 15%", ", columna TTW operacional", regex=False
)
FACTORES_INTEGRADOS.loc[_mask_mar_no_cont, "observacion"] = FACTORES_INTEGRADOS.loc[_mask_mar_no_cont, "observacion"].str.replace(
    "El valor integrado incluye el DAF de 15% publicado por GLEC.",
    "Se utiliza la columna TTW operacional publicada por GLEC; no se incorpora el ajuste de distancia de los valores de usuario final.",
    regex=False,
)

# Densidades usadas únicamente para convertir una entrada del usuario en litros
# a la unidad original (kg) del factor publicado. El factor original no se modifica.
_DENSIDADES_PERFIL = {
    "GLEC v3.2 | Carretero | Diésel | Emisiones operativas": (
        0.832,
        "Smart Freight Centre, GLEC Framework v3.2, factor europeo de diésel: densidad 0,832 kg/L.",
    ),
    "GLEC v3.2 | Ferroviario | Diésel | Emisiones operativas": (
        0.832,
        "Smart Freight Centre, GLEC Framework v3.2, factor europeo de diésel: densidad 0,832 kg/L.",
    ),
    "GLEC v3.2 | Aéreo | Jet A1/A | Emisiones operativas": (
        0.802,
        "Smart Freight Centre, GLEC Framework v3.2, Jet Kerosene: densidad 0,802 kg/L.",
    ),
    "GLEC v3.2 | Marítimo | MDO/MGO VLSFO | Emisiones operativas": (
        0.890,
        "ANCAP, ficha de seguridad Gasoil Marino: densidad 890 kg/m³ a 15 °C. Se usa solo para convertir L a kg.",
    ),
    "GLEC v3.2 | Fluvial | MDO/MGO VLSFO | Emisiones operativas": (
        0.890,
        "ANCAP, ficha de seguridad Gasoil Marino: densidad 890 kg/m³ a 15 °C. Se usa solo para convertir L a kg.",
    ),
    "GLEC v3.2 | Marítimo | HFO VLSFO | Emisiones operativas": (
        0.991,
        "ANCAP, especificación de fuel oil marino intermedio: densidad máxima 991 kg/m³ a 15 °C. Valor editable para la conversión L a kg.",
    ),
}
FACTORES_INTEGRADOS["densidad_kg_l"] = FACTORES_INTEGRADOS["perfil"].map(
    lambda x: _DENSIDADES_PERFIL.get(x, (None, ""))[0]
)
FACTORES_INTEGRADOS["fuente_densidad"] = FACTORES_INTEGRADOS["perfil"].map(
    lambda x: _DENSIDADES_PERFIL.get(x, (None, ""))[1]
)

COLUMNAS_FACTORES = [
    "perfil",
    "modo",
    "metodo",
    "intensidad_wtt_g_tkm",
    "intensidad_ttw_g_tkm",
    "intensidad_wtw_g_tkm",
    "unidad_energia",
    "densidad_kg_l",
    "fuente_densidad",
    "ef_wtt_kgco2e_unidad",
    "ef_ttw_kgco2e_unidad",
    "fuente",
    "anio",
    "clase_dato",
    "tier",
    "base_gwp",
    "observacion",
]

COLUMNAS_FACTORES_OPCIONALES = [
    "intensidad_wtt_g_teukm",
    "intensidad_ttw_g_teukm",
    "intensidad_wtw_g_teukm",
]

# Esquema visible para bases personalizadas. No expone componentes fuera de la
# frontera operacional de la herramienta.
COLUMNAS_FACTORES_CSV = [
    "perfil",
    "modo",
    "metodo",
    "intensidad_ttw_g_tkm",
    "intensidad_ttw_g_teukm",
    "unidad_energia",
    "densidad_kg_l",
    "fuente_densidad",
    "ef_ttw_kgco2e_unidad",
    "fuente",
    "anio",
    "clase_dato",
    "tier",
    "base_gwp",
    "observacion",
]


# =============================================================================
# UTILIDADES DE DATOS
# =============================================================================


def _num(value: Any, default: float = 0.0) -> float:
    """Convierte valores numéricos y NaN a float seguro."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return str(value)


def validar_catalogo_factores(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Valida una base externa limitada a emisiones operativas."""
    mensajes: List[str] = []

    requeridas = [
        "perfil", "modo", "metodo", "intensidad_ttw_g_tkm",
        "unidad_energia", "densidad_kg_l", "fuente_densidad",
        "ef_ttw_kgco2e_unidad", "fuente", "anio", "clase_dato",
        "tier", "base_gwp", "observacion",
    ]
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        mensajes.append(
            "La base de factores no contiene todas las columnas requeridas: "
            + ", ".join(faltantes)
        )
        return pd.DataFrame(columns=COLUMNAS_FACTORES + COLUMNAS_FACTORES_OPCIONALES), mensajes

    limpio = df.copy()
    if "intensidad_ttw_g_teukm" not in limpio.columns:
        limpio["intensidad_ttw_g_teukm"] = None

    # Si un archivo heredado contiene componentes fuera de la frontera operacional,
    # esos registros se rechazan en lugar de mezclarlos con los resultados actuales.
    cols_fuera_alcance = [
        "intensidad_wtt_g_tkm", "intensidad_wtw_g_tkm",
        "intensidad_wtt_g_teukm", "intensidad_wtw_g_teukm",
        "ef_wtt_kgco2e_unidad", "ef_wtw_kgco2e_unidad",
    ]
    mascara_fuera = pd.Series(False, index=limpio.index)
    for col in cols_fuera_alcance:
        if col in limpio.columns:
            mascara_fuera = mascara_fuera | (pd.to_numeric(limpio[col], errors="coerce").fillna(0) > 0)
    if mascara_fuera.any():
        mensajes.append(
            "Se ignoraron factores externos que están fuera de la frontera operacional definida. "
            "La herramienta acepta únicamente factores de emisiones generadas durante la operación del transporte."
        )
        limpio = limpio.loc[~mascara_fuera].copy()

    limpio["modo"] = limpio["modo"].astype(str).str.strip()
    limpio["metodo"] = limpio["metodo"].astype(str).str.strip()

    modos_invalidos = sorted(set(limpio["modo"]) - set(MODOS))
    if modos_invalidos:
        mensajes.append(
            "Se ignoraron registros con modos no reconocidos: " + ", ".join(modos_invalidos)
        )
        limpio = limpio[limpio["modo"].isin(MODOS)]

    metodos_invalidos = sorted(set(limpio["metodo"]) - set(METODOS_CALCULO))
    if metodos_invalidos:
        mensajes.append(
            "Se ignoraron registros con métodos no reconocidos: " + ", ".join(metodos_invalidos)
        )
        limpio = limpio[limpio["metodo"].isin(METODOS_CALCULO)]

    # Reconstruye las columnas internas de compatibilidad como vacías. El usuario
    # nunca necesita completarlas y el núcleo solo lee el componente operacional.
    for col in set(COLUMNAS_FACTORES + COLUMNAS_FACTORES_OPCIONALES + ["ef_wtw_kgco2e_unidad"]):
        if col not in limpio.columns:
            limpio[col] = None
    limpio["intensidad_wtt_g_tkm"] = None
    limpio["intensidad_wtw_g_tkm"] = None
    limpio["intensidad_wtt_g_teukm"] = None
    limpio["intensidad_wtw_g_teukm"] = None
    limpio["ef_wtt_kgco2e_unidad"] = None
    limpio["ef_wtw_kgco2e_unidad"] = None

    orden = COLUMNAS_FACTORES + COLUMNAS_FACTORES_OPCIONALES
    return limpio[orden].reset_index(drop=True), mensajes

def cargar_catalogo(uploaded_file) -> Tuple[pd.DataFrame, List[str]]:
    catalogo = FACTORES_INTEGRADOS.copy()
    mensajes: List[str] = []

    if uploaded_file is not None:
        try:
            externo = pd.read_csv(uploaded_file)
            externo, avisos = validar_catalogo_factores(externo)
            mensajes.extend(avisos)
            if not externo.empty:
                catalogo = pd.concat([catalogo, externo], ignore_index=True)
        except Exception as exc:
            mensajes.append(f"No fue posible leer la base de factores cargada: {exc}")

    return catalogo, mensajes


def perfil_a_dict(catalogo: pd.DataFrame, perfil: str) -> Optional[Dict[str, Any]]:
    if perfil == "Entrada manual":
        return None
    fila = catalogo[catalogo["perfil"] == perfil]
    if fila.empty:
        return None
    return fila.iloc[0].to_dict()


# =============================================================================
# GEOLOCALIZACIÓN Y DISTANCIA
# =============================================================================


def _distancia_gcd_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia de gran círculo mediante la fórmula de Haversine."""
    r_km = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r_km * math.asin(math.sqrt(a))


@st.cache_data(ttl=86400, show_spinner=False)
def _consultas_geograficas(lugar: str, modo: str) -> List[str]:
    """Genera consultas específicas por modo para reducir ambigüedades."""
    limpio = _str(lugar).strip()
    if not limpio:
        return []

    consultas: List[str] = []
    codigo = limpio.upper().strip()
    if modo == "Aéreo":
        # Los códigos IATA de tres letras suelen resolverse mejor añadiendo "airport".
        if len(codigo) == 3 and codigo.isalpha():
            consultas.extend([f"{codigo} airport", f"{codigo} aeropuerto"])
        consultas.extend([f"{limpio} airport", f"{limpio} aeropuerto", limpio])
    elif modo in {"Marítimo", "Fluvial"}:
        consultas.extend([f"{limpio} port", f"Puerto de {limpio}", limpio])
    elif modo == "Ferroviario":
        consultas.extend([limpio, f"{limpio} railway station", f"{limpio} estación ferroviaria"])
    else:
        consultas.append(limpio)

    # Quita duplicados conservando el orden.
    return list(dict.fromkeys(q for q in consultas if q.strip()))


@st.cache_data(ttl=86400, show_spinner=False)
def geocodificar_lugar(
    lugar: str,
    modo: str,
    priorizar_uruguay: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Resuelve un lugar con Nominatim.

    Primero intenta consultas específicas del modo en búsqueda global. Uruguay se
    utiliza como segundo intento cuando el nombre es ambiguo. Esto evita que un
    código internacional, por ejemplo POA o PVG, quede forzado a un resultado local.
    """
    if not REQUESTS_DISPONIBLE:
        raise RuntimeError(
            "La función de distancia automática requiere el paquete requests. "
            "Incluya requests en requirements.txt y reinicie la aplicación."
        )
    lugar = _str(lugar).strip()
    if not lugar:
        return None

    consultas = _consultas_geograficas(lugar, modo)
    headers = {
        "User-Agent": "herramienta-emisiones-multimodales-uruguay/0.5 "
        "(uso académico y sector logístico)"
    }
    url = "https://nominatim.openstreetmap.org/search"

    intentos: List[Dict[str, str]] = []
    # Búsqueda global primero, con consulta adaptada al modo.
    for q in consultas:
        intentos.append({"q": q})
    # Uruguay queda como respaldo cuando el nombre no es suficientemente específico.
    if priorizar_uruguay:
        for q in consultas:
            intentos.append({"q": q, "countrycodes": "uy"})

    for base in intentos:
        params = {**base, "format": "jsonv2", "limit": 1, "addressdetails": 1}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        if data:
            item = data[0]
            return {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "direccion": _str(item.get("display_name")),
                "consulta": _str(base.get("q")),
            }
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def ruta_carretera_osrm(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, float]:
    """Calcula distancia y duración vial con el servidor público de OSRM."""
    if not REQUESTS_DISPONIBLE:
        raise RuntimeError("La función de distancia automática requiere el paquete requests.")
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
    )
    params = {"overview": "false", "steps": "false"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError("No se encontró una ruta vial entre los puntos indicados.")
    ruta = data["routes"][0]
    return {
        "distancia_km": float(ruta["distance"]) / 1000.0,
        "duracion_h": float(ruta.get("duration", 0.0)) / 3600.0,
    }


def calcular_distancia_automatica(
    origen: str,
    destino: str,
    modo: str,
    priorizar_uruguay: bool = True,
) -> Dict[str, Any]:
    """Calcula una distancia normalizada en kilómetros a partir de origen y destino."""
    if not origen.strip() or not destino.strip():
        raise ValueError("Ingrese origen y destino antes de calcular la distancia.")

    ori = geocodificar_lugar(origen, modo, priorizar_uruguay)
    des = geocodificar_lugar(destino, modo, priorizar_uruguay)
    if not ori:
        raise ValueError(f"No fue posible localizar el origen: {origen}.")
    if not des:
        raise ValueError(f"No fue posible localizar el destino: {destino}.")

    gcd = _distancia_gcd_km(ori["lat"], ori["lon"], des["lat"], des["lon"])

    if modo == "Carretero":
        ruta = ruta_carretera_osrm(ori["lat"], ori["lon"], des["lat"], des["lon"])
        distancia = ruta["distancia_km"]
        metodo = "Ruta vial automática — OSRM"
        nota = (
            "Distancia vial estimada sobre la red de carreteras. Si el servicio utiliza un recorrido "
            "diferente, peajes específicos o desvíos operacionales, puede reemplazarla por la distancia manual."
        )
        duracion_h = ruta.get("duracion_h")
    else:
        distancia = gcd
        duracion_h = None
        if modo == "Aéreo":
            metodo = "GCD automática — distancia geodésica aeropuerto a aeropuerto"
            nota = (
                "Se utiliza la distancia de gran círculo entre los puntos localizados. Es la distancia "
                "geométrica base del tramo aéreo. Correcciones adicionales solo deben aplicarse cuando "
                "lo exija la metodología del factor seleccionado."
            )
        elif modo == "Ferroviario":
            metodo = "GCD automática — referencia geográfica"
            nota = (
                "La GCD no reproduce el trazado de la vía férrea. Úsela como referencia cuando no disponga "
                "del recorrido ferroviario real; si conoce la distancia operacional, ingrésela manualmente."
            )
        elif modo == "Marítimo":
            metodo = "GCD automática — referencia geográfica puerto a puerto"
            nota = (
                "La GCD no reproduce canales, costas, estrechos ni rutas comerciales. Para resultados finales "
                "es preferible la distancia náutica u operacional del servicio cuando esté disponible."
            )
        else:
            metodo = "GCD automática — referencia geográfica fluvial"
            nota = (
                "La GCD no reproduce meandros ni el recorrido real de la vía navegable. Si dispone de la "
                "distancia operacional del servicio fluvial, ingrésela manualmente."
            )

    return {
        "distancia_km": float(distancia),
        "metodo": metodo,
        "nota": nota,
        "origen_resuelto": ori["direccion"],
        "destino_resuelto": des["direccion"],
        "origen_lat": ori["lat"],
        "origen_lon": ori["lon"],
        "destino_lat": des["lat"],
        "destino_lon": des["lon"],
        "duracion_h": duracion_h,
        "firma": f"{modo}|{origen.strip().casefold()}|{destino.strip().casefold()}",
    }


def _frontera_perfil(perfil_data: Dict[str, Any]) -> str:
    if not perfil_data:
        return ""
    if perfil_data.get("metodo") == "Consumo energético":
        return _determinar_frontera(
            _num(perfil_data.get("ef_wtt_kgco2e_unidad")),
            _num(perfil_data.get("ef_ttw_kgco2e_unidad")),
            _num(perfil_data.get("ef_wtw_kgco2e_unidad")),
        )
    frontera_tkm = _determinar_frontera(
        _num(perfil_data.get("intensidad_wtt_g_tkm")),
        _num(perfil_data.get("intensidad_ttw_g_tkm")),
        _num(perfil_data.get("intensidad_wtw_g_tkm")),
    )
    if frontera_tkm:
        return frontera_tkm
    return _determinar_frontera(
        _num(perfil_data.get("intensidad_wtt_g_teukm")),
        _num(perfil_data.get("intensidad_ttw_g_teukm")),
        _num(perfil_data.get("intensidad_wtw_g_teukm")),
    )


def _alcance_amigable(frontera: str) -> str:
    """La herramienta utiliza una única frontera cuantitativa."""
    return "Solo las emisiones durante la operación del transporte" if frontera == "TTW" else "Fuera del alcance de esta versión"


# =============================================================================
# NÚCLEO MATEMÁTICO
# =============================================================================


def _determinar_frontera(wtt: float, ttw: float, wtw_total: float = 0.0) -> str:
    if ttw > 0 and wtt <= 0 and wtw_total <= 0:
        return "TTW"
    return ""


def _convertir_cantidad_a_unidad_factor(
    cantidad: float,
    unidad_usuario: str,
    unidad_factor: str,
    densidad_kg_l: float = 0.0,
) -> float:
    """Convierte la cantidad informada por el usuario a la unidad original del factor."""
    if unidad_usuario == unidad_factor or not unidad_factor:
        return cantidad
    if unidad_usuario == "L" and unidad_factor == "kg":
        if densidad_kg_l <= 0:
            raise ValueError(
                "El factor está expresado por kg y el consumo fue informado en litros. "
                "Registre una densidad documentada del combustible para efectuar la conversión."
            )
        return cantidad * densidad_kg_l
    if unidad_usuario == "kg" and unidad_factor == "L":
        if densidad_kg_l <= 0:
            raise ValueError(
                "El factor está expresado por litro y el consumo fue informado en kg. "
                "Registre una densidad documentada del combustible."
            )
        return cantidad / densidad_kg_l
    raise ValueError(
        f"No existe una conversión automática entre {unidad_usuario} y {unidad_factor}. "
        "Use la unidad original del factor o documente una conversión compatible."
    )


def calcular_segmento(seg: Dict[str, Any]) -> Dict[str, Any]:
    """Calcula exclusivamente emisiones operativas del tramo de transporte."""
    masa_t = _num(seg.get("masa_t"))
    distancia_carga_km = _num(seg.get("distancia_carga_km"))
    distancia_vacia_km = _num(seg.get("distancia_vacia_km"))
    actividad_tkm = masa_t * distancia_carga_km

    if masa_t <= 0:
        raise ValueError("La masa de la expedición utilizada para la actividad debe ser mayor que cero.")
    if distancia_carga_km <= 0:
        raise ValueError("La distancia con carga debe ser mayor que cero.")

    resultado: Dict[str, Any] = dict(seg)
    teu_equivalente = _num(seg.get("teu_equivalente"))
    actividad_teukm = teu_equivalente * distancia_carga_km if teu_equivalente > 0 else 0.0
    resultado.update(
        {
            "actividad_tkm": actividad_tkm,
            "actividad_teukm": actividad_teukm,
            "distancia_operativa_km": distancia_carga_km + distancia_vacia_km,
            "energia_asignada": None,
            "cantidad_factor_asignada": None,
            "emision_wtt_kgco2e": 0.0,
            "emision_ttw_kgco2e": 0.0,
            "emision_total_kgco2e": 0.0,
            "intensidad_resultante_g_tkm": 0.0,
            "frontera_calculo": "TTW",
        }
    )

    metodo = seg.get("metodo_calculo")

    if metodo == "Intensidad de emisión":
        if any(
            _num(seg.get(c)) > 0
            for c in (
                "intensidad_wtt_g_tkm", "intensidad_wtw_g_tkm",
                "intensidad_wtt_g_teukm", "intensidad_wtw_g_teukm",
            )
        ):
            raise ValueError(
                "Esta versión cuantifica únicamente las emisiones durante la operación del transporte. "
                "El factor seleccionado contiene componentes fuera de esa frontera y no puede utilizarse."
            )

        ttw = _num(seg.get("intensidad_ttw_g_tkm"))
        ttw_teu = _num(seg.get("intensidad_ttw_g_teukm"))
        if ttw > 0 and ttw_teu > 0:
            raise ValueError("No combine intensidades por tkm con intensidades por TEU-km en el mismo tramo.")

        if ttw_teu > 0:
            if teu_equivalente <= 0:
                raise ValueError(
                    "El factor seleccionado está expresado por TEU-km. Informe la cantidad y el tipo de contenedor ISO."
                )
            em_ttw = actividad_teukm * ttw_teu / 1000.0
            intensidad_tkm_resultante = em_ttw / actividad_tkm * 1000.0 if actividad_tkm > 0 else 0.0
            resultado["unidad_intensidad_aplicada"] = "g CO₂e/TEU-km"
        elif ttw > 0:
            em_ttw = actividad_tkm * ttw / 1000.0
            intensidad_tkm_resultante = ttw
            resultado["unidad_intensidad_aplicada"] = "g CO₂e/tkm"
        else:
            raise ValueError(
                "Seleccione un perfil operacional integrado o ingrese una intensidad operacional documentada mayor que cero."
            )

        resultado["emision_ttw_kgco2e"] = em_ttw
        resultado["emision_total_kgco2e"] = em_ttw
        resultado["intensidad_resultante_g_tkm"] = intensidad_tkm_resultante

    elif metodo == "Consumo energético":
        forma_consumo = _str(seg.get("forma_consumo"), "Consumo total del tramo")
        unidad_usuario = _str(seg.get("unidad_energia"))
        unidad_factor = _str(seg.get("unidad_factor"), unidad_usuario)
        densidad_kg_l = _num(seg.get("densidad_kg_l"))

        if forma_consumo in {
            "Cantidad total", "Cantidad total del tramo", "Consumo total del tramo", "Litros totales consumidos",
        }:
            q_bruto = _num(seg.get("cantidad_energia_total"))
        elif forma_consumo in {"Consumo por 100 km", "Consumo medio en L/100 km"}:
            q_bruto = (
                _num(seg.get("consumo_por_100km_carga")) / 100.0 * distancia_carga_km
                + _num(seg.get("consumo_por_100km_vacio")) / 100.0 * distancia_vacia_km
            )
        elif forma_consumo == "Rendimiento en km/L":
            rend_carga = _num(seg.get("rendimiento_km_l_carga"))
            rend_vacio = _num(seg.get("rendimiento_km_l_vacio"))
            if rend_carga <= 0:
                raise ValueError("El rendimiento con carga debe ser mayor que cero.")
            q_bruto = distancia_carga_km / rend_carga
            if distancia_vacia_km > 0:
                if rend_vacio <= 0:
                    raise ValueError("Informe el rendimiento en vacío cuando exista una distancia vacía asociada.")
                q_bruto += distancia_vacia_km / rend_vacio
        else:
            q_bruto = (
                _num(seg.get("consumo_por_km_carga")) * distancia_carga_km
                + _num(seg.get("consumo_por_km_vacio")) * distancia_vacia_km
            )

        asignacion = _num(seg.get("asignacion_pct"), 100.0) / 100.0
        if q_bruto <= 0:
            raise ValueError("El consumo energético debe ser mayor que cero.")
        if asignacion <= 0 or asignacion > 1:
            raise ValueError("La asignación del consumo debe estar entre 0 y 100%.")
        if not unidad_usuario:
            raise ValueError("Informe la unidad de consumo utilizada.")
        if _num(seg.get("ef_wtt_kgco2e_unidad")) > 0 or _num(seg.get("ef_wtw_kgco2e_unidad")) > 0:
            raise ValueError(
                "Esta versión acepta únicamente factores operativos. El suministro de energía queda fuera del cálculo."
            )

        ef_ttw = _num(seg.get("ef_ttw_kgco2e_unidad"))
        if ef_ttw <= 0:
            raise ValueError("Informe un factor operacional documentado para el tramo.")

        q_usuario_asignado = q_bruto * asignacion
        q_factor_asignado = _convertir_cantidad_a_unidad_factor(
            q_usuario_asignado, unidad_usuario, unidad_factor, densidad_kg_l
        )
        em_ttw = q_factor_asignado * ef_ttw
        resultado["energia_asignada"] = q_usuario_asignado
        resultado["cantidad_factor_asignada"] = q_factor_asignado
        resultado["emision_ttw_kgco2e"] = em_ttw
        resultado["emision_total_kgco2e"] = em_ttw
        resultado["intensidad_resultante_g_tkm"] = em_ttw / actividad_tkm * 1000.0 if actividad_tkm > 0 else 0.0

    else:
        raise ValueError("Método de cálculo no reconocido.")

    return resultado

def calcular_cadena(segmentos: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    if not segmentos:
        return pd.DataFrame(), pd.DataFrame(), {}

    calculados: List[Dict[str, Any]] = []
    for i, seg in enumerate(segmentos, start=1):
        resultado = calcular_segmento(seg)
        resultado["segmento"] = i
        calculados.append(resultado)

    df = pd.DataFrame(calculados)

    fronteras = {
        _str(v).strip()
        for v in df.get("frontera_calculo", pd.Series(dtype=str)).tolist()
        if _str(v).strip()
    }
    if len(fronteras) > 1:
        raise ValueError(
            "La cadena combina fronteras energéticas incompatibles: "
            + ", ".join(sorted(fronteras))
            + ". Para agregar los segmentos, utilice una única frontera en toda la cadena."
        )

    bases_gwp = {
        _str(v).strip()
        for v in df.get("base_gwp", pd.Series(dtype=str)).tolist()
        if _str(v).strip()
    }

    total_emision = df["emision_total_kgco2e"].sum()
    total_actividad = df["actividad_tkm"].sum()

    por_modo = (
        df.groupby("modo", as_index=False)
        .agg(
            segmentos=("segmento", "count"),
            masa_referencia_t=("masa_t", "mean"),
            distancia_carga_km=("distancia_carga_km", "sum"),
            actividad_tkm=("actividad_tkm", "sum"),
            emisiones_kgco2e=("emision_total_kgco2e", "sum"),
        )
        .sort_values("emisiones_kgco2e", ascending=False)
    )

    por_modo["intensidad_gco2e_tkm"] = por_modo.apply(
        lambda r: r["emisiones_kgco2e"] / r["actividad_tkm"] * 1000.0
        if r["actividad_tkm"] > 0
        else 0.0,
        axis=1,
    )
    por_modo["participacion_pct"] = (
        por_modo["emisiones_kgco2e"] / total_emision * 100.0
        if total_emision > 0
        else 0.0
    )

    resumen = {
        "emisiones_totales_kgco2e": float(total_emision),
        "actividad_total_tkm": float(total_actividad),
        "intensidad_cadena_gco2e_tkm": (
            float(total_emision / total_actividad * 1000.0)
            if total_actividad > 0
            else 0.0
        ),
        "numero_segmentos": int(len(df)),
        "numero_modos": int(df["modo"].nunique()),
        "frontera_energetica": next(iter(fronteras), "No informada"),
        "base_gwp": next(iter(bases_gwp), "No informada") if len(bases_gwp) <= 1 else "Según la fuente de cada factor",
    }

    return df, por_modo, resumen


# =============================================================================
# TRAZABILIDAD Y VALIDACIONES DE CADENA
# =============================================================================


def verificar_continuidad(segmentos: List[Dict[str, Any]]) -> List[str]:
    avisos: List[str] = []
    for i in range(len(segmentos) - 1):
        destino = _str(segmentos[i].get("destino")).strip().casefold()
        origen_sig = _str(segmentos[i + 1].get("origen")).strip().casefold()
        if destino and origen_sig and destino != origen_sig:
            avisos.append(
                f"Entre los segmentos {i + 1} y {i + 2} el destino y el origen siguiente no coinciden. "
                "Revise si existe un punto de transferencia no identificado o una diferencia de nomenclatura."
            )
    return avisos


def cobertura_por_categoria(df: pd.DataFrame, columna: str) -> pd.DataFrame:
    if df.empty or columna not in df.columns:
        return pd.DataFrame()
    total = df["emision_total_kgco2e"].sum()
    out = (
        df.groupby(columna, dropna=False, as_index=False)["emision_total_kgco2e"]
        .sum()
        .rename(columns={"emision_total_kgco2e": "emisiones_kgco2e"})
    )
    out["participacion_pct"] = (
        out["emisiones_kgco2e"] / total * 100.0 if total > 0 else 0.0
    )
    return out.sort_values("emisiones_kgco2e", ascending=False)


def dataframe_resultados(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    columnas = [
        "segmento",
        "origen",
        "destino",
        "modo",
        "unidad_carga",
        "masa_mercaderia_t",
        "masa_embalaje_t",
        "masa_equipo_t",
        "masa_operacional_t",
        "masa_t",
        "distancia_carga_km",
        "distancia_vacia_km",
        "actividad_tkm",
        "metodo_calculo",
        "clase_dato",
        "tier",
        "base_gwp",
        "frontera_calculo",
        "emision_total_kgco2e",
        "intensidad_resultante_g_tkm",
        "forma_consumo",
        "unidad_energia",
        "unidad_factor",
        "densidad_kg_l",
        "energia_asignada",
        "cantidad_factor_asignada",
        "fuente_factor",
        "anio_factor",
    ]
    disponibles = [c for c in columnas if c in df.columns]
    return df[disponibles].copy()


# =============================================================================
# REPORTE PDF
# =============================================================================


def generar_pdf(
    proyecto: Dict[str, str],
    df: pd.DataFrame,
    por_modo: pd.DataFrame,
    resumen: Dict[str, Any],
) -> bytes:
    buffer = io.BytesIO()

    with PdfPages(buffer) as pdf:
        fig = plt.figure(figsize=(11.69, 8.27))
        ax = fig.add_axes([0.04, 0.05, 0.92, 0.90])
        ax.axis("off")

        ax.text(0.0, 0.97, APP_SHORT, fontsize=18, fontweight="bold", va="top")
        ax.text(0.0, 0.925, METODOLOGIA, fontsize=9, va="top")
        ax.text(
            0.0,
            0.885,
            "Frontera: segmentos de transporte. Emisiones operativas de nodos logísticos excluidas.",
            fontsize=9,
            va="top",
        )

        info = (
            f"Proyecto: {proyecto.get('proyecto', '')}\n"
            f"Empresa u organización: {proyecto.get('empresa', '')}\n"
            f"Producto: {proyecto.get('producto', '')}\n"
            f"Fecha del reporte: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        ax.text(0.0, 0.82, info, fontsize=9, va="top")

        kpi = (
            f"Emisiones totales: {resumen['emisiones_totales_kgco2e']:,.2f} kg CO₂e\n"
            f"Actividad acumulada: {resumen['actividad_total_tkm']:,.2f} tkm\n"
            f"Intensidad de la cadena: {resumen['intensidad_cadena_gco2e_tkm']:,.2f} g CO₂e/tkm\n"
            f"Alcance del resultado: {_alcance_amigable(resumen['frontera_energetica'])}   Base GWP: {resumen['base_gwp']}\n"
            f"Tramos: {resumen['numero_segmentos']}   Modos: {resumen['numero_modos']}"
        )
        ax.text(0.0, 0.70, kpi, fontsize=12, fontweight="bold", va="top")

        tabla = por_modo.copy()
        if not tabla.empty:
            tabla = tabla[["modo", "distancia_carga_km", "actividad_tkm", "emisiones_kgco2e", "intensidad_gco2e_tkm", "participacion_pct"]]
            tabla.columns = [
                "Modo",
                "Dist. km",
                "Actividad tkm",
                "kg CO₂e",
                "g CO₂e/tkm",
                "% total",
            ]
            tabla = tabla.round(2)
            table = ax.table(
                cellText=tabla.values,
                colLabels=tabla.columns,
                cellLoc="center",
                colLoc="center",
                bbox=[0.0, 0.22, 1.0, 0.34],
            )
            table.auto_set_font_size(False)
            table.set_fontsize(8)

        ax.text(
            0.0,
            0.12,
            "Nota: los nodos logísticos pueden aparecer como puntos de transferencia entre segmentos, pero su consumo energético no forma parte del resultado cuantitativo.",
            fontsize=8,
            va="top",
            wrap=True,
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        ax = fig.add_axes([0.02, 0.04, 0.96, 0.92])
        ax.axis("off")
        ax.text(0.0, 0.98, "Trazabilidad por tramo", fontsize=16, fontweight="bold", va="top")

        tab = dataframe_resultados(df).copy()
        ren = {
            "segmento": "Tramo",
            "modo": "Modo",
            "masa_t": "Masa t",
            "distancia_carga_km": "Dist. km",
            "actividad_tkm": "tkm",
            "emision_total_kgco2e": "kg CO₂e",
            "intensidad_resultante_g_tkm": "g/tkm",
            "clase_dato": "Dato",
            "tier": "Nivel IPCC",
        }
        cols_pdf = [c for c in ren if c in tab.columns]
        tab = tab[cols_pdf].rename(columns=ren).round(2)
        if "Dato" in tab.columns:
            tab["Dato"] = tab["Dato"].map(lambda x: ETIQUETAS_CLASE_DATO.get(x, x))
        if "Nivel IPCC" in tab.columns:
            tab["Nivel IPCC"] = tab["Nivel IPCC"].map(lambda x: ETIQUETAS_NIVEL_IPCC.get(x, x))

        if len(tab) > 18:
            tab = tab.iloc[:18].copy()
            ax.text(0.0, 0.91, "Se muestran los primeros 18 segmentos. El archivo CSV conserva el detalle completo.", fontsize=8)

        table = ax.table(
            cellText=tab.values,
            colLabels=tab.columns,
            cellLoc="center",
            colLoc="center",
            bbox=[0.0, 0.12, 1.0, 0.72],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7)

        ax.text(
            0.0,
            0.05,
            "La clasificación por nivel metodológico IPCC es complementaria y solo se informa cuando corresponde a la metodología o fuente utilizada. No sustituye la clasificación del Marco GLEC entre datos primarios, modelados y por defecto.",
            fontsize=8,
            va="top",
            wrap=True,
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    buffer.seek(0)
    return buffer.getvalue()


# =============================================================================
# ESTADO DE STREAMLIT
# =============================================================================


def inicializar_estado() -> None:
    defaults = {
        "segmentos": [],
        "proyecto": "",
        "empresa": "",
        "producto": "",
        "responsable": "",
        "ruta_automatica_nueva": None,
        "preparar_siguiente_tramo": None,
        "mensaje_segmento_agregado": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    esquema = "solo_operacion_ttw_v1"
    if st.session_state.get("esquema_metodologico") != esquema:
        if st.session_state.get("segmentos"):
            st.session_state["segmentos"] = []
            st.session_state["mensaje_segmento_agregado"] = (
                "La metodología fue actualizada para cuantificar exclusivamente emisiones operativas. "
                "Los tramos de la sesión anterior fueron reiniciados para evitar mezclar fronteras de cálculo."
            )
        st.session_state["esquema_metodologico"] = esquema


# =============================================================================
# INTERFAZ
# =============================================================================


def cabecera() -> None:
    st.title(APP_TITLE)
    st.caption(f"{METODOLOGIA} | versión {VERSION}")
    st.info(
        "Uso rápido: 1) seleccione el modo y defina origen y destino; "
        "2) indique la carga; si conoce el consumo real puede informarlo, y si no, la herramienta aplica una intensidad documentada; "
        "3) agregue el tramo y repita hasta completar la operación. "
        "Los nodos logísticos se utilizan como puntos de conexión y sus emisiones operativas quedan fuera de la frontera del estudio."
    )


def panel_proyecto() -> Dict[str, str]:
    with st.expander("Identificación del estudio", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.session_state["proyecto"] = st.text_input(
                "Nombre del estudio o escenario",
                value=st.session_state["proyecto"],
            )
            st.session_state["empresa"] = st.text_input(
                "Empresa u organización",
                value=st.session_state["empresa"],
            )
        with c2:
            st.session_state["producto"] = st.text_input(
                "Producto o tipo de carga",
                value=st.session_state["producto"],
            )
            st.session_state["responsable"] = st.text_input(
                "Responsable del cálculo",
                value=st.session_state["responsable"],
            )

    return {
        "proyecto": st.session_state["proyecto"],
        "empresa": st.session_state["empresa"],
        "producto": st.session_state["producto"],
        "responsable": st.session_state["responsable"],
    }


def sidebar_factores() -> pd.DataFrame:
    st.sidebar.header("Configuración")
    st.sidebar.caption(
        "Para un cálculo estándar no necesita cargar archivos adicionales. "
        "La herramienta ya incluye perfiles nacionales y referencias internacionales identificadas."
    )

    uploaded = None
    with st.sidebar.expander("Base de factores personalizada", expanded=False):
        st.caption(
            "Utilice esta opción solo si su empresa o estudio dispone de factores propios o de una base documentada."
        )
        uploaded = st.file_uploader(
            "Cargar factores en CSV",
            type=["csv"],
            help="El archivo debe respetar la estructura de la plantilla disponible abajo.",
        )
        template = pd.DataFrame(columns=COLUMNAS_FACTORES_CSV)
        st.download_button(
            "Descargar plantilla CSV",
            data=template.to_csv(index=False).encode("utf-8-sig"),
            file_name="plantilla_factores_emision.csv",
            mime="text/csv",
        )

    catalogo, mensajes = cargar_catalogo(uploaded)
    for msg in mensajes:
        st.sidebar.warning(msg)

    with st.sidebar.expander("Información metodológica", expanded=False):
        st.markdown(
            """
            **Clase de dato**  
            Primario: medición o información específica de la operación.  
            Modelado: estimación construida con parámetros operacionales.  
            Por defecto: valor publicado utilizado cuando no existe información más representativa.

            **Nivel metodológico IPCC**  
            Se registra solo cuando corresponde a la metodología utilizada. No se asigna automáticamente un mismo nivel a toda la cadena.

            **Alcance de las emisiones**  
            La herramienta cuantifica únicamente las emisiones generadas durante la operación del transporte. El suministro previo de la energía queda fuera de la frontera del estudio.

            **Base GWP**  
            La base GWP informada por cada fuente se conserva únicamente como metadato de trazabilidad. El usuario no necesita seleccionarla para realizar el cálculo.
            """
        )

    return catalogo

def formulario_segmento(catalogo: pd.DataFrame) -> None:
    # Prepara el siguiente tramo antes de crear los widgets.
    siguiente = st.session_state.get("preparar_siguiente_tramo")
    if siguiente is not None:
        st.session_state["nuevo_origen"] = siguiente
        st.session_state["nuevo_destino"] = ""
        st.session_state["ruta_automatica_nueva"] = None
        st.session_state["preparar_siguiente_tramo"] = None

    mensaje = st.session_state.get("mensaje_segmento_agregado", "")
    if mensaje:
        st.success(mensaje)
        st.session_state["mensaje_segmento_agregado"] = ""

    st.subheader("Agregar un tramo de transporte")
    st.caption(
        "Un tramo es un desplazamiento de la carga realizado por un único modo. "
        "La herramienta calcula cada tramo por separado. Un único tramo puede analizarse como operación unimodal y varios tramos pueden consolidarse como una cadena multimodal."
    )

    # ------------------------------------------------------------------
    # Carga de referencia
    # ------------------------------------------------------------------
    st.markdown("### Carga de referencia")
    st.caption(
        "Defina cómo está presentada la carga y qué masa desea utilizar para la actividad de transporte. "
        "La masa de la expedición se expresa en toneladas y se utiliza para calcular tkm."
    )

    c_c1, c_c2 = st.columns(2)
    with c_c1:
        forma_carga = st.radio(
            "¿Cómo desea informar la carga?",
            ["Por masa", "Por unidad logística"],
            horizontal=True,
            help=(
                "Use 'Por masa' si conoce las toneladas de mercadería. Use 'Por unidad logística' "
                "si trabaja con palés, contenedores, ULD u otra unidad y conoce su peso por unidad."
            ),
        )
    with c_c2:
        unidad_carga = st.selectbox(
            "Unidad de carga",
            UNIDADES_CARGA,
            help=(
                "La unidad de carga describe cómo se mueve físicamente la expedición. La tara de un contenedor, "
                "ULD o equipo de transporte se registra por separado y no se suma automáticamente a la actividad en tkm."
            ),
        )

    alcance_masa = st.radio(
        "Masa considerada para la actividad de transporte",
        ["Mercadería + embalaje comercial", "Solo mercadería"],
        horizontal=True,
        help=(
            "Por defecto se recomienda mercadería + embalaje comercial. Palés, contenedores, ULD y otros equipos "
            "de transporte se registran aparte salvo que una metodología documentada indique que forman parte de la expedición."
        ),
    )

    masa_mercaderia_t = 0.0
    masa_embalaje_t = 0.0
    masa_equipo_t = 0.0
    numero_unidades = 0
    tara_unidad_kg = 0.0

    if forma_carga == "Por masa":
        mc1, mc2 = st.columns(2)
        with mc1:
            masa_mercaderia_t = st.number_input(
                "Masa neta de la mercadería [t]",
                min_value=0.001,
                value=13.0,
                step=0.1,
                help="Peso de los bienes transportados sin la tara del vehículo o equipo de transporte.",
            )
        with mc2:
            masa_embalaje_t = st.number_input(
                "Masa del embalaje comercial [t]",
                min_value=0.0,
                value=0.0,
                step=0.01,
                help="Cajas, bolsas, film u otros embalajes que acompañan a la mercadería.",
                disabled=alcance_masa == "Solo mercadería",
            )

        if unidad_carga != "Carga suelta o granel":
            with st.expander("Registrar tara de la unidad logística", expanded=False):
                eu1, eu2 = st.columns(2)
                with eu1:
                    numero_unidades = st.number_input(
                        "Cantidad de unidades",
                        min_value=0,
                        value=0,
                        step=1,
                        help="Número de palés, contenedores, ULD u otras unidades utilizadas.",
                    )
                with eu2:
                    tara_unidad_kg = st.number_input(
                        "Tara por unidad [kg]",
                        min_value=0.0,
                        value=0.0,
                        step=1.0,
                        help="Use la tara real de la unidad cuando esté disponible.",
                    )
                masa_equipo_t = numero_unidades * tara_unidad_kg / 1000.0
                st.caption(
                    "La tara se registra para conocer la masa física movilizada, pero no se incorpora automáticamente "
                    "a la masa de la expedición utilizada para tkm."
                )
    else:
        if unidad_carga == "Carga suelta o granel":
            st.info(
                "Para carga suelta o granel es más claro utilizar la opción **Por masa**. "
                "Se mantiene esta modalidad por flexibilidad."
            )
        uc1, uc2, uc3 = st.columns(3)
        with uc1:
            numero_unidades = st.number_input(
                "Número de unidades",
                min_value=1,
                value=1,
                step=1,
            )
        with uc2:
            peso_bruto_unidad_kg = st.number_input(
                "Peso bruto por unidad [kg]",
                min_value=0.001,
                value=1000.0,
                step=10.0,
                help="Peso medido de cada unidad logística antes de descontar su tara.",
            )
        with uc3:
            tara_unidad_kg = st.number_input(
                "Tara de la unidad logística [kg]",
                min_value=0.0,
                value=0.0,
                step=1.0,
                help="Peso del palé, contenedor, ULD u otro soporte vacío.",
            )
        masa_equipo_t = numero_unidades * tara_unidad_kg / 1000.0

        embalaje_por_unidad_kg = st.number_input(
            "Embalaje comercial por unidad [kg]",
            min_value=0.0,
            value=0.0,
            step=1.0,
            help=(
                "Complete este campo solo si necesita separar el embalaje comercial de la mercadería. "
                "No corresponde a la tara del palé, contenedor o ULD."
            ),
        )
        masa_embalaje_t = numero_unidades * embalaje_por_unidad_kg / 1000.0
        masa_bruta_total_t = numero_unidades * peso_bruto_unidad_kg / 1000.0
        masa_mercaderia_t = max(masa_bruta_total_t - masa_equipo_t - masa_embalaje_t, 0.0)

    masa_t = masa_mercaderia_t + (masa_embalaje_t if alcance_masa == "Mercadería + embalaje comercial" else 0.0)
    masa_operacional_t = masa_t + masa_equipo_t

    if masa_t > 0:
        cm1, cm2 = st.columns(2)
        cm1.metric("Masa de la expedición para tkm", f"{masa_t:,.3f} t")
        cm2.metric("Masa física registrada", f"{masa_operacional_t:,.3f} t")
        st.caption(
            "La primera masa se utiliza para la actividad en tkm. La segunda suma la tara registrada de la unidad logística "
            "y sirve como información operacional. Si el cálculo se basa en consumo real de combustible, el efecto de esa tara "
            "ya está reflejado en el consumo medido."
        )

    teu_equivalente = 0.0
    tipo_contenedor_teu = ""
    condicion_contenedor = "Seco"
    if unidad_carga == "Contenedor ISO":
        st.markdown("**Caracterización del contenedor**")
        tc0, tc1, tc2 = st.columns([1.0, 1.0, 1.2])
        with tc0:
            condicion_contenedor = st.selectbox(
                "Condición del contenedor",
                ["Seco", "Refrigerado (reefer)"],
                help=(
                    "La condición térmica influye en la intensidad de emisión marítima. "
                    "Los contenedores refrigerados requieren energía adicional durante el transporte."
                ),
            )
        with tc1:
            if forma_carga == "Por unidad logística":
                numero_contenedores_teu = int(numero_unidades)
                st.number_input(
                    "Número de contenedores",
                    min_value=1,
                    value=max(numero_contenedores_teu, 1),
                    step=1,
                    disabled=True,
                    help="Se toma del número de unidades logísticas informado arriba.",
                    key="numero_contenedores_teu_mostrado",
                )
            else:
                numero_contenedores_teu = st.number_input(
                    "Número de contenedores",
                    min_value=1,
                    value=max(int(numero_unidades or 1), 1),
                    step=1,
                    help="Necesario cuando se utilicen factores marítimos expresados por TEU-km.",
                )
        with tc2:
            tipo_contenedor_teu = st.selectbox(
                "Tipo de contenedor",
                list(TIPOS_CONTENEDOR_TEU.keys()),
                help=(
                    "La equivalencia TEU sigue las conversiones utilizadas por GLEC/ISO 14083: "
                    "20 pies = 1 TEU; 40 pies estándar = 2 TEU; 40 pies High Cube = 2,25 TEU."
                ),
            )
        teu_equivalente = float(numero_contenedores_teu) * TIPOS_CONTENEDOR_TEU[tipo_contenedor_teu]
        st.caption(f"Equivalencia utilizada: **{teu_equivalente:.2f} TEU**. Condición: **{condicion_contenedor}**.")

    # ------------------------------------------------------------------
    # 1. Modo y recorrido
    # ------------------------------------------------------------------
    st.markdown("### 1. Modo y recorrido")
    c_modo, c_dist = st.columns([1, 1.4])
    with c_modo:
        modo = st.selectbox(
            "Modo de transporte",
            MODOS,
            key="nuevo_modo",
            help="Seleccione el modo utilizado exclusivamente en este tramo.",
        )
    with c_dist:
        ingreso_distancia = st.radio(
            "¿Cómo desea definir la distancia?",
            MODOS_INGRESO_DISTANCIA,
            index=0,
            horizontal=True,
            key=f"nuevo_modo_distancia_{modo}",
            help=(
                "Automático: ruta vial para transporte carretero y GCD para los demás modos. "
                "Manual: permite ingresar kilómetros, millas terrestres o millas náuticas; el sistema normaliza todo a km."
            ),
        )

    c1, c2 = st.columns(2)
    with c1:
        origen = st.text_input(
            "Origen",
            key="nuevo_origen",
            placeholder=("Ej.: MVD o Aeropuerto de Carrasco" if modo == "Aéreo" else "Ej.: Rivera, Uruguay"),
            help=(
                "Puede ingresar ciudad, puerto, aeropuerto, terminal, dirección o código IATA en transporte aéreo. "
                "Para aeropuertos internacionales, los códigos de tres letras como MVD, POA o PVG suelen reducir ambigüedades."
            ),
        )
    with c2:
        destino = st.text_input(
            "Destino",
            key="nuevo_destino",
            placeholder=("Ej.: PVG o Shanghai Pudong International Airport" if modo == "Aéreo" else "Ej.: Puerto de Shanghai, China"),
            help="Agregue ciudad y país cuando el nombre pueda ser ambiguo.",
        )

    ruta_info: Optional[Dict[str, Any]] = None
    distancia_carga = 0.0
    metodo_dist = ""
    unidad_distancia_original = "km — kilómetros"
    distancia_original = 0.0

    if ingreso_distancia == "Calcular automáticamente":
        priorizar_uy = st.checkbox(
            "Usar Uruguay como respaldo cuando el nombre sea ambiguo",
            value=True,
            help=(
                "La búsqueda automática intenta primero localizar el lugar de forma global y específica para el modo. "
                "Uruguay se usa como segundo intento, por lo que destinos internacionales no quedan forzados al país."
            ),
        )

        if st.button("Calcular distancia", type="secondary", use_container_width=False):
            try:
                with st.spinner("Localizando los puntos y calculando la distancia..."):
                    st.session_state["ruta_automatica_nueva"] = calcular_distancia_automatica(
                        origen, destino, modo, priorizar_uy
                    )
            except Exception as exc:
                st.session_state["ruta_automatica_nueva"] = None
                st.error(
                    f"No fue posible calcular la distancia automáticamente: {exc} "
                    "Puede especificar mejor los lugares o utilizar el ingreso manual."
                )

        candidata = st.session_state.get("ruta_automatica_nueva")
        firma_actual = f"{modo}|{origen.strip().casefold()}|{destino.strip().casefold()}"
        if candidata and candidata.get("firma") == firma_actual:
            ruta_info = candidata
            distancia_carga = float(candidata["distancia_km"])
            distancia_original = distancia_carga
            metodo_dist = _str(candidata.get("metodo"))
            m1, m2 = st.columns(2)
            m1.metric("Distancia calculada", f"{distancia_carga:,.1f} km")
            if candidata.get("duracion_h"):
                m2.metric("Tiempo vial estimado", f"{candidata['duracion_h']:,.1f} h")
            else:
                m2.metric("Método", metodo_dist)
            st.caption(f"Origen reconocido: {candidata.get('origen_resuelto', '')}")
            st.caption(f"Destino reconocido: {candidata.get('destino_resuelto', '')}")
            with st.expander("Ver puntos localizados en el mapa", expanded=False):
                st.map(
                    pd.DataFrame(
                        {
                            "lat": [candidata.get("origen_lat"), candidata.get("destino_lat")],
                            "lon": [candidata.get("origen_lon"), candidata.get("destino_lon")],
                        }
                    )
                )
            st.info(_str(candidata.get("nota")))
        else:
            st.caption(
                "Ingrese origen y destino y pulse **Calcular distancia**. Todos los resultados se expresan en kilómetros."
            )
    else:
        c_dm1, c_dm2, c_dm3 = st.columns([1.1, 0.9, 1.5])
        with c_dm1:
            distancia_original = st.number_input(
                "Distancia informada",
                min_value=0.001,
                value=100.0,
                step=1.0,
                help="Ingrese la distancia disponible en la unidad que utilice su organización.",
            )
        with c_dm2:
            unidad_distancia_original = st.selectbox(
                "Unidad",
                list(UNIDADES_DISTANCIA.keys()),
                help="La herramienta convierte automáticamente millas y millas náuticas a kilómetros.",
            )
        with c_dm3:
            metodo_dist = st.selectbox(
                "Cómo se obtuvo la distancia",
                METODOS_DISTANCIA,
                help=(
                    "Real u operacional: recorrido efectivamente realizado. SFD: menor recorrido factible. "
                    "GCD: distancia geodésica entre coordenadas. Modelada: resultado de un modelo o estimación."
                ),
            )
        distancia_carga = distancia_original * UNIDADES_DISTANCIA[unidad_distancia_original]
        st.caption(f"Distancia normalizada para el cálculo: **{distancia_carga:,.2f} km**")

    # ------------------------------------------------------------------
    # 2. Factor de emisión
    # ------------------------------------------------------------------
    st.markdown("### 2. Factor de emisión")
    st.caption(
        "La herramienta aplica factores documentados integrados en su base metodológica. "
        "Cuando existe un factor compatible no es necesario que el usuario ingrese ningún valor manual."
    )

    # La herramienta utiliza una única frontera para todos los modos: emisiones
    # operativas durante el transporte. La base GWP se conserva como metadato de
    # la fuente, pero no se solicita al usuario ni se usa para elegir perfiles.
    frontera_objetivo = "TTW"

    st.markdown("**¿Qué información tiene disponible para este tramo?**")
    if modo == "Carretero":
        opciones_info = [
            "No conozco el consumo — calcular por actividad e intensidad de emisión",
            "Conozco el consumo real — calcular por combustible o energía",
        ]
    else:
        opciones_info = [
            "No conozco el consumo — calcular por actividad e intensidad de emisión",
            "Tengo consumo real documentado — cálculo avanzado por combustible o energía",
        ]
    disponibilidad_info = st.radio(
        "Disponibilidad de datos",
        opciones_info,
        index=0,
        horizontal=True,
        label_visibility="collapsed",
        help=(
            "Si no conoce el consumo del vehículo, tren, buque o aeronave, la herramienta utiliza una intensidad "
            "operacional documentada por unidad de actividad."
        ),
    )
    metodo_objetivo = "Intensidad de emisión" if disponibilidad_info.startswith("No conozco") else "Consumo energético"

    if modo == "Marítimo" and metodo_objetivo == "Intensidad de emisión":
        st.info(
            "No necesita conocer el combustible consumido por el buque. La herramienta calcula las emisiones operativas "
            "con una intensidad documentada. Para contenedores se utiliza TEU-km y para carga no contenerizada tkm."
        )
    elif modo == "Aéreo" and metodo_objetivo == "Intensidad de emisión":
        st.info(
            "No necesita conocer el combustible consumido por la aeronave. La herramienta usa la masa de la carga, "
            "la distancia y una intensidad operacional GLEC compatible con la longitud del vuelo."
        )

    filas_modo = catalogo[(catalogo["modo"] == modo) & (catalogo["metodo"] == metodo_objetivo)].copy()

    perfiles_modo: List[str] = []
    for _, fila in filas_modo.iterrows():
        data = fila.to_dict()
        if _frontera_perfil(data) != "TTW":
            continue
        usa_teu = any(
            _num(data.get(c)) > 0
            for c in ("intensidad_wtt_g_teukm", "intensidad_ttw_g_teukm", "intensidad_wtw_g_teukm")
        )
        if modo == "Marítimo" and metodo_objetivo == "Intensidad de emisión":
            if unidad_carga == "Contenedor ISO" and not usa_teu:
                continue
            if unidad_carga != "Contenedor ISO" and usa_teu:
                continue
        elif usa_teu and unidad_carga != "Contenedor ISO":
            continue
        perfiles_modo.append(_str(data.get("perfil")))

    if st.session_state["segmentos"]:
        st.caption(
            "Todos los tramos de la operación utilizan la misma frontera: solo emisiones generadas durante el transporte."
        )

    with st.expander("¿Qué diferencia hay entre los perfiles?", expanded=False):
        st.markdown(
            """
            **Perfil Uruguay · Emisiones operativas**  
            Utiliza parámetros nacionales cuando existe información aplicable al modo y al combustible.

            **Perfil GLEC · Emisiones operativas**  
            Utiliza la columna TTW operacional publicada por GLEC como referencia internacional cuando no se dispone de datos primarios o nacionales suficientes.

            **Factor propio documentado**  
            Es una alternativa avanzada y debe representar exclusivamente emisiones generadas durante la operación del transporte.
            """
        )

    perfil_recomendado = ""
    if metodo_objetivo == "Intensidad de emisión":
        if modo == "Marítimo" and unidad_carga == "Contenedor ISO":
            es_reefer = condicion_contenedor.startswith("Refrigerado")
            perfil_recomendado = (
                "GLEC v3.2 | Marítimo contenedor | Promedio industria | Reefer | Emisiones operativas"
                if es_reefer
                else "GLEC v3.2 | Marítimo contenedor | Promedio industria | Seco | Emisiones operativas"
            )
        elif modo == "Marítimo":
            perfil_recomendado = "GLEC v3.2 | Marítimo no contenerizado | Bulk carrier 10.000–34.999 dwt | Intensidad estándar"
        elif modo == "Carretero":
            perfil_recomendado = "GLEC v3.2 | Carretero | Camión articulado 34–40 t | Sudamérica/Europa | Intensidad operacional"
        elif modo == "Ferroviario":
            perfil_recomendado = "GLEC v3.2 | Ferroviario | Tracción diésel | Referencia internacional"
        elif modo == "Aéreo":
            perfil_recomendado = (
                "GLEC v3.2 | Aéreo | Configuración desconocida | Corta distancia <1500 km | Intensidad estándar"
                if distancia_carga > 0 and distancia_carga < 1500
                else "GLEC v3.2 | Aéreo | Configuración desconocida | Larga distancia >1500 km | Intensidad estándar"
            )
        elif modo == "Fluvial":
            perfil_recomendado = "GLEC v3.2 | Fluvial | Buque motor 85–110 m | Intensidad estándar"
    else:
        # Con consumo real se priorizan los factores operativos nacionales del Uruguay.
        recomendados_consumo = {
            "Carretero": "Uruguay | Carretero | Gasoil | Emisiones operativas",
            "Ferroviario": "Uruguay | Ferroviario | Gasoil | Emisiones operativas",
            "Marítimo": "Uruguay | Marítimo | Gasoil marino | Emisiones operativas",
            "Fluvial": "Uruguay | Fluvial | Gasoil marino | Emisiones operativas",
            "Aéreo": "Uruguay | Aéreo | Turbocombustible Jet A1 | Emisiones operativas",
        }
        perfil_recomendado = recomendados_consumo.get(modo, "")

    if not perfiles_modo:
        st.warning(
            "No hay un perfil operacional integrado compatible con esta configuración. "
            "Revise el modo, la unidad de carga o utilice un factor operacional propio documentado."
        )

    # La herramienta utiliza por defecto factores integrados y documentados.
    # La carga manual se mantiene como una alternativa avanzada y nunca se solicita
    # al usuario si existe un perfil compatible en la base de la herramienta.
    usar_factor_propio = False
    if perfil_recomendado and perfil_recomendado in perfiles_modo:
        otros_perfiles = [p for p in perfiles_modo if p != perfil_recomendado]
        opciones_perfil = [perfil_recomendado] + otros_perfiles
        perfil = st.selectbox(
            "Perfil de cálculo aplicado",
            opciones_perfil,
            index=0,
            key=f"nuevo_perfil_{modo}_{metodo_objetivo}",
            help=(
                "La herramienta propone automáticamente un perfil metodológico documentado compatible con el modo y la unidad de carga."
            ),
        )
        st.success(
            "El factor de emisión está incorporado en la herramienta y se aplica automáticamente. "
            "No necesita ingresar factores técnicos. La intensidad operacional se aplica automáticamente."
        )
        usar_factor_propio = st.checkbox(
            "Dispongo de un factor propio documentado y deseo sustituir el factor integrado",
            value=False,
            key=f"usar_factor_propio_{modo}_{metodo_objetivo}",
            help="Active esta opción solo si su empresa dispone de un factor oficial, contractual o técnicamente documentado.",
        )
    elif perfiles_modo:
        perfil = st.selectbox(
            "Perfil de cálculo aplicado",
            perfiles_modo,
            index=0,
            key=f"nuevo_perfil_{modo}_{metodo_objetivo}",
            help=(
                "La herramienta prioriza automáticamente los factores integrados compatibles. "
                "Para Uruguay se priorizan los perfiles nacionales cuando son metodológicamente compatibles con la operación."
            ),
        )
        st.success(
            "El factor de emisión está incorporado en la herramienta y se aplica automáticamente. "
            "No necesita ingresar un valor de factor manual."
        )
        usar_factor_propio = st.checkbox(
            "Dispongo de un factor propio documentado y deseo sustituir el factor integrado",
            value=False,
            key=f"usar_factor_propio_{modo}_{metodo_objetivo}",
            help="Active esta opción solo si dispone de una fuente técnica que publique el factor y su unidad.",
        )
    else:
        perfil = "Entrada manual"
        usar_factor_propio = True
        st.warning(
            "No hay un factor operacional integrado compatible con esta configuración. "
            "Para continuar deberá utilizar un factor operacional documentado propio o modificar la configuración del tramo."
        )

    if usar_factor_propio:
        perfil = "Entrada manual"

    perfil_data = perfil_a_dict(catalogo, perfil)

    if perfil_data:
        frontera_perfil = _frontera_perfil(perfil_data)
        alcance_txt = _alcance_amigable(frontera_perfil)
        if perfil.startswith("Uruguay"):
            st.success(f"**Perfil nacional.** Alcance: **{alcance_txt}**.")
        else:
            st.info(f"**Referencia internacional GLEC.** Alcance: **{alcance_txt}**.")
        with st.expander("Ver fuente y detalles del factor"):
            st.write(f"**Fuente del factor:** {perfil_data.get('fuente', '')}")
            st.write(f"**Año:** {perfil_data.get('anio', '')}")
            st.write(f"**Base GWP:** {perfil_data.get('base_gwp', '')}")
            if _num(perfil_data.get("densidad_kg_l")) > 0:
                st.write(f"**Densidad para conversión:** {_num(perfil_data.get('densidad_kg_l')):.3f} kg/L")
                st.write(f"**Fuente de la densidad:** {perfil_data.get('fuente_densidad', '')}")

    metodo_default = perfil_data.get("metodo") if perfil_data else metodo_objetivo
    metodo_index = METODOS_CALCULO.index(metodo_default) if metodo_default in METODOS_CALCULO else 1

    # ------------------------------------------------------------------
    # 3. Datos para el cálculo
    # ------------------------------------------------------------------
    if metodo_default == "Intensidad de emisión":
        if modo == "Marítimo" and perfil_data is not None:
            st.markdown("### 3. Actividad marítima")
            st.caption(
                "No necesita informar el consumo del buque ni completar factores de emisión manualmente. "
                "La herramienta combina la distancia del tramo con la unidad de carga y la intensidad documentada del perfil seleccionado."
            )
        else:
            st.markdown("### 3. Actividad e intensidad de emisión")
            st.caption(
                "Este método no requiere conocer el combustible consumido por el vehículo o buque. "
                "La emisión se calcula con la actividad de transporte y una intensidad documentada."
            )
    else:
        st.markdown("### 3. Consumo de combustible o energía")
        st.caption(
            "Use esta opción únicamente cuando disponga de datos reales o confiables de consumo y de un factor de emisión documentado. "
            "Para combustibles líquidos la interfaz prioriza litros; si el factor original está expresado por kg, la conversión se realiza internamente mediante una densidad documentada."
        )
        if modo != "Carretero":
            st.info(
                "Para usuarios de servicios marítimos, aéreos, ferroviarios o fluviales normalmente se recomienda el cálculo por "
                "**actividad e intensidad de emisión**, porque no suele disponerse del consumo total ni de factores energéticos específicos del vehículo."
            )

    metodo = metodo_default
    if perfil_data is None:
        st.caption(f"Método seleccionado según la disponibilidad de datos: **{metodo}**")
    else:
        st.caption(f"Método aplicado por el perfil seleccionado: **{metodo}**")

    valores: Dict[str, Any] = {}
    distancia_vacia = 0.0
    asignacion = 100.0

    intensidad_bloqueada = False
    if metodo == "Intensidad de emisión":
        int_wtt = int_ttw = int_wtw = 0.0
        int_wtt_teu = int_ttw_teu = int_wtw_teu = 0.0
        if perfil_data:
            int_wtt = _num(perfil_data.get("intensidad_wtt_g_tkm"))
            int_ttw = _num(perfil_data.get("intensidad_ttw_g_tkm"))
            int_wtw = _num(perfil_data.get("intensidad_wtw_g_tkm"))
            int_wtt_teu = _num(perfil_data.get("intensidad_wtt_g_teukm"))
            int_ttw_teu = _num(perfil_data.get("intensidad_ttw_g_teukm"))
            int_wtw_teu = _num(perfil_data.get("intensidad_wtw_g_teukm"))
            if any(v > 0 for v in (int_wtt_teu, int_ttw_teu, int_wtw_teu)):
                intensidad_total_teu = int_wtw_teu if int_wtw_teu > 0 else (int_wtt_teu + int_ttw_teu)
                st.caption(f"Intensidad aplicada automáticamente: **{intensidad_total_teu:.1f} g CO₂e/TEU-km**.")
                if teu_equivalente <= 0:
                    st.warning(
                        "Este perfil necesita TEU equivalentes. Seleccione **Contenedor ISO** como unidad de carga "
                        "e informe la cantidad y el tipo de contenedor."
                    )
                else:
                    st.caption(
                        f"Actividad para este factor: {teu_equivalente:.2f} TEU × {distancia_carga:,.2f} km "
                        f"= {teu_equivalente * distancia_carga:,.2f} TEU-km."
                    )
            else:
                intensidad_total = int_ttw
                st.caption(f"Intensidad aplicada automáticamente: **{intensidad_total:.2f} g CO₂e/tkm**.")
        else:
            if modo == "Marítimo":
                st.info(
                    "La distancia por sí sola no determina las emisiones. Para una carga marítima no contenerizada "
                    "también se necesita una intensidad representativa del servicio o tipo de buque. "
                    "Los campos manuales se muestran solo si usted dispone de ese factor."
                )
                habilitar_factor_manual = st.checkbox(
                    "Tengo una intensidad marítima documentada y deseo ingresarla",
                    value=False,
                )
            else:
                habilitar_factor_manual = True

            if habilitar_factor_manual:
                st.markdown("**Factor de intensidad documentado**")
                unidades_int_manual = ["g CO₂e/tkm"]
                if modo == "Marítimo" and unidad_carga == "Contenedor ISO":
                    unidades_int_manual.append("g CO₂e/TEU-km")
                unidad_int_manual = st.selectbox(
                    "Unidad de la intensidad",
                    unidades_int_manual,
                    help="Seleccione la unidad exactamente como aparece en la fuente documental.",
                )
                intensidad_manual = st.number_input(
                    f"Intensidad total [{unidad_int_manual}]",
                    min_value=0.0, value=0.0, step=0.1,
                    help="Ingrese un único valor total tal como aparece en la fuente documental.",
                )
                if intensidad_manual <= 0:
                    intensidad_bloqueada = True
                st.caption("El valor debe representar únicamente emisiones generadas durante la operación del transporte.")
                if unidad_int_manual == "g CO₂e/TEU-km":
                    int_ttw_teu = intensidad_manual
                else:
                    int_ttw = intensidad_manual
            else:
                intensidad_bloqueada = True
                st.caption("Seleccione un perfil marítimo integrado para continuar sin ingresar factores manuales.")

        valores.update(
            {
                "intensidad_wtt_g_tkm": int_wtt,
                "intensidad_ttw_g_tkm": int_ttw,
                "intensidad_wtw_g_tkm": int_wtw,
                "intensidad_wtt_g_teukm": int_wtt_teu,
                "intensidad_ttw_g_teukm": int_ttw_teu,
                "intensidad_wtw_g_teukm": int_wtw_teu,
                "forma_consumo": None,
                "unidad_energia": None,
                "unidad_factor": None,
                "densidad_kg_l": None,
                "fuente_densidad": "",
                "cantidad_energia_total": None,
                "consumo_por_100km_carga": None,
                "consumo_por_100km_vacio": None,
                "consumo_por_km_carga": None,
                "consumo_por_km_vacio": None,
                "rendimiento_km_l_carga": None,
                "rendimiento_km_l_vacio": None,
                "asignacion_pct": None,
                "ef_wtt_kgco2e_unidad": None,
                "ef_ttw_kgco2e_unidad": None,
                "ef_wtw_kgco2e_unidad": None,
            }
        )
    else:
        densidad = 0.0
        fuente_densidad = ""
        energia_bloqueada = False
        ef_wtw = 0.0
        if perfil_data:
            unidad_factor = _str(perfil_data.get("unidad_energia")) or "L"
            ef_wtt = _num(perfil_data.get("ef_wtt_kgco2e_unidad"))
            ef_ttw = _num(perfil_data.get("ef_ttw_kgco2e_unidad"))
            densidad = _num(perfil_data.get("densidad_kg_l"))
            fuente_densidad = _str(perfil_data.get("fuente_densidad"))

            # Para combustibles líquidos, la interfaz solicita litros aunque el factor original sea por kg.
            unidad = "L" if unidad_factor in {"L", "kg"} else unidad_factor
            ef_wtw = _num(perfil_data.get("ef_wtw_kgco2e_unidad"))
            factor_total_integrado = ef_ttw
            if unidad_factor == "kg" and densidad > 0:
                st.info(
                    f"El factor original está expresado por **kg de combustible**. Para facilitar el uso, ingrese el consumo en **litros**. "
                    f"La herramienta convierte L → kg con una densidad documentada de **{densidad:.3f} kg/L**."
                )
            st.caption(
                f"Factor de emisión aplicado automáticamente: **{factor_total_integrado:.5f} kg CO₂e/{unidad_factor}**. "
                f"Alcance: **{_alcance_amigable(_frontera_perfil(perfil_data))}**."
            )
        else:
            # La entrada manual de factores energéticos es una función avanzada.
            # El usuario informa un único factor total documentado y su alcance general.
            permitir_factor_energia_manual = True
            if modo != "Carretero":
                st.warning(
                    "Para este modo no es habitual que el usuario disponga del consumo energético y de un factor propio. "
                    "Si no cuenta con esos datos documentados, vuelva a **No conozco el consumo** y utilice un perfil por actividad e intensidad."
                )
                permitir_factor_energia_manual = st.checkbox(
                    "Dispongo de un factor de emisión documentado y deseo ingresarlo manualmente",
                    value=False,
                    help="Active esta opción únicamente si dispone de una fuente técnica que publique el factor y su unidad.",
                )

            if permitir_factor_energia_manual:
                mu1, mu2 = st.columns(2)
                with mu1:
                    unidad_factor = st.selectbox(
                        "Unidad original del factor",
                        UNIDADES_ENERGIA,
                        index=0,
                        help="Seleccione la unidad exactamente como aparece en la fuente documental del factor.",
                    )
                with mu2:
                    if unidad_factor == "kg":
                        entrada_litros = st.checkbox(
                            "Ingresar el consumo en litros",
                            value=True,
                            help="Mantiene el factor por kg y convierte internamente los litros mediante la densidad.",
                        )
                        unidad = "L" if entrada_litros else "kg"
                    else:
                        unidad = unidad_factor

                if unidad == "L" and unidad_factor == "kg":
                    densidad = st.number_input(
                        "Densidad del combustible [kg/L]",
                        min_value=0.001,
                        value=0.832,
                        step=0.001,
                        format="%.3f",
                        help="Use una densidad documentada y registre su fuente en la trazabilidad.",
                    )
                    fuente_densidad = st.text_input(
                        "Fuente de la densidad",
                        value="",
                        placeholder="Documento técnico o especificación del combustible",
                    )

                factor_manual_total = st.number_input(
                    f"Factor de emisión [kg CO₂e/{unidad_factor}]",
                    min_value=0.0, value=0.0, step=0.001, format="%.5f",
                    help="Ingrese el valor total exactamente como aparece en la fuente documental.",
                )
                st.caption("El factor debe corresponder únicamente a emisiones generadas durante la operación del transporte.")
                ef_wtt = 0.0
                ef_ttw = factor_manual_total
                ef_wtw = 0.0
            else:
                # No se solicita consumo ni factores al usuario cuando no dispone de datos documentados.
                # Debe utilizar el método por actividad e intensidad con un perfil integrado.
                unidad_factor = "L"
                unidad = "L"
                ef_wtt = 0.0
                ef_ttw = 0.0
                ef_wtw = 0.0
                energia_bloqueada = True

        cantidad_total = 0.0
        consumo_carga = 0.0
        consumo_carga_100 = 0.0
        rendimiento_carga = 0.0

        if energia_bloqueada:
            forma = None
            st.info(
                "No necesita completar factores técnicos. Seleccione **No conozco el consumo** en la sección 2 "
                "para que la herramienta utilice un perfil de intensidad compatible con este modo."
            )
        elif unidad == "L":
            forma = st.radio(
                "¿Cómo conoce el consumo de combustible?",
                FORMAS_CONSUMO_LIQUIDO,
                index=1 if modo == "Carretero" else 0,
                horizontal=True,
                help=(
                    "Elija la forma que coincida con los registros de su empresa. Todas las opciones se convierten a litros totales del tramo."
                ),
            )
            if forma == "Litros totales consumidos":
                cantidad_total = st.number_input(
                    "Combustible consumido en este tramo [L]",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    help="Litros utilizados desde el origen hasta el destino de este tramo.",
                )
            elif forma == "Consumo medio en L/100 km":
                consumo_carga_100 = st.number_input(
                    "Consumo medio con carga [L/100 km]",
                    min_value=0.0,
                    value=30.0 if modo == "Carretero" else 0.0,
                    step=0.1,
                    help="Ejemplo: si el vehículo consume 32 L cada 100 km, ingrese 32.",
                )
            else:
                rendimiento_carga = st.number_input(
                    "Rendimiento con carga [km/L]",
                    min_value=0.0,
                    value=3.0 if modo == "Carretero" else 0.0,
                    step=0.1,
                    help="Kilómetros recorridos por litro de combustible.",
                )
        else:
            forma = st.radio(
                "¿Cómo conoce el consumo de energía?",
                FORMAS_CONSUMO_GENERAL,
                index=0,
                horizontal=True,
            )
            if forma == "Consumo total del tramo":
                cantidad_total = st.number_input(
                    f"Consumo total del tramo [{unidad}]", min_value=0.0, value=0.0, step=0.1
                )
            elif forma == "Consumo por 100 km":
                consumo_carga_100 = st.number_input(
                    f"Consumo con carga [{unidad}/100 km]", min_value=0.0, value=0.0, step=0.1
                )
            else:
                consumo_carga = st.number_input(
                    f"Consumo con carga [{unidad}/km]", min_value=0.0, value=0.0, step=0.001, format="%.4f"
                )

        valores.update(
            {
                "intensidad_wtt_g_tkm": None,
                "intensidad_ttw_g_tkm": None,
                "intensidad_wtw_g_tkm": None,
                "intensidad_wtt_g_teukm": None,
                "intensidad_ttw_g_teukm": None,
                "intensidad_wtw_g_teukm": None,
                "forma_consumo": forma,
                "unidad_energia": unidad,
                "unidad_factor": unidad_factor,
                "densidad_kg_l": densidad,
                "fuente_densidad": fuente_densidad,
                "cantidad_energia_total": cantidad_total,
                "consumo_por_100km_carga": consumo_carga_100,
                "consumo_por_100km_vacio": 0.0,
                "consumo_por_km_carga": consumo_carga,
                "consumo_por_km_vacio": 0.0,
                "rendimiento_km_l_carga": rendimiento_carga,
                "rendimiento_km_l_vacio": 0.0,
                "asignacion_pct": 100.0,
                "ef_wtt_kgco2e_unidad": ef_wtt,
                "ef_ttw_kgco2e_unidad": ef_ttw,
                "ef_wtw_kgco2e_unidad": ef_wtw,
            }
        )

    # Metadatos y casos menos frecuentes.
    with st.expander("Opciones avanzadas y trazabilidad", expanded=perfil_data is None):
        distancia_vacia = st.number_input(
            "Distancia vacía asociada [km]",
            min_value=0.0,
            value=0.0,
            step=1.0,
            help=(
                "No genera actividad de carga en tkm. Úsela cuando el retorno o desplazamiento vacío forma parte del servicio."
            ),
        )

        if metodo == "Consumo energético" and not energia_bloqueada:
            if valores.get("unidad_energia") == "L" and valores.get("unidad_factor") == "kg":
                st.markdown("**Conversión de litros a la unidad original del factor**")
                densidad_editada = st.number_input(
                    "Densidad aplicada [kg/L]",
                    min_value=0.0,
                    value=float(valores.get("densidad_kg_l") or 0.0),
                    step=0.001,
                    format="%.3f",
                    help=(
                        "La densidad se usa únicamente para convertir los litros informados por el usuario a kg de combustible. "
                        "El factor de emisión original permanece expresado por kg."
                    ),
                    key=f"densidad_conversion_{modo}_{perfil}",
                )
                valores["densidad_kg_l"] = densidad_editada
                if valores.get("fuente_densidad"):
                    st.caption(f"Fuente de la densidad propuesta: {valores.get('fuente_densidad')}")

            asignacion = st.number_input(
                "Asignación del consumo a esta carga [%]",
                min_value=0.01,
                max_value=100.0,
                value=100.0,
                step=1.0,
                help="Mantenga 100% si todo el consumo corresponde a la expedición analizada.",
            )
            if valores.get("forma_consumo") in {"Consumo por 100 km", "Consumo medio en L/100 km"}:
                valores["consumo_por_100km_vacio"] = st.number_input(
                    f"Consumo en vacío [{valores.get('unidad_energia')}/100 km]",
                    min_value=0.0,
                    value=0.0,
                    step=0.1,
                )
            elif valores.get("forma_consumo") == "Consumo por km":
                valores["consumo_por_km_vacio"] = st.number_input(
                    f"Consumo en vacío [{valores.get('unidad_energia')}/km]",
                    min_value=0.0,
                    value=0.0,
                    step=0.001,
                    format="%.4f",
                )
            elif valores.get("forma_consumo") == "Rendimiento en km/L":
                valores["rendimiento_km_l_vacio"] = st.number_input(
                    "Rendimiento en vacío [km/L]",
                    min_value=0.0,
                    value=0.0,
                    step=0.1,
                )
            valores["asignacion_pct"] = asignacion

        # Los metadatos metodológicos de perfiles integrados se completan internamente.
        # El usuario común no necesita seleccionar Tier, base GWP ni clase de dato.
        if perfil_data is not None:
            clase_dato = _str(perfil_data.get("clase_dato"), "Predeterminado")
            tier = _str(perfil_data.get("tier"), "No aplica")
            fuente = _str(perfil_data.get("fuente"))
            base_gwp = _str(perfil_data.get("base_gwp"), "Según la fuente")
            anio = int(_num(perfil_data.get("anio"), datetime.now().year))
            if fuente:
                st.caption(f"Fuente metodológica aplicada automáticamente: {fuente}")
        else:
            clase_dato = "Primario"
            tier = "No aplica"
            fuente = st.text_area(
                "Fuente del factor o dato",
                value="",
                help="Registre el documento, base de datos u operador que sustenta el factor propio.",
            )
            base_gwp = "Según la fuente documentada"
            anio = st.number_input(
                "Año de la fuente",
                min_value=1990,
                max_value=2100,
                value=datetime.now().year,
                step=1,
            )

        observacion = st.text_area(
            "Observaciones",
            value="",
            placeholder="Información opcional sobre vehículo, servicio, combustible o supuestos del tramo.",
        )

    boton_bloqueado = bool(
        (metodo == "Consumo energético" and locals().get("energia_bloqueada", False))
        or (metodo == "Intensidad de emisión" and intensidad_bloqueada)
    )
    submitted = st.button(
        "Agregar tramo a la operación",
        type="primary",
        use_container_width=True,
        disabled=boton_bloqueado,
        help=(
            "Complete un perfil operacional integrado compatible antes de agregar este tramo."
            if boton_bloqueado else None
        ),
    )

    if submitted:
        if ingreso_distancia == "Calcular automáticamente" and ruta_info is None:
            st.error("Primero calcule la distancia automática o seleccione ingreso manual antes de agregar el tramo.")
            return
        if masa_t <= 0:
            st.error("La masa de la expedición debe ser mayor que cero.")
            return

        ruta_campos = {
            "origen_resuelto": ruta_info.get("origen_resuelto") if ruta_info else "",
            "destino_resuelto": ruta_info.get("destino_resuelto") if ruta_info else "",
            "origen_lat": ruta_info.get("origen_lat") if ruta_info else None,
            "origen_lon": ruta_info.get("origen_lon") if ruta_info else None,
            "destino_lat": ruta_info.get("destino_lat") if ruta_info else None,
            "destino_lon": ruta_info.get("destino_lon") if ruta_info else None,
            "nota_distancia": ruta_info.get("nota") if ruta_info else "",
            "modo_ingreso_distancia": ingreso_distancia,
            "distancia_original": distancia_original,
            "unidad_distancia_original": unidad_distancia_original,
        }

        seg = {
            "origen": origen.strip(),
            "destino": destino.strip(),
            "modo": modo,
            "forma_declaracion_carga": forma_carga,
            "unidad_carga": unidad_carga,
            "alcance_masa": alcance_masa,
            "masa_mercaderia_t": masa_mercaderia_t,
            "masa_embalaje_t": masa_embalaje_t,
            "masa_equipo_t": masa_equipo_t,
            "masa_operacional_t": masa_operacional_t,
            "numero_unidades": int(numero_unidades),
            "tara_unidad_kg": tara_unidad_kg,
            "masa_t": masa_t,
            "teu_equivalente": teu_equivalente,
            "tipo_contenedor_teu": tipo_contenedor_teu,
            "condicion_contenedor": condicion_contenedor,
            "distancia_carga_km": distancia_carga,
            "distancia_vacia_km": distancia_vacia,
            "metodo_distancia": metodo_dist,
            "metodo_calculo": metodo,
            "clase_dato": clase_dato,
            "tier": tier,
            "base_gwp": base_gwp.strip(),
            "fuente_factor": fuente.strip(),
            "anio_factor": int(anio),
            "perfil_factor": perfil,
            "observacion": observacion.strip(),
            **ruta_campos,
            **valores,
        }

        try:
            calcular_segmento(seg)
            if not fuente.strip():
                st.warning("El tramo no tiene una fuente documental registrada. Complete la fuente para mantener la trazabilidad.")
            if not base_gwp.strip():
                st.warning("No se identificó la base de GWP. Regístrela cuando el factor esté expresado en CO₂e.")
            st.session_state["segmentos"].append(seg)
            st.session_state["preparar_siguiente_tramo"] = destino.strip()
            st.session_state["ruta_automatica_nueva"] = None
            st.session_state["mensaje_segmento_agregado"] = (
                f"Tramo {len(st.session_state['segmentos'])} agregado correctamente. "
                "El destino quedó preparado como origen del siguiente tramo."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def gestionar_segmentos() -> None:
    segmentos = st.session_state["segmentos"]
    if not segmentos:
        st.warning("Todavía no se incorporaron tramos de transporte.")
        return

    st.subheader("Operación de transporte configurada" if len(segmentos) == 1 else "Cadena de transporte configurada")
    df_visual = pd.DataFrame(
        [
            {
                "Tramo": i,
                "Origen": s.get("origen", ""),
                "Destino": s.get("destino", ""),
                "Modo": s.get("modo", ""),
                "Unidad de carga": s.get("unidad_carga", ""),
                "Masa expedición [t]": s.get("masa_t", 0),
                "Distancia [km]": s.get("distancia_carga_km", 0),
                "Método": s.get("metodo_calculo", ""),
            }
            for i, s in enumerate(segmentos, start=1)
        ]
    )
    st.dataframe(df_visual, use_container_width=True, hide_index=True)

    avisos = verificar_continuidad(segmentos)
    for aviso in avisos:
        st.warning(aviso)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Eliminar último segmento"):
            st.session_state["segmentos"].pop()
            st.rerun()
    with c2:
        if st.button("Eliminar toda la operación"):
            st.session_state["segmentos"] = []
            st.rerun()


def panel_resultados(proyecto: Dict[str, str]) -> None:
    segmentos = st.session_state["segmentos"]
    if not segmentos:
        return

    try:
        df, por_modo, resumen = calcular_cadena(segmentos)
    except ValueError as exc:
        st.error(f"No fue posible calcular la operación: {exc}")
        return

    st.divider()
    st.header("Resultados")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Emisiones totales", f"{resumen['emisiones_totales_kgco2e']:,.2f} kg CO₂e")
    k2.metric("Actividad acumulada", f"{resumen['actividad_total_tkm']:,.2f} tkm")
    k3.metric("Intensidad de la cadena", f"{resumen['intensidad_cadena_gco2e_tkm']:,.2f} g CO₂e/tkm")
    k4.metric("Modos representados", f"{resumen['numero_modos']}")

    st.caption(
        f"Alcance del resultado: {_alcance_amigable(resumen['frontera_energetica'])}. "
        f"Base GWP: {resumen['base_gwp']}. "
        "La intensidad de la cadena utiliza como denominador la suma de la actividad de los segmentos de transporte. "
        "Las emisiones de los nodos logísticos no forman parte del numerador."
    )

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Por tramo", "Por modo", "Calidad de datos y nivel IPCC", "Trazabilidad"]
    )

    with tab1:
        vis = dataframe_resultados(df).copy()
        rename = {
            "segmento": "Tramo",
            "origen": "Origen",
            "destino": "Destino",
            "modo": "Modo",
            "unidad_carga": "Unidad de carga",
            "masa_mercaderia_t": "Mercadería [t]",
            "masa_embalaje_t": "Embalaje [t]",
            "masa_equipo_t": "Tara unidad logística [t]",
            "masa_operacional_t": "Masa física registrada [t]",
            "masa_t": "Masa expedición [t]",
            "distancia_carga_km": "Distancia [km]",
            "distancia_vacia_km": "Distancia vacía [km]",
            "actividad_tkm": "Actividad [tkm]",
            "metodo_calculo": "Método",
            "clase_dato": "Clase de dato",
            "tier": "Nivel IPCC",
            "base_gwp": "Base GWP",
            "frontera_calculo": "Alcance del cálculo",
            "emision_total_kgco2e": "Emisión contabilizada [kg CO₂e]",
            "intensidad_resultante_g_tkm": "Intensidad [g CO₂e/tkm]",
            "forma_consumo": "Forma de consumo",
            "unidad_energia": "Unidad informada",
            "unidad_factor": "Unidad original del factor",
            "densidad_kg_l": "Densidad [kg/L]",
            "energia_asignada": "Consumo asignado (unidad informada)",
            "cantidad_factor_asignada": "Cantidad convertida (unidad del factor)",
            "fuente_factor": "Fuente",
            "anio_factor": "Año",
        }
        vis = vis.rename(columns=rename)
        vis = vis.drop(columns=[c for c in ["emision_wtt_kgco2e", "emision_ttw_kgco2e"] if c in vis.columns], errors="ignore")
        if "Alcance del cálculo" in vis.columns:
            vis["Alcance del cálculo"] = vis["Alcance del cálculo"].map(_alcance_amigable)
        if "Clase de dato" in vis.columns:
            vis["Clase de dato"] = vis["Clase de dato"].map(lambda x: ETIQUETAS_CLASE_DATO.get(x, x))
        if "Nivel IPCC" in vis.columns:
            vis["Nivel IPCC"] = vis["Nivel IPCC"].map(lambda x: ETIQUETAS_NIVEL_IPCC.get(x, x))
        st.dataframe(vis, use_container_width=True, hide_index=True)

        graf_seg = df[["segmento", "emision_total_kgco2e"]].copy()
        graf_seg["segmento"] = graf_seg["segmento"].astype(str)
        graf_seg = graf_seg.set_index("segmento")
        st.bar_chart(graf_seg, y="emision_total_kgco2e")

    with tab2:
        modo_view = por_modo.rename(
            columns={
                "modo": "Modo",
                "segmentos": "Segmentos",
                "masa_referencia_t": "Masa media de expedición [t]",
                "distancia_carga_km": "Distancia [km]",
                "actividad_tkm": "Actividad [tkm]",
                "emisiones_kgco2e": "Emisiones [kg CO₂e]",
                "intensidad_gco2e_tkm": "Intensidad [g CO₂e/tkm]",
                "participacion_pct": "Participación [%]",
            }
        )
        st.dataframe(modo_view, use_container_width=True, hide_index=True)
        st.bar_chart(por_modo.set_index("modo"), y="emisiones_kgco2e")

    with tab3:
        st.markdown("**Distribución de las emisiones según la clase de dato**")
        calidad = cobertura_por_categoria(df, "clase_dato")
        if not calidad.empty:
            calidad["clase_dato"] = calidad["clase_dato"].map(lambda x: ETIQUETAS_CLASE_DATO.get(x, x))
            calidad = calidad.rename(columns={
                "clase_dato": "Clase de dato",
                "emisiones_kgco2e": "Emisiones [kg CO₂e]",
                "participacion_pct": "Participación [%]",
            })
        st.dataframe(calidad, use_container_width=True, hide_index=True)

        st.markdown("**Distribución de las emisiones según el nivel metodológico IPCC registrado**")
        tiers = cobertura_por_categoria(df, "tier")
        if not tiers.empty:
            tiers["tier"] = tiers["tier"].map(lambda x: ETIQUETAS_NIVEL_IPCC.get(x, x))
            tiers = tiers.rename(columns={
                "tier": "Nivel IPCC",
                "emisiones_kgco2e": "Emisiones [kg CO₂e]",
                "participacion_pct": "Participación [%]",
            })
        st.dataframe(tiers, use_container_width=True, hide_index=True)

        st.info(
            "La clase de dato y el nivel metodológico IPCC no son equivalentes. La primera describe la procedencia y representatividad del dato según la ISO 14083 y el Marco GLEC. "
            "El nivel IPCC se registra como atributo complementario cuando la metodología aplicada permite identificarlo."
        )

    with tab4:
        traz = df[
            [
                "segmento",
                "modo",
                "modo_ingreso_distancia",
                "metodo_distancia",
                "origen_resuelto",
                "destino_resuelto",
                "metodo_calculo",
                "perfil_factor",
                "fuente_factor",
                "anio_factor",
                "clase_dato",
                "tier",
                "base_gwp",
                "frontera_calculo",
            ]
        ].copy()
        traz["clase_dato"] = traz["clase_dato"].map(lambda x: ETIQUETAS_CLASE_DATO.get(x, x))
        traz["tier"] = traz["tier"].map(lambda x: ETIQUETAS_NIVEL_IPCC.get(x, x))
        if "frontera_calculo" in traz.columns:
            traz["frontera_calculo"] = traz["frontera_calculo"].map(_alcance_amigable)
        traz = traz.rename(columns={
            "segmento": "Tramo",
            "modo": "Modo",
            "modo_ingreso_distancia": "Ingreso de distancia",
            "metodo_distancia": "Método de distancia",
            "origen_resuelto": "Origen geolocalizado",
            "destino_resuelto": "Destino geolocalizado",
            "metodo_calculo": "Método de cálculo",
            "perfil_factor": "Perfil del factor",
            "fuente_factor": "Fuente del factor",
            "anio_factor": "Año",
            "clase_dato": "Clase de dato",
            "tier": "Nivel IPCC",
            "base_gwp": "Base GWP",
            "frontera_calculo": "Alcance del cálculo",
        })
        st.dataframe(traz, use_container_width=True, hide_index=True)

    st.subheader("Exportación")
    c1, c2, c3 = st.columns(3)

    csv_bytes = dataframe_resultados(df).to_csv(index=False).encode("utf-8-sig")
    c1.download_button(
        "Descargar resultados CSV",
        data=csv_bytes,
        file_name="resultados_emisiones_transporte.csv",
        mime="text/csv",
    )

    paquete = {
        "metadatos": {
            **proyecto,
            "fecha_calculo": datetime.now().isoformat(timespec="seconds"),
            "metodologia": METODOLOGIA,
            "version_herramienta": VERSION,
            "frontera": "Segmentos de transporte. Nodos logísticos excluidos de la cuantificación.",
        },
        "resumen": resumen,
        "segmentos": df.where(pd.notna(df), None).to_dict(orient="records"),
        "resumen_por_modo": por_modo.where(pd.notna(por_modo), None).to_dict(orient="records"),
    }
    c2.download_button(
        "Descargar trazabilidad JSON",
        data=json.dumps(paquete, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        file_name="trazabilidad_emisiones_transporte.json",
        mime="application/json",
    )

    if MATPLOTLIB_DISPONIBLE:
        pdf_bytes = generar_pdf(proyecto, df, por_modo, resumen)
        c3.download_button(
            "Descargar reporte PDF",
            data=pdf_bytes,
            file_name="reporte_emisiones_transporte.pdf",
            mime="application/pdf",
        )
    else:
        c3.info(
            "El reporte PDF estará disponible cuando Matplotlib esté instalado. "
            "Verifique que requirements.txt incluya matplotlib y reinicie la aplicación."
        )


def panel_metodologia() -> None:
    with st.expander("Notas metodológicas de la herramienta"):
        st.markdown(
            """
            **1. Unidad funcional y distancia**  
            La actividad de cada tramo se calcula como masa de la expedición por distancia con carga y se expresa en tkm. La distancia puede ingresarse manualmente o calcularse a partir de origen y destino. Para transporte carretero se utiliza una ruta vial automática mediante OSRM. Para transporte aéreo se utiliza GCD. En los modos ferroviario, marítimo y fluvial la GCD automática se presenta como referencia geográfica y debe sustituirse por la distancia operacional cuando esta esté disponible.

            **2. Cálculo por intensidad**  
            La emisión se obtiene multiplicando la actividad del segmento por una intensidad operacional documentada expresada en g CO₂e/tkm o, cuando corresponda, en g CO₂e/TEU-km. En todos los modos se cuantifican exclusivamente las emisiones generadas durante la operación del transporte.

            **3. Cálculo por consumo energético**  
            Cuando existe consumo real, la emisión se obtiene multiplicando la cantidad de combustible o energía asignada a la operación por un factor operacional documentado. La herramienta no incorpora emisiones asociadas al suministro previo de la energía o del combustible.

            **4. Viajes vacíos**  
            La distancia vacía no genera actividad de carga en tkm. Puede incorporarse al consumo energético cuando forma parte del servicio atribuible a la operación analizada.

            **5. Operaciones unimodales y multimodales**  
            Un único tramo puede analizarse de forma independiente como una operación unimodal. Cuando existen dos o más tramos, la herramienta los consolida en una cadena de transporte, que puede contener uno o varios modos. Cada modo conserva sus resultados individuales y también puede agregarse al resultado total.

            **6. Nodos logísticos**  
            Puertos, aeropuertos, terminales y centros de transferencia se consideran interfaces logísticas. Sus emisiones operativas no se cuantifican en esta versión porque se encuentran fuera de la frontera definida para el estudio.

            **7. Coherencia de agregación**  
            Todos los segmentos utilizan una única frontera cuantitativa: emisiones generadas durante la operación del transporte. Los factores que incorporan componentes fuera de esa frontera son rechazados por el núcleo de cálculo.

            **8. Trazabilidad**  
            Cada segmento conserva la fuente, el año, la clase de dato, el nivel IPCC cuando corresponda, la base GWP informada por la fuente y los parámetros de cálculo. Estos campos son metadatos de trazabilidad y no requieren intervención del usuario cuando se utiliza un perfil integrado.


            **9. Interpretación del resultado**  
            El resultado representa exclusivamente emisiones operativas de los segmentos de transporte definidos en el estudio. Esta delimitación debe declararse al comparar los resultados con inventarios o reportes que utilicen una frontera más amplia.
            """
        )


def main() -> None:
    st.set_page_config(page_title=APP_SHORT, page_icon="🌱", layout="wide")
    inicializar_estado()
    cabecera()
    catalogo = sidebar_factores()
    proyecto = panel_proyecto()
    formulario_segmento(catalogo)
    gestionar_segmentos()
    panel_resultados(proyecto)
    panel_metodologia()


if __name__ == "__main__":
    main()
