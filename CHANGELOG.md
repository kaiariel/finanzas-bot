# Cambios

Registro de cambios funcionales, del más reciente al más antiguo. El detalle de uso
de cada función está en el [README](README.md).

## 2026-10-06 · Confirmaciones con botones, tickets por total y bandeja única

### Bot de Telegram

- Cada movimiento registrado se confirma mostrando tipo, importe, categoría y
  concepto, con botones para cambiar el tipo, elegir otra categoría (que se
  aprende) o deshacer (restaurable). Antes solo decía "Enviado para registro" y
  un ingreso guardado como gasto pasaba inadvertido.
- Si la categoría no está clara, o el mensaje es un Bizum o transferencia, se
  guarda igual y se abre la lista de categorías. Antes el Bizum se descartaba y
  había que reescribirlo.
- `/deshacer` elimina el último movimiento propio, con botón Restaurar.
- Tickets: al recibir una foto el bot pide el total y el comercio (respuesta al
  aviso o `#12 21,40 Mercadona`). El movimiento queda con la fecha del ticket y
  cuenta en el sobre semanal el mismo día. Si el pie de foto trae el importe, se
  registra sin preguntar. El detalle por producto pasa a ser opcional desde el
  panel y, si se hace, sustituye al total.
- Parser: `ingresos`, `recibido`, `cobrado` y `pagaron` cuentan como ingreso;
  `cobraron`, `descontaron`, `pagado` y `cargo` como gasto. El primer verbo de
  la frase manda: `gasto 30 venta ropa` es un gasto.
- `/resumen` muestra el cierre estimado, el sobre semanal y lo pendiente de
  revisar. El mismo texto llega el domingo a las 19:00 (o el lunes si el equipo
  estaba apagado). Se desactiva con `WEEKLY_SUMMARY_ENABLED=0`.

### Panel

- Nueva sección **Para revisar**, con contador en el menú y en el Resumen:
  tickets sin registrar (registrar total o detallar productos), movimientos sin
  categoría clara (categoría con un clic, que se aprende), conceptos marcados
  como pagados o cobrados sin movimiento (registrar o vincular a un movimiento
  existente, uno a uno o por mes) y posibles duplicados.
- **Marcar como pagado o cobrado siempre crea el movimiento**, y volver a
  pendiente lo elimina; también desde el formulario del concepto. Antes dependía
  de la casilla "domiciliado", desactivada en todos los conceptos, y el cierre
  estimado perdía esos importes (en octubre, 455 € cobrados y 100 € pagados).
- Resumen: las finanzas van primero y la agenda `Hoy` se pliega (recuerda su
  estado y resume el día en una línea). La comparativa del mes en curso usa el
  mismo tramo del mes anterior, hasta el día de hoy. El aviso "Resultado
  negativo" solo aparece en meses cerrados.
- Diagnóstico sin el índice orientativo. Proyección y Próximos meses se limitan
  a 12 meses. Cuentas, presupuestos por categoría y objetivos solo se muestran
  si tienen datos.
- Al eliminar el único movimiento de un ticket, el ticket vuelve a la bandeja;
  restaurarlo lo saca. API nueva: `POST /api/receipts/<id>/total`,
  `POST /api/transactions/<id>/quick` y
  `POST /api/projections/<id>/<AAAA-MM>/register`.

### Datos y mantenimiento

- Revisión del 6 de octubre: el ingreso del 4/10 guardado como gasto pasa a
  ingreso; `Agua`, `Electricidad septiembre` y `Personal Tv y Telefonia` pasan a
  `Suministros`; `Guardar Alquiler` pasa a `Ahorro`; los siete conceptos de
  octubre marcados sin movimiento quedan registrados. Copia previa en
  `data/backups/finances-20261006-100638-980552.db`.
- `pytest` sin argumentos vuelve a funcionar (`pytest.ini` limita la búsqueda a
  `tests/`). 153 pruebas automatizadas.

## 2026-09-30 · Resumen diario de la agenda por Telegram

- Envío opcional a las 07:00 en la zona configurada, solo con eventos para el día y
  a usuarios autorizados. Activación mediante `CALENDAR_DAILY_SUMMARY_ENABLED`.
