# Automatizacion de finanzas personales sin APIs de pago

Este proyecto crea un bot de Telegram para registrar gastos, ingresos y tickets sin usar la API de OpenAI ni la API de Google Drive.

El flujo queda asi:

- Telegram recibe texto, fotos, documentos y notas de voz.
- El bot registra texto y notas de voz claras directamente en SQLite y CSV.
- Las fotos y PDFs siempre se guardan como archivos pendientes para analizarlos primero en Codex.
- Google Drive para escritorio sincroniza esa carpeta local con tu Drive.
- Cuando abras Codex, puedes analizar los tickets pendientes desde la carpeta local.
- Opcionalmente, las notas de voz se transcriben en local con Whisper, sin APIs de pago.
- El revisor local detecta duplicados, marca archivos dudosos o perdidos, y mantiene trazabilidad entre cada movimiento y su PDF, imagen o audio.
- El reporte HTML muestra alertas, ranking de tiendas, cobertura de tickets, gastos fijos/variables y enlaces a los archivos originales.

## 1. Conectar Google Drive local

Instala Google Drive para escritorio e inicia sesion con tu cuenta.

Luego crea en Drive una carpeta, por ejemplo:

```text
Mi unidad/Finanzas - Tickets
```

En Windows, busca esa carpeta desde el Explorador de archivos. La ruta suele ser parecida a una de estas:

```text
G:\Mi unidad\Finanzas - Tickets
C:\Users\TuUsuario\Google Drive\Mi unidad\Finanzas - Tickets
```

Copia esa ruta en `.env`:

```env
RECEIPTS_SYNC_DIR=G:\Mi unidad\Finanzas - Tickets
```

Google Drive para escritorio se encarga de sincronizar los archivos entre tu PC y Drive. El bot solo escribe archivos en esa carpeta.
Los tickets se guardan dentro de carpetas mensuales, por ejemplo:

```text
G:\Mi unidad\Finanzas - Tickets\2026-06 Junio
```

## 2. Preparar entorno

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`requirements.txt` incluye lectura local de PDFs y OCR opcional para imagenes. Para OCR de fotos de tickets, instala tambien el ejecutable de Tesseract OCR en Windows; si no esta instalado, las imagenes quedaran como `dudoso` para revisar en Codex.

## 3. Conectar Telegram

1. Abre Telegram y habla con `@BotFather`.
2. Ejecuta `/newbot`.
3. Copia el token en `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
```

Arranca una vez el bot, envia `/start` y copia tu user id en:

```env
ALLOWED_TELEGRAM_USER_IDS=123456789
```

Para permitir varios usuarios, separa sus IDs con comas:

```env
ALLOWED_TELEGRAM_USER_IDS=123456789,987654321,555444333
```

Cada usuario debe abrir el bot y enviar `/start` para ver su ID. Puedes asignar nombres visibles en reportes con:

```env
TELEGRAM_USER_NAMES=123456789:Ariel,987654321:Maria,555444333:Juan
```

El bot guarda quien envio cada gasto o ticket para poder filtrar el reporte por usuario.

## 4. Ejecutar el bot

```powershell
.\.venv\Scripts\Activate.ps1
python run_bot.py
```

## 5. Activar audios automáticos sin API

