import base64
import csv
import datetime
import difflib
import io
import json
import os
import re
import urllib.error
import urllib.request
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .catalog import (
    asegurar_semilla_catalogos,
    cargar_maestro_pros,
    cargar_responsables,
    guardar_en_csv_maestro,
    obtener_parametros_activos,
    sincronizar_csv_responsables,
)
from .models import (
    CalificacionParametro,
    EvaluacionVariedad,
    FotoEvidencia,
    ParametroCatalogo,
    PerfilUsuario,
    ProductoMaestro,
    RecepcionGuia,
    VariedadCatalogo,
    calcular_semana_comercial,
)


def generar_siguiente_codigo_guia(fecha: datetime.date = None):
    if not fecha:
        fecha = timezone.localdate()
    semana = calcular_semana_comercial(fecha)
    prefijo = f"REC-{semana}-"
    ultimo = (
        RecepcionGuia.objects.filter(codigo_guia__startswith=prefijo)
        .order_by("-id")
        .first()
    )
    if ultimo:
        try:
            secuencia = int(ultimo.codigo_guia.split("-")[-1]) + 1
        except ValueError:
            secuencia = RecepcionGuia.objects.count() + 1
    else:
        secuencia = 1
    return f"{prefijo}{secuencia:03d}"


def parsear_fecha(fecha_str: str) -> datetime.date:
    if fecha_str:
        try:
            return datetime.date.fromisoformat(fecha_str.strip())
        except ValueError:
            pass
    return timezone.localdate()


def obtener_nombre_responsable(user: User) -> str:
    if not user or not user.is_authenticated:
        return ""
    return (user.first_name or user.username).strip()


def obtener_rol_usuario(user: User) -> str:
    """
    Retorna el código de rol del usuario:
    - ADMIN: Administrador General
    - DESCARGUE: Personal de Descargue
    - CALIDAD: Asegurador de Calidad
    """
    if not user or not user.is_authenticated:
        return "CALIDAD"
    if user.is_superuser:
        return "ADMIN"
    if hasattr(user, "perfil") and user.perfil and user.perfil.rol:
        return user.perfil.rol
    if user.is_staff:
        return "ADMIN"
    return "CALIDAD"


def login_view(request):
    """
    Pantalla de inicio de sesión móvil para identificar automáticamente al Asegurador o Admin.
    """
    asegurar_semilla_catalogos()
    error = None

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip().lower()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("recepcion:index")
        else:
            error = "Usuario o contraseña incorrectos."

    return render(
        request,
        "recepcion/login.html",
        {
            "error": error,
        },
    )


def logout_view(request):
    logout(request)
    return redirect("recepcion:login")


def index(request):
    """
    Vista principal móvil-primero (Mobile-First Step Wizard) de Recepción y Calidad de Planta.
    Si el usuario no ha iniciado sesión, redirige al login para mapear automáticamente el responsable.
    """
    asegurar_semilla_catalogos()
    if not request.user.is_authenticated:
        return redirect("recepcion:login")

    maestro_dict, _ = cargar_maestro_pros()
    parametros_activos = obtener_parametros_activos()
    todos_parametros = [p.to_dict() for p in ParametroCatalogo.objects.all().order_by("orden", "id")]

    fecha_hoy = timezone.localdate()
    semana_actual = calcular_semana_comercial(fecha_hoy)

    recepciones_qs = RecepcionGuia.objects.prefetch_related(
        "variedades__parametros", "fotos"
    ).all()[:200]

    recepciones_serializadas = [serializar_recepcion(r) for r in recepciones_qs]
    responsable_actual = obtener_nombre_responsable(request.user)
    rol_usuario = obtener_rol_usuario(request.user)
    es_admin = bool(request.user.is_staff or request.user.is_superuser or rol_usuario == "ADMIN")

    usuarios_lista = []
    for u in User.objects.filter(is_active=True).select_related("perfil").order_by("-is_staff", "id"):
        r_codigo = "ADMIN" if (u.is_superuser or u.is_staff) else "CALIDAD"
        if hasattr(u, "perfil") and u.perfil and u.perfil.rol:
            r_codigo = u.perfil.rol
        r_display = dict(PerfilUsuario.ROL_CHOICES).get(r_codigo, r_codigo)
        usuarios_lista.append(
            {
                "id": u.id,
                "username": u.username,
                "nombre_completo": u.first_name or u.username,
                "es_admin": bool(u.is_staff or u.is_superuser or r_codigo == "ADMIN"),
                "rol": r_codigo,
                "rol_display": r_display,
            }
        )

    context = {
        "productos_maestros": list(maestro_dict.keys()),
        "maestro_variedades_json": json.dumps(maestro_dict, ensure_ascii=False),
        "responsable_actual": responsable_actual,
        "rol_usuario": rol_usuario,
        "es_admin": es_admin,
        "usuarios_json": json.dumps(usuarios_lista, ensure_ascii=False),
        "parametros": parametros_activos,
        "parametros_json": json.dumps(parametros_activos, ensure_ascii=False),
        "todos_parametros_json": json.dumps(todos_parametros, ensure_ascii=False),
        "fecha_hoy_iso": fecha_hoy.isoformat(),
        "semana_actual": semana_actual,
        "siguiente_guia": generar_siguiente_codigo_guia(fecha_hoy),
        "recepciones": recepciones_qs,
        "recepciones_json": json.dumps(recepciones_serializadas, ensure_ascii=False),
        "total_guias": RecepcionGuia.objects.count(),
    }
    return render(request, "recepcion/index.html", context)


@require_GET
def api_catalogo(request):
    """
    Endpoint JSON con el mapeo completo de Productos Maestros -> Variedades,
    Responsables y Parámetros de Calidad activos.
    """
    maestro_dict, _ = cargar_maestro_pros()
    producto_filtro = request.GET.get("producto_maestro", "").strip()

    if producto_filtro:
        variedades = maestro_dict.get(producto_filtro, [])
        return JsonResponse(
            {
                "producto_maestro": producto_filtro,
                "total_variedades": len(variedades),
                "variedades": variedades,
            }
        )

    return JsonResponse(
        {
            "productos_maestros": maestro_dict,
            "responsables": cargar_responsables(),
            "parametros": obtener_parametros_activos(),
        }
    )


