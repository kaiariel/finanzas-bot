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
- Genera un reporte HTML con filtros, graficas, tickets enlazados, proyeccion mensual y panel de ahorro.
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

## Cambios recientes

- La revision de imagenes y audios ahora prefiere Codex por defecto: `review_pending.py`
  marca esos pendientes como `dudoso` para revision manual, y el OCR/transcripcion local
  queda como alternativa con `PREFER_CODEX_MEDIA_REVIEW=0`.
- El parser entiende importes hablados sencillos, como `dos euros`, `veintidos euros` o
  `treinta y cinco euros`, y separa mejor frases de audio con varios importes.
- Las categorias `Alimentacion`, `Hogar` e `Izhan` se consolidaron en
  `Hogar y Alimentación`; la migracion tambien corrige movimientos y proyecciones antiguas.
- Los movimientos pueden enlazarse automaticamente con proyecciones mensuales. Si el
  cruce es fiable, la proyeccion queda como completada y el movimiento guarda el
  `projection_template_id`.
- El reporte HTML incorpora filtros en la tabla de movimientos, lista de ingresos
  filtrados, gasto diario, tendencia de hasta 36 meses y avisos de IA cuando una categoria o
  proyeccion se asumio con dudas.
- El panel editable permite marcar proyecciones como `Pagar`, `Cobrar` o `Pendiente` con
  un clic, conservando importe y nota del mes.
- El registro manual de tickets valida todas las entradas antes de escribir, evita
  duplicar movimientos en un pendiente ya registrado salvo que se autorice, y mantiene
  el reporte actualizado.
- El panel `Ahorro` calcula cuanto dinero podrias tener al terminar un rango de meses,
  combinando saldo actual opcional, carteras seleccionables y ahorro previsto. Admite
  rangos futuros de hasta 36 meses.
- Las proyecciones admiten mes de inicio y mes final, y se pueden terminar desde un mes
  concreto conservando los meses anteriores.
- El HTML, CSS y JavaScript del panel se movieron a archivos propios en
  `finance_bot/ui/` en lugar de vivir como strings dentro de `report.py`, que bajo de
  unas 2800 a unas 440 lineas.
- El panel ganó navegacion lateral con modo oscuro, botones de ayuda contextual por
  seccion (`¿Qué es esto?`), revision de tickets sin salir del panel y un boton para
  deshacer la ultima edicion de un movimiento.
- Nuevos scripts de mantenimiento: `scripts/doctor.py` (diagnostico rapido de Python,
  token de Telegram, integridad de SQLite y disponibilidad de `git`),
  `scripts/import_csv.py` (importa un extracto bancario con vista previa y deteccion de
  duplicados) y `scripts/restore_finances.py` (restaura un backup, guardando antes una
  copia de seguridad de la base actual).
- SQLite ahora abre cada conexion en modo `WAL` con `busy_timeout`, para tolerar mejor
  el acceso concurrente del bot, el panel y el supervisor sobre el mismo archivo.

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
SQLITE_DB_PATH=%USERPROFILE%\FinanzasLocal\finances.db
EXPORT_CSV_PATH=data/movimientos.csv
REPORT_HTML_PATH=reports/finanzas.html
TIMEZONE=Europe/Madrid

RECEIPTS_SYNC_DIR=data/receipts
VOICES_SYNC_DIR=data/voices
PREFER_CODEX_MEDIA_REVIEW=1

VOICE_TRANSCRIPTION_ENABLED=0
VOICE_TRANSCRIPTION_MODEL=base
VOICE_TRANSCRIPTION_DEVICE=cpu
VOICE_TRANSCRIPTION_COMPUTE_TYPE=int8

