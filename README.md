# Finanzas personales por Telegram

Aplicacion local para registrar ingresos, gastos, tickets y notas de voz desde Telegram.
Guarda todo en SQLite, genera CSV y crea un reporte HTML interactivo. No usa APIs de
pago para analizar documentos: los archivos se guardan localmente y se pueden revisar
desde Codex o con OCR local.

## Que hace

- Recibe mensajes de Telegram con gastos e ingresos.
- Guarda fotos, PDFs y notas de voz como pendientes de revision.
- Registra movimientos en SQLite.
- Exporta movimientos a CSV.
- Genera un reporte HTML con filtros, graficas, tickets enlazados y proyeccion mensual.
- Mantiene trazabilidad entre movimientos y archivos originales.
- Permite varios usuarios autorizados.
- Puede sincronizar tickets con Google Drive para escritorio, sin usar la API de Google Drive.
- Puede transcribir notas de voz con Whisper local, sin APIs externas.
- Puede leer imagenes de tickets con Tesseract OCR si esta instalado.

## Flujo general

```text
Telegram
  -> run_bot.py
  -> data/finances.db
  -> data/movimientos.csv
  -> reports/finanzas.html
  -> carpeta local de tickets sincronizada por Google Drive
```

Los mensajes de texto claros se registran directamente como movimientos.

Las fotos, PDFs y voces ambiguas quedan en la bandeja de pendientes. No aparecen en la
lista de movimientos hasta que se revisan y se convierten en entradas contables.

## Instalacion

Requisitos recomendados:

- Windows con PowerShell.
- Python 3.11 o superior.
- Una cuenta de Telegram.
- Opcional: Google Drive para escritorio.
- Opcional: Tesseract OCR para leer imagenes automaticamente.

Crear entorno e instalar dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Dependencias opcionales para notas de voz:

```powershell
pip install -r requirements-voice.txt
```

## Configuracion

Edita `.env` con tus valores reales. No subas `.env` a GitHub.

Variables principales:

```env
TELEGRAM_BOT_TOKEN=pon_aqui_el_token_de_botfather
ALLOWED_TELEGRAM_USER_IDS=
TELEGRAM_USER_NAMES=

DATA_DIR=data
SQLITE_DB_PATH=data/finances.db
EXPORT_CSV_PATH=data/movimientos.csv
REPORT_HTML_PATH=reports/finanzas.html
TIMEZONE=Europe/Madrid

RECEIPTS_SYNC_DIR=data/receipts
VOICES_SYNC_DIR=data/voices

VOICE_TRANSCRIPTION_ENABLED=0
VOICE_TRANSCRIPTION_MODEL=base
VOICE_TRANSCRIPTION_DEVICE=cpu
VOICE_TRANSCRIPTION_COMPUTE_TYPE=int8

TESSERACT_CMD=
```

Para crear el bot:

1. Abre Telegram y habla con `@BotFather`.
2. Ejecuta `/newbot`.
3. Copia el token en `TELEGRAM_BOT_TOKEN`.
4. Arranca el bot.
5. Envia `/start`.
6. Copia tu ID de Telegram en `ALLOWED_TELEGRAM_USER_IDS`.

Para varios usuarios:

```env
ALLOWED_TELEGRAM_USER_IDS=11111111,22222222
TELEGRAM_USER_NAMES=11111111:Ariel,22222222:Dahiana
```

## Google Drive local

El proyecto no usa la API de Google Drive. Si quieres sincronizar tickets, instala
Google Drive para escritorio y apunta `RECEIPTS_SYNC_DIR` a una carpeta local sincronizada.

Ejemplos:

```env
RECEIPTS_SYNC_DIR=G:\Mi unidad\Finanzas - Tickets
RECEIPTS_SYNC_DIR=C:\Users\TuUsuario\Google Drive\Mi unidad\Finanzas - Tickets
```

El bot creara subcarpetas mensuales como:

```text
G:\Mi unidad\Finanzas - Tickets\2026-06 Junio
```

## Ejecutar el bot

```powershell
.\.venv\Scripts\Activate.ps1
python run_bot.py
```

El bot queda escuchando por polling. Para detenerlo, usa `Ctrl+C`.

## Comandos de Telegram

