"""
Sincronización y gestión de catálogos dinámicos (Productos Maestros, Variedades,
Parámetros de Calidad y Usuarios/Aseguradores) con soporte inicial desde cosechas/*.csv.
"""

import csv
from collections import defaultdict
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.text import slugify

from .models import ParametroCatalogo, ProductoMaestro, VariedadCatalogo

PARAMETROS_INICIALES = [
    {
        "codigo": "raiz_deficiente",
        "nombre": "Raíz Deficiente",
        "nombre_corto": "Raíz Def.",
        "descripcion": "Presencia de raíz pobre, necrosada, corta o con falta de desarrollo activo.",
        "placeholder_obs": "Ej: 20% raíz corta, puntas oxidadas...",
        "sugerencias": ["10% raíz débil", "25% raíz corta", "Necrosis radicular"],
        "es_critico": False,
    },
    {
        "codigo": "enraizamiento",
        "nombre": "Enraizamiento",
        "nombre_corto": "Enraizam.",
        "descripcion": "Nivel de anclaje, formación de cepellón y cohesión de raíces con el sustrato.",
        "placeholder_obs": "Ej: Cepellón flojo en 15% de plantas...",
        "sugerencias": ["15% cepellón suelto", "Desprendimiento de sustrato", "Raíz poco activa"],
        "es_critico": False,
    },
    {
        "codigo": "agallas",
        "nombre": "Agallas",
        "nombre_corto": "Agallas",
        "descripcion": "Inspección de tumores o agallas en corona, cuello o sistema radicular.",
        "placeholder_obs": "Ej: 5% presencia de agallas en cuello...",
        "sugerencias": ["5% agallas en corona", "Agallas radiculares leves", "Síntomas severos"],
        "es_critico": True,
    },
    {
        "codigo": "dano_follaje",
        "nombre": "Daño en Follaje",
        "nombre_corto": "Daño Follaje",
        "descripcion": "Integridad física de hojas y tallos (maltrato mecánico, hojas rotas o aplastadas).",
        "placeholder_obs": "Ej: 12% hojas quebradas por apilamiento...",
        "sugerencias": ["10% daño mecánico", "Hojas basales maltratadas", "Aplastamiento"],
        "es_critico": False,
    },
    {
        "codigo": "sanidad",
        "nombre": "Sanidad (Punto de Alimentación / Quemazón)",
        "nombre_corto": "Sanidad",
        "descripcion": "Evaluación fitosanitaria: punto de alimentación, quemazón foliar, Botrytis, plagas.",
        "placeholder_obs": "Ej: 39% botrytis, 15% quemazón, punto de alimentación...",
        "sugerencias": ["39% botrytis", "15% quemazón", "Punto de alimentación", "Presencia de trips"],
        "es_critico": True,
    },
    {
        "codigo": "plantas_con_hijos",
        "nombre": "Plantas con Hijos",
        "nombre_corto": "Con Hijos",
        "descripcion": "Presencia de brotes laterales, hijuelos basales o bifurcaciones no deseadas.",
        "placeholder_obs": "Ej: 18% plantas con brotes laterales...",
        "sugerencias": ["10% con hijuelos", "20% brotes laterales", "Bifurcación apical"],
        "es_critico": False,
    },
    {
        "codigo": "induccion_floral",
        "nombre": "Inducción Floral",
        "nombre_corto": "Ind. Floral",
        "descripcion": "Presencia prematura de botón floral o diferenciación reproductiva en planta joven.",
        "placeholder_obs": "Ej: 8% plantas inducidas / con botón...",
        "sugerencias": ["5% botón visible", "15% inducción prematura", "Alta inducción floral"],
        "es_critico": False,
    },
    {
        "codigo": "elongacion",
        "nombre": "Elongación",
        "nombre_corto": "Elongación",
        "descripcion": "Estiramiento excesivo de entrenudos o altura fuera de la especificación técnica.",
        "placeholder_obs": "Ej: 25% plantas elongadas (> estándar)...",
        "sugerencias": ["15% entrenudo largo", "30% plantas elongadas", "Ahilamiento"],
        "es_critico": False,
    },
    {
        "codigo": "empaque",
        "nombre": "Empaque e Identificación desde Propa",
        "nombre_corto": "Empaque / ID",
        "descripcion": "Estado del contenedor y correcta identificación de variedades desde propagación (GRAVÍSIMO si está mal identificado).",
        "placeholder_obs": "Ej: ¡GRAVE! Variedad mal identificada desde propa...",
        "sugerencias": [
            "¡GRAVÍSIMO! Mal identificado desde propa",
            "Mezcla de variedades en bandeja",
            "Etiqueta ilegible o faltante",
        ],
        "es_critico": True,
    },
    {
        "codigo": "humedad",
        "nombre": "Humedad (Secas vs. Capacidad de Campo)",
        "nombre_corto": "Humedad",
        "descripcion": "Condición hídrica al arribo: plantas secas, deshidratadas o en capacidad de campo.",
        "placeholder_obs": "Ej: Sustrato seco en bordes (30%) / Capacidad de campo...",
        "sugerencias": ["Capacidad de campo", "25% celdas secas en bordes", "Planta seca"],
        "es_critico": False,
    },
    {
        "codigo": "uniformidad",
        "nombre": "Uniformidad dentro de Bandeja o Gaveta",
        "nombre_corto": "Uniformidad",
        "descripcion": "Homogeneidad de porte y vigor entre inicio (5), medio (5) y final (5) de la bandeja o gaveta.",
        "placeholder_obs": "Ej: Plantas pequeñas al final de la bandeja (20% desuniforme)...",
        "sugerencias": ["15% desuniformidad de porte", "Bordes más pequeños", "Tamaños mezclados"],
        "es_critico": False,
    },
]