TESSERACT_CMD=
```

> **La base de datos no debe vivir dentro de OneDrive.** OneDrive sincroniza
> tambien los archivos auxiliares de SQLite (`-wal` y `-shm`) y puede corromper
> `finances.db`. Apunta `SQLITE_DB_PATH` a una carpeta local común, por ejemplo
> `%USERPROFILE%\FinanzasLocal\finances.db`. Las copias de
> seguridad (`scripts/backup_finances.py`) siguen guardandose en `data/backups`,
> dentro de OneDrive, que es justo lo que interesa respaldar.

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

### Inicio recomendado en Windows

Haz doble clic en `Iniciar Finanzas.cmd`. El lanzador inicia el bot y el panel
en segundo plano, abre el navegador, evita instancias duplicadas y guarda los
errores en `data/logs`.

El lanzador **espera a que el arranque se confirme** antes de dar por buena la
puesta en marcha. Si algo falla, la ventana no se cierra: muestra el motivo, el
codigo de salida y la ruta del registro. Ademas, si una sesion anterior quedo a
medias (por ejemplo, cerrada desde el Administrador de tareas), cierra los
procesos huerfanos que ocupaban el puerto y continua.

| Codigo | Significado |
| --- | --- |
| 0 | Finanzas se inicio correctamente. |
| 2 | Ya estaba iniciada o el puerto `8765` esta ocupado por otro programa. |
| 3 | El panel no llego a responder; revisa `data/logs/dashboard.log`. |
| 4 | El arranque no se confirmo en 60 segundos. |
| 5 | Error inesperado; el registro incluye el detalle completo. |

Para cerrar ambos procesos, usa `Cerrar Finanzas.cmd`, que detiene primero el
supervisor (para que no los relance) y despues el bot y el panel, y avisa si el
puerto sigue ocupado.

### Arranque automatico con Windows

Telegram solo guarda **24 horas** los mensajes que el bot no ha recogido. Si la
compu esta apagada mas tiempo, los tickets enviados antes de ese margen se
pierden y no hay forma de recuperarlos desde el bot. Para acortar ese hueco,
activa el inicio automatico al iniciar sesion (sin consola y sin abrir el
navegador):

```powershell
.venv-working\Scripts\python.exe scripts\start_finance_app.py --install-autostart
```

Se desactiva con `--remove-autostart`.

Al arrancar, el bot:

- espera a que haya red en lugar de cerrarse si Windows aun no conecto;
- procesa los mensajes acumulados con la **fecha en que se enviaron** (no la de
  procesado), asi que un ticket del 31 no cae en el mes siguiente;
- ignora mensajes que ya registro (Telegram los reentrega si el bot se cerro de
  golpe) y los mensajes editados;
- si detecta que estuvo apagado mas de 24 h (`data/bot_heartbeat.json`), avisa
  por Telegram del rango de fechas cuyos mensajes hay que reenviar.

No es necesario activar manualmente el entorno virtual.

El lanzador ejecuta `scripts/start_finance_app.py --detached`, que deja un
supervisor residente y coordina dos procesos independientes:

- `run_bot.py`: recibe mensajes y tickets desde Telegram.
- `scripts/serve_dashboard.py`: sirve el panel editable en `http://127.0.0.1:8765`.

Si uno de los procesos se cae, el supervisor mantiene el otro activo y lo
relanza con una espera creciente (5 s, 10 s, 20 s... hasta 5 min). Los logs se
rotan al superar 5 MB (se conserva el anterior como `.log.1`). Los bloqueos `data/finance_app.lock` y
`data/telegram_bot.lock` evitan iniciar dos supervisores o dos bots simultaneamente.

Archivos de diagnostico:

```text
data/logs/launcher.log
data/logs/bot.log
data/logs/dashboard.log
data/runtime_status.json
data/launch_result.json
```

`data/logs/launcher.log` recoge todo lo que imprime el supervisor en segundo
plano, incluidos los errores de arranque que antes se perdian.
`data/runtime_status.json` es temporal y permite al panel mostrar el estado actual;
el supervisor lo reescribe cada dos segundos y tolera que OneDrive o el propio
panel lo tengan abierto en ese momento.
Todos estos archivos quedan fuera de Git porque la carpeta `data/` esta ignorada.

### Inicio manual

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

El parser tambien tolera importes hablados sencillos, por ejemplo `spotify veintidos euros`
o `gasto treinta y cinco euros mercadona`.

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

Revisar automaticamente pendientes con reglas locales. Por defecto, las imagenes y audios se derivan a revision manual por Codex:

