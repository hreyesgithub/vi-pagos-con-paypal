from supabase.client import Client
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import os

def obtener_permisos_por_plan(plan: str) -> Dict[str, Any]:
    """Devuelve los permisos y límites según el plan."""
    if plan == 'inversor':
        return {
            "permiso_habilitar_propiedad": False,
            "permiso_habilitar_analisis_ia": True,
            "permiso_habilitar_reportes": True,
            "permiso_habilitar_alertas": False,
            "permiso_habilitar_chatbot": True,
            "limite_docs": 5,
            "docs_analizados_ia_disponible": "5",
            "pago_price_usd": 35,
            "pago_plan": "inversor",
            "subscription_status": "activo",
            "status": "activo",  # para perfiles.status
        }
    elif plan == 'corporativo':
        return {
            "permiso_habilitar_propiedad": True,
            "permiso_habilitar_analisis_ia": True,
            "permiso_habilitar_reportes": True,
            "permiso_habilitar_alertas": True,
            "permiso_habilitar_chatbot": True,
            "limite_docs": 10,
            "docs_analizados_ia_disponible": "10",
            "pago_price_usd": 120,
            "pago_plan": "corporativo",
            "subscription_status": "activo",
            "status": "activo",
        }
    else:  # free
        return {
            "permiso_habilitar_propiedad": False,
            "permiso_habilitar_analisis_ia": False,
            "permiso_habilitar_reportes": False,
            "permiso_habilitar_alertas": False,
            "permiso_habilitar_chatbot": False,
            "limite_docs": 0,
            "docs_analizados_ia_disponible": "0",
            "pago_price_usd": 0,
            "pago_plan": "free",
            "subscription_status": "free",
            "status": "activo",
        }

