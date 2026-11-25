import datetime
import math
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def calcular_semana_comercial(fecha: datetime.date) -> str:
    """
    Calcula la semana del año en formato comercial YYWW (ej. 2026-11-10 -> '2646').
    """
    if not fecha:
        fecha = timezone.localdate()
    iso_year, iso_week, _ = fecha.isocalendar()
    return f"{str(iso_year)[-2:]}{iso_week:02d}"


def fecha_actual_local() -> datetime.date:
    return timezone.localdate()


class ProductoMaestro(models.Model):
    """
    Catálogo dinámico de Productos Maestros (administrable desde la UI por el Admin).
    """
    nombre = models.CharField("Producto Maestro", max_length=120, unique=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Producto Maestro"
        verbose_name_plural = "Productos Maestros"

    def __str__(self):
        return self.nombre


class VariedadCatalogo(models.Model):
    """
    Catálogo dinámico de Variedades asociadas a un Producto Maestro.
    """
    producto_maestro = models.ForeignKey(
        ProductoMaestro,
        related_name="variedades",
        on_delete=models.CASCADE,
    )
    nombre = models.CharField("Nombre de Variedad", max_length=120)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        unique_together = ("producto_maestro", "nombre")
        verbose_name = "Variedad de Catálogo"
        verbose_name_plural = "Variedades de Catálogo"

    def __str__(self):
        return f"{self.nombre} ({self.producto_maestro.nombre})"


class ParametroCatalogo(models.Model):
    """
    Catálogo dinámico de Parámetros de Calidad (inicializado con los 11 parámetros base,
    pero el Admin puede agregar nuevos parámetros sin tocar el código backend).
    """
    codigo = models.CharField("Código Interno", max_length=60, unique=True)
    nombre = models.CharField("Nombre del Parámetro", max_length=140)
    nombre_corto = models.CharField("Nombre Corto", max_length=60)
    descripcion = models.CharField("Descripción / Criterio", max_length=280, blank=True, default="")
    placeholder_obs = models.CharField(
        "Placeholder de Observación",
        max_length=180,
        blank=True,
        default="Ej: Detalle de observación o % de incidencia...",
    )
    sugerencias_csv = models.TextField(
        "Sugerencias rápidas separadas por coma",
        blank=True,
        default="",
    )
    es_critico = models.BooleanField("¿Es Parámetro Crítico?", default=False)
    orden = models.PositiveIntegerField("Orden de aparición", default=10)
    activo = models.BooleanField("Activo", default=True)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Parámetro de Calidad"
        verbose_name_plural = "Parámetros de Calidad"

    def save(self, *args, **kwargs):
        if not self.codigo:
            base_slug = slugify(self.nombre).replace("-", "_")[:50] or "parametro"
            slug = base_slug
            cnt = 1
            while ParametroCatalogo.objects.filter(codigo=slug).exclude(pk=self.pk).exists():
                cnt += 1
                slug = f"{base_slug}_{cnt}"
            self.codigo = slug
        if not self.nombre_corto:
            self.nombre_corto = self.nombre[:22]
        super().save(*args, **kwargs)

    def obtener_sugerencias(self):
        if not self.sugerencias_csv:
            return []
        return [s.strip() for s in self.sugerencias_csv.split(",") if s.strip()]

    def to_dict(self):
        return {
            "id": self.id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "nombre_corto": self.nombre_corto,
            "descripcion": self.descripcion,
            "placeholder_obs": self.placeholder_obs,
            "sugerencias": self.obtener_sugerencias(),
            "es_critico": self.es_critico,
            "orden": self.orden,
            "activo": self.activo,
        }

    def __str__(self):
        return f"{self.orden}. {self.nombre}"


class RecepcionGuia(models.Model):
    """
    Cabecera de una Guía de Recepción de Planta evaluada por el Personal de Descargue y Aseguradores de Calidad.
    Soporta guardado parcial en Descargue ('EN_CONTEO' o 'VALIDADO_DESCARGUE') y finalizado en Calidad ('COMPLETADA').
    """
    ESTADO_FLUJO_CHOICES = [
        ("EN_CONTEO", "En Conteo (Paso 2)"),
        ("VALIDADO_DESCARGUE", "Descargue Validado (Listo para Calidad)"),
        ("COMPLETADA", "Calificación Completada"),
    ]

    codigo_guia = models.CharField("Código / N° de Guía", max_length=60, unique=True)
    numero_factura = models.CharField("N° de Factura / Guía Física", max_length=140, blank=True, default="")
    fecha_factura = models.CharField("Fecha de Factura / Guía Física", max_length=120, blank=True, default="")
    fecha_recepcion = models.DateField("Fecha de Registro de Calificación", default=fecha_actual_local, db_index=True)
    semana = models.CharField("Semana del Año (YYWW)", max_length=10, db_index=True, default="")
    usuario = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recepciones",
    )
    responsable = models.CharField("Asegurador Responsable", max_length=120)
    responsable_descargue = models.CharField("Responsable de Descargue", max_length=120, blank=True, default="")
    producto_maestro = models.CharField("Producto Maestro", max_length=120, db_index=True)
    modulo_destino = models.CharField("Módulo / Destino", max_length=60, blank=True, default="")
    tiene_novedades_descargue = models.BooleanField("¿Hubo novedades en el descargue?", default=False)
    observaciones_descargue = models.TextField("Observaciones de Novedades en Descargue", blank=True, default="")
    observaciones_generales = models.TextField("Observaciones Generales de Guía", blank=True, default="")
    estado_flujo = models.CharField(
        "Estado del Flujo",
        max_length=25,
        choices=ESTADO_FLUJO_CHOICES,
        default="COMPLETADA",
    )

    # Totales consolidados de Primera Revisión (Muestreo 10% y 15 plantas: 5 inicio, 5 medio, 5 final)
    total_variedades_evaluadas = models.PositiveIntegerField(default=0)
    total_plantas_recibidas = models.PositiveIntegerField(default=0)
    total_contenedores_recibidos = models.PositiveIntegerField(default=0)
    total_contenedores_muestreados = models.PositiveIntegerField(default=0)
    total_plantas_muestreadas = models.PositiveIntegerField(default=0)

    # Calificaciones consolidadas por Producto Maestro (sobre 3.00 puntos y %)
    calificacion_producto_maestro = models.FloatField("Calificación Producto Maestro (/3.00)", default=0.0)
    calificacion_ponderada_maestro = models.FloatField("Calificación Ponderada (/3.00)", default=0.0)
    porcentaje_calidad_maestro = models.FloatField("Porcentaje de Calidad (%)", default=0.0)
    estado_calidad_maestro = models.CharField("Estado Global", max_length=30, default="MUY_BUENO")
    alerta_empaque_critica = models.BooleanField("Alerta Crítica de Empaque/Propa", default=False)

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_recepcion", "-id"]
        verbose_name = "Guía de Recepción"
        verbose_name_plural = "Guías de Recepción"

    def save(self, *args, **kwargs):
        if self.fecha_recepcion and not self.semana:
            self.semana = calcular_semana_comercial(self.fecha_recepcion)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Sem {self.semana} · {self.codigo_guia} - {self.producto_maestro} ({self.responsable})"

    def recalcular_consolidado(self):
        variedades = list(self.variedades.all())
        if not variedades:
            return

        self.semana = calcular_semana_comercial(self.fecha_recepcion)
        self.total_variedades_evaluadas = len(variedades)
        self.total_plantas_recibidas = sum(v.plantas_recibidas for v in variedades)
        self.total_contenedores_recibidos = sum(v.contenedores_recibidos for v in variedades)
        self.total_contenedores_muestreados = sum(v.contenedores_a_revisar for v in variedades)
        self.total_plantas_muestreadas = sum(v.total_plantas_a_revisar for v in variedades)

        promedio_simple = sum(v.calificacion_variedad for v in variedades) / len(variedades)
        self.calificacion_producto_maestro = round(promedio_simple, 2)

        if self.total_plantas_recibidas > 0:
            suma_ponderada = sum(v.calificacion_variedad * v.plantas_recibidas for v in variedades)
            self.calificacion_ponderada_maestro = round(suma_ponderada / self.total_plantas_recibidas, 2)
        else:
            self.calificacion_ponderada_maestro = self.calificacion_producto_maestro

        self.porcentaje_calidad_maestro = round((self.calificacion_producto_maestro / 3.0) * 100.0, 1)
        self.alerta_empaque_critica = any(v.alerta_empaque for v in variedades)

        if self.calificacion_producto_maestro >= 2.60:
            self.estado_calidad_maestro = "MUY_BUENO"
        elif self.calificacion_producto_maestro >= 1.85:
            self.estado_calidad_maestro = "REGULAR"
        else:
            self.estado_calidad_maestro = "MUY_MALO"

        self.save()