```text
/start       Muestra ayuda inicial y tu user id.
/ayuda       Lista comandos y ejemplos.
/estado      Estado global de las finanzas.
/resumen     Ingresos, gastos y balance del mes actual.
/exportar    Genera y envia el CSV de movimientos.
/reporte     Genera y envia el HTML interactivo.
/pendientes  Lista tickets y voces pendientes.
```

## Registrar movimientos por texto

Ejemplos:

```text
gasto 12,50 mercadona comida
ingreso 1200 nomina
+250 venta bici
-9,99 spotify
```

Tambien puedes mandar varias lineas:

```text
gasto 3,45 Lidl leche
gasto 4,20 Lidl frutas
ingreso 250 trabajo extra
```

Gastos pagados por adelantado para clientes:

```text
pago 50 Google Ads cliente marketing
```

Se registran como `Egreso` en la categoria `Ingresos clientes`, porque el cliente debe
devolver ese dinero. Cuando el cliente pague:

```text
cobro 50 cliente marketing
```

## Tickets, PDFs y notas de voz

Si envias una foto, PDF o documento, el bot guarda el archivo y crea un pendiente.
El caption se conserva como pista, pero el movimiento no se registra hasta la revision.

Ejemplo de caption util:

```text
Alquiler Junio y factura agua 47,47. Total 747,47.
```

Estados posibles de la bandeja:

```text
nuevo / pending   Recibido, aun sin analizar.
voice_pending     Audio pendiente de transcripcion o revision.
processed         Analizado y registrado.
dudoso            Necesita revision manual.
duplicado         Coincide con un movimiento existente.
missing           El archivo local ya no existe.
```

Ver pendientes desde terminal:

```powershell
python scripts/list_pending.py
```

Revisar automaticamente pendientes con reglas locales, PDF, OCR o voz:

```powershell
python scripts/review_pending.py
```

Notas importantes:

- `review_pending.py` modifica la base de datos si consigue registrar movimientos.
- Los PDFs se leen con `pypdf`.
- Las imagenes necesitan Tesseract OCR instalado.
- Si Tesseract no esta en el `PATH`, configura `TESSERACT_CMD` en `.env`.
- Si OCR no esta disponible, las imagenes quedan como `dudoso` para revision manual.

## Registrar tickets manualmente

Cuando revises un ticket manualmente, crea un JSON con las entradas y ejecuta:

```powershell
python scripts/register_manual_entries.py entradas.json
```

Tambien puedes pasar el JSON por stdin:

```powershell
Get-Content entradas.json | python scripts/register_manual_entries.py -
```

Ejemplo:

```json
{
  "receipt_id": 10,
  "source_text": "Mercadona 02/06/2026. Total 21,32 EUR.",
  "entries": [
    {
      "type": "Egreso",
      "amount": 605,
      "category": "Izhan",
      "description": "Pañales talla 5",
      "store": "Mercadona",
      "date": "2026-06-02"
    },
    {
      "type": "Egreso",
      "amount": "4,90",
      "category": "Alimentación",
      "description": "Atún claro oliva pack 6",
      "store": "Mercadona",
      "date": "2026-06-02"
    }
  ]
}
```

`amount` puede ser un numero en centimos, como `605`, o texto decimal, como `"6,05"`.

Al terminar, el script:

- Crea los movimientos.
- Enlaza los movimientos al ticket.
- Marca el ticket como `processed`.
- Regenera `reports/finanzas.html`.

## Reporte HTML

Generar reporte:

```powershell
python scripts/generate_report.py
```

Archivo generado:

```text
reports/finanzas.html
```

El reporte incluye:

- Resumen mensual.
- Balance.
- Gastos por categoria.
- Ranking de tiendas.
- Movimientos filtrables.
- Usuarios.
- Estado de tickets.
- Enlaces a archivos originales.
- Proyeccion mensual de ingresos y gastos.

## Panel local editable

Arrancar el panel:

```powershell
python scripts/serve_dashboard.py
```

Abrir:

```text
http://127.0.0.1:8765
```

Desde el panel puedes editar movimientos y proyecciones. Los cambios se escriben en
SQLite y regeneran el reporte HTML.

Advertencia: el panel local tiene APIs de escritura. Usalo solo en tu maquina o red de
confianza.

## Proyecciones

El reporte y el panel incluyen una pestaña de proyeccion para planificar meses futuros.
Puedes tener gastos fijos, cuotas, ingresos esperados, items pagados/cobrados y omitidos.