@require_POST
def api_guardar_recepcion(request):
    """
    Guarda o actualiza una o varias Guías de Recepción agrupadas por Producto Maestro:
    - En una misma factura física vienen varios Productos Maestros mezclados (ej. HYPERICUM, GYPSOPHILA).
    - Cada variedad se asigna automáticamente a su Producto Maestro del catálogo.
    """
    asegurar_semilla_catalogos()
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Formato JSON inválido."}, status=400)

    responsable_usuario = obtener_nombre_responsable(request.user)
    responsable = (payload.get("responsable") or responsable_usuario or "").strip()
    producto_maestro_fallback = (payload.get("producto_maestro") or "").strip().upper()
    fecha_obj = parsear_fecha(payload.get("fecha_recepcion") or "")
    semana_calc = calcular_semana_comercial(fecha_obj)
    codigo_guia_base = (payload.get("codigo_guia") or "").strip() or generar_siguiente_codigo_guia(fecha_obj)
    numero_factura = (payload.get("numero_factura") or "").strip()
    fecha_factura = (payload.get("fecha_factura") or "").strip()
    observaciones_generales = (payload.get("observaciones_generales") or "").strip()
    responsable_descargue = (payload.get("responsable_descargue") or "").strip()
    tiene_novedades_descargue = bool(payload.get("tiene_novedades_descargue", False))
    observaciones_descargue = (payload.get("observaciones_descargue") or "").strip()
    estado_flujo = (payload.get("estado_flujo") or "COMPLETADA").upper()
    if estado_flujo not in ("EN_CONTEO", "VALIDADO_DESCARGUE", "COMPLETADA"):
        estado_flujo = "COMPLETADA"

    recepcion_id = payload.get("recepcion_id")
    recepcion_ids_por_pm = payload.get("recepcion_ids_por_pm") or {}
    preservar_variedades_previas = bool(payload.get("preservar_variedades_previas", False))
    variedades_data = payload.get("variedades") or []

    if not responsable:
        return JsonResponse(
            {"ok": False, "error": "No se identificó el Asegurador / Responsable."},
            status=400,
        )
    if not variedades_data:
        return JsonResponse(
            {
                "ok": False,
                "error": "Debe agregar al menos una variedad recibida.",
            },
            status=400,
        )

    maestro_dict, _ = cargar_maestro_pros()
    mapa_var_a_pm = {}
    for pm_k, lista_v in maestro_dict.items():
        for v_nom in lista_v:
            mapa_var_a_pm[v_nom.upper()] = pm_k.upper()

    # Agrupar variedades por su Producto Maestro
    grupos_por_pm = {}
    for item in variedades_data:
        nombre_var = (item.get("variedad") or "").strip().upper()
        if not nombre_var:
            continue
        pm_item = (
            (item.get("producto_maestro") or "").strip().upper()
            or mapa_var_a_pm.get(nombre_var, "")
            or producto_maestro_fallback
            or "GENERAL"
        )
        grupos_por_pm.setdefault(pm_item, []).append(item)

    if not grupos_por_pm:
        return JsonResponse(
            {"ok": False, "error": "Debe confirmar al menos una variedad válida."},
            status=400,
        )

    parametros_activos = obtener_parametros_activos()
    mapa_nombres_param = {p["codigo"]: p["nombre"] for p in parametros_activos}

    guias_guardadas = []
    nuevos_ids_por_pm = dict(recepcion_ids_por_pm)

    with transaction.atomic():
        total_grupos = len(grupos_por_pm)
        for idx_pm, (pm_actual, lista_items_pm) in enumerate(grupos_por_pm.items()):
            guia = None
            id_existente = nuevos_ids_por_pm.get(pm_actual) or (recepcion_id if total_grupos == 1 else None)
            if id_existente:
                guia = RecepcionGuia.objects.filter(pk=id_existente).first()

            if guia:
                guia.fecha_recepcion = fecha_obj
                guia.semana = semana_calc
                guia.responsable = responsable
                guia.producto_maestro = pm_actual
                if numero_factura:
                    guia.numero_factura = numero_factura[:140]
                if fecha_factura:
                    guia.fecha_factura = fecha_factura[:120]
                guia.observaciones_generales = observaciones_generales
                if responsable_descargue:
                    guia.responsable_descargue = responsable_descargue
                guia.tiene_novedades_descargue = tiene_novedades_descargue
                guia.observaciones_descargue = observaciones_descargue
                guia.estado_flujo = estado_flujo
                if request.user.is_authenticated:
                    guia.usuario = request.user
                guia.save()
                if preservar_variedades_previas:
                    nombres_entrantes = [
                        (it.get("variedad") or "").strip().upper()
                        for it in lista_items_pm
                        if (it.get("variedad") or "").strip()
                    ]
                    guia.variedades.filter(variedad__in=nombres_entrantes).delete()
                else:
                    guia.variedades.all().delete()
            else:
                sufijo_pm = f"-{pm_actual[:3]}" if total_grupos > 1 else ""
                cod_candidato = f"{codigo_guia_base}{sufijo_pm}"
                if RecepcionGuia.objects.filter(codigo_guia=cod_candidato).exists():
                    cod_candidato = f"{cod_candidato}-{RecepcionGuia.objects.count() + 1:02d}"
                guia = RecepcionGuia.objects.create(
                    codigo_guia=cod_candidato,
                    numero_factura=numero_factura[:140],
                    fecha_factura=fecha_factura[:120],
                    fecha_recepcion=fecha_obj,
                    semana=semana_calc,
                    usuario=request.user if request.user.is_authenticated else None,
                    responsable=responsable,
                    responsable_descargue=responsable_descargue or responsable,
                    producto_maestro=pm_actual,
                    observaciones_generales=observaciones_generales,
                    tiene_novedades_descargue=tiene_novedades_descargue,
                    observaciones_descargue=observaciones_descargue,
                    estado_flujo=estado_flujo,
                )

            for item in lista_items_pm:
                nombre_var = (item.get("variedad") or "").strip().upper()
                if not nombre_var:
                    continue

                lotes = item.get("lotes") or []
                if lotes:
                    plantas_recibidas = sum(int(l.get("plantas") or 0) for l in lotes)
                    contenedores_recibidos = sum(int(l.get("contenedores") or 0) for l in lotes)
                    tipos_lotes = {
                        (l.get("tipo_contenedor") or "BANDEJA").upper()
                        for l in lotes
                        if int(l.get("contenedores") or 0) > 0
                    }
                    if len(tipos_lotes) > 1:
                        tipo_contenedor = "MIXTO"
                    elif len(tipos_lotes) == 1:
                        tipo_contenedor = list(tipos_lotes)[0]
                    else:
                        tipo_contenedor = (item.get("tipo_contenedor") or "BANDEJA").upper()
                else:
                    plantas_recibidas = int(item.get("plantas_recibidas") or 0)
                    contenedores_recibidos = int(item.get("contenedores_recibidos") or 0)
                    tipo_contenedor = (item.get("tipo_contenedor") or "BANDEJA").upper()

                if tipo_contenedor not in ("BANDEJA", "GAVETA", "MIXTO"):
                    tipo_contenedor = "BANDEJA"

                if plantas_recibidas <= 0 or contenedores_recibidos <= 0:
                    continue

                plantas_factura = int(item.get("plantas_factura") or item.get("plantas_guia") or plantas_recibidas or 0)
                contenedores_factura = int(item.get("contenedores_factura") or item.get("contenedores_guia") or contenedores_recibidos or 0)

                eval_var = EvaluacionVariedad(
                    recepcion=guia,
                    variedad=nombre_var,
                    tipo_contenedor=tipo_contenedor,
                    plantas_factura=plantas_factura,
                    plantas_recibidas=plantas_recibidas,
                    contenedores_factura=contenedores_factura,
                    contenedores_recibidos=contenedores_recibidos,
                    detalle_lotes_json=json.dumps(lotes, ensure_ascii=False),
                    observacion_general_variedad=(item.get("observacion_general") or "").strip(),
                )
                eval_var.calcular_muestreo()
                eval_var.save()

                parametros_ingresados = item.get("parametros") or {}
                suma_puntos = 0
                alerta_empaque = False

                for param_def in parametros_activos:
                    cod = param_def["codigo"]
                    datos_p = parametros_ingresados.get(cod) or {}
                    try:
                        puntaje = int(datos_p.get("puntaje", 3))
                    except (ValueError, TypeError):
                        puntaje = 3
                    puntaje = max(1, min(3, puntaje))
                    obs = (datos_p.get("observacion") or "").strip()

                    try:
                        plantas_afectadas = max(0, int(datos_p.get("plantas_afectadas") or 0))
                    except (ValueError, TypeError):
                        plantas_afectadas = 0

                    try:
                        porcentaje_afectacion = max(0.0, float(datos_p.get("porcentaje_afectacion") or 0.0))
                    except (ValueError, TypeError):
                        porcentaje_afectacion = 0.0

                    if eval_var.total_plantas_a_revisar > 0 and plantas_afectadas > 0:
                        porcentaje_afectacion = round((plantas_afectadas / eval_var.total_plantas_a_revisar) * 100.0, 2)

                    suma_puntos += puntaje
                    if cod == "empaque" and puntaje == 1:
                        alerta_empaque = True

                    cal_p = CalificacionParametro.objects.create(
                        evaluacion_variedad=eval_var,
                        codigo_parametro=cod,
                        nombre_parametro=mapa_nombres_param.get(cod, cod),
                        puntaje=puntaje,
                        observacion=obs,
                        plantas_afectadas=plantas_afectadas,
                        porcentaje_afectacion=porcentaje_afectacion,
                    )

                    foto_b64 = datos_p.get("foto_base64")
                    if foto_b64 and isinstance(foto_b64, str) and ";base64," in foto_b64:
                        try:
                            formato, imgstr = foto_b64.split(";base64,")
                            ext = formato.split("/")[-1].lower() if "/" in formato else "jpg"
                            if ext not in ("jpg", "jpeg", "png", "webp"):
                                ext = "jpg"
                            nombre_archivo = f"evid_{cod}_{eval_var.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
                            cal_p.foto.save(nombre_archivo, ContentFile(base64.b64decode(imgstr)), save=True)
                        except Exception as e_f:
                            pass

                total_params = max(1, len(parametros_activos))
                calificacion_var = round(suma_puntos / total_params, 2)
                porcentaje_var = round((calificacion_var / 3.0) * 100.0, 1)

                if calificacion_var >= 2.60:
                    estado_var = "MUY_BUENO"
                elif calificacion_var >= 1.85:
                    estado_var = "REGULAR"
                else:
                    estado_var = "MUY_MALO"

                eval_var.suma_puntos = suma_puntos
                eval_var.maximo_puntos = total_params * 3
                eval_var.calificacion_variedad = calificacion_var
                eval_var.porcentaje_calidad = porcentaje_var
                eval_var.estado_calidad = estado_var
                eval_var.alerta_empaque = alerta_empaque
                eval_var.save()

            if guia.variedades.count() == 0:
                guia.delete()
                continue

            guia.recalcular_consolidado()
            nuevos_ids_por_pm[pm_actual] = guia.id
            guias_guardadas.append(guia)

        if not guias_guardadas:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Las variedades deben tener cantidades mayores a 0 en plantas y gavetas/bandejas.",
                },
                status=400,
            )

    guia_principal = guias_guardadas[0]
    resumen_pms = ", ".join(f"{g.producto_maestro} ({g.porcentaje_calidad_maestro:.1f}%)" for g in guias_guardadas)
    if estado_flujo == "VALIDADO_DESCARGUE":
        msg = f"Descargue validado y enviado a Calidad ({resumen_pms})."
    elif estado_flujo == "EN_CONTEO":
        msg = f"Conteo guardado en Descargue ({resumen_pms})."
    else:
        msg = f"Recepción guardada: {resumen_pms}."

    return JsonResponse(
        {
            "ok": True,
            "mensaje": msg,
            "recepcion_id": guia_principal.id,
            "recepcion_ids_por_pm": nuevos_ids_por_pm,
            "ids_guardados": [g.id for g in guias_guardadas],
            "estado_flujo": guia_principal.estado_flujo,
            "recepcion": serializar_recepcion(guia_principal),
            "recepciones": [serializar_recepcion(g) for g in guias_guardadas],
            "siguiente_guia": generar_siguiente_codigo_guia(fecha_obj),
        }
    )