def guardar_suscripcion_inicial(supabase: Client, user_id: str, paypal_subscription_id: str, plan_id: str, paypal_data: Dict):
    """Crea un registro en subscriptions con estado 'pending'."""
    data = {
        "user_id": user_id,
        "paypal_subscription_id": paypal_subscription_id,
        "plan_id": plan_id,
        "status": "pending",
        "paypal_data": paypal_data,
        "start_date": None,
        "next_billing_date": None,
        "current_period_end": None,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    result = supabase.table('subscriptions').insert(data).execute()
    return result.data[0] if result.data else None

def activar_suscripcion(supabase: Client, paypal_subscription_id: str, plan: str, current_period_end: datetime):
    """
    Activa la suscripción: cambia status a 'active', actualiza fechas y perfiles.
    plan: 'inversor' o 'corporativo'
    """
    # 1. Actualizar tabla subscriptions
    update_data = {
        "status": "active",
        "start_date": datetime.now(timezone.utc).isoformat(),
        "next_billing_date": current_period_end.isoformat(),
        "current_period_end": current_period_end.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    result = supabase.table('subscriptions').update(update_data).eq('paypal_subscription_id', paypal_subscription_id).execute()
    if not result.data:
        raise Exception("No se encontró la suscripción para activar")
    sub = result.data[0]
    user_id = sub['user_id']

    # 2. Obtener permisos según plan
    permisos = obtener_permisos_por_plan(plan)
    # Añadir fechas
    permisos.update({
        "subscription_until": current_period_end.isoformat(),
        "pago_proxima_fecha": current_period_end.isoformat(),
        "pago_recurring": True,
        "pago_payment_method": "PayPal",
        "updated_at": datetime.now(timezone.utc).isoformat()
    })

    # 3. Actualizar perfiles
    supabase.table('perfiles').update(permisos).eq('id', user_id).execute()

    # 4. Registrar en pagos_historico (primer pago)
    supabase.table('pagos_historico').insert({
        "user_id": user_id,
        "monto": permisos['pago_price_usd'],
        "fecha": datetime.now(timezone.utc).isoformat(),
        "url_recibo": None  # Podrías obtener la URL de PayPal
    }).execute()

    return sub

def renovar_suscripcion(supabase: Client, paypal_subscription_id: str, new_expiry: datetime):
    """Renueva la suscripción extendiendo current_period_end."""
    # 1. Obtener suscripción para saber plan y user_id
    sub_result = supabase.table('subscriptions').select('user_id, plan_id').eq('paypal_subscription_id', paypal_subscription_id).execute()
    if not sub_result.data:
        raise Exception("Suscripción no encontrada")
    sub = sub_result.data[0]
    user_id = sub['user_id']
    plan_id = sub['plan_id']  # Podrías mapear a 'inversor' o 'corporativo' según plan_id

    # 2. Actualizar subscriptions
    update_data = {
        "current_period_end": new_expiry.isoformat(),
        "next_billing_date": new_expiry.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    supabase.table('subscriptions').update(update_data).eq('paypal_subscription_id', paypal_subscription_id).execute()

    # 3. Actualizar perfiles (fechas)
    supabase.table('perfiles').update({
        "subscription_until": new_expiry.isoformat(),
        "pago_proxima_fecha": new_expiry.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq('id', user_id).execute()

    # 4. Registrar pago en historico
    # Obtener precio del plan (lo tenemos en permisos, pero podemos obtenerlo de perfiles)
    perfil = supabase.table('perfiles').select('pago_price_usd').eq('id', user_id).execute()
    precio = perfil.data[0]['pago_price_usd'] if perfil.data else 0
    supabase.table('pagos_historico').insert({
        "user_id": user_id,
        "monto": precio,
        "fecha": datetime.now(timezone.utc).isoformat(),
        "url_recibo": None
    }).execute()

def cancelar_suscripcion(supabase: Client, paypal_subscription_id: str):
    """Marca la suscripción como cancelada y vuelve a free."""
    # 1. Actualizar subscriptions
    update_data = {"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}
    result = supabase.table('subscriptions').update(update_data).eq('paypal_subscription_id', paypal_subscription_id).execute()
    if not result.data:
        return
    sub = result.data[0]
    user_id = sub['user_id']

    # 2. Actualizar perfiles a free
    permisos = obtener_permisos_por_plan('free')
    permisos.update({
        "subscription_until": None,
        "pago_proxima_fecha": None,
        "pago_recurring": False,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    supabase.table('perfiles').update(permisos).eq('id', user_id).execute()

def expirar_suscripcion(supabase: Client, paypal_subscription_id: str):
    """Marca la suscripción como expirada (similar a cancelar)."""
    update_data = {"status": "expired", "updated_at": datetime.now(timezone.utc).isoformat()}
    result = supabase.table('subscriptions').update(update_data).eq('paypal_subscription_id', paypal_subscription_id).execute()
    if not result.data:
        return
    sub = result.data[0]
    user_id = sub['user_id']
    permisos = obtener_permisos_por_plan('free')
    permisos.update({
        "subscription_until": None,
        "pago_proxima_fecha": None,
        "pago_recurring": False,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    supabase.table('perfiles').update(permisos).eq('id', user_id).execute()

def obtener_suscripcion_activa(supabase: Client, user_id: str) -> Optional[Dict]:
    """Devuelve la suscripción activa (status='active' y current_period_end > ahora)."""
    now = datetime.now(timezone.utc).isoformat()
    result = supabase.table('subscriptions')\
        .select('*')\
        .eq('user_id', user_id)\
        .eq('status', 'active')\
        .gt('current_period_end', now)\
        .order('current_period_end', desc=True)\
        .limit(1)\
        .execute()
    return result.data[0] if result.data else None

def mapear_plan_id_a_nombre(plan_id: str) -> str:
    """Convierte el plan_id de PayPal a 'inversor' o 'corporativo'."""
    inversor_id = os.environ.get("PAYPAL_PLAN_ID_INVERSOR")
    corporativo_id = os.environ.get("PAYPAL_PLAN_ID_CORPORATIVO")
    if plan_id == inversor_id:
        return "inversor"
    elif plan_id == corporativo_id:
        return "corporativo"
    else:
        return "inversor"  # fallback