class EvaluacionVariedad(models.Model):
    """
    Evaluación de una Variedad recibida (puede consolidar 1 o varias guías/camiones de la misma variedad).
    """
    TIPO_CONTENEDOR_CHOICES = [
        ("BANDEJA", "Bandejas"),
        ("GAVETA", "Gavetas"),
    ]

    recepcion = models.ForeignKey(
        RecepcionGuia,
        related_name="variedades",
        on_delete=models.CASCADE,
    )
    variedad = models.CharField("Variedad", max_length=120, db_index=True)
    tipo_contenedor = models.CharField(
        "Tipo de Contenedor",
        max_length=20,
        choices=TIPO_CONTENEDOR_CHOICES,
        default="BANDEJA",
    )

    # Totales recibidos (suma automática de todas las guías/camiones de esta variedad)
    plantas_factura = models.PositiveIntegerField("Plantas según Factura/Guía", default=0)
    contenedores_factura = models.PositiveIntegerField("Bandejas o Gavetas según Factura/Guía", default=0)
    plantas_recibidas = models.PositiveIntegerField("Plantas que llegan (Conteo Físico)")
    contenedores_recibidos = models.PositiveIntegerField("Gavetas o Bandejas recibidas (Conteo Físico)")
    detalle_lotes_json = models.TextField(
        "Desglose de Guías/Camiones (JSON)",
        blank=True,
        default="[]",
    )

    # Cálculos de Primera Revisión (Muestreo 10% y 15 plantas por unidad: 5-5-5)
    plantas_por_contenedor = models.FloatField("Plantas por Gaveta/Bandeja", default=0.0)
    muestreo_diez_porciento_exacto = models.FloatField("10% Exacto de Contenedores", default=0.0)
    contenedores_a_revisar = models.PositiveIntegerField("Bandejas/Gavetas a Revisar (10%)", default=1)
    plantas_por_contenedor_muestreo = models.PositiveIntegerField("Plantas a revisar por contenedor", default=15)
    plantas_inicio = models.PositiveIntegerField("Plantas al Inicio", default=5)
    plantas_medio = models.PositiveIntegerField("Plantas al Medio", default=5)
    plantas_final = models.PositiveIntegerField("Plantas al Final", default=5)
    total_plantas_a_revisar = models.PositiveIntegerField("Total Plantas a Calificar", default=15)

    # Calificación por Variedad sobre 3 puntos y %
    suma_puntos = models.PositiveIntegerField("Suma de Puntos", default=33)
    maximo_puntos = models.PositiveIntegerField("Máximo de Puntos", default=33)
    calificacion_variedad = models.FloatField("Calificación Variedad (/3.00)", default=3.0)
    porcentaje_calidad = models.FloatField("Porcentaje Calidad Variedad (%)", default=100.0)
    estado_calidad = models.CharField("Estado de Calidad", max_length=30, default="MUY_BUENO")
    alerta_empaque = models.BooleanField("Alerta Crítica de Empaque/Identificación", default=False)
    observacion_general_variedad = models.TextField("Observación General de la Variedad", blank=True, default="")

    class Meta:
        ordering = ["id"]
        verbose_name = "Evaluación de Variedad"
        verbose_name_plural = "Evaluaciones de Variedad"

    def __str__(self):
        return f"{self.variedad} ({self.porcentaje_calidad:.1f}%)"

    def calcular_muestreo(self):
        if self.contenedores_recibidos > 0:
            self.plantas_por_contenedor = round(self.plantas_recibidas / self.contenedores_recibidos, 2)
            self.muestreo_diez_porciento_exacto = round(self.contenedores_recibidos * 0.10, 2)
            self.contenedores_a_revisar = max(1, math.ceil(self.contenedores_recibidos * 0.10))
        else:
            self.plantas_por_contenedor = 0.0
            self.muestreo_diez_porciento_exacto = 0.0
            self.contenedores_a_revisar = 0

        self.plantas_inicio = 5
        self.plantas_medio = 5
        self.plantas_final = 5
        self.plantas_por_contenedor_muestreo = 15
        self.total_plantas_a_revisar = self.contenedores_a_revisar * 15