def serializar_recepcion(guia: RecepcionGuia):
    variedades_list = []
    for v in guia.variedades.prefetch_related("parametros").all():
        params_list = [
            {
                "codigo": p.codigo_parametro,
                "nombre": p.nombre_parametro,
                "puntaje": p.puntaje,
                "observacion": p.observacion,
                "plantas_afectadas": p.plantas_afectadas,
                "porcentaje_afectacion": p.porcentaje_afectacion,
                "foto_url": p.foto.url if p.foto else "",
            }
            for p in v.parametros.all()
        ]
        try:
            lotes_parsed = json.loads(v.detalle_lotes_json or "[]")
        except ValueError:
            lotes_parsed = []

        variedades_list.append(
            {
                "id": v.id,
                "variedad": v.variedad,
                "tipo_contenedor": v.tipo_contenedor,
                "plantas_factura": v.plantas_factura or v.plantas_recibidas,
                "plantas_recibidas": v.plantas_recibidas,
                "diferencia_plantas": v.plantas_recibidas - (v.plantas_factura or v.plantas_recibidas),
                "contenedores_factura": v.contenedores_factura or v.contenedores_recibidos,
                "contenedores_recibidos": v.contenedores_recibidos,
                "diferencia_contenedores": v.contenedores_recibidos - (v.contenedores_factura or v.contenedores_recibidos),
                "lotes": lotes_parsed,
                "plantas_por_contenedor": v.plantas_por_contenedor,
                "muestreo_diez_porciento_exacto": v.muestreo_diez_porciento_exacto,
                "contenedores_a_revisar": v.contenedores_a_revisar,
                "plantas_por_contenedor_muestreo": v.plantas_por_contenedor_muestreo,
                "plantas_inicio": v.plantas_inicio,
                "plantas_medio": v.plantas_medio,
                "plantas_final": v.plantas_final,
                "total_plantas_a_revisar": v.total_plantas_a_revisar,
                "suma_puntos": v.suma_puntos,
                "maximo_puntos": v.maximo_puntos,
                "calificacion_variedad": v.calificacion_variedad,
                "porcentaje_calidad": v.porcentaje_calidad,
                "estado_calidad": v.estado_calidad,
                "alerta_empaque": v.alerta_empaque,
                "observacion_general_variedad": v.observacion_general_variedad,
                "parametros": params_list,
            }
        )

    fecha_val = guia.fecha_recepcion
    if isinstance(fecha_val, datetime.datetime):
        fecha_val = fecha_val.date()

    fotos_list = [f.to_dict() for f in guia.fotos.all()]

    return {
        "id": guia.id,
        "codigo_guia": guia.codigo_guia,
        "numero_factura": guia.numero_factura or "",
        "fecha_factura": guia.fecha_factura or "",
        "fecha_recepcion": fecha_val.strftime("%d/%m/%Y") if fecha_val else "",
        "fecha_iso": fecha_val.isoformat() if fecha_val else "",
        "semana": guia.semana or calcular_semana_comercial(fecha_val),
        "responsable": guia.responsable,
        "responsable_descargue": guia.responsable_descargue or "",
        "tiene_novedades_descargue": bool(guia.tiene_novedades_descargue),
        "observaciones_descargue": guia.observaciones_descargue or "",
        "producto_maestro": guia.producto_maestro,
        "estado_flujo": guia.estado_flujo,
        "observaciones_generales": guia.observaciones_generales,
        "total_variedades_evaluadas": guia.total_variedades_evaluadas,
        "total_plantas_recibidas": guia.total_plantas_recibidas,
        "total_contenedores_recibidos": guia.total_contenedores_recibidos,
        "total_contenedores_muestreados": guia.total_contenedores_muestreados,
        "total_plantas_muestreadas": guia.total_plantas_muestreadas,
        "calificacion_producto_maestro": guia.calificacion_producto_maestro,
        "calificacion_ponderada_maestro": guia.calificacion_ponderada_maestro,
        "porcentaje_calidad_maestro": guia.porcentaje_calidad_maestro,
        "estado_calidad_maestro": guia.estado_calidad_maestro,
        "alerta_empaque_critica": guia.alerta_empaque_critica,
        "variedades": variedades_list,
        "fotos": fotos_list,
    }


@require_GET
def api_detalle_recepcion(request, pk):
    guia = get_object_or_404(
        RecepcionGuia.objects.prefetch_related("variedades__parametros", "fotos"), pk=pk
    )
    return JsonResponse({"ok": True, "recepcion": serializar_recepcion(guia)})


@require_http_methods(["DELETE", "POST"])
def api_eliminar_recepcion(request, pk):
    guia = get_object_or_404(RecepcionGuia, pk=pk)
    codigo = guia.codigo_guia
    guia.delete()
    return JsonResponse({"ok": True, "mensaje": f"Guía {codigo} eliminada."})


# ==============================================================================
# ENDPOINTS EXCLUSIVOS DEL ADMINISTRADOR (USUARIOS, PARÁMETROS Y PRODUCTOS)
# ==============================================================================
def verificar_es_admin(request):
    return bool(
        request.user.is_authenticated
        and (request.user.is_staff or request.user.is_superuser)
    )


@require_POST
def api_admin_crear_usuario(request):
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado. Solo el Administrador puede gestionar usuarios."}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    username = slugify((data.get("username") or "").strip()).replace("-", "")
    nombre_completo = (data.get("nombre_completo") or "").strip()
    password = (data.get("password") or "1234").strip()
    rol = (data.get("rol") or "").strip().upper()
    es_admin = bool(data.get("es_admin", False))
    if rol not in ("ADMIN", "DESCARGUE", "CALIDAD"):
        rol = "ADMIN" if es_admin else "CALIDAD"
    if rol == "ADMIN":
        es_admin = True
    else:
        es_admin = False

    if not username or not nombre_completo:
        return JsonResponse({"ok": False, "error": "Ingrese usuario y nombre completo del responsable."}, status=400)

    if User.objects.filter(username=username).exists():
        return JsonResponse({"ok": False, "error": f"El usuario '{username}' ya existe."}, status=400)

    nuevo = User.objects.create_user(username=username, password=password)
    nuevo.first_name = nombre_completo
    nuevo.is_staff = es_admin
    nuevo.is_superuser = es_admin
    nuevo.save()
    PerfilUsuario.objects.update_or_create(user=nuevo, defaults={"rol": rol})
    sincronizar_csv_responsables()

    rol_display = dict(PerfilUsuario.ROL_CHOICES).get(rol, rol)
    return JsonResponse(
        {
            "ok": True,
            "mensaje": f"Usuario '{username}' ({nombre_completo}) creado con rol '{rol_display}'.",
            "usuario": {
                "id": nuevo.id,
                "username": nuevo.username,
                "nombre_completo": nuevo.first_name,
                "es_admin": es_admin,
                "rol": rol,
                "rol_display": rol_display,
            },
        }
    )


@require_POST
def api_admin_cambiar_rol_usuario(request, pk):
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado."}, status=403)
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)
    u = get_object_or_404(User, pk=pk)
    rol = (data.get("rol") or "").strip().upper()
    if rol not in ("ADMIN", "DESCARGUE", "CALIDAD"):
        return JsonResponse({"ok": False, "error": "Rol no válido."}, status=400)

    perfil, _ = PerfilUsuario.objects.get_or_create(user=u)
    perfil.rol = rol
    perfil.save()
    if rol == "ADMIN":
        u.is_staff = True
    else:
        if u.username != "admin":
            u.is_staff = False
            u.is_superuser = False
    u.save()
    rol_display = dict(PerfilUsuario.ROL_CHOICES).get(rol, rol)
    return JsonResponse(
        {
            "ok": True,
            "mensaje": f"Rol de @{u.username} actualizado a '{rol_display}'.",
            "rol": rol,
            "rol_display": rol_display,
        }
    )


@require_POST
def api_admin_eliminar_usuario(request, pk):
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado."}, status=403)
    u = get_object_or_404(User, pk=pk)
    if u.username == "admin" or u.pk == request.user.pk:
        return JsonResponse({"ok": False, "error": "No puedes eliminar tu propio usuario Administrador."}, status=400)
    u.delete()
    sincronizar_csv_responsables()
    return JsonResponse({"ok": True, "mensaje": "Usuario eliminado."})


@require_POST
def api_admin_crear_parametro(request):
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado. Solo el Administrador puede crear parámetros."}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    nombre = (data.get("nombre") or "").strip()
    descripcion = (data.get("descripcion") or "").strip()
    sugerencias_csv = (data.get("sugerencias_csv") or "").strip()
    es_critico = bool(data.get("es_critico", False))

    if not nombre:
        return JsonResponse({"ok": False, "error": "Ingrese el nombre del nuevo parámetro de calidad."}, status=400)

    orden_sig = ParametroCatalogo.objects.count() + 1
    nuevo_p = ParametroCatalogo.objects.create(
        nombre=nombre,
        nombre_corto=nombre[:22],
        descripcion=descripcion or "Parámetro de control de calidad en recepción.",
        sugerencias_csv=sugerencias_csv,
        es_critico=es_critico,
        orden=orden_sig,
        activo=True,
    )

    return JsonResponse(
        {
            "ok": True,
            "mensaje": f"Parámetro '{nuevo_p.nombre}' agregado correctamente.",
            "parametro": nuevo_p.to_dict(),
            "parametros_activos": obtener_parametros_activos(),
        }
    )


@require_POST
def api_admin_toggle_parametro(request, pk):
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado."}, status=403)
    p = get_object_or_404(ParametroCatalogo, pk=pk)
    p.activo = not p.activo
    p.save()
    return JsonResponse(
        {
            "ok": True,
            "activo": p.activo,
            "parametros_activos": obtener_parametros_activos(),
        }
    )


