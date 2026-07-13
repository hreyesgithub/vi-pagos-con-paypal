import os
import base64
import requests
import logging
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "live")
PAYPAL_API_URL = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"

def get_paypal_access_token() -> str:
    """Obtiene token de acceso para API de PayPal."""
    auth = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}
    try:
        response = requests.post(f"{PAYPAL_API_URL}/v1/oauth2/token", headers=headers, data=data, timeout=10)
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        logger.error(f"Error obteniendo token PayPal: {e}")
        raise

def create_subscription(plan_id: str, subscriber_email: str, custom_id: str, return_url: str, cancel_url: str) -> Dict[str, Any]:
    """Crea una suscripción en PayPal y devuelve la respuesta."""
    token = get_paypal_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "plan_id": plan_id,
        "subscriber": {"email_address": subscriber_email},
        "custom_id": custom_id,   # user_id de Supabase
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "user_action": "SUBSCRIBE_NOW"
        }
    }
    response = None
    try:
        response = requests.post(f"{PAYPAL_API_URL}/v1/billing/subscriptions", headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error creando suscripción en PayPal: {e}")
        if response is not None:
            logger.error(f"Respuesta PayPal: {response.text}")
        raise

def get_subscription_details(subscription_id: str) -> Dict[str, Any]:
    """Obtiene detalles de una suscripción de PayPal."""
    token = get_paypal_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{PAYPAL_API_URL}/v1/billing/subscriptions/{subscription_id}", headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error obteniendo detalles de suscripción {subscription_id}: {e}")
        raise

def verify_webhook_signature(headers: dict, raw_body: str, webhook_id: str) -> bool:
    """Verifica la firma del webhook usando la API de PayPal."""
    token = get_paypal_access_token()
    headers_req = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
        "webhook_event": json.loads(raw_body) if raw_body else {}
    }
    try:
        response = requests.post(f"{PAYPAL_API_URL}/v1/notifications/verify-webhook-signature", headers=headers_req, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result.get("verification_status") == "SUCCESS"
    except Exception as e:
        logger.error(f"Error verificando webhook: {e}")
        return False  # En producción, esto debería fallar si no coincide