# Tracker de Gastos — Telegram Bot + Claude

Bot privado de Telegram que:

- Registra gastos con flujo **interactivo** (`/nuevo`) o rápido (`/gasto`) en
  **múltiples monedas** (ARS, USD, USDT, EUR).
- Categoriza automáticamente con Claude (Haiku).
- El **día 1 de cada mes** te pregunta cuánto te llegó de cada tarjeta y
  guarda el dato.
- Genera reportes y decisiones contextualizadas con Claude (Sonnet).
- Persiste todo en JSON local. Tus datos no salen de tu máquina (excepto
  monto + descripción que viaja a Anthropic para categorizar).

## Estructura

```
expense_bot/
├── bot.py              # Handlers de Telegram + setup
├── run.py              # Wrapper que carga .env
├── config.py           # Tokens, monedas, tarjetas, scheduler, perfil financiero
├── storage.py          # Persistencia JSON: gastos + resúmenes de tarjeta
├── parser.py           # Parser regex (monto + moneda + descripción)
├── claude_client.py    # Wrapper SDK Anthropic
├── conversation.py     # /nuevo y /tarjetas (ConversationHandler)
├── scheduler.py        # Job mensual (día 1) + catch-up al startup
├── reports.py          # Agregaciones multi-moneda
├── alerts.py           # Reglas determinísticas
├── requirements.txt
├── .env.example
└── README.md
```

## Setup en 5 minutos

### 1. Crear el bot en Telegram

1. Abrí Telegram → **@BotFather** → `/newbot`.
2. Guardá el token que te da.
3. (Opcional) `/setcommands` y pegá:

```
nuevo - Registrar gasto paso a paso
gasto - Registrar gasto rápido
tarjetas - Cargar resúmenes mensuales
resumenes - Ver resúmenes del mes
hoy - Resumen del día
semana - Análisis 7 días
mes - Reporte mensual
categoria - Top categorías
tendencia - Últimos 30 días
proyeccion - Estimación fin de mes
alertas - Alertas activas
decision - ¿Conviene gastar X?
editar - Editar un gasto por ID
borrar - Borrar un gasto por ID
presupuesto - Ver/cambiar presupuesto
exportar - CSV con todos los gastos
ayuda - Listar comandos
cancelar - Abortar conversación interactiva
```

### 2. API key Anthropic

<https://console.anthropic.com/> → crear key → cargar USD 5 (te dura meses).

### 3. Tu user_id

Mandale `/start` al bot — la respuesta te muestra tu `chat_id`. Ese número va
en `ALLOWED_USER_ID`.

### 4a. Local

```bash
cd expense_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# editá .env con tus 3 valores
python run.py
```

### 4b. Replit / VPS

Igual que antes, pero las env vars van en **Secrets** (Replit) o `systemd` /
docker env (VPS). Para el scheduler funcione 24/7 necesitás que el bot esté
corriendo siempre — Replit Free se duerme, mantenelo vivo con UptimeRobot.

### 5. Personalizá

Editá `config.py`:

- `PERFIL_FINANCIERO` → tus deudas, presupuesto, plan.
- `TARJETAS_RESUMEN_MENSUAL` → qué tarjetas pregunta el día 1 (por defecto las
  que pusiste en `deudas`).
- `TASA_CAMBIO` → blue/MEP/CCL del momento. Actualizá cuando se mueva.
- `HORA_PROMPT_MENSUAL` y `DIA_PROMPT_MENSUAL` → cuándo dispara el prompt
  mensual (default: día 1 a las 10:00 de Buenos Aires).

## Uso día a día

### Registrar gasto interactivamente

```
Vos: /nuevo
Bot: 💰 ¿Cuánto gastaste? Mandame solo el número.
Vos: 500
Bot: 💱 ¿En qué moneda?  [ARS] [USD] [USDT] [EUR]
Vos: (tap ARS)
Bot: 📝 ¿En qué fue?
Vos: supermercado
Bot: 🧠 Categorizando...
Bot: 📋 Confirmá el gasto:
     💰 500 ARS
     📝 supermercado
     📂 Alimentación (necesario) · variable
     [✅ Confirmar] [❌ Descartar]
Vos: (tap ✅)
Bot: ✅ Registrado: 500 ARS
     📂 Alimentación (necesario) · variable
     ...
```

### Registrar gasto rápido (una línea)

```
Vos: /gasto 500 supermercado
Vos: 300 USD viaje
Vos: 100 usdt criptos
Vos: gasté 1500 nafta
```

El parser detecta automáticamente la moneda. Si no hay moneda explícita, asume
la principal (ARS).

### Día 1 del mes (automático)

