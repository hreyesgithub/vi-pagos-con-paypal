from fastapi import FastAPI, Request, Body, HTTPException
from typing import Dict, Any
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client # type:ignore

logger = logging.getLogger(__name__)

# Importaciones adicionales
from services.paypal import create_subscription, verify_webhook_signature, get_subscription_details
from services.suscripcion import (
    guardar_suscripcion_inicial,
    activar_suscripcion,
    renovar_suscripcion,
    cancelar_suscripcion,
    expirar_suscripcion,
    obtener_suscripcion_activa,
    mapear_plan_id_a_nombre,
    obtener_permisos_por_plan
)

app = FastAPI()

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
if not supabase_url or not supabase_key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")
supabase = create_client(supabase_url, supabase_key)

# ==================== ENDPOINTS DE SUSCRIPCIÓN ====================

@app.post("/api/crear-suscripcion")
def crear_suscripcion_endpoint(request: Request, data: Dict[str, Any] = Body(...)):
    """
    Inicia el proceso de suscripción.
    Body: { "plan": "inversor" | "corporativo" }
    Devuelve: { "approval_url": "..." }
    """
    # Validar token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Falta token")
    token = auth_header.split("Bearer ")[1]
    try:
        auth_result = supabase.auth.get_user(token)
        user = getattr(auth_result, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        user_id = user.id
        email = user.email
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token inválido")

    # Obtener plan
    plan = data.get("plan", "inversor")
    if plan not in ["inversor", "corporativo"]:
        raise HTTPException(status_code=400, detail="Plan no válido. Usa 'inversor' o 'corporativo'.")

    # Obtener plan_id según plan
    plan_id = os.environ.get("PAYPAL_PLAN_ID_CORPORATIVO") if plan == "corporativo" else os.environ.get("PAYPAL_PLAN_ID_INVERSOR")
    if not plan_id:
        raise HTTPException(status_code=500, detail="Plan no configurado en el servidor")

    # URLs de retorno
    app_url = os.environ.get("NEXT_PUBLIC_APP_URL")
    return_url = f"{app_url}/suscripcion-exito"
    cancel_url = f"{app_url}/suscripcion-cancelada"

    # Crear suscripción en PayPal
    try:
        paypal_resp = create_subscription(plan_id, email, user_id, return_url, cancel_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear suscripción en PayPal: {str(e)}")

    # Guardar en BD (estado pending)
    try:
        guardar_suscripcion_inicial(supabase, user_id, paypal_resp["id"], plan_id, paypal_resp)
    except Exception as e:
        logger.error(f"Error guardando suscripción inicial: {e}")

    # Obtener URL de aprobación
    approval_url = None
    for link in paypal_resp.get("links", []):
        if link.get("rel") == "approve":
            approval_url = link.get("href")
            break

    if not approval_url:
        raise HTTPException(status_code=500, detail="No se obtuvo URL de aprobación")

    return {"approval_url": approval_url}

@app.post("/api/webhook/paypal")
async def paypal_webhook(request: Request):
    """
    Webhook que recibe notificaciones de PayPal.
    """
    raw_body = await request.body()
    body_str = raw_body.decode('utf-8')
    headers = dict(request.headers)

    # Verificar firma (recomendado)
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
    if webhook_id and not verify_webhook_signature(headers, body_str, webhook_id):
        raise HTTPException(status_code=401, detail="Firma inválida")

    try:
        event = json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body no JSON")

    event_type = event.get("event_type")
    resource = event.get("resource", {})

    # Procesar según tipo de evento
    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        sub_id = resource.get("id")
        custom_id = resource.get("custom_id")  # user_id
        try:
            details = get_subscription_details(sub_id)
            plan_id = details.get("plan_id") or ""
            plan_nombre = mapear_plan_id_a_nombre(plan_id)
            billing_info = details.get("billing_info", {})
            next_billing = billing_info.get("next_billing_time")
            if next_billing:
                expiry = datetime.fromisoformat(next_billing.replace('Z', '+00:00'))
            else:
                expiry = datetime.now(timezone.utc) + timedelta(days=30)
            activar_suscripcion(supabase, sub_id, plan_nombre, expiry)
        except Exception as e:
            logger.error(f"Error activando suscripción {sub_id}: {e}")

    elif event_type == "PAYMENT.SALE.COMPLETED":
        sub_id = resource.get("billing_agreement_id")
        if sub_id:
            try:
                details = get_subscription_details(sub_id)
                billing_info = details.get("billing_info", {})
                next_billing = billing_info.get("next_billing_time")
                if next_billing:
                    new_expiry = datetime.fromisoformat(next_billing.replace('Z', '+00:00'))
                    renovar_suscripcion(supabase, sub_id, new_expiry)
                else:
                    new_expiry = datetime.now(timezone.utc) + timedelta(days=30)
                    renovar_suscripcion(supabase, sub_id, new_expiry)
            except Exception as e:
                logger.error(f"Error renovando suscripción {sub_id}: {e}")

    elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        sub_id = resource.get("id")
        if sub_id:
            cancelar_suscripcion(supabase, sub_id)

    elif event_type == "BILLING.SUBSCRIPTION.EXPIRED":
        sub_id = resource.get("id")
        if sub_id:
            expirar_suscripcion(supabase, sub_id)

    return {"status": "received"}

@app.get("/api/estado-suscripcion")
def estado_suscripcion(request: Request):
    """Devuelve el estado de la suscripción del usuario autenticado."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Falta token")
    token = auth_header.split("Bearer ")[1]
    try:
        auth_result = supabase.auth.get_user(token)
        user = getattr(auth_result, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        user_id = user.id
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token inválido")

    sub = obtener_suscripcion_activa(supabase, user_id)
    if sub:
        plan_id = sub.get('plan_id') or ""
        plan_nombre = mapear_plan_id_a_nombre(plan_id)
        return {
            "status": "active",
            "plan": plan_nombre,
            "expires_at": sub.get('current_period_end')
        }
    else:
        # Ver si tiene alguna suscripción pero no activa
        result = supabase.table('subscriptions').select('status').eq('user_id', user_id).order('created_at', desc=True).limit(1).execute()
        if result.data:
            return {"status": result.data[0]['status'], "message": "Tu suscripción no está activa"}
        else:
            return {"status": "free", "message": "No tienes suscripción activa"}

# ==================== DECORADOR PARA PROTEGER RUTAS ====================

def requiere_suscripcion(func):
    """Decorador para endpoints que requieren suscripción activa."""
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Buscar request
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        if not request:
            request = kwargs.get('request')
        if not request:
            raise HTTPException(status_code=500, detail="No se pudo obtener la request")
        
        # Validar token y obtener user_id
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Falta token")
        token = auth_header.split("Bearer ")[1]
        try:
            auth_result = supabase.auth.get_user(token)
            user = getattr(auth_result, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="Usuario no encontrado")
            user_id = user.id
        except Exception:
            raise HTTPException(status_code=401, detail="Token inválido")
        
        # Verificar suscripción activa
        sub = obtener_suscripcion_activa(supabase, user_id)
        if not sub:
            raise HTTPException(status_code=403, detail="Se requiere suscripción activa para acceder a este recurso")
        
        kwargs['user_id'] = user_id
        return func(*args, **kwargs)
    return wrapper

# Ejemplo de uso:
# @app.get("/api/datos-privados")
# @requiere_suscripcion
# def datos_privados(request: Request, user_id: str = None):
#     return {"mensaje": "Datos solo para suscriptores"}