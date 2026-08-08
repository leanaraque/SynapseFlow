/**
 * El cliente de la API. Un solo lugar donde se arma el `Authorization`.
 *
 * Concentrarlo acá no es estilo: un `fetch` suelto que se olvide del header no
 * falla de manera visible en desarrollo —donde puede haber un proxy permisivo—
 * y falla con 401 en producción. Con una sola puerta, olvidarse no es posible.
 *
 * Las rutas son **relativas**. En desarrollo las reenvía el proxy de Vite y en
 * producción el rewrite de Firebase Hosting hacia Cloud Run: una URL cableada
 * funciona en una máquina y falla en las otras dos.
 */

import { token } from "./firebase";

export class ErrorDeApi extends Error {
  constructor(
    readonly estado: number,
    mensaje: string,
  ) {
    super(mensaje);
    this.name = "ErrorDeApi";
  }
}

export async function cabeceras(hilo?: string): Promise<Headers> {
  const jwt = await token();
  if (!jwt) {
    // Antes de salir a la red: sin sesión el 401 es seguro, y este mensaje dice
    // qué hacer mientras que el del servidor solo dice que no.
    throw new ErrorDeApi(401, "No hay sesión iniciada.");
  }

  const cabeceras = new Headers({
    Authorization: `Bearer ${jwt}`,
    "Content-Type": "application/json",
  });
  if (hilo) cabeceras.set("X-Thread-Id", hilo);
  return cabeceras;
}

export async function fallar(respuesta: Response): Promise<never> {
  // El cuerpo de error de la API es `{error: string}`. Se intenta leerlo porque
  // ahí está el motivo —«el rol X no puede aprobar Y»— y un «Error 403» pelado
  // manda a alguien a revisar permisos sin saber cuáles.
  let detalle = respuesta.statusText;
  try {
    const cuerpo = (await respuesta.json()) as { error?: string };
    if (cuerpo.error) detalle = cuerpo.error;
  } catch {
    // Cuerpo vacío o no-JSON: queda el statusText.
  }
  throw new ErrorDeApi(respuesta.status, detalle);
}

async function json<T>(ruta: string): Promise<T> {
  const respuesta = await fetch(ruta, { headers: await cabeceras() });
  if (!respuesta.ok) await fallar(respuesta);
  return (await respuesta.json()) as T;
}

// ─────────────────────────────────────────────────────────────────────────────
// Tipos del contrato
// ─────────────────────────────────────────────────────────────────────────────

export interface AccionDisponible {
  nombre: string;
  efecto: string | null;
  requiere_aprobacion: boolean | null;
}

export interface Identidad {
  usuario: string;
  nombre: string | null;
  rol: string;
  acciones: AccionDisponible[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Operaciones
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Quién soy y qué acciones me habilita mi rol, según la ontología.
 *
 * El catálogo sale del mismo `compile_tools` que ve el agente: si la consola
 * armara el suyo, ofrecería acciones que el modelo no tiene —o escondería
 * acciones que el usuario sí puede pedir.
 */
export function identidad(): Promise<Identidad> {
  return json<Identidad>("/api/yo");
}

/**
 * Abre el flujo de una consulta.
 *
 * Devuelve la respuesta cruda: el parseo de SSE vive en `sse.ts`, para que se
 * pueda verificar sin red. Y usa `fetch` y no `EventSource` porque aquella solo
 * hace GET y no admite cabeceras — no habría dónde poner el `Authorization`.
 */
export async function consultar(pregunta: string, hilo?: string): Promise<Response> {
  const respuesta = await fetch("/api/consultas", {
    method: "POST",
    headers: await cabeceras(hilo),
    body: JSON.stringify({ pregunta, thread_id: hilo ?? null }),
  });
  if (!respuesta.ok) await fallar(respuesta);
  return respuesta;
}

/**
 * El hilo de la conversación, que la API informa en una cabecera.
 *
 * Cuando lo genera el servidor la consola no lo conoce, y sin él no puede
 * continuar la conversación ni aprobar el gate que ese mismo recorrido abre.
 */
export function hiloDe(respuesta: Response): string | null {
  return respuesta.headers.get("X-Thread-Id");
}