Para que las notas de voz se procesen automaticamente, instala el paquete opcional:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-voice.txt
```

Luego activa esto en `.env`:

```env
VOICE_TRANSCRIPTION_ENABLED=1
VOICE_TRANSCRIPTION_MODEL=base
VOICE_TRANSCRIPTION_DEVICE=cpu
VOICE_TRANSCRIPTION_COMPUTE_TYPE=int8
```

Reinicia el bot:

```powershell
python run_bot.py
```

La primera nota de voz puede tardar mas porque descarga el modelo local. Despues queda en cache en tu equipo. Si el audio no incluye un importe claro, se guarda como pendiente para revisar en Codex.

## Uso en Telegram

Comandos:

- `/start`: ayuda rapida y tu user id.
- `/estado`: estado global de las finanzas.
- `/resumen`: ingresos, gastos y balance del mes actual.
- `/exportar`: envia un CSV con todos los movimientos.
- `/reporte`: genera y envia el HTML interactivo.
- `/pendientes`: lista tickets y voces pendientes.

Texto libre:

```text
gasto 12,50 mercadona comida
ingreso 1200 nomina
+250 venta bici
-9,99 spotify
```

Gastos pagados por adelantado para clientes:

```text
pago 50 Google Ads cliente marketing
```

Se registran como `Egreso` en categoria `Ingresos clientes`, porque luego el cliente debe devolver ese importe. Cuando el cliente lo pague, registra el cobro como ingreso:

```text
cobro 50 cliente marketing
```

Tickets:

- Si envias una foto o PDF, se guarda en `RECEIPTS_SYNC_DIR` y queda pendiente aunque tenga caption.
- El caption se conserva como pista, pero Codex debe analizar el documento antes de registrar movimientos.
- Esto permite dividir extractos o facturas mixtas, por ejemplo 700,00 de alquiler y 47,47 de agua dentro del mismo PDF.

Ejemplo de caption:

```text
Alquiler Junio y factura agua 47,47. Total 747,47.
```

Tambien puedes enviar varias lineas en un solo mensaje:

```text
gasto 3,45 Lidl leche
gasto 4,20 Lidl frutas
gasto 3,45 Lidl leche
```

Si se repite una misma descripcion e importe dentro del mismo mensaje, el bot marca la segunda fila con `(revisar duplicado)`.

Al registrar movimientos, el bot responde solo con una confirmacion corta como:

```text
Enviado para registro.
```

Cada usuario autorizado puede consultar el estado global con `/estado`, descargar el CSV con `/exportar` o pedir el HTML con `/reporte`.

## Analisis en Codex

Cuando quieras analizar tickets pendientes, abre Codex en esta carpeta y pide algo como:

```text
Analiza los tickets pendientes de mi carpeta sincronizada y convierte cada item al formato financiero.
```

Para ver pendientes desde terminal:

```powershell
python scripts/list_pending.py
```

Para revisar automaticamente los pendientes que tengan informacion suficiente:

```powershell
python scripts/review_pending.py
```

Este revisor lee PDFs locales, transcribe notas de voz si tienes Whisper local activado, registra lo claro y deja pendiente lo que necesite decision manual.
En tickets de supermercado intenta crear una fila por cada producto detectado. Si el total del ticket no coincide con la suma de productos, añade una fila `Diferencia / redondeo` en categoria `Hogar`.

Estados de la bandeja:

```text
nuevo / pending      recibido, aun por analizar
voice_pending        audio pendiente de transcripcion local
processed            analizado y registrado
dudoso               necesita revision manual
duplicado            coincide con un movimiento ya registrado
missing              el archivo ya no existe en la ruta guardada
```

Cuando Codex ya haya analizado un pendiente, puede registrar una o varias filas con:

```powershell
python scripts/register_manual_entries.py entradas.json
```

Ejemplo de `entradas.json` para un PDF mixto:

```json
{
  "receipt_id": 3,
  "entries": [
    {
      "Fecha": "01/06/2026",
      "Descripción": "Alquiler Junio",
      "Categoría": "Alquiler",
      "Cantidad": "700,00 €",
      "Tipo": "Egreso",
      "Tienda": "",
      "Es fijo": "Sí"
    },
    {
      "Fecha": "01/06/2026",
      "Descripción": "Factura agua",
      "Categoría": "Suministros",
      "Cantidad": "47,47 €",
      "Tipo": "Egreso",
      "Tienda": "",
      "Es fijo": "No"
    }
  ]
}
```

Para generar el reporte HTML desde terminal:

```powershell
python scripts/generate_report.py
```

Para editar movimientos desde la lista, abre el panel local editable:

```powershell
python scripts/serve_dashboard.py
```

Luego entra en:

```text
http://127.0.0.1:8765
```

En `Movimientos`, pulsa `Editar`, corrige fecha, descripcion, categoria, cantidad, tipo, tienda o si es fijo, y guarda. El cambio se escribe en SQLite y tambien regenera `reports/finanzas.html`.
El archivo `file:///.../reports/finanzas.html` queda como vista de solo lectura.

El mismo panel tiene una pestana `Proyeccion` para planificar gastos, ingresos y balance de los meses siguientes sin mezclarlo con el pantallazo inicial. Puedes editar cada concepto, cambiar el importe solo para ese mes o actualizarlo como base para proximos meses, marcarlo como pendiente, pagado/cobrado u omitido, anadir items nuevos y borrar items del mes proyectado.

Las cuotas se calculan por mes. Si una cuota llega a su final, deja de aparecer en los meses posteriores.

Para cargar o reponer la plantilla inicial de gastos e ingresos proyectados:

```powershell
python scripts/seed_projection_plan.py
```

Para reorganizar tickets antiguos en carpetas mensuales y actualizar sus rutas en la base:

```powershell
python scripts/organize_receipts_by_month.py
```

Para enlazar movimientos antiguos con sus archivos fuente:

```powershell
python scripts/link_transactions_to_receipts.py
```

Para crear un backup local de SQLite, CSV y HTML:

```powershell
python scripts/backup_finances.py
```

El archivo se crea en:

```text
reports/finanzas.html
```

El reporte incluye filtros por mes, categoria, tipo, usuario, fijo/no fijo y con/sin ticket. Tambien muestra alertas, salud del sistema, ranking de tiendas, evolucion mensual, gasto por categoria, tabla de movimientos, enlaces a PDFs/imagenes/audios y bandeja completa de archivos.

Codex leera los archivos locales de la carpeta sincronizada. No hace falta meter claves de OpenAI en `.env`.

## Formato financiero

El CSV y las respuestas de registro usan siempre estas columnas, en este orden:

```text
Mes | Fecha | Descripción | Categoría | Cantidad | Tipo | Tienda | Es fijo
```

Categorias validas:

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

Tipos validos:

```text
Ingreso
Egreso
```

`Es fijo` se marca como `Sí` para alquiler, deudas, ayuda familiar mensual y suscripciones recurrentes. El resto se marca como `No`.