Cargar o reponer la plantilla inicial:

```powershell
python scripts/seed_projection_plan.py
```

Este script modifica la base de datos de proyecciones.

## Scripts utiles

```powershell
python scripts/list_pending.py
```

Lista tickets y voces pendientes. No modifica datos.

```powershell
python scripts/review_pending.py
```

Intenta procesar pendientes automaticamente. Modifica datos si registra movimientos.

```powershell
python scripts/register_manual_entries.py entradas.json
```

Registra movimientos revisados manualmente. Modifica datos.

```powershell
python scripts/generate_report.py
```

Regenera `reports/finanzas.html`.

```powershell
python scripts/serve_dashboard.py
```

Arranca el panel local editable.

```powershell
python scripts/backup_finances.py
```

Crea backup de SQLite, CSV y HTML en `data/backups`.

```powershell
python scripts/organize_receipts_by_month.py
```

Mueve tickets dentro de carpetas mensuales y actualiza rutas en la base.

```powershell
python scripts/link_transactions_to_receipts.py
```

Enlaza movimientos antiguos con recibos existentes si comparten ruta de archivo.

## Formato de datos

El CSV usa estas columnas:

```text
Mes | Fecha | Descripción | Categoría | Cantidad | Tipo | Tienda | Es fijo
```

Tipos validos:

```text
Ingreso
Egreso
```

Categorías actuales:

```text
Alimentación
Hogar
Suministros
Alquiler
Izhan
Salud & Cuidado
Ropa
Educación
Deudas
Ayuda familiar
Suscripciones
Transporte
Ocio
Ahorro
Ingresos laborales
Ingresos clientes
Trabajos extra
```

Categorías fijas por defecto:

```text
Alquiler
Deudas
Ayuda familiar
Suscripciones
```

## Probar el proyecto

Ejecutar tests:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Comprobar que los modulos compilan:

```powershell
python -m compileall finance_bot scripts run_bot.py tests
```

Comprobar integridad de SQLite desde PowerShell:

```powershell
@'
from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase

settings = Settings.from_env()
db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
with db._connect() as con:
    print(con.execute("PRAGMA quick_check").fetchone()[0])
'@ | python -
```

## Datos privados y GitHub

No subas datos financieros reales ni tokens.

El `.gitignore` debe excluir:

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
data/
reports/
*.db
*.sqlite
*.csv
```

El repositorio deberia contener:

```text
finance_bot/
scripts/
tests/
run_bot.py
requirements.txt
requirements-voice.txt
.env.example
.gitignore
README.md
```

No deberia contener:

```text
.env
data/
reports/
tickets
audios
CSV de movimientos reales
bases SQLite reales
```

## Trabajo con Git

Flujo basico:

```powershell
git status
git add README.md
git commit -m "Update project documentation"
git push
```

Crear una rama:

```powershell
git switch main
git pull
git switch -c mejora-reportes
```

Subir la rama:

```powershell
git add .
git commit -m "Improve report filters"
git push -u origin mejora-reportes
```

Luego abre un Pull Request en GitHub para revisar y unir los cambios a `main`.

Colaboradores:

```powershell
git clone https://github.com/TU_USUARIO/TU_REPO.git
cd TU_REPO
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Cada colaborador debe usar su propio `.env` y sus propios datos locales.

## Problemas comunes

Los tickets no aparecen en movimientos:

```powershell
python scripts/list_pending.py
```

Si aparecen como `pending`, revisalos con `review_pending.py` o registralos manualmente.

Las imagenes no se leen automaticamente:

- Instala Tesseract OCR.
- Agrega Tesseract al `PATH`, o configura `TESSERACT_CMD`.
- Vuelve a ejecutar `python scripts/review_pending.py`.

El CSV no tiene lo ultimo:

- Usa `/exportar` desde Telegram.
- O ejecuta `python scripts/backup_finances.py`, que tambien regenera el CSV.

El reporte no tiene lo ultimo:

```powershell
python scripts/generate_report.py
```

El bot no arranca:

- Revisa `TELEGRAM_BOT_TOKEN`.
- Revisa que `.env` exista.
- Ejecuta `pip install -r requirements.txt`.
- Prueba `python -m pytest -q`.
