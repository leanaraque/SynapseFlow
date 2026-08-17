/**
 * La bandeja: lo que un supervisor ve cuando el agente propone algo irreversible.
 *
 * ## Lo que se muestra es lo que se va a ejecutar
 *
 * Los argumentos **exactos**, no un resumen: aprobar «una parada» no es aprobar
 * nada. Y el texto de la propuesta viene de `approval_prompt` en la ontología, ya
 * formateado por la API con los valores reales — la consola no lo redacta, para
 * que lo que se lee al aprobar sea lo que el dominio declara.
 *
 * ## La consola no decide quién puede aprobar
 *
 * La bandeja solo trae lo que este usuario puede resolver, filtrado por la API
 * con el mismo código que valida el POST. Acá no se repite esa regla: dos reglas
 * de autoridad que dicen lo mismo son dos reglas que se desincronizan, y la que
 * corre en el navegador no protege de nada.
 *
 * ## Los botones salen de `decisiones`
 *
 * No están cableados. Ofrecer «editar» donde la ontología no lo permite es
 * prometer algo que el endpoint rechaza, y el usuario descubre el límite después
 * de decidir.
 *
 * ## La antigüedad se muestra
 *
 * Un hilo interrumpido que nadie resolvió queda ocupando estado, y sobre todo
 * significa que hay un equipo esperando una decisión. Que se vea cuánto hace que
 * espera es la diferencia entre una bandeja y una lista.
 */

import { useCallback, useEffect, useState } from "react";

import {
  decidir,
  ErrorDeApi,
  pendientes as pedirPendientes,
  type Decision,
  type Pendiente,
} from "./api";
import { eventos, TIPOS } from "./sse";

/** A partir de acá, un pendiente lleva demasiado sin resolverse. */
const HORAS_PARA_VENCER = 24;

export function Aprobaciones() {
  const [filas, setFilas] = useState<Pendiente[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resolviendo, setResolviendo] = useState<string | null>(null);
  const [resultado, setResultado] = useState<{ hilo: string; texto: string } | null>(null);

  const recargar = useCallback(async () => {
    try {
      const { pendientes } = await pedirPendientes();
      setFilas(pendientes);
      setError(null);
    } catch (causa: unknown) {
      setError(causa instanceof ErrorDeApi ? causa.message : "No se pudo leer la bandeja.");
      setFilas([]);
    }
  }, []);

  useEffect(() => {
    void recargar();
  }, [recargar]);

  const resolver = useCallback(
    async (hilo: string, decision: Decision, extra: { argumentos?: Record<string, unknown>; motivo?: string }) => {
      setResolviendo(hilo);
      setResultado(null);
      try {
        const respuesta = await decidir(hilo, decision, extra);

        // Aprobar no es un endpoint que devuelve ok: es el resto del recorrido.
        // Se consume el flujo para que el supervisor vea ejecutarse lo que
        // aprobó, por el mismo canal que ya conoce.
        let texto = "";
        for await (const evento of eventos(respuesta)) {
          if (evento.tipo === TIPOS.token) texto += String(evento.datos.texto ?? "");
          if (evento.tipo === TIPOS.herramientaFin) {
            // **No dice «ejecutado».** `herramienta_fin` significa que la
            // herramienta corrió, no que el efecto ocurrió: la acción de dominio
            // valida y puede negarse —pasó en producción, con un id de
            // inspección que el modelo había inventado— y anunciar «ejecutado»
            // ahí le hace creer al supervisor que la parada se materializó.
            //
            // Se muestra lo que devolvió, que es lo que lo dice.
            const nombre = String(evento.datos.herramienta ?? "");
            const salida = String(evento.datos.contenido ?? "").split("\n")[0] ?? "";
            texto += `\n· ${nombre}: ${salida}`;
          }
          if (evento.tipo === TIPOS.error) texto += `\n${String(evento.datos.error ?? "")}`;
        }
        setResultado({ hilo, texto: texto.trim() || "Decisión registrada." });
      } catch (causa: unknown) {
        setError(causa instanceof ErrorDeApi ? causa.message : "No se pudo registrar la decisión.");
      } finally {
        setResolviendo(null);
        await recargar();
      }
    },
    [recargar],
  );

  if (filas === null) {
    return (
      <main className="bandeja centrado" aria-busy="true">
        <p>Leyendo la bandeja…</p>
      </main>
    );
  }

  return (
    <main className="bandeja">
      <header className="encabezado-bandeja">
        <h2>Aprobaciones pendientes</h2>
        <button type="button" onClick={() => void recargar()}>
          Actualizar
        </button>
      </header>

      <p className="nota">
        Solo aparecen las que tu rol puede resolver, y nunca las que vos mismo propusiste. La
        separación de funciones es lo que hace que la aprobación signifique algo.
      </p>

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {resultado ? (
        <div className="resultado" role="status">
          <strong>Hilo {resultado.hilo}</strong>
          <pre>{resultado.texto}</pre>
        </div>
      ) : null}

      {filas.length === 0 && !error ? (
        <p className="nota">No hay nada esperando tu decisión.</p>
      ) : null}

      <ul className="pendientes">
        {filas.map((fila) => (
          <Tarjeta
            key={fila.thread_id}
            pendiente={fila}
            ocupado={resolviendo === fila.thread_id}
            onDecidir={resolver}
          />
        ))}
      </ul>
    </main>
  );
}

