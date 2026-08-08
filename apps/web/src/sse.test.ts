/**
 * Contrato del lector de SSE.
 *
 * **Lo que verifica de verdad es el corte de los trozos.** Un flujo de red llega
 * en pedazos arbitrarios: un bloque puede partirse entre `event:` y `data:`, o a
 * mitad de un carácter multibyte. Partir por `\n\n` sobre cada trozo suelto
 * funciona con respuestas cortas —que es como se prueba a mano— y corrompe las
 * largas, que son justo las de este sistema.
 *
 * Es la única lógica no trivial del cliente, y por eso es la que tiene tests.
 */

import { describe, expect, it } from "vitest";

import { eventos, TERMINALES, TIPOS, type EventoSSE } from "./sse";

/** Una respuesta cuyo cuerpo entrega exactamente estos trozos, en este orden. */
function respuestaCon(...trozos: string[]): Response {
  const codificador = new TextEncoder();
  const flujo = new ReadableStream<Uint8Array>({
    start(controlador) {
      for (const trozo of trozos) controlador.enqueue(codificador.encode(trozo));
      controlador.close();
    },
  });
  return new Response(flujo);
}

function bloque(tipo: string, datos: unknown): string {
  return `event: ${tipo}\ndata: ${JSON.stringify(datos)}\n\n`;
}

async function leer(respuesta: Response): Promise<EventoSSE[]> {
  const salida: EventoSSE[] = [];
  for await (const evento of eventos(respuesta)) salida.push(evento);
  return salida;
}

describe("el corte de los trozos", () => {
  it("junta un bloque partido entre dos trozos", async () => {
    const completo = bloque(TIPOS.token, { texto: "El activo NO está apto." });
    const mitad = Math.floor(completo.length / 2);

    const leidos = await leer(
      respuestaCon(completo.slice(0, mitad), completo.slice(mitad)),
    );

    expect(leidos).toEqual([{ tipo: TIPOS.token, datos: { texto: "El activo NO está apto." } }]);
  });

  it("separa varios bloques que llegaron en un solo trozo", async () => {
    const leidos = await leer(
      respuestaCon(
        bloque(TIPOS.herramientaInicio, { herramienta: "consultar_activo" }) +
          bloque(TIPOS.token, { texto: "listo" }) +
          bloque(TIPOS.fin, {}),
      ),
    );

    expect(leidos.map((e) => e.tipo)).toEqual([TIPOS.herramientaInicio, TIPOS.token, TIPOS.fin]);
  });

  it("no corrompe un carácter multibyte partido entre trozos", async () => {
    // «ó» son dos bytes en UTF-8. Sin `stream: true` en el decodificador, este
    // caso produce un carácter de reemplazo y el JSON deja de parsear — con
    // texto en español eso no es un caso raro, es el caso normal.
    const completo = new TextEncoder().encode(bloque(TIPOS.token, { texto: "inspección" }));
    const corte = completo.indexOf(0xc3); // primer byte de «ó»

    const flujo = new ReadableStream<Uint8Array>({
      start(controlador) {
        controlador.enqueue(completo.slice(0, corte + 1));
        controlador.enqueue(completo.slice(corte + 1));
        controlador.close();
      },
    });

    const leidos = await leer(new Response(flujo));

    expect(leidos[0]?.datos.texto).toBe("inspección");
  });
});

describe("el formato del bloque", () => {
  it("descarta el comentario de apertura", async () => {
    // El flujo arranca con `: abierto` para que los proxies suelten las
    // cabeceras. No es un evento.
    const leidos = await leer(respuestaCon(": abierto\n\n" + bloque(TIPOS.fin, {})));

    expect(leidos.map((e) => e.tipo)).toEqual([TIPOS.fin]);
  });

  it("acepta un bloque sin datos", async () => {
    const leidos = await leer(respuestaCon(`event: ${TIPOS.fin}\n\n`));

    expect(leidos).toEqual([{ tipo: TIPOS.fin, datos: {} }]);
  });

  it("ignora un bloque sin tipo", async () => {
    const leidos = await leer(respuestaCon('data: {"x":1}\n\n' + bloque(TIPOS.fin, {})));

    expect(leidos.map((e) => e.tipo)).toEqual([TIPOS.fin]);
  });

  it("no pierde el texto con saltos de línea adentro", async () => {
    const texto = "Primera línea.\nSegunda línea.";

    const leidos = await leer(respuestaCon(bloque(TIPOS.token, { texto })));

    expect(leidos[0]?.datos.texto).toBe(texto);
  });

  it("un bloque ilegible no tumba el flujo", async () => {
    // El usuario perdería la respuesta entera por culpa de un evento.
    const leidos = await leer(
      respuestaCon(`event: ${TIPOS.token}\ndata: {no es json}\n\n` + bloque(TIPOS.fin, {})),
    );

    expect(leidos.map((e) => e.tipo)).toEqual([TIPOS.error, TIPOS.fin]);
  });
});

describe("el contrato con la API", () => {
  it("reconoce los seis tipos que emite el servidor", () => {
    expect(Object.values(TIPOS)).toEqual([
      "token",
      "herramienta_inicio",
      "herramienta_fin",
      "citas",
      "aprobacion_requerida",
      "error",
      "fin",
    ]);
  });

  it("los terminales son fin y error", () => {
    expect(TERMINALES).toEqual(["fin", "error"]);
  });

  it("una respuesta sin cuerpo falla con un motivo", async () => {
    await expect(leer(new Response(null))).rejects.toThrow("flujo");
  });
});