```powershell
python scripts/review_pending.py
```

Notas importantes:

- `review_pending.py` modifica la base de datos si consigue registrar movimientos.
- Los PDFs se leen con `pypdf`.
- Por defecto, las imagenes y audios se marcan como `dudoso` para revision por Codex en vez de usar OCR/transcripcion local.
- Si quieres volver al flujo anterior, configura `PREFER_CODEX_MEDIA_REVIEW=0` en `.env`.
- Con `PREFER_CODEX_MEDIA_REVIEW=0`, las imagenes necesitan Tesseract OCR instalado.
- Si Tesseract no esta en el `PATH`, configura `TESSERACT_CMD` en `.env`.

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
      "category": "Hogar y Alimentación",
      "description": "Pañales talla 5",
      "store": "Mercadona",
      "date": "2026-06-02"
    },
    {
      "type": "Egreso",
      "amount": "4,90",
      "category": "Hogar y Alimentación",
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
- Lista de ingresos filtrados.
- Movimientos filtrables.
- Usuarios.
- Estado de tickets.
- Enlaces a archivos originales.
- Proyeccion mensual de ingresos y gastos.
- Grafico diario de gasto para el mes filtrado.
- Grafico de tendencia de hasta 36 meses con balance proyectado y balance real.
- Pestaña `Diagnóstico` con diagnostico del mes, meses futuros en riesgo y recomendaciones basadas en movimientos, tickets y proyecciones.

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

El panel local tambien permite crear movimientos manuales desde `Movimientos`. Cada alta
o cambio relevante queda registrado en `audit_log` para conservar un historial tecnico
de la operacion. Al editar un movimiento, el boton `Deshacer último cambio` revierte la
ultima edicion registrada en `audit_log` para ese movimiento concreto.

La navegacion queda en una barra lateral con las secciones `Resumen`, `Movimientos`,
`Proyección`, `Ahorro`, `Archivos` y `Diagnóstico`. El boton `◐ Modo oscuro` de la
barra lateral cambia el tema visual del panel; la preferencia se guarda en el
navegador. Cada seccion tiene un boton `¿Qué es esto?` con una explicacion breve de
para que sirve ese panel.

Desde `Archivos`, un ticket pendiente se puede revisar sin salir del panel: el boton
`Revisar y confirmar` abre un formulario donde se corrigen las lineas detectadas y,
al confirmar, se convierten en movimientos enlazados al ticket. Es el equivalente en
la interfaz a `scripts/register_manual_entries.py`.

La pestaña `Ahorro` permite elegir un mes inicial y uno final, incluidos meses futuros.
En el mes actual usa solo cobros y pagos pendientes; en los meses posteriores usa las
proyecciones completas. El resumen separa el saldo actual incluido, las carteras
incluidas, el ahorro nuevo del periodo y el dinero estimado al terminar.

El saldo actual es un importe manual corregible que no crea movimientos. Las carteras
pueden crearse, editarse, incluirse o excluirse del total y archivarse. Ejemplos:
`Emergencias`, `Viaje` y `Efectivo reservado`. El saldo actual representa dinero fuera
de las carteras para evitar contar dos veces la misma cantidad.

El panel permite guardar una reserva mensual para imprevistos, crear objetivos con
cantidad, fecha y cartera opcional, y probar gastos o ingresos puntuales o mensuales
con un simulador que no modifica los datos reales. El gráfico y la tabla muestran cada
mes con ingresos, gastos, reserva, resultado y acumulado. Los accesos rápidos permiten
ver 3, 6 o 12 meses; el rango manual admite hasta 36 meses.

Las cuentas bancarias, el efectivo y las transferencias se conservan en el apartado
plegable `Mis cuentas y transferencias`, pero no se suman automáticamente al ahorro.
Las transferencias internas se guardan separadas y no cuentan como ingresos o gastos.
Los presupuestos por categoría también se mantienen en un apartado plegable.

Cuando el panel se inicia mediante `Iniciar Finanzas.cmd`, muestra el estado del bot,
el servidor local, la ultima actividad registrada y la cantidad de tickets pendientes.
El estado se actualiza cada cinco segundos y tambien puede refrescarse con el boton
`Actualizar estado`.

Estados posibles del bot:

- `Bot conectado`: el supervisor confirma que el proceso sigue activo.
- `Bot detenido`: el proceso termino; revisa `data/logs/bot.log`.
- `Bot no supervisado`: el panel se inicio manualmente y no puede confirmar el bot.

En la pestaña `Proyeccion` cada item pendiente tiene un boton rapido `✓ Pagar` o
`✓ Cobrar` que cambia el estado con un clic, sin abrir el formulario de edicion.
Los items ya completados muestran `↩ Pendiente` para deshacer. El cambio se aplica
al instante sin recargar la pagina. La pestaña tambien incluye un grafico de
tendencia de hasta 36 meses con ingresos y gastos proyectados, balance proyectado y
balance real registrado.

Advertencia: el panel local tiene APIs de escritura. Usalo solo en tu maquina o red de
confianza.

## Proyecciones

El reporte y el panel incluyen una pestaña de proyeccion para planificar meses futuros.
Puedes tener gastos fijos, cuotas, ingresos esperados, items pagados/cobrados y omitidos.
Cada concepto puede tener un mes de inicio y un mes final. Por ejemplo, `Marketing
Appsol` puede estar activo de marzo a septiembre. Al editarlo puedes ampliar o reducir
el rango, o usar `Finalizar desde este mes` para omitir los meses posteriores y
conservar el historial anterior.
Cuando un movimiento nuevo coincide de forma clara con una proyeccion activa del mes,
el sistema la marca automaticamente como completada. Esto funciona tanto para ingresos
como para gastos recurrentes, y evita tener que cerrar manualmente cada pago/cobro.

La proyeccion `Hogar y Alimentación` actua como presupuesto variable: el reporte calcula
cuanto se ha gastado realmente en esa categoria durante el mes y cuanto queda disponible
respecto al importe proyectado.

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

Intenta procesar pendientes automaticamente. Los PDFs aplican reglas locales; imagenes y audios se reservan para revision por Codex salvo que desactives `PREFER_CODEX_MEDIA_REVIEW`.

```powershell
python scripts/register_manual_entries.py entradas.json
```

Registra movimientos revisados manualmente. Modifica datos.
Primero valida todas las entradas, por lo que un JSON parcialmente invalido no deja
movimientos a medias. Si el `receipt_id` ya tiene movimientos enlazados, el script se
detiene para evitar duplicados; puedes permitirlo con
`"allow_existing_receipt_entries": true`.

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

Crea una copia consistente de SQLite y un manifiesto con recuentos y SHA-256 en
`data/backups`. El supervisor comprueba cada hora si hace falta una nueva y
conserva como máximo una copia verificada al día.

Tambien copia las carpetas locales de tickets y voces con la misma marca temporal para
que la trazabilidad de los adjuntos pueda recuperarse junto con la base de datos.

Para restaurar un backup primero comprueba que es valido y despues confirma la escritura:

```powershell
python scripts/restore_finances.py data/backups/finances-AAAAMMDD-HHMMSS.db
python scripts/restore_finances.py data/backups/finances-AAAAMMDD-HHMMSS.db --confirm
```

Antes de sobrescribir nada, `--confirm` guarda la base actual en
`data/backups/pre-restore-<fecha>.db`, y si la carpeta de tickets o voces ya existe la
mueve a `data/backups/receipts-antes-de-restaurar-<fecha>` (o `voices-...`) en vez de
mezclarla con la del backup. Cierra el bot y el panel antes de restaurar.

Para importar un extracto CSV, la primera orden solo genera una vista previa y marca
posibles duplicados. La segunda registra las líneas cuando no quedan coincidencias:

```powershell
python scripts/import_csv.py extracto.csv
python scripts/import_csv.py extracto.csv --confirm
```

```powershell
python scripts/organize_receipts_by_month.py
```

Mueve tickets dentro de carpetas mensuales y actualiza rutas en la base.
Si la carpeta de tickets no es accesible, informa el problema y sale sin modificar datos.

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
Hogar y Alimentación
Suministros
Alquiler
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

Si el Python integrado de Windows no esta disponible, usa el entorno gestionado por
`uv`:

```powershell
uv venv --python 3.12 .venv-working
uv pip install --python .venv-working\Scripts\python.exe -r requirements.txt
$env:TEMP = "$PWD\.pytest-tmp"
$env:TMP = $env:TEMP
.venv-working\Scripts\python.exe -m pytest -q
```

El diagnostico rapido comprueba la version de Python, que `TELEGRAM_BOT_TOKEN`
este configurado, la integridad de SQLite (`PRAGMA quick_check`) y que `git`
este disponible. Imprime un JSON con cada chequeo y termina con codigo 1 si
alguno falla:

```powershell
.venv-working\Scripts\python.exe scripts/doctor.py
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
.venv-working/
__pycache__/
.pytest_cache/
.pytest-tmp/
.claude/settings.local.json
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
Iniciar Finanzas.cmd
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

El remoto habitual de este proyecto es `origin`. Antes de subir, confirma su URL:

```powershell
git remote -v
```

Flujo recomendado para preparar y subir cambios mediante una rama:

```powershell
git status --short
git diff --check
.venv\Scripts\python.exe -B -m pytest -q
git switch -c mejora-inicio-finanzas
git add README.md .gitignore run_bot.py "Iniciar Finanzas.cmd" finance_bot scripts tests
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Add one-click finance app launcher"
git push -u origin mejora-inicio-finanzas
```

Luego abre un Pull Request en GitHub desde `mejora-inicio-finanzas` hacia `main`.
Revisa el Pull Request y, cuando todo sea correcto, fusiona la rama.

Si trabajas solo y prefieres subir directamente a `main`, usa:

```powershell
git switch main
git add README.md .gitignore run_bot.py "Iniciar Finanzas.cmd" finance_bot scripts tests
git commit -m "Add one-click finance app launcher"
git push origin main
```

No uses `git add .` sin revisar antes `git status --short`. Nunca deben subirse `.env`,
`data/`, `reports/`, bases SQLite, tickets reales ni configuraciones locales.

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

Las imagenes no se leen automaticamente si prefieres Codex:

- Deja `PREFER_CODEX_MEDIA_REVIEW=1` o sin definir.
- Ejecuta `python scripts/review_pending.py` para marcarlas como revision manual.

Si quieres volver a OCR local:

- Configura `PREFER_CODEX_MEDIA_REVIEW=0`.
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

El analisis no parece completo:

- Revisa que no queden tickets pendientes con `python scripts/list_pending.py`.
- Regenera el reporte despues de registrar tickets o editar proyecciones.
- Completa la pestaña `Proyeccion`; el panel `Diagnóstico` depende de esos importes para anticipar meses futuros.

El bot no arranca:

- Revisa `TELEGRAM_BOT_TOKEN`.
- Revisa que `.env` exista.
- Ejecuta `pip install -r requirements.txt`.
- Prueba `python -m pytest -q`.
- Revisa `data/logs/bot.log`.

El lanzador indica que el puerto `8765` esta ocupado:

- Si el panel quedo huerfano de una sesion anterior, el lanzador lo cierra solo:
  vuelve a hacer doble clic en `Iniciar Finanzas.cmd`.
- Si el aviso persiste, ejecuta `Cerrar Finanzas.cmd` y reintenta.
- Si sigue ocupado, el puerto lo esta usando otro programa ajeno a Finanzas.
- No finalices procesos al azar: `Cerrar Finanzas.cmd` solo detiene los que
  figuran en `data/runtime_status.json`.

El lanzador indica que el bot ya esta iniciado:

- Ya existe otra ejecucion de `run_bot.py`.
- Cierra la terminal anterior con `Ctrl+C`.
- Vuelve a iniciar mediante `Iniciar Finanzas.cmd` para que bot y panel queden supervisados.

El navegador no se abre automaticamente:

- Comprueba que la terminal muestre `Finanzas iniciadas`.
- Abre manualmente `http://127.0.0.1:8765`.
- Si el panel no responde, revisa `data/logs/dashboard.log`.
