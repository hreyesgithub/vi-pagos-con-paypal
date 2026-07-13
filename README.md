## Sistema de Suscripciones con PayPal

Este módulo gestiona las suscripciones mensuales para los planes **Inversor** y **Corporativo**.

### Flujo de trabajo

1. **Selección de plan**: El usuario elige su plan en `/suscripcion`.
2. **Creación en PayPal**: El backend crea la suscripción en PayPal y redirige al usuario para que apruebe.
3. **Webhook**: PayPal notifica al backend cuando la suscripción se activa, se renueva o se cancela.
4. **Actualización de permisos**: El backend actualiza los permisos y límites en la tabla `perfiles` según el plan contratado.
5. **Renovaciones automáticas**: Cada mes, PayPal envía un evento `PAYMENT.SALE.COMPLETED` que renueva la fecha de expiración.

### Variables de entorno requeridas

- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_PLAN_ID_INVERSOR`
- `PAYPAL_PLAN_ID_CORPORATIVO`
- `PAYPAL_WEBHOOK_ID`
- `NEXT_PUBLIC_APP_URL`

### Endpoints

- `POST /api/crear-suscripcion` - Inicia el proceso de suscripción.
- `POST /api/webhook/paypal` - Recibe notificaciones de PayPal.
- `GET /api/estado-suscripcion` - Consulta el estado actual.

### Protección de rutas

Usa el decorador `@requiere_suscripcion` para proteger endpoints que requieran suscripción activa.