/**
 * Lector de Server-Sent Events sobre `fetch`.
 *
 * ## Por qué no `EventSource`
 *
 * `EventSource` es la API nativa para SSE y **no sirve acá**: solo hace GET y no
 * permite mandar cabeceras, así que no hay dónde poner el `Authorization`. Una
 * consulta es un POST con la pregunta en el cuerpo y con identidad obligatoria.
 *
 * ## El parseo es del protocolo, no del contenido
 *
 * Un bloque termina en línea en blanco y sus campos son `event:` y `data:`. Un
 * trozo de red puede cortar en cualquier byte —incluso a mitad de un carácter
 * multibyte—, así que se decodifica con `stream: true` y se acumula hasta tener
 * el bloque completo. Partir por `\n\n` sobre cada trozo suelto funciona con
 * respuestas cortas y corrompe las largas, que son justo las de este sistema.
 */

/** Un evento del recorrido, tal como lo emite `services/api/streaming.py`. */
export interface EventoSSE {
  tipo: string;
  datos: Record<string, unknown>;
}

export const TIPOS = {
  token: "token",
  herramientaInicio: "herramienta_inicio",
  herramientaFin: "herramienta_fin",
  citas: "citas",
  aprobacionRequerida: "aprobacion_requerida",
  error: "error",
  fin: "fin",
} as const;

/** Los dos finales posibles. El flujo termina siempre en exactamente uno. */
export const TERMINALES: readonly string[] = [TIPOS.fin, TIPOS.error];

/**
 * Recorre los eventos de una respuesta SSE.
 *
 * @param respuesta lo que devuelve `consultar()` o `decidir()`.
 */
export async function* eventos(respuesta: Response): AsyncGenerator<EventoSSE> {
  const cuerpo = respuesta.body;
  if (!cuerpo) throw new Error("La respuesta no trae flujo.");

  const lector = cuerpo.getReader();
  const decodificador = new TextDecoder();
  let pendiente = "";

  try {
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;

      pendiente += decodificador.decode(value, { stream: true });

      let corte = pendiente.indexOf("\n\n");
      while (corte !== -1) {
        const bloque = pendiente.slice(0, corte);
        pendiente = pendiente.slice(corte + 2);

        const evento = interpretar(bloque);
        if (evento) yield evento;

        corte = pendiente.indexOf("\n\n");
      }
    }
  } finally {
    // Si quien consume corta el bucle —el usuario cambió de pantalla— hay que
    // liberar la conexión. Sin esto queda un flujo abierto por cada consulta
    // abandonada, y el navegador limita cuántos admite en paralelo.
    await lector.cancel().catch(() => undefined);
  }
}

/**
 * Un bloque SSE como evento, o `null` si no lo es.
 *
 * El flujo arranca con un comentario —una línea que empieza con `:`— para que
 * los proxies suelten las cabeceras enseguida. No es un evento y se descarta.
 */
function interpretar(bloque: string): EventoSSE | null {
  let tipo = "";
  const partes: string[] = [];

  for (const linea of bloque.split("\n")) {
    if (linea.startsWith(":")) continue;
    if (linea.startsWith("event:")) tipo = linea.slice(6).trim();
    // Varias líneas `data:` se concatenan con saltos, según la especificación.
    // Hoy la API manda una sola —JSON escapa los saltos— y respetarlo igual
    // cuesta nada y evita un bug si eso cambia.
    else if (linea.startsWith("data:")) partes.push(linea.slice(5).trim());
  }

  if (!tipo) return null;

  try {
    const datos = partes.length ? (JSON.parse(partes.join("\n")) as Record<string, unknown>) : {};
    return { tipo, datos };
  } catch {
    // Un bloque ilegible no puede tumbar el flujo: el usuario perdería la
    // respuesta entera por un evento.
    return { tipo: TIPOS.error, datos: { error: "Evento ilegible del servidor." } };
  }
}