@require_POST
def api_admin_crear_producto_variedad(request):
    """
    Permite al Admin crear un nuevo Producto Maestro y/o agregarle nuevas Variedades
    mapeadas automáticamente en la base de datos y en cosechas/MAESTRO_PROS.csv.
    """
    if not verificar_es_admin(request):
        return JsonResponse({"ok": False, "error": "Permiso denegado. Solo el Administrador puede gestionar productos y variedades."}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    nombre_pm = (data.get("producto_maestro") or "").strip().upper()
    variedades_raw = (data.get("variedades") or "").strip().upper()

    if not nombre_pm:
        return JsonResponse({"ok": False, "error": "Ingrese el nombre del Producto Maestro."}, status=400)

    pm_obj, _ = ProductoMaestro.objects.get_or_create(nombre=nombre_pm)
    pm_obj.activo = True
    pm_obj.save()

    variedades_creadas = []
    if variedades_raw:
        partes = [v.strip() for v in variedades_raw.replace(";", ",").split(",") if v.strip()]
        for nombre_v in partes:
            v_obj, created = VariedadCatalogo.objects.get_or_create(
                producto_maestro=pm_obj,
                nombre=nombre_v,
            )
            v_obj.activo = True
            v_obj.save()
            guardar_en_csv_maestro(nombre_pm, nombre_v)
            variedades_creadas.append(nombre_v)

    maestro_dict, _ = cargar_maestro_pros()
    return JsonResponse(
        {
            "ok": True,
            "mensaje": f"Producto Maestro '{nombre_pm}' actualizado con {len(variedades_creadas)} variedad(es).",
            "maestro_variedades": maestro_dict,
        }
    )


@require_POST
def api_admin_limpiar_recepciones_prueba(request):
    """
    Elimina únicamente las recepciones, calificaciones y fotos de prueba,
    dejando 100% intactos los Usuarios, los Parámetros de Calidad y el Catálogo de Productos/Variedades.
    """
    if not verificar_es_admin(request):
        return JsonResponse(
            {"ok": False, "error": "Permiso denegado. Solo el Administrador puede limpiar datos de prueba."},
            status=403,
        )

    total_borradas = RecepcionGuia.objects.count()
    for f in FotoEvidencia.objects.all():
        if f.imagen:
            try:
                f.imagen.delete(save=False)
            except Exception:
                pass
    RecepcionGuia.objects.all().delete()

    siguiente = generar_siguiente_codigo_guia(timezone.localdate())
    return JsonResponse(
        {
            "ok": True,
            "mensaje": (
                f"Se eliminaron {total_borradas} recepción(es) y fotos de prueba. "
                "Tus usuarios, parámetros y productos quedaron intactos."
            ),
            "siguiente_guia": siguiente,
        }
    )


@require_GET
def exportar_csv(request):
    """
    Exporta todas las recepciones con su Semana Comercial (YYWW), Fecha (sin hora),
    variedades, muestreo (10% y 15 plantas) y parámetros a CSV.
    """
    parametros_todos = obtener_parametros_activos()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    fecha_archivo = timezone.localdate().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="recepcion_calidad_planta_{fecha_archivo}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response, delimiter=";")
    encabezados = [
        "Semana (YYWW)",
        "Fecha Recepcion",
        "Codigo Guia",
        "Estado Flujo",
        "Asegurador Responsable",
        "Producto Maestro",
        "% Calidad Producto Maestro",
        "Calificacion Producto Maestro (/3.00)",
        "Variedad",
        "Tipo Contenedor",
        "Plantas Recibidas",
        "Contenedores Recibidos",
        "Plantas por Contenedor (1ra Revision)",
        "Contenedores a Revisar (10%)",
        "Total Plantas a Calificar (15 c/u: 5-5-5)",
        "% Calidad Variedad",
        "Calificacion Variedad (/3.00)",
        "Estado Variedad",
    ]
    for p in parametros_todos:
        encabezados.append(f"{p['nombre_corto']} (1-3)")
        encabezados.append(f"{p['nombre_corto']} Observacion")

    writer.writerow(encabezados)

    guias = RecepcionGuia.objects.prefetch_related("variedades__parametros").all()
    for g in guias:
        fecha_fmt = g.fecha_recepcion.strftime("%Y-%m-%d") if g.fecha_recepcion else ""
        for v in g.variedades.all():
            mapa_p = {p.codigo_parametro: p for p in v.parametros.all()}
            fila = [
                g.semana,
                fecha_fmt,
                g.codigo_guia,
                g.estado_flujo,
                g.responsable,
                g.producto_maestro,
                f"{g.porcentaje_calidad_maestro:.1f}%",
                f"{g.calificacion_producto_maestro:.2f}",
                v.variedad,
                v.tipo_contenedor,
                v.plantas_recibidas,
                v.contenedores_recibidos,
                f"{v.plantas_por_contenedor:.2f}",
                v.contenedores_a_revisar,
                v.total_plantas_a_revisar,
                f"{v.porcentaje_calidad:.1f}%",
                f"{v.calificacion_variedad:.2f}",
                v.estado_calidad,
            ]
            for p_def in parametros_todos:
                obj_p = mapa_p.get(p_def["codigo"])
                fila.append(obj_p.puntaje if obj_p else "")
                fila.append(obj_p.observacion if obj_p else "")
            writer.writerow(fila)

    return response


# ==============================================================================
# CARGA DE FOTOS DE EVIDENCIA DE CALIDAD Y ESCANEO DE GUÍAS FÍSICAS (ESFERO)
# ==============================================================================
@require_POST
def api_subir_foto_evidencia(request, pk):
    """
    Permite subir 1 o varias fotos al finalizar la calificación de calidad
    o adjuntar la foto de la guía física escrita a mano.
    """
    guia = get_object_or_404(RecepcionGuia, pk=pk)
    archivos = request.FILES.getlist("fotos")
    if not archivos and "foto" in request.FILES:
        archivos = [request.FILES["foto"]]

    if not archivos:
        return JsonResponse({"ok": False, "error": "Selecciona o toma al menos una foto."}, status=400)

    variedad = (request.POST.get("variedad") or "").strip().upper()
    tipo_foto = (request.POST.get("tipo_foto") or "CALIDAD").strip().upper()
    if tipo_foto not in ("CALIDAD", "GUIA_FISICA"):
        tipo_foto = "CALIDAD"
    comentario = (request.POST.get("comentario") or "").strip()

    creadas = []
    for arch in archivos:
        f = FotoEvidencia.objects.create(
            recepcion=guia,
            variedad=variedad,
            tipo_foto=tipo_foto,
            imagen=arch,
            comentario=comentario,
        )
        creadas.append(f.to_dict())

    return JsonResponse(
        {
            "ok": True,
            "mensaje": f"{len(creadas)} foto(s) guardada(s) correctamente.",
            "fotos_nuevas": creadas,
            "recepcion": serializar_recepcion(guia),
        }
    )


@require_POST
def api_eliminar_foto_evidencia(request, foto_id):
    f = get_object_or_404(FotoEvidencia, pk=foto_id)
    recepcion = f.recepcion
    if f.imagen:
        try:
            f.imagen.delete(save=False)
        except Exception:
            pass
    f.delete()
    return JsonResponse(
        {
            "ok": True,
            "mensaje": "Foto eliminada.",
            "recepcion": serializar_recepcion(recepcion),
        }
    )


@require_POST
def api_escanear_guia_manuscrita(request):
    """
    Recibe la foto de una guía física escrita a mano con esfero y extrae:
    1. Número de Factura / Guía física
    2. Producto Maestro (si aparece)
    3. Variedades, cantidad de plantas y cantidad de bandejas o gavetas
    Usa IA de Visión (Gemini 2.5 Flash) si GEMINI_API_KEY está configurada en .env.
    """
    archivo = request.FILES.get("imagen")
    if not archivo:
        return JsonResponse({"ok": False, "error": "Sube o toma una foto de la guía física."}, status=400)

    pm_contexto = (request.POST.get("producto_maestro") or "").strip().upper()
    maestro_dict, _ = cargar_maestro_pros()

    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not gemini_key or gemini_key.startswith("tu_api_key"):
        return JsonResponse(
            {
                "ok": False,
                "requiere_api_key": True,
                "error": (
                    "La foto de la guía se cargó en pantalla para lectura asistida. "
                    "Para activar la extracción automática de letra escrita a mano con esfero, "
                    "agrega GEMINI_API_KEY=tu_api_key en tu archivo .env y reinicia el contenedor con docker compose restart web."
                ),
            }
        )

    try:
        img_bytes = archivo.read()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        mime_type = (archivo.content_type or "").strip().lower()
        if not mime_type or not mime_type.startswith("image/"):
            ext = os.path.splitext(archivo.name or "")[1].lower()
            if ext in (".png",):
                mime_type = "image/png"
            elif ext in (".webp",):
                mime_type = "image/webp"
            elif ext in (".gif",):
                mime_type = "image/gif"
            else:
                mime_type = "image/jpeg"

        # Mapa inverso variedad -> producto_maestro para cruzar cualquier variedad del catálogo
        mapa_var_a_pm = {}
        todas_las_variedades = []
        for pm_k, lista_v in maestro_dict.items():
            for v_nom in lista_v:
                mapa_var_a_pm[v_nom.upper()] = pm_k.upper()
                todas_las_variedades.append(v_nom.upper())

        catalogo_info = (
            f"Producto Maestro preseleccionado: {pm_contexto}. Variedades de {pm_contexto}: {maestro_dict.get(pm_contexto, [])}\n"
            f"Catálogo completo (Producto Maestro -> Variedades): {json.dumps(maestro_dict, ensure_ascii=False)}"
            if pm_contexto and pm_contexto in maestro_dict
            else f"Catálogo completo (Producto Maestro -> Variedades): {json.dumps(maestro_dict, ensure_ascii=False)}"
        )

        prompt_vision = (
            "Eres un asistente experto en leer facturas y guías físicas de recepción de plantas "
            "(impresas o escritas a mano con esfero/bolígrafo).\n"
            "REGLAS CLAVE:\n"
            "1. Extrae 'numero_factura': el número COMPLETO de factura o guía que aparece en el documento (todos los dígitos, ej: '001-002-0004512' o '4512', nunca lo trunques en '000').\n"
            "2. Extrae 'fecha_factura': la fecha de emisión/envío que aparece escrita o impresa en la factura/guía física (normalízala al formato DD/MM/YYYY, por ejemplo '12/11/2026'). Si no hay fecha visible, devuelve ''.\n"
            "3. En la tabla de la guía/factura, la columna 'CANTIDAD' (o Total Plantas / Cant.) corresponde al número de PLANTAS ('plantas').\n"
            "4. Extrae TODAS las filas escritas en la guía dentro de la lista 'variedades'. Para cada fila devuelve:\n"
            "   - 'texto_leido': lo que dice literalmente el papel en la columna de variedad/descripción.\n"
            "   - 'variedad': el nombre EXACTO del catálogo oficial.\n"
            "     EQUIVALENCIAS COMERCIALES OFICIALES EN FINCA:\n"
            "     * Si en la guía dice 'SN2', 'SN', 'S-N', 'S/N' o 'COD S/N' -> mapea 'variedad': 'COD-S/N' (HYPERICUM). NO es Craspedia, es Hypericum COD-S/N.\n"
            "     * Si en la guía dice 'RED', 'HYPERICUM RED', 'HYP RED' o 'RED WINE' -> mapea 'variedad': 'MAGICAL RED WINE' (HYPERICUM).\n"
            "     * Si en la guía dice 'ROYAL RED' -> mapea 'variedad': 'ROYAL RED' (HYPERICUM).\n"
            "     * Si en la guía dice 'RED SEWEL' o 'RED JEWEL' -> mapea 'variedad': 'RED SEWEL' (HYPERICUM).\n"
            "     * Si en la guía dice 'SUNSET' o 'SUNSET SEWET' -> mapea 'variedad': 'SUNSET SEWET' (HYPERICUM). OJO: NO confundir SUNSET SEWET con RED SEWEL.\n"
            "   - 'producto_maestro': el Producto Maestro del catálogo al que pertenece.\n"
            "   - 'plantas': número entero tomado de la columna CANTIDAD (si dice 1.200 o 1,200 devuelve 1200).\n"
            "   - 'contenedores': número entero de bandejas o gavetas SI aparece explícitamente en otra columna; si no aparece, devuelve 0.\n"
            "   - 'tipo_contenedor': 'BANDEJA' o 'GAVETA' (por defecto 'BANDEJA').\n"
            "   - 'requiere_revision': false si coincide con el catálogo oficial, o true si fue mapeado por equivalencia abreviada (como 'SN2' o 'RED') o si la letra con esfero es dudosa.\n"
            f"Catálogo oficial:\n{catalogo_info}\n"
            "Responde ÚNICAMENTE con un objeto JSON válido."
        )

        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_vision},
                        {"inline_data": {"mime_type": mime_type, "data": img_b64}},
                    ]
                }
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        }

        modelos_vision = [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
        ]
        resp_data = None
        ultimo_error = None

        for modelo in modelos_vision:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={gemini_key}"
            try:
                print(f"[OCR] Analizando guía con {modelo}...")
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    if resp_data.get("candidates"):
                        print(f"[OCR] ✓ Éxito con {modelo}")
                        break
            except Exception as err_m:
                print(f"[OCR] ⚠️ {modelo} falló: {err_m}")
                ultimo_error = err_m
                continue

        if not resp_data or not resp_data.get("candidates"):
            raise RuntimeError(f"{ultimo_error or 'Servicio de visión temporalmente ocupado'}")

        candidatos_ia = resp_data.get("candidates") or []
        partes = candidatos_ia[0].get("content", {}).get("parts", [])
        bloques_texto = [p.get("text", "") for p in partes if isinstance(p, dict) and p.get("text")]
        texto_json = "\n".join(bloques_texto).strip()

        if "```" in texto_json:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", texto_json)
            texto_json = m.group(1).strip() if m else texto_json.replace("```json", "").replace("```", "").strip()

        datos_brutos = json.loads(texto_json)

        # Normalizar y clasificar cada fila en: identificada vs requiere_revision
        filas_procesadas = []
        conteo_pm = {}
        identificadas_count = 0
        revision_count = 0

        for item in datos_brutos.get("variedades") or []:
            texto_leido = str(item.get("texto_leido") or item.get("variedad") or "").strip().upper()
            var_ia = str(item.get("variedad") or "").strip().upper()
            req_rev_ia = bool(item.get("requiere_revision", False))

            # Limpiar número de plantas (columna Cantidad)
            raw_plantas = str(item.get("plantas") or "0").replace(".", "").replace(",", "").strip()
            try:
                plantas_val = max(0, int(float(raw_plantas)))
            except ValueError:
                plantas_val = 0

            try:
                cont_val = max(0, int(item.get("contenedores") or 0))
            except (ValueError, TypeError):
                cont_val = 0

            t_cont = str(item.get("tipo_contenedor") or "BANDEJA").strip().upper()
            if t_cont not in ("BANDEJA", "GAVETA"):
                t_cont = "BANDEJA"

            var_final = ""
            pm_final = ""
            requiere_revision = req_rev_ia
            sugerencias = []

            candidatos = (
                maestro_dict.get(pm_contexto, [])
                if pm_contexto and pm_contexto in maestro_dict
                else todas_las_variedades
            )

            # Normalizar texto leído para detectar abreviaturas comunes (ej. SN2 -> COD-S/N en HYPERICUM)
            texto_limpio = (
                texto_leido.replace("-", "")
                .replace("/", "")
                .replace(".", "")
                .replace(" ", "")
            )
            es_codigo_sn = (
                texto_limpio in ("SN", "SN2", "CODSN", "CODSN2")
                or texto_leido in ("SN", "SN2", "S-N", "S/N", "S-N 2", "S/N 2", "COD-SN", "COD-S/N", "COD S/N")
                or (texto_limpio.startswith("SN") and len(texto_limpio) <= 4 and not texto_limpio.startswith("SNO"))
            )

            # Equivalencias comerciales directas usadas en las guías físicas de finca
            MAPA_ALIAS_COMERCIALES = {
                # Códigos SN -> COD-S/N (HYPERICUM, NO Craspedia)
                "SN2": "COD-S/N",
                "SN": "COD-S/N",
                "S-N": "COD-S/N",
                "S/N": "COD-S/N",
                "SN 2": "COD-S/N",
                "S-N 2": "COD-S/N",
                "S/N 2": "COD-S/N",
                "COD SN": "COD-S/N",
                "COD-SN": "COD-S/N",
                "COD S/N": "COD-S/N",
                "COD-S/N": "COD-S/N",

                # Variedad RED -> MAGICAL RED WINE (HYPERICUM)
                "RED": "MAGICAL RED WINE",
                "HYP RED": "MAGICAL RED WINE",
                "HYPERICUM RED": "MAGICAL RED WINE",
                "MAGICAL RED": "MAGICAL RED WINE",
                "RED WINE": "MAGICAL RED WINE",

                # ROYAL RED (HYPERICUM)
                "ROYAL RED": "ROYAL RED",
                "ROYAL": "ROYAL RED",

                # RED SEWEL (HYPERICUM) - Cuidado no confundir con SUNSET SEWET
                "RED SEWEL": "RED SEWEL",
                "RED JEWEL": "RED SEWEL",
                "RED SEWET": "RED SEWEL",

                # SUNSET SEWET (HYPERICUM) - Cuidado no confundir con RED SEWEL
                "SUNSET": "SUNSET SEWET",
                "SUNSET SEWET": "SUNSET SEWET",
                "SUNSET SEWEL": "SUNSET SEWET",
                "SUNSET JEWEL": "SUNSET SEWET",
            }

            alias_match = MAPA_ALIAS_COMERCIALES.get(texto_leido) or MAPA_ALIAS_COMERCIALES.get(texto_limpio)

            # Caso ambiguo si en el papel solo dice "SEWEL" o "SEWET" sin "RED" ni "SUNSET"
            es_sewel_ambiguo = (
                texto_leido in ("SEWEL", "SEWET", "JEWEL")
                or (("SEWEL" in texto_leido or "SEWET" in texto_leido) and "RED" not in texto_leido and "SUNSET" not in texto_leido)
            )

            # Buscar todas las variedades del catálogo que contengan la palabra leída
            coincidencias_subcadena = []
            if texto_leido and len(texto_leido) >= 2:
                palabras_busq = [
                    p for p in texto_leido.replace("-", " ").replace("/", " ").split() if len(p) >= 2
                ]
                for cand in candidatos:
                    if texto_leido in cand:
                        coincidencias_subcadena.append(cand)
                    elif palabras_busq and all(p in cand for p in palabras_busq):
                        coincidencias_subcadena.append(cand)

            if es_sewel_ambiguo:
                # Ofrecer ambas variedades para que el monitor elija con un toque
                for cand_sw in ("RED SEWEL", "SUNSET SEWET"):
                    if cand_sw in mapa_var_a_pm and cand_sw not in sugerencias:
                        sugerencias.append(cand_sw)
                var_final = "RED SEWEL"
                pm_final = "HYPERICUM"
                requiere_revision = True
            elif es_codigo_sn:
                var_final = "COD-S/N"
                pm_final = "HYPERICUM"
                sugerencias.append("COD-S/N")
                if texto_leido != var_final:
                    requiere_revision = True
            elif alias_match and alias_match in mapa_var_a_pm:
                var_final = alias_match
                pm_final = mapa_var_a_pm[var_final]
                sugerencias.append(var_final)
                # Si en el papel vino abreviado (distinto al nombre completo en catálogo), marcar para confirmar con ✓ OK
                if texto_leido != var_final:
                    requiere_revision = True
            elif texto_leido in mapa_var_a_pm:
                var_final = texto_leido
                pm_final = mapa_var_a_pm[texto_leido]
            elif var_ia in mapa_var_a_pm:
                var_final = var_ia
                pm_final = mapa_var_a_pm[var_ia]
                if len(coincidencias_subcadena) > 1 and texto_leido != var_ia:
                    requiere_revision = True
            else:
                if coincidencias_subcadena:
                    var_final = coincidencias_subcadena[0].upper()
                    pm_final = mapa_var_a_pm.get(var_final, pm_contexto)
                    if len(coincidencias_subcadena) > 1 or texto_leido != var_final:
                        requiere_revision = True
                else:
                    busqueda = var_ia or texto_leido
                    coincidencias = difflib.get_close_matches(busqueda, candidatos, n=3, cutoff=0.72)
                    if coincidencias:
                        var_final = coincidencias[0].upper()
                        pm_final = mapa_var_a_pm.get(var_final, pm_contexto)
                        sugerencias.extend(c.upper() for c in coincidencias if c.upper() not in sugerencias)
                        if texto_leido != var_final:
                            requiere_revision = True
                    else:
                        coincidencias_bajas = difflib.get_close_matches(busqueda, candidatos, n=4, cutoff=0.38)
                        var_final = coincidencias_bajas[0].upper() if coincidencias_bajas else ""
                        pm_final = mapa_var_a_pm.get(var_final, pm_contexto)
                        sugerencias.extend(c.upper() for c in coincidencias_bajas if c.upper() not in sugerencias)
                        requiere_revision = True

            for c_sub in coincidencias_subcadena[:5]:
                if c_sub.upper() not in sugerencias:
                    sugerencias.append(c_sub.upper())
            if var_final and var_final not in sugerencias:
                sugerencias.insert(0, var_final)

            if not var_final:
                requiere_revision = True

            if pm_final:
                conteo_pm[pm_final] = conteo_pm.get(pm_final, 0) + 1

            if requiere_revision:
                revision_count += 1
            else:
                identificadas_count += 1

            filas_procesadas.append(
                {
                    "texto_leido": texto_leido or var_final or "Fila sin nombre claro",
                    "variedad": var_final,
                    "producto_maestro": pm_final,
                    "sugerencias": sugerencias[:5],
                    "plantas": plantas_val,
                    "contenedores": cont_val,
                    "tipo_contenedor": t_cont,
                    "requiere_revision": requiere_revision,
                }
            )

        pm_principal = (datos_brutos.get("producto_maestro") or "").strip().upper()
        if pm_principal not in maestro_dict:
            if conteo_pm:
                pm_principal = max(conteo_pm, key=conteo_pm.get)
            else:
                pm_principal = pm_contexto

        fecha_factura_raw = str(datos_brutos.get("fecha_factura") or "").strip()
        datos_extraidos = {
            "numero_factura": str(datos_brutos.get("numero_factura") or "").strip(),
            "fecha_factura": fecha_factura_raw,
            "producto_maestro": pm_principal,
            "identificadas_count": identificadas_count,
            "revision_count": revision_count,
            "variedades": filas_procesadas,
        }

        return JsonResponse(
            {
                "ok": True,
                "mensaje": (
                    f"Factura leída: {identificadas_count} variedad(es) identificada(s)"
                    + (f" y {revision_count} marcada(s) para revisión manual." if revision_count > 0 else ".")
                ),
                "datos": datos_extraidos,
            }
        )
    except Exception as e:
        return JsonResponse(
            {
                "ok": False,
                "error": f"No se pudo procesar automáticamente la imagen ({e}). Puedes transcribir viendo la foto en pantalla.",
            }
        )