- Progreso persistente por fecha, destinatario y parte enviada; reintento de fallos
  breves, sin envío de resúmenes atrasados al iniciar después de las 07:05.
- Pruebas de días vacíos/omitidos, horario local, recurrencias, destinatarios,
  reinicios y continuación de respuestas largas.

## 2026-09-30 · Agenda familiar, accesibilidad e inicio de Windows

- Rediseño azul claro, tarjetas con bordes visibles y emojis acompañados de texto.
  Se conservan Resumen, Movimientos, Proyección, Ahorro, Archivos y Diagnóstico;
  Calendario es una sección adicional. Modo oscuro y navegación móvil conservados.
- `Hoy` y los accesos familiares aparecen antes de los filtros financieros y de
  `Así va el mes`. Las comidas muestran todos los platos, con texto de 18 px y
  opción de texto grande persistente. Los botones principales tienen 44 px de alto,
  foco visible y el calendario admite navegación con las flechas del teclado.
- Calendario mensual y agenda: eventos puntuales, repetición diaria, semanal con
  días elegibles, mensual y anual, hora y fecha final opcionales. Edición del día
  seleccionado o de toda la serie, completado, omisión y archivo de series.
  Los meses sin el día indicado se omiten; los borradores de cada ámbito se conservan.
- `/hoy` y el texto `hoy` consultan la misma agenda en Europe/Madrid, respetan los
  usuarios autorizados y dividen respuestas largas sin perder los detalles.
  La consulta incluye lo añadido en la app y no activa avisos automáticos.
- Nuevas tablas `calendar_events` y `calendar_occurrences`, con cambios auditados.
  La agenda no altera importes ni estados de las proyecciones financieras.
- Importador JSON revisado con claves de origen únicas, copia de SQLite verificada
  previa y conservación de correcciones. Los documentos originales se enlazan
  desde el panel y permanecen bajo `data/calendar/`, excluida de Git.
- `Iniciar Finanzas.vbs` abre sin consola. Los accesos `.cmd` delegan en él y la
  reapertura reutiliza la sesión existente, incluido el proceso hijo de Python en
  Windows. Un bloqueo coordina aperturas simultáneas sin duplicar bot ni panel.
- Inicio automático mediante `Finanzas.vbs` en la carpeta Inicio del usuario,
  sin consola ni navegador. Instalación repetible y desactivación; migración del
  antiguo acceso propio `.cmd` conservando scripts ajenos. La configuración del
  escritorio y de Inicio se aplica localmente y se puede reinstalar en otro equipo.
- Maqueta inicial en `prototypes/inicio-familiar/`, con datos ficticios y capturas;
  documentada como referencia histórica, independiente de la aplicación real.
- Validación del conjunto: 127 pruebas automatizadas; siete secciones comprobadas
  en 360, 390, 768 y 1280 px, texto normal/grande y modo oscuro. Verificados edición
  puntual, repeticiones semanales/mensuales, inicio/cierre y reaperturas simultáneas.

## 2026-09-25 · Presupuesto semanal para Hogar y Alimentación

- El sobre de `Hogar y Alimentación` admite un **presupuesto semanal** (columna
  `projection_templates.weekly_budget_cents`), de lunes a domingo y **sin arrastre**:
  lo que no se gasta una semana no se suma a la siguiente.
- Lo pendiente del mes pasa a ser lo que falta de la semana en curso más el semanal
  de las semanas restantes (a prorrata en semanas partidas). Antes era el importe
  mensual menos lo gastado, así que a final de mes seguía mostrando cientos de euros
  pendientes y el cierre estimado salía demasiado pesimista.
- Lo previsto del mes se calcula como semanal × días / 7. Campo nuevo en el formulario
  del concepto; vacío mantiene el cálculo mensual.

## 2026-09-25 · Cifra de cierre única, categorías que aprenden y conceptos domiciliados

### Panel

- **Una sola cifra principal.** `Resumen` y `Proyección` muestran el *cierre estimado
  del mes* con su cuenta a la vista: registrado hasta hoy + por cobrar − por pagar.
  Antes convivían cuatro cifras distintas (resultado registrado, cierre estimado,
  plan completo y "faltan X"), y la tarjeta más grande era un resultado en rojo. El
  plan completo del mes queda como dato secundario.
