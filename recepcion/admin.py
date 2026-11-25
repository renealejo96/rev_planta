from django.contrib import admin
from .models import (
    CalificacionParametro,
    EvaluacionVariedad,
    ParametroCatalogo,
    ProductoMaestro,
    RecepcionGuia,
    VariedadCatalogo,
)


class VariedadCatalogoInline(admin.TabularInline):
    model = VariedadCatalogo
    extra = 1


@admin.register(ProductoMaestro)
class ProductoMaestroAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo", "creado_en")
    search_fields = ("nombre",)
    inlines = [VariedadCatalogoInline]


@admin.register(ParametroCatalogo)
class ParametroCatalogoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "codigo", "orden", "es_critico", "activo")
    list_editable = ("orden", "es_critico", "activo")
    search_fields = ("nombre", "codigo")


class CalificacionParametroInline(admin.TabularInline):
    model = CalificacionParametro
    extra = 0


class EvaluacionVariedadInline(admin.TabularInline):
    model = EvaluacionVariedad
    extra = 0
    show_change_link = True


@admin.register(RecepcionGuia)
class RecepcionGuiaAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_guia",
        "fecha_recepcion",
        "semana",
        "responsable",
        "producto_maestro",
        "estado_flujo",
        "total_variedades_evaluadas",
        "total_plantas_recibidas",
        "porcentaje_calidad_maestro",
        "estado_calidad_maestro",
    )
    list_filter = ("estado_flujo", "semana", "producto_maestro", "responsable", "estado_calidad_maestro")
    search_fields = ("codigo_guia", "semana", "producto_maestro", "responsable")
    inlines = [EvaluacionVariedadInline]


@admin.register(EvaluacionVariedad)
class EvaluacionVariedadAdmin(admin.ModelAdmin):
    list_display = (
        "variedad",
        "recepcion",
        "tipo_contenedor",
        "plantas_recibidas",
        "contenedores_recibidos",
        "plantas_por_contenedor",
        "contenedores_a_revisar",
        "total_plantas_a_revisar",
        "porcentaje_calidad",
        "estado_calidad",
    )
    list_filter = ("tipo_contenedor", "estado_calidad", "alerta_empaque")
    search_fields = ("variedad", "recepcion__codigo_guia", "recepcion__producto_maestro")
    inlines = [CalificacionParametroInline]