# ==============================================================================
# EXPORTACIÓN A EXCEL (.XLSX) DE INFORME GENERAL SEMANAL Y CONTROL DE CALIDAD
# ==============================================================================
@require_GET
def exportar_excel(request, pk=None):
    """
    Genera un informe consolidado en Excel (.xlsx) robusto y profesional:
    - Hoja 1: 'Informe Semanal Consolidado' (Resumen ejecutivo de todo lo que llegó
      en la semana, agrupando Novedades de Envío, Novedades de Descargue Físico y
      Auditoría de Calidad de Planta con totales semanales).
    - Hoja 2: 'Detalle Técnico Parámetros' (Matriz completa con cada parámetro calificado 1-3
      y observaciones técnicas para auditoría agronómica).
    Permite filtrar por ?semana=YYWW, por ?ids=1,2,3 o por pk individual.
    """
    parametros_todos = obtener_parametros_activos()
    wb = openpyxl.Workbook()

    # Hoja 1: Informe Semanal Consolidado
    ws1 = wb.active
    ws1.title = "Informe Semanal Consolidado"
    ws1.views.sheetView[0].showGridLines = True

    # Hoja 2: Detalle Técnico Parámetros
    ws2 = wb.create_sheet("Detalle Técnico Parámetros")
    ws2.views.sheetView[0].showGridLines = True

    # Estilos compartidos
    fill_header_dark = PatternFill(start_color="112218", end_color="112218", fill_type="solid")
    fill_header_table = PatternFill(start_color="15803D", end_color="15803D", fill_type="solid")
    font_title = Font(color="FFFFFF", bold=True, size=13)
    font_sub = Font(color="D1FAE5", size=9.5, italic=True)
    font_meta = Font(color="FFFFFF", size=9, bold=True)
    font_header_table = Font(color="FFFFFF", bold=True, size=9.5)

    fill_totals = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    font_totals = Font(color="0F172A", bold=True, size=10)

    fill_optimo = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    font_optimo = Font(color="166534", bold=True)
    fill_regular = PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid")
    font_regular = Font(color="854D0E", bold=True)
    fill_grave = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    font_grave = Font(color="991B1B", bold=True)

    fill_warn_diff = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    font_warn_diff = Font(color="B45309", bold=True)

    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )
    totals_border = Border(
        left=Side(style="thin", color="94A3B8"),
        right=Side(style="thin", color="94A3B8"),
        top=Side(style="thin", color="0F172A"),
        bottom=Side(style="double", color="0F172A"),
    )

    ids_param = (request.GET.get("ids") or "").strip()
    semana_param = (request.GET.get("semana") or "").strip()

    if pk:
        guias = RecepcionGuia.objects.prefetch_related("variedades__parametros", "fotos").filter(pk=pk)
    elif ids_param:
        ids_list = [int(x) for x in ids_param.split(",") if x.strip().isdigit()]
        guias = RecepcionGuia.objects.prefetch_related("variedades__parametros", "fotos").filter(pk__in=ids_list).order_by("fecha_recepcion", "id")
    elif semana_param:
        guias = RecepcionGuia.objects.prefetch_related("variedades__parametros", "fotos").filter(semana=semana_param).order_by("fecha_recepcion", "id")
    else:
        guias = RecepcionGuia.objects.prefetch_related("variedades__parametros", "fotos").all().order_by("-semana", "fecha_recepcion", "id")

    semanas_set = sorted(list(set(g.semana for g in guias if g.semana)), reverse=True)
    semana_label = semana_param if semana_param else (", ".join(semanas_set) if semanas_set else "General")
    fecha_hoy_fmt = timezone.localdate().strftime("%d/%m/%Y")

    # =========================================================================
    # CONSTRUCCIÓN DE HOJA 1: INFORME SEMANAL CONSOLIDADO
    # =========================================================================
    # 1. Banner superior
    ws1.merge_cells("A1:X1")
    c_title = ws1.cell(row=1, column=1, value="INFORME GENERAL SEMANAL DE RECEPCIÓN Y CONTROL DE CALIDAD DE PLANTA")
    c_title.fill = fill_header_dark
    c_title.font = font_title
    c_title.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[1].height = 28

    ws1.merge_cells("A2:X2")
    c_sub = ws1.cell(row=2, column=1, value="Consolidado General de Entregas · Novedades de Envío, Descargue Físico y Auditoría Fitosanitaria")
    c_sub.fill = fill_header_dark
    c_sub.font = font_sub
    c_sub.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 20

    ws1.merge_cells("A3:X3")
    c_meta = ws1.cell(
        row=3,
        column=1,
        value=f"Semana: {semana_label}  |  Fecha de Emisión: {fecha_hoy_fmt}  |  Total Recepciones: {guias.count()}  |  Estado: Reporte Oficial de Auditoría",
    )
    c_meta.fill = fill_header_dark
    c_meta.font = font_meta
    c_meta.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[3].height = 22

    # 2. Encabezados de la Tabla Semanal (Fila 5)
    encabezados_semanal = [
        "Semana (YYWW)",
        "Fecha Recepción",
        "N° Factura(s) / Guía(s)",
        "Código Recepción",
        "Producto Maestro",
        "Variedad Evaluada",
        "Tipo Contenedor",
        "Plantas Factura",
        "Plantas Físicas Recibidas",
        "Diferencia Plantas (+/-)",
        "Bandejas/Gavetas Factura",
        "Bandejas/Gavetas Recibidas",
        "Diferencia Bandejas/Gavetas (+/-)",
        "¿Novedades en Descargue?",
        "Responsable de Descargue",
        "Observaciones de Descargue Físico",
        "% Calidad Variedad",
        "Calificación (/3.00)",
        "Estado Calidad",
        "¿Alerta Empaque Crítica?",
        "Muestreo 10% (Unidades)",
        "Plantas Calificadas (15 c/u: 5-5-5)",
        "Asegurador de Calidad",
        "Novedades y Hallazgos Calidad de Planta",
    ]
    ws1.append([""] * len(encabezados_semanal))  # Fila 4 en blanco
    ws1.append(encabezados_semanal)              # Fila 5 encabezados
    ws1.row_dimensions[5].height = 28

    for col_idx in range(1, len(encabezados_semanal) + 1):
        c = ws1.cell(row=5, column=col_idx)
        c.fill = fill_header_table
        c.font = font_header_table
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 3. Llenado de filas de datos y acumulación de totales
    row_num1 = 6
    tot_pl_fac = 0
    tot_pl_rec = 0
    tot_pl_dif = 0
    tot_cont_fac = 0
    tot_cont_rec = 0
    tot_cont_dif = 0
    suma_ponderada_calidad = 0.0
    suma_plantas_calidad = 0
    conteo_guias_novedad = 0
    conteo_alertas_empaque = 0

    novedades_envio = []
    novedades_descargue = []
    novedades_calidad = []

    for g in guias:
        fecha_fmt = g.fecha_recepcion.strftime("%d/%m/%Y") if g.fecha_recepcion else ""
        if g.tiene_novedades_descargue:
            conteo_guias_novedad += 1
            novedades_descargue.append(
                f"Guía {g.codigo_guia} ({g.producto_maestro} - {g.responsable_descargue or g.responsable}): "
                + (g.observaciones_descargue or "Novedades físicas reportadas en descargue.")
            )

        if g.alerta_empaque_critica:
            conteo_alertas_empaque += 1

        for v in g.variedades.all():
            mapa_p = {p.codigo_parametro: p for p in v.parametros.all()}

            resumen_obs_calidad = []
            for p_def in parametros_todos:
                obj_p = mapa_p.get(p_def["codigo"])
                if obj_p:
                    etiq = "Óptimo" if obj_p.puntaje == 3 else ("Regular" if obj_p.puntaje == 2 else "Grave")
                    if obj_p.observacion:
                        resumen_obs_calidad.append(f"{p_def['nombre_corto']} ({obj_p.puntaje}/3 {etiq}): {obj_p.observacion}")
                    elif obj_p.puntaje < 3:
                        resumen_obs_calidad.append(f"{p_def['nombre_corto']} ({obj_p.puntaje}/3 {etiq})")

            obs_calidad_texto = " | ".join(resumen_obs_calidad) if resumen_obs_calidad else "Conforme (100% Óptimo)"
            estado_legible = "ÓPTIMO" if v.estado_calidad == "MUY_BUENO" else ("REGULAR" if v.estado_calidad == "REGULAR" else "GRAVE")

            plantas_fac = v.plantas_factura or v.plantas_recibidas
            plantas_rec = v.plantas_recibidas
            dif_pl = plantas_rec - plantas_fac

            cont_fac = v.contenedores_factura or v.contenedores_recibidos
            cont_rec = v.contenedores_recibidos
            dif_cont = cont_rec - cont_fac

            tot_pl_fac += plantas_fac
            tot_pl_rec += plantas_rec
            tot_pl_dif += dif_pl

            tot_cont_fac += cont_fac
            tot_cont_rec += cont_rec
            tot_cont_dif += dif_cont

            if v.porcentaje_calidad > 0 and plantas_rec > 0:
                suma_ponderada_calidad += (v.porcentaje_calidad * plantas_rec)
                suma_plantas_calidad += plantas_rec

            if dif_pl != 0 or dif_cont != 0:
                novedades_envio.append(
                    f"{g.producto_maestro} - {v.variedad} (Factura {g.numero_factura or g.codigo_guia}): "
                    f"Diferencia de {dif_pl:+d} plantas y {dif_cont:+d} {v.tipo_contenedor.lower()}s."
                )

            if v.estado_calidad != "MUY_BUENO" or v.alerta_empaque or resumen_obs_calidad:
                if v.estado_calidad != "MUY_BUENO" or v.alerta_empaque:
                    novedades_calidad.append(
                        f"{g.producto_maestro} - {v.variedad}: Estado {estado_legible} ({v.porcentaje_calidad:.1f}%). {obs_calidad_texto}"
                    )

            dif_pl_str = f"{dif_pl:+d}" if dif_pl != 0 else "0 (Exacto)"
            dif_cont_str = f"{dif_cont:+d}" if dif_cont != 0 else "0 (Exacto)"
            nov_descargue_str = "SÍ (Con Novedad)" if (g.tiene_novedades_descargue or dif_pl != 0 or dif_cont != 0) else "NO"
            alerta_empaque_str = "SÍ (¡Crítica!)" if (v.alerta_empaque or g.alerta_empaque_critica) else "NO"

            fila_semanal = [
                g.semana,
                fecha_fmt,
                g.numero_factura or "S/N",
                g.codigo_guia,
                g.producto_maestro,
                v.variedad,
                v.tipo_contenedor,
                plantas_fac,
                plantas_rec,
                dif_pl_str,
                cont_fac,
                cont_rec,
                dif_cont_str,
                nov_descargue_str,
                g.responsable_descargue or g.responsable,
                g.observaciones_descargue or "Sin novedades reportadas",
                round(v.porcentaje_calidad, 1),
                round(v.calificacion_variedad, 2),
                estado_legible,
                alerta_empaque_str,
                v.contenedores_a_revisar,
                v.total_plantas_a_revisar,
                g.responsable,
                obs_calidad_texto,
            ]
            ws1.append(fila_semanal)

            # Estilos de celda
            for c_i in range(1, len(fila_semanal) + 1):
                cell_item = ws1.cell(row=row_num1, column=c_i)
                cell_item.border = thin_border
                cell_item.alignment = Alignment(vertical="center", wrap_text=(c_i in (16, 24)))

            # Centrar campos numéricos y claves
            for c_center in (1, 2, 4, 7, 10, 13, 14, 17, 18, 19, 20, 21, 22):
                ws1.cell(row=row_num1, column=c_center).alignment = Alignment(horizontal="center", vertical="center")

            # Resaltar diferencias de plantas (Col 10)
            if dif_pl != 0:
                cell_dif_pl = ws1.cell(row=row_num1, column=10)
                cell_dif_pl.fill = fill_warn_diff
                cell_dif_pl.font = font_warn_diff

            # Resaltar diferencias de contenedores (Col 13)
            if dif_cont != 0:
                cell_dif_cont = ws1.cell(row=row_num1, column=13)
                cell_dif_cont.fill = fill_warn_diff
                cell_dif_cont.font = font_warn_diff

            # Resaltar novedades en descargue (Col 14)
            if nov_descargue_str.startswith("SÍ"):
                cell_nov = ws1.cell(row=row_num1, column=14)
                cell_nov.fill = fill_grave
                cell_nov.font = font_grave

            # Colorear Estado de Calidad (Col 19)
            cell_est = ws1.cell(row=row_num1, column=19)
            if estado_legible == "ÓPTIMO":
                cell_est.fill = fill_optimo
                cell_est.font = font_optimo
            elif estado_legible == "REGULAR":
                cell_est.fill = fill_regular
                cell_est.font = font_regular
            else:
                cell_est.fill = fill_grave
                cell_est.font = font_grave

            # Resaltar alerta de empaque (Col 20)
            if alerta_empaque_str.startswith("SÍ"):
                cell_emp = ws1.cell(row=row_num1, column=20)
                cell_emp.fill = fill_grave
                cell_emp.font = font_grave

            row_num1 += 1

    # 4. Fila de Totales Consolidados de la Semana
    prom_ponderado_sem = (suma_ponderada_calidad / suma_plantas_calidad) if suma_plantas_calidad > 0 else 0.0
    dif_pl_tot_str = f"{tot_pl_dif:+d} pl" if tot_pl_dif != 0 else "0 pl (Exacto)"
    dif_cont_tot_str = f"{tot_cont_dif:+d}" if tot_cont_dif != 0 else "0 (Exacto)"

    ws1.merge_cells(f"A{row_num1}:G{row_num1}")
    c_tot_label = ws1.cell(row=row_num1, column=1, value="TOTALES CONSOLIDADOS DE LA SEMANA")
    c_tot_label.font = font_totals
    c_tot_label.alignment = Alignment(horizontal="right", vertical="center")

    ws1.cell(row=row_num1, column=8, value=tot_pl_fac).font = font_totals
    ws1.cell(row=row_num1, column=9, value=tot_pl_rec).font = font_totals
    c_tot_dif_pl = ws1.cell(row=row_num1, column=10, value=dif_pl_tot_str)
    c_tot_dif_pl.font = font_totals
    c_tot_dif_pl.alignment = Alignment(horizontal="center", vertical="center")
    if tot_pl_dif != 0:
        c_tot_dif_pl.fill = fill_warn_diff

    ws1.cell(row=row_num1, column=11, value=tot_cont_fac).font = font_totals
    ws1.cell(row=row_num1, column=12, value=tot_cont_rec).font = font_totals
    c_tot_dif_cont = ws1.cell(row=row_num1, column=13, value=dif_cont_tot_str)
    c_tot_dif_cont.font = font_totals
    c_tot_dif_cont.alignment = Alignment(horizontal="center", vertical="center")
    if tot_cont_dif != 0:
        c_tot_dif_cont.fill = fill_warn_diff

    c_tot_nov = ws1.cell(
        row=row_num1,
        column=14,
        value=f"{conteo_guias_novedad} con novedad" if conteo_guias_novedad > 0 else "Conforme",
    )
    c_tot_nov.font = font_totals
    c_tot_nov.alignment = Alignment(horizontal="center", vertical="center")

    ws1.cell(row=row_num1, column=15, value="")
    ws1.cell(row=row_num1, column=16, value="")

    c_tot_pct = ws1.cell(row=row_num1, column=17, value=round(prom_ponderado_sem, 1))
    c_tot_pct.font = font_totals
    c_tot_pct.alignment = Alignment(horizontal="center", vertical="center")
    if prom_ponderado_sem >= 86.7:
        c_tot_pct.fill = fill_optimo
    elif prom_ponderado_sem >= 61.7:
        c_tot_pct.fill = fill_regular
    else:
        c_tot_pct.fill = fill_grave

    c_tot_calif = ws1.cell(row=row_num1, column=18, value=round((prom_ponderado_sem / 100.0) * 3.0, 2))
    c_tot_calif.font = font_totals
    c_tot_calif.alignment = Alignment(horizontal="center", vertical="center")

    for c_rest in range(19, 25):
        ws1.cell(row=row_num1, column=c_rest, value="")

    for c_i in range(1, 25):
        cell_t = ws1.cell(row=row_num1, column=c_i)
        cell_t.border = totals_border
        if not cell_t.fill.start_color or cell_t.fill.start_color.rgb == "00000000":
            cell_t.fill = fill_totals

    ws1.row_dimensions[row_num1].height = 24

    # 5. Sección de Resumen Ejecutivo de Novedades (Debajo de la tabla)
    sec_row = row_num1 + 2
    ws1.merge_cells(f"A{sec_row}:X{sec_row}")
    c_sec_tit = ws1.cell(row=sec_row, column=1, value="RESUMEN EJECUTIVO DE NOVEDADES Y DICTAMEN DE LA SEMANA")
    c_sec_tit.fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    c_sec_tit.font = Font(color="FFFFFF", bold=True, size=11)
    c_sec_tit.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws1.row_dimensions[sec_row].height = 24

    # Bloque 1: Novedades de Envío / Facturación
    sec_row += 1
    ws1.merge_cells(f"A{sec_row}:X{sec_row}")
    c_h1 = ws1.cell(row=sec_row, column=1, value="1. Novedades de Envío y Facturación (Diferencias Factura vs Conteo Físico):")
    c_h1.font = Font(bold=True, color="0F172A", size=10)
    sec_row += 1

    if novedades_envio:
        for item_nov in novedades_envio:
            ws1.merge_cells(f"A{sec_row}:X{sec_row}")
            ws1.cell(row=sec_row, column=1, value=f"  • {item_nov}").font = font_warn_diff
            sec_row += 1
    else:
        ws1.merge_cells(f"A{sec_row}:X{sec_row}")
        ws1.cell(row=sec_row, column=1, value="  ✓ Conforme: Cantidades físicas recibidas coinciden exactamente con la factura / guía.").font = font_optimo
        sec_row += 1

    # Bloque 2: Novedades de Descargue Físico
    ws1.merge_cells(f"A{sec_row}:X{sec_row}")
    c_h2 = ws1.cell(row=sec_row, column=1, value="2. Novedades en Descargue Físico (Estado de Furgón, Estiba e Incidencias en Finca):")
    c_h2.font = Font(bold=True, color="0F172A", size=10)
    sec_row += 1

    if novedades_descargue:
        for item_nov in novedades_descargue:
            ws1.merge_cells(f"A{sec_row}:X{sec_row}")
            ws1.cell(row=sec_row, column=1, value=f"  • {item_nov}").font = font_grave
            sec_row += 1
    else:
        ws1.merge_cells(f"A{sec_row}:X{sec_row}")
        ws1.cell(row=sec_row, column=1, value="  ✓ Conforme: Descargue físico realizado sin novedades de estiba, roturas ni faltantes.").font = font_optimo
        sec_row += 1

    # Bloque 3: Novedades y Dictamen de Calidad de Planta
    ws1.merge_cells(f"A{sec_row}:X{sec_row}")
    c_h3 = ws1.cell(row=sec_row, column=1, value="3. Novedades y Dictamen de Calidad de Planta (Alertas Fitosanitarias, Raíz, Follaje y Empaque):")
    c_h3.font = Font(bold=True, color="0F172A", size=10)
    sec_row += 1

    if novedades_calidad:
        for item_nov in novedades_calidad:
            ws1.merge_cells(f"A{sec_row}:X{sec_row}")
            ws1.cell(row=sec_row, column=1, value=f"  • {item_nov}").font = font_grave
            sec_row += 1
    else:
        ws1.merge_cells(f"A{sec_row}:X{sec_row}")
        ws1.cell(row=sec_row, column=1, value="  ✓ Conforme: Todos los lotes y variedades cumplen con el estándar óptimo de calidad vegetal.").font = font_optimo
        sec_row += 1

    # Ajustar anchos de columnas en Hoja 1
    anchos_sugeridos_ws1 = {
        "A": 15, "B": 14, "C": 20, "D": 18, "E": 18, "F": 22, "G": 16,
        "H": 16, "I": 16, "J": 18, "K": 16, "L": 16, "M": 18, "N": 18,
        "O": 22, "P": 32, "Q": 16, "R": 14, "S": 16, "T": 16, "U": 16,
        "V": 18, "W": 22, "X": 45,
    }
    for col_letter, ancho in anchos_sugeridos_ws1.items():
        ws1.column_dimensions[col_letter].width = ancho

    # =========================================================================
    # CONSTRUCCIÓN DE HOJA 2: DETALLE TÉCNICO Y PARÁMETROS DE CALIDAD
    # =========================================================================
    encabezados_tecnicos = [
        "Semana", "Fecha", "Código Recepción", "N° Factura", "Producto Maestro",
        "Variedad", "Tipo Contenedor", "Plantas Recibidas", "Contenedores",
        "% Calidad Variedad", "Calificación (/3.00)", "Estado Variedad",
        "¿Alerta Empaque?", "Resumen Observaciones",
    ]
    for p in parametros_todos:
        encabezados_tecnicos.append(f"{p['nombre_corto']} (1-3)")
        encabezados_tecnicos.append(f"{p['nombre_corto']} Obs")

    ws2.append(encabezados_tecnicos)
    ws2.row_dimensions[1].height = 28
    for col_idx in range(1, len(encabezados_tecnicos) + 1):
        c2 = ws2.cell(row=1, column=col_idx)
        c2.fill = fill_header_table
        c2.font = font_header_table
        c2.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    row_num2 = 2
    for g in guias:
        fecha_fmt = g.fecha_recepcion.strftime("%d/%m/%Y") if g.fecha_recepcion else ""
        for v in g.variedades.all():
            mapa_p = {p.codigo_parametro: p for p in v.parametros.all()}
            resumen_obs_partes = []
            for p_def in parametros_todos:
                obj_p = mapa_p.get(p_def["codigo"])
                if obj_p:
                    etiq = "Óptimo" if obj_p.puntaje == 3 else ("Regular" if obj_p.puntaje == 2 else "Grave")
                    if obj_p.observacion:
                        resumen_obs_partes.append(f"{p_def['nombre_corto']} ({obj_p.puntaje}/3): {obj_p.observacion}")

            resumen_obs_texto = " | ".join(resumen_obs_partes) if resumen_obs_partes else "100% Conforme"
            estado_legible = "ÓPTIMO" if v.estado_calidad == "MUY_BUENO" else ("REGULAR" if v.estado_calidad == "REGULAR" else "GRAVE")

            fila_tec = [
                g.semana,
                fecha_fmt,
                g.codigo_guia,
                g.numero_factura or "S/N",
                g.producto_maestro,
                v.variedad,
                v.tipo_contenedor,
                v.plantas_recibidas,
                v.contenedores_recibidos,
                round(v.porcentaje_calidad, 1),
                round(v.calificacion_variedad, 2),
                estado_legible,
                "SÍ" if (v.alerta_empaque or g.alerta_empaque_critica) else "NO",
                resumen_obs_texto,
            ]
            for p_def in parametros_todos:
                obj_p = mapa_p.get(p_def["codigo"])
                fila_tec.append(obj_p.puntaje if obj_p else 3)
                fila_tec.append(obj_p.observacion if obj_p else "")

            ws2.append(fila_tec)

            for c_i in range(1, len(fila_tec) + 1):
                c = ws2.cell(row=row_num2, column=c_i)
                c.border = thin_border
                c.alignment = Alignment(vertical="center", wrap_text=(c_i == 14 or c_i >= 15 and c_i % 2 == 0))

            # Colorear estado (col 12)
            c_est2 = ws2.cell(row=row_num2, column=12)
            if estado_legible == "ÓPTIMO":
                c_est2.fill = fill_optimo
                c_est2.font = font_optimo
            elif estado_legible == "REGULAR":
                c_est2.fill = fill_regular
                c_est2.font = font_regular
            else:
                c_est2.fill = fill_grave
                c_est2.font = font_grave

            # Colorear cada puntaje de parámetro
            for idx_p in range(len(parametros_todos)):
                col_pts = 15 + (idx_p * 2)
                c_pts = ws2.cell(row=row_num2, column=col_pts)
                c_pts.alignment = Alignment(horizontal="center", vertical="center")
                if c_pts.value == 3:
                    c_pts.fill = fill_optimo
                    c_pts.font = font_optimo
                elif c_pts.value == 2:
                    c_pts.fill = fill_regular
                    c_pts.font = font_regular
                elif c_pts.value == 1:
                    c_pts.fill = fill_grave
                    c_pts.font = font_grave

            row_num2 += 1

    # Ajustar anchos de columnas en Hoja 2
    for col in ws2.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws2.column_dimensions[col_letter].width = min(max(max_len + 3, 11), 45)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    fecha_archivo = timezone.localdate().strftime("%Y%m%d")
    if semana_param:
        nombre_archivo = f"informe_general_calidad_semana_{semana_param}_{fecha_archivo}.xlsx"
    elif pk and guias.exists():
        nombre_archivo = f"informe_calidad_{guias[0].codigo_guia}_{fecha_archivo}.xlsx"
    else:
        nombre_archivo = f"informe_general_semanal_calidad_{fecha_archivo}.xlsx"

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response
