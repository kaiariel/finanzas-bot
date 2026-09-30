# Propuesta visual de Mis finanzas

Abrir `index.html` en el navegador. No necesita instalación, conexión ni servidor.

Primera etapa del rediseño: maqueta interactiva independiente, con datos ficticios de octubre de 2026. No lee ni escribe la base de datos. Sus cambios se pierden al recargar.

## Dirección visual

- Fondo azul claro `#f2f6ff`, superficies blancas y texto azul tinta `#253754`.
- Acción principal azul `#3565c9`; ahorro menta `#e1f4eb`, pagos melocotón `#fff0e6` y documentos lavanda `#ece8ff`.
- Trebuchet MS en títulos y Segoe UI en texto; fuentes locales, sin descargas.
- Inicio con cierre mensual y semana visible, accesos por función y pendientes.
- Barra lateral en escritorio y navegación inferior en móvil.

## Interacciones de prueba

- Añadir movimientos y actualizar el cierre y el presupuesto semanal.
- Buscar y filtrar movimientos.
- Cambiar entre lista y calendario; registrar y deshacer pagos o cobros ficticios.
- Filtrar documentos por carpeta y confirmar revisiones sin duplicar movimientos.
- Añadir aportaciones ficticias a los objetivos.

## Estado de la integración

Se conserva como referencia de la primera propuesta visual. El diseño, el calendario
familiar, Hoy, el modo oscuro y las acciones reales ya se integraron en la aplicación
principal, manteniendo los módulos y las reglas financieras existentes. Esta maqueta
sigue usando datos ficticios y no refleja todas las mejoras posteriores.

Para utilizar la aplicación real, consulta el [README principal](../../README.md).

## Comprobación

Verificados en Chrome: navegación, alta de movimiento, búsqueda, registro y deshacer de pago, calendario, revisión de documentos y aportación al ahorro. Sin errores JavaScript ni desbordamiento horizontal en las cinco pantallas a 360, 390, 768 y 1280 píxeles. Capturas del inicio en `escritorio.png` y `movil.png`.