class CalificacionParametro(models.Model):
    """
    Valoración individual de cada parámetro para una variedad:
    3 = Muy Bueno (Óptimo), 2 = Regular, 1 = Muy Malo (Grave) + Observaciones específicas.
    """
    PUNTAJE_CHOICES = [
        (3, "3 - Muy Bueno (Óptimo)"),
        (2, "2 - Regular"),
        (1, "1 - Muy Malo (Grave)"),
    ]

    evaluacion_variedad = models.ForeignKey(
        EvaluacionVariedad,
        related_name="parametros",
        on_delete=models.CASCADE,
    )
    codigo_parametro = models.CharField("Código del Parámetro", max_length=60)
    nombre_parametro = models.CharField("Nombre del Parámetro", max_length=140)
    puntaje = models.PositiveSmallIntegerField("Puntaje (1 a 3)", choices=PUNTAJE_CHOICES, default=3)
    observacion = models.TextField("Observaciones del Parámetro", blank=True, default="")

    class Meta:
        ordering = ["id"]
        verbose_name = "Calificación de Parámetro"
        verbose_name_plural = "Calificaciones de Parámetros"

    def __str__(self):
        return f"{self.nombre_parametro}: {self.puntaje}/3"


class FotoEvidencia(models.Model):
    """
    Evidencias fotográficas cargadas al finalizar la revisión de calidad
    o al escanear la guía física escrita a mano.
    """
    TIPO_FOTO_CHOICES = [
        ("CALIDAD", "Evidencia de Calidad de Planta"),
        ("GUIA_FISICA", "Foto de Guía Física / Factura"),
    ]

    recepcion = models.ForeignKey(
        RecepcionGuia,
        related_name="fotos",
        on_delete=models.CASCADE,
    )
    variedad = models.CharField("Variedad Asociada", max_length=120, blank=True, default="")
    tipo_foto = models.CharField(
        "Tipo de Foto",
        max_length=20,
        choices=TIPO_FOTO_CHOICES,
        default="CALIDAD",
    )
    imagen = models.FileField("Archivo de Foto", upload_to="evidencias/%Y/%m/")
    comentario = models.CharField("Observación de la Foto", max_length=240, blank=True, default="")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Foto de Evidencia"
        verbose_name_plural = "Fotos de Evidencia"

    def to_dict(self):
        return {
            "id": self.id,
            "variedad": self.variedad,
            "tipo_foto": self.tipo_foto,
            "url": self.imagen.url if self.imagen else "",
            "comentario": self.comentario,
            "creado_en": self.creado_en.strftime("%d/%m/%Y %H:%M") if self.creado_en else "",
        }

    def __str__(self):
        return f"Foto {self.tipo_foto} - {self.recepcion.codigo_guia}"


class PerfilUsuario(models.Model):
    """
    Rol operativo del usuario en la planta:
    - ADMIN: Administrador General con acceso a todo.
    - DESCARGUE: Personal de Descargue (Paso 1, 2 y Consolidado de Envío con Novedades).
    - CALIDAD: Asegurador de Calidad (Muestreo del 10% y Calificación 1-2-3).
    """
    ROL_CHOICES = [
        ("ADMIN", "Administrador General"),
        ("DESCARGUE", "Personal de Descargue"),
        ("CALIDAD", "Asegurador de Calidad"),
    ]

    user = models.OneToOneField(
        User,
        related_name="perfil",
        on_delete=models.CASCADE,
    )
    rol = models.CharField(
        "Rol del Usuario",
        max_length=20,
        choices=ROL_CHOICES,
        default="CALIDAD",
    )

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuario"

    def __str__(self):
        return f"{self.user.username} ({self.get_rol_display()})"