function Tarjeta({
  pendiente,
  ocupado,
  onDecidir,
}: {
  pendiente: Pendiente;
  ocupado: boolean;
  onDecidir: (
    hilo: string,
    decision: Decision,
    extra: { argumentos?: Record<string, unknown>; motivo?: string },
  ) => Promise<void>;
}) {
  const [motivo, setMotivo] = useState("");
  const [editando, setEditando] = useState(false);
  const [argumentos, setArgumentos] = useState(() =>
    JSON.stringify(pendiente.argumentos, null, 2),
  );
  const [errorDeEdicion, setErrorDeEdicion] = useState<string | null>(null);

  const vencida = antiguedadEnHoras(pendiente.creado_en) > HORAS_PARA_VENCER;
  const puede = (decision: string) => pendiente.decisiones.includes(decision);

  return (
    <li className={vencida ? "pendiente vencida" : "pendiente"}>
      <div className="cabecera">
        <code>{pendiente.herramienta}</code>
        {vencida ? (
          <span className="marca-vencida" title="Nadie la resolvió en 24 horas">
            esperando hace {Math.round(antiguedadEnHoras(pendiente.creado_en))} h
          </span>
        ) : (
          <span className="cuando">{fechaLegible(pendiente.creado_en)}</span>
        )}
      </div>

      {/* Los argumentos exactos. Aprobar «una parada» no es aprobar nada. */}
      <dl className="argumentos-exactos">
        {Object.entries(pendiente.argumentos).map(([clave, valor]) => (
          <div key={clave}>
            <dt>{clave}</dt>
            <dd>{String(valor)}</dd>
          </div>
        ))}
      </dl>

      {pendiente.descripcion ? <pre className="descripcion">{pendiente.descripcion}</pre> : null}

      <p className="nota">
        Propuesta por <code>{pendiente.propuesta_por}</code> ({pendiente.rol_proponente}) · hilo{" "}
        <code>{pendiente.thread_id}</code>
      </p>

      {editando ? (
        <div className="edicion">
          <label htmlFor={`args-${pendiente.thread_id}`}>
            Argumentos que se van a ejecutar en lugar de los propuestos
          </label>
          <textarea
            id={`args-${pendiente.thread_id}`}
            value={argumentos}
            onChange={(evento) => setArgumentos(evento.target.value)}
            rows={Math.min(10, Object.keys(pendiente.argumentos).length + 3)}
          />
          {errorDeEdicion ? (
            <p className="error" role="alert">
              {errorDeEdicion}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="decisiones">
        {puede("approve") ? (
          <button
            type="button"
            disabled={ocupado || editando}
            onClick={() => void onDecidir(pendiente.thread_id, "aprobar", {})}
          >
            Aprobar
          </button>
        ) : null}

        {puede("reject") ? (
          <>
            <input
              type="text"
              value={motivo}
              onChange={(evento) => setMotivo(evento.target.value)}
              placeholder="Motivo del rechazo"
              aria-label="Motivo del rechazo"
            />
            <button
              type="button"
              disabled={ocupado}
              onClick={() => void onDecidir(pendiente.thread_id, "rechazar", { motivo })}
            >
              Rechazar
            </button>
          </>
        ) : null}

        {puede("edit") ? (
          <button
            type="button"
            disabled={ocupado}
            onClick={() => {
              if (!editando) {
                setEditando(true);
                return;
              }
              // Se valida acá y no se manda JSON roto: el 422 del servidor
              // llegaría sin decir qué línea, y el supervisor perdería lo que
              // escribió.
              try {
                const nuevos = JSON.parse(argumentos) as Record<string, unknown>;
                setErrorDeEdicion(null);
                void onDecidir(pendiente.thread_id, "editar", { argumentos: nuevos });
                setEditando(false);
              } catch {
                setErrorDeEdicion("Los argumentos no son un JSON válido.");
              }
            }}
          >
            {editando ? "Ejecutar con estos argumentos" : "Editar"}
          </button>
        ) : null}

        {ocupado ? <span className="nota">Reanudando el recorrido…</span> : null}
      </div>

      <p className="nota">
        Aprueban: {pendiente.aprobadores.join(", ") || "(ninguno declarado)"}
      </p>
    </li>
  );
}

function antiguedadEnHoras(iso: string): number {
  const creado = Date.parse(iso);
  if (Number.isNaN(creado)) return 0;
  return (Date.now() - creado) / 3_600_000;
}

function fechaLegible(iso: string): string {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? iso : fecha.toLocaleString("es-AR");
}