- **Iconos por categoría** en los movimientos y en el menú (Phosphor Icons, MIT),
  generados en `finance_bot/ui/icons.js`. Sustituyen a las siglas de dos letras.
- La etiqueta de cada sección sube a la cabecera; ya no se repite el título.
- `Diagnóstico` solo ofrece meses con datos y los tres siguientes (antes, 39 meses).
- En móvil el menú es una rejilla de 3×2 con las seis secciones visibles, y el periodo
  y el buscador ocupan cada uno su fila.

### Categorías

- Nueva categoría **`Sin clasificar`** como valor por defecto de los gastos sin
  palabras clave. Antes caían en `Ocio` y lo inflaban con envíos familiares, coworking
  o publicidad.
- **El bot aprende de las correcciones.** Al cambiar la categoría de un movimiento en
  el panel se guarda una regla (`category_rules`, por texto normalizado y tipo). El
  mismo concepto que llegue después por Telegram usa esa categoría. La migración de
  esquema v2 convierte en reglas las correcciones ya registradas en `audit_log`.

### Proyección

- **Conceptos domiciliados** (columna `auto_register`). Con la casilla *Se cobra o paga
  automáticamente*:
  - `Marcar como pagado` crea el movimiento del mes (marcado con
    `source_text = "Registrado automaticamente al marcar el concepto como pagado"`);
  - `Volver a pendiente` u `Omitir` lo eliminan;
  - si después llega el movimiento real y se vincula al concepto, sustituye al
    automático y no se cuenta dos veces.
  Esto corrige que los conceptos pagados sin movimiento desapareciesen del cierre
  estimado.

## 2026-09-25 · Arranque robusto, base protegida y panel depurado

### Bot de Telegram

- Los mensajes acumulados con la compu apagada se guardan con su **fecha de envío**,
  no con la de procesado (un ticket del día 31 ya no cae en el mes siguiente).
- Se ignoran las entregas repetidas y los mensajes editados, que antes duplicaban
  movimientos.
- Las descargas de tickets y audios se reintentan ante cortes de red.
- Al arrancar sin red el bot espera y reintenta, en lugar de cerrarse.
- Latido en `data/bot_heartbeat.json`: si el bot estuvo más de 24 h sin recoger
  mensajes (el límite de Telegram), avisa por Telegram del rango de fechas que hay
  que reenviar.
- `bot.log` ya no registra cada petición HTTP, que incluía el token en claro.

### Lanzador

- El supervisor relanza el bot y el panel si se caen, con espera creciente (5 s → 5 min).
- `Cerrar Finanzas` detiene primero el supervisor para que no los relance.
- Los logs rotan al superar 5 MB.
- `--install-autostart` / `--remove-autostart` para iniciar Finanzas con Windows.

### Almacenamiento

- La base vive fuera de OneDrive (`%USERPROFILE%\FinanzasLocal\finances.db` por
  defecto), porque OneDrive puede corromper los archivos `-wal`/`-shm` de SQLite. Las
  rutas relativas de `.env` se resuelven respecto a la raíz del proyecto.
- `finance_bot/storage.py`: comprobación de salud, copias verificadas (recuentos y
  SHA-256) y negativa a crear una base vacía por error. Las instalaciones nuevas se
  crean de forma explícita con `scripts/init_finances.py --new`.

### Panel

- Los movimientos se pueden **eliminar**, con `Deshacer`; la fila completa queda en
  `audit_log` y se restaura con `POST /api/transactions/<id>/restore`.
- `Diagnóstico` deja de marcar cada producto de un ticket como vínculo sospechoso del
  presupuesto `Hogar y Alimentación`, agrupa los avisos repetidos y `Revisar
  movimiento` abre el movimiento citado.
- Carga unas tres veces más rápida: las URL de adjuntos se calculan sin consultar el
  disco (antes, una consulta por línea de ticket en la unidad de Google Drive).
- Un ingreso nunca hereda una categoría de gasto del parser: pasa a `Trabajos extra`
  con un aviso para revisarlo.
