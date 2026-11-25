import json
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .catalog import (
    asegurar_semilla_catalogos,
    cargar_maestro_pros,
    cargar_responsables,
    obtener_parametros_activos,
)
from .models import RecepcionGuia


class RecepcionPlantaTests(TestCase):
    def setUp(self):
        asegurar_semilla_catalogos()

    def test_carga_catalogos_y_usuarios_semilla(self):
        maestro_dict, _ = cargar_maestro_pros()
        responsables = cargar_responsables()
        parametros = obtener_parametros_activos()

        self.assertIn("VERONICA", maestro_dict)
        self.assertEqual(len(maestro_dict["VERONICA"]), 9)
        self.assertIn("SOLIDAGO", maestro_dict)
        self.assertIn("GOLDEN GLORY YELLOW", maestro_dict["SOLIDAGO"])
        self.assertIn("JUAN DE LA TORRE", responsables)
        self.assertEqual(len(parametros), 11)
        self.assertTrue(User.objects.filter(username="admin").exists())
        self.assertTrue(User.objects.filter(username="juan").exists())

    def test_multiples_camiones_misma_variedad_y_guardado_paso2_paso4(self):
        juan = User.objects.get(username="juan")
        self.client.force_login(juan)

        # Paso 2: Llegan 2 camiones de GOLDEN GLORY YELLOW (SOLIDAGO) y se guarda el conteo (EN_CONTEO)
        payload_paso2 = {
            "estado_flujo": "EN_CONTEO",
            "fecha_recepcion": "2026-11-10",
            "producto_maestro": "SOLIDAGO",
            "codigo_guia": "REC-2646-001",
            "variedades": [
                {
                    "variedad": "GOLDEN GLORY YELLOW",
                    "tipo_contenedor": "BANDEJA",
                    "lotes": [
                        {"guia_camion": "Camión 1", "plantas": 1200, "contenedores": 12},
                        {"guia_camion": "Camión 2", "plantas": 800, "contenedores": 8},
                    ],
                    "parametros": {},
                }
            ],
        }

        resp2 = self.client.post(
            reverse("recepcion:api_guardar_recepcion"),
            data=json.dumps(payload_paso2),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["ok"])
        self.assertEqual(data2["estado_flujo"], "EN_CONTEO")
        rec_id = data2["recepcion_id"]

        rec_paso2 = data2["recepcion"]
        # Verificar que el responsable se mapeó automáticamente con el usuario logueado (JUAN DE LA TORRE)
        self.assertEqual(rec_paso2["responsable"], "JUAN DE LA TORRE")
        self.assertEqual(rec_paso2["semana"], "2646")
        # 1200 + 800 = 2000 plantas; 12 + 8 = 20 bandejas -> 10% = 2 bandejas -> 30 plantas a calificar
        self.assertEqual(rec_paso2["total_plantas_recibidas"], 2000)
        self.assertEqual(rec_paso2["total_contenedores_recibidos"], 20)
        self.assertEqual(rec_paso2["total_contenedores_muestreados"], 2)
        self.assertEqual(rec_paso2["total_plantas_muestreadas"], 30)

        # Paso 4: Se completa la calificación actualizando el mismo registro (sin duplicar)
        payload_paso4 = {
            "recepcion_id": rec_id,
            "estado_flujo": "COMPLETADA",
            "fecha_recepcion": "2026-11-10",
            "producto_maestro": "SOLIDAGO",
            "codigo_guia": "REC-2646-001",
            "variedades": [
                {
                    "variedad": "GOLDEN GLORY YELLOW",
                    "tipo_contenedor": "BANDEJA",
                    "lotes": [
                        {"guia_camion": "Camión 1", "plantas": 1200, "contenedores": 12},
                        {"guia_camion": "Camión 2", "plantas": 800, "contenedores": 8},
                    ],
                    "parametros": {
                        "sanidad": {"puntaje": 1, "observacion": "39% botrytis"},
                    },
                }
            ],
        }
        resp4 = self.client.post(
            reverse("recepcion:api_guardar_recepcion"),
            data=json.dumps(payload_paso4),
            content_type="application/json",
        )
        self.assertEqual(resp4.status_code, 200)
        data4 = resp4.json()
        self.assertTrue(data4["ok"])
        self.assertEqual(data4["estado_flujo"], "COMPLETADA")
        self.assertEqual(RecepcionGuia.objects.count(), 1)

    def test_permisos_admin_crear_usuario_parametro_y_producto(self):
        admin_user = User.objects.get(username="admin")
        self.client.force_login(admin_user)

        # 1. Crear nuevo parámetro de calidad
        r_param = self.client.post(
            reverse("recepcion:api_admin_crear_parametro"),
            data=json.dumps(
                {
                    "nombre": "GROSOR DE TALLO",
                    "descripcion": "Calibre del tallo",
                    "sugerencias_csv": "Tallo delgado, Óptimo",
                    "es_critico": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(r_param.status_code, 200)
        self.assertEqual(len(obtener_parametros_activos()), 12)

        # 2. Crear nuevo usuario asegurador
        r_user = self.client.post(
            reverse("recepcion:api_admin_crear_usuario"),
            data=json.dumps(
                {
                    "username": "pedro",
                    "nombre_completo": "Pedro Gómez",
                    "password": "1234",
                    "es_admin": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(r_user.status_code, 200)
        self.assertTrue(User.objects.filter(username="pedro").exists())

        # 3. Crear nuevo Producto Maestro con variedades
        r_prod = self.client.post(
            reverse("recepcion:api_admin_crear_producto_variedad"),
            data=json.dumps(
                {
                    "producto_maestro": "DELPHINIUM",
                    "variedades": "GUARDIAN BLUE, CENTURION WHITE",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(r_prod.status_code, 200)
        maestro_dict, _ = cargar_maestro_pros()
        self.assertIn("DELPHINIUM", maestro_dict)
        self.assertIn("GUARDIAN BLUE", maestro_dict["DELPHINIUM"])