```
[Bot, a las 10:00 del día 1]
Bot: 📅 Empezó el mes — 2026-06
     ¿Te llegaron los resúmenes de tarjeta?
     Pendientes: Santander, Galicia
     Mandá /tarjetas para registrarlos.

Vos: /tarjetas
Bot: 💳 Santander — ¿cuánto te llegó este mes?
Vos: 1750000
Bot: 💱 ¿En qué moneda? [ARS] [USD] [USDT] [EUR]
Vos: (tap ARS)
Bot: ✅ Santander: 1,750,000 ARS (mes 2026-06)
Bot: 💳 Galicia — ¿cuánto te llegó este mes?
Vos: 2500000
Bot: ✅ Galicia: 2,500,000 ARS (mes 2026-06)
Bot: ✅ Listo, cargaste todos los resúmenes.
```

Si el bot estaba apagado el día 1, al arrancar te avisa que faltan resúmenes
(catch-up). También dispara recordatorios livianos los días 3, 5 y 8.

Si todavía no llegó algún resumen, escribí `skip` y te lo pregunta de nuevo
en el próximo recordatorio.

### Ver lo cargado

```
Vos: /resumenes
Bot: 💳 Resúmenes de tarjeta — 2026-06
     • Santander: 1,750,000 ARS
     • Galicia: 2,500,000 ARS
     Total: 4,250,000 ARS
```

Estos resúmenes también entran como contexto en `/mes` y `/decision`, así
Claude sabe cuánto debe pagar de tarjeta cuando te recomiende cosas.

### Multi-moneda en reportes

Los gastos se guardan en su moneda original. Los reportes muestran:

- Totales por moneda nativa (ej: `850k ARS · 50 USD · 100 USDT`).
- Total convertido a moneda principal (`≈ 1,109,000 ARS al tipo configurado`).
- Categorías y necesidades en moneda principal para comparaciones limpias.

La tasa se toma de `config.TASA_CAMBIO`. **Editá manualmente** cuando
cambien blue/MEP. El bot no consulta cotización online (decisión deliberada:
querés controlar qué tasa usás para tus números).

## Estructura de datos persistidos

```jsonc
// data/gastos.json
{
  "1234567": {
    "usuario_id": 1234567,
    "chat_id": 1234567,
    "creado": "2026-05-02T17:42:00",
    "gastos": [
      {
        "id": "a3f9c2b1",
        "fecha": "2026-05-02", "hora": "17:42:00",
        "monto": 500, "moneda": "ARS",
        "categoria": "Alimentación", "tipo": "variable",
        "necesidad": "necesario", "urgencia": "media",
        "descripcion": "supermercado", "nota": ""
      },
      {
        "id": "b4e8d1a2",
        "fecha": "2026-05-03", "hora": "12:15:00",
        "monto": 300, "moneda": "USD",
        "categoria": "Viajes", ...
      }
    ],
    "resumenes_tarjeta": [
      {
        "id": "c1d2e3f4",
        "fecha_registro": "2026-06-01",
        "mes": "2026-06",
        "tarjeta": "Santander",
        "monto": 1750000, "moneda": "ARS",
        "nota": ""
      }
    ],
    "presupuesto": {"mes_actual": "2026-05", "objetivo": 950000}
  }
}
```

Compatible con datos de la versión anterior: gastos sin campo `moneda` se
toman como ARS automáticamente al leer.

## Decisiones de diseño

**Conversación interactiva con botones**: la moneda es un set chico (4) y
elegirla es 1 tap, no 5 caracteres. La descripción y monto siguen siendo texto
libre porque son arbitrarios.

**Prompt mensual con catch-up**: Telegram bots no pueden iniciar conversación
si el usuario nunca habló con el bot. Por eso guardamos `chat_id` al primer
`/start` y lo usamos para el job mensual. Si el bot se apaga, al volver detecta
que el día ya pasó y avisa igual.

**Tasa de cambio manual**: cotizaciones online cambian todo el día y arruinan
la comparabilidad de reportes mes a mes. Vos elegís un valor fijo (ej: blue
del primer día del mes) y lo actualizás cuando querés.

**Storage JSON**: para 1 usuario y miles de gastos sigue andando bien. Si
crece, migrar a SQLite es cambiar solo `storage.py` (la API pública de
funciones es estable).

## Costo estimado

- ~100 gastos/mes × Haiku ≈ USD 0.02
- ~30 reportes + decisiones/mes × Sonnet ≈ USD 0.30
- Telegram + Replit Free + JobQueue: gratis
- **Total: ~USD 0.50/mes**

## Troubleshooting

**El job del día 1 no disparó**: requiere que el bot esté corriendo a las 10:00
de ese día. Si estaba apagado, `catch_up_startup` lo detecta al arrancar y te
avisa. Si nunca usaste el bot antes (no hay `chat_id` guardado), no puede
iniciar conversación; mandá un `/start` primero.

**"Falta TELEGRAM_TOKEN"**: env var no cargada. En local: `python run.py`. En
Replit: Secrets, no `.env`.

**El bot no responde**: chequeá logs. ¿Está activa `ALLOWED_USER_ID` con un
valor distinto al tuyo?

**JobQueue not available**: instalaste `python-telegram-bot` sin el extra.
Reinstalá con `pip install python-telegram-bot[job-queue]`.

**Categorización rara**: Haiku se equivoca con descripciones cortas o
ambiguas. Editá con `/editar <id>`.
