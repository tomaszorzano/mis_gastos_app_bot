# 🤖 Expense Tracker Bot

Bot de Telegram para trackear gastos personales con análisis inteligente usando IA.

## ✨ Features

### Registro de Gastos
- **Interactivo:** `/nuevo` (paso a paso con categorización automática)
- **Rápido:** `/gasto 500 super` o solo `2k uber`
- **Multi-moneda:** ARS, USD, USDT, EUR
- **Forma de pago:** Crédito, débito, efectivo, otro

### Reportes Inteligentes (con IA)
- `/hoy` `/semana` `/mes` — análisis contextual
- `/decision ¿gasto X en Y?` — veredicto basado en tus objetivos
- `/graficos` — visuales interactivos (HTML)
- `/tendencia` `/proyeccion` `/categoria`

### Tarjetas de Crédito
- `/tarjetas` — cargar resúmenes mensuales
- `/conciliar` — comparar gastos vs resumen
- `/pagos` — split por forma de pago

### Perfil Financiero (v2)
- `/setup` — configura ingresos, objetivos, deudas (11 preguntas)
- `/perfil` — ver tu configuración
- `/editar_objetivo` — cambiar algo puntual
- **IA usa tus objetivos** para mejores recomendaciones

### Recordatorios Automáticos
- **Nocturno:** hora personalizada (configurable en `/setup`)
- **Mensual día 1:** pide resúmenes de tarjetas
- **Seguimiento:** días 3, 5, 8 si faltan resúmenes

### Admin (Multi-usuario)
- Sistema de aprobación (`/admin`)
- Perfiles aislados y cifrados por usuario
- Sin acceso cruzado a datos

## 🚀 Setup

### 1. Crear Bot en Telegram
```
@BotFather → /newbot → copiar token
@userinfobot → /start → copiar user_id
```

### 2. API Key de IA (Gemini gratis)
```
https://aistudio.google.com/app/apikey
```

### 3. Generar Master Key
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
⚠️ Guardar en password manager

### 4. Configurar Variables de Entorno

**Render/Railway:** agregar en dashboard

**Local:** crear `.env`:
```bash
TELEGRAM_TOKEN=123456789:ABCDEF...
AI_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
ALLOWED_USER_IDS=6568261984
MASTER_KEY=xZ8mK2Pq9LvN...=
MODEL_SMART=gemini-2.5-flash
```

### 5. Instalar y Correr

```bash
pip install -r requirements.txt
python run.py
```

## 📊 Gráficos

`/graficos` genera HTML con:
- 💰 Números clave (gastado, ahorro, comparación)
- 📊 Torta por categorías (mes actual)
- 📈 Barras totales últimos 6 meses
- 🔍 Categorías mes a mes (apilado)
- 💳 Tabla evolución deudas

Abrís el archivo en navegador (mobile/desktop).

## 🔐 Seguridad

- Cifrado AES (Fernet) para datos en reposo
- Aislamiento por usuario (multi-tenant)
- Admin NO puede ver datos de usuarios
- Master key requerida (sin recovery si se pierde)

## 📦 Stack

- **Bot:** python-telegram-bot 21.x
- **IA:** Google Gemini 2.5 Flash (free tier: 250 req/día)
- **Storage:** JSON cifrado local
- **Scheduler:** APScheduler
- **Gráficos:** Chart.js

## 🎯 Comandos

**Básicos:**
`/nuevo` `/gasto` `/hoy` `/mes` `/decision` `/graficos`

**Tarjetas:**
`/tarjetas` `/resumenes` `/pagos` `/conciliar`

**Perfil:**
`/setup` `/perfil` `/editar_objetivo` `/agregar_deuda`

**Admin:**
`/admin` `/aprobar` `/rechazar`

**Utils:**
`/alertas` `/editar` `/borrar` `/exportar` `/reset`

Ver todos: `/ayuda`

## 🐛 Troubleshooting

**Bot no responde:**
```bash
# Verificar que esté corriendo
ps aux | grep python
```

**Error 409 Conflict:**
```bash
pkill -9 -f "python run.py"
python run.py
```

**Mensajes cortados:** Ya arreglado en v2 (split automático)

**Quota exceeded Gemini:** Usar `MODEL_SMART=gemini-2.5-flash`

## 📈 Roadmap

- **v1:** ✅ Registro + reportes + scheduler
- **v2:** ✅ Perfil por usuario + admin + gráficos
- **v3:** Auto-registro + MongoDB + tiers de pago
- **v4:** Dashboard web + app nativa

## 📄 Licencia

Uso personal. Para comercial contactar al autor.

---

**Desarrollador:** @tomaszorzano  
**Repo:** https://github.com/tomaszorzano/mis_gastos_app_bot