def asegurar_semilla_catalogos():
    """
    Inicializa en la base de datos (si aún está vacía):
    1. Los 11 parámetros de calidad iniciales.
    2. Los Productos Maestros y Variedades desde cosechas/MAESTRO_PROS.csv.
    3. El usuario Admin y los usuarios Aseguradores desde cosechas/responsables.csv.
    """
    # 1. Semilla de Parámetros de Calidad
    if ParametroCatalogo.objects.count() == 0:
        for idx, p in enumerate(PARAMETROS_INICIALES, start=1):
            ParametroCatalogo.objects.create(
                codigo=p["codigo"],
                nombre=p["nombre"],
                nombre_corto=p["nombre_corto"],
                descripcion=p["descripcion"],
                placeholder_obs=p["placeholder_obs"],
                sugerencias_csv=", ".join(p["sugerencias"]),
                es_critico=p["es_critico"],
                orden=idx,
                activo=True,
            )

    # 2. Semilla de Productos Maestros y Variedades desde cosechas/MAESTRO_PROS.csv
    if ProductoMaestro.objects.count() == 0:
        ruta_maestro = settings.COSECHAS_DIR / "MAESTRO_PROS.csv"
        if ruta_maestro.exists():
            mapa_pm = {}
            with open(ruta_maestro, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    variedad = (row.get("variedad") or "").strip().upper()
                    prod_maestro = (row.get("prod_maestro") or "").strip().upper()
                    if not variedad or not prod_maestro or prod_maestro == "VACIO":
                        continue
                    if prod_maestro not in mapa_pm:
                        mapa_pm[prod_maestro], _ = ProductoMaestro.objects.get_or_create(
                            nombre=prod_maestro
                        )
                    VariedadCatalogo.objects.get_or_create(
                        producto_maestro=mapa_pm[prod_maestro],
                        nombre=variedad,
                    )

    # 3. Semilla de Usuarios (Admin + Aseguradores de responsables.csv)
    if not User.objects.filter(username="admin").exists():
        admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@calidadplanta.local",
            password="admin123",
        )
        admin_user.first_name = "Administrador General"
        admin_user.save()

    ruta_resp = settings.COSECHAS_DIR / "responsables.csv"
    nombres_resp = []
    if ruta_resp.exists():
        with open(ruta_resp, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            nombres_resp = [
                (r.get("responsable") or "").strip().upper()
                for r in reader
                if (r.get("responsable") or "").strip()
            ]

    # Limpiar usuarios antiguos de prueba que ya no están en responsables.csv
    User.objects.filter(username__in=["kevin", "marcia", "carlos", "gabriela"]).delete()

    for nombre_completo in nombres_resp:
        primer_nombre = slugify(nombre_completo.split()[0]) or "operario"
        u, created = User.objects.get_or_create(username=primer_nombre)
        if created:
            u.set_password("1234")
            u.is_staff = False
        u.first_name = nombre_completo
        u.save()


def obtener_parametros_activos():
    asegurar_semilla_catalogos()
    return [p.to_dict() for p in ParametroCatalogo.objects.filter(activo=True).order_by("orden", "id")]


def cargar_maestro_pros():
    """
    Retorna el diccionario { 'PRODUCTO_MAESTRO': ['VARIEDAD 1', ...] } desde la base de datos
    (sincronizada inicialmente desde cosechas/MAESTRO_PROS.csv).
    """
    asegurar_semilla_catalogos()
    dict_maestro = {}
    productos = ProductoMaestro.objects.filter(activo=True).prefetch_related("variedades").order_by("nombre")
    lista_pares = []

    for pm in productos:
        vars_activas = [
            v.nombre for v in pm.variedades.filter(activo=True).order_by("nombre")
        ]
        dict_maestro[pm.nombre] = vars_activas
        for vn in vars_activas:
            lista_pares.append({"variedad": vn, "prod_maestro": pm.nombre})

    return dict_maestro, lista_pares


def guardar_en_csv_maestro(prod_maestro: str, variedad: str):
    """
    Mantiene sincronizado cosechas/MAESTRO_PROS.csv cuando el Admin crea nuevos productos/variedades.
    """
    import sys
    if "test" in sys.argv:
        return
    ruta = settings.COSECHAS_DIR / "MAESTRO_PROS.csv"
    if not ruta.exists():
        return
    try:
        with open(ruta, mode="r", encoding="utf-8-sig") as f:
            lineas = f.read()
        par_str = f"{variedad};{prod_maestro}"
        if par_str not in lineas:
            with open(ruta, mode="a", encoding="utf-8") as f:
                if not lineas.endswith("\n"):
                    f.write("\n")
                f.write(f"{par_str}\n")
    except OSError:
        pass


def sincronizar_csv_responsables():
    """
    Actualiza cosechas/responsables.csv con los aseguradores activos en el sistema.
    """
    import sys
    if "test" in sys.argv:
        return
    ruta = settings.COSECHAS_DIR / "responsables.csv"
    try:
        usuarios = User.objects.filter(is_active=True, is_superuser=False).order_by("id")
        with open(ruta, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["responsable"])
            for u in usuarios:
                nombre = (u.first_name or u.username).strip()
                if nombre:
                    writer.writerow([nombre])
    except OSError:
        pass


def cargar_responsables():
    asegurar_semilla_catalogos()
    usuarios = User.objects.filter(is_active=True).order_by("id")
    nombres = []
    for u in usuarios:
        nombre = (u.first_name or u.username).strip()
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    return nombres
