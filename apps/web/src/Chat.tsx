/**
 * El chat: la pantalla donde una inspectora pregunta.
 *
 * ## Se muestra el proceso, no un spinner
 *
 * Los eventos de herramienta aparecen **mientras ocurren**: «consultando el
 * activo P-2101-A», «calculando vida remanente». No es decoración. En este
 * dominio, ver qué se consultó es parte de poder defender la respuesta, y veinte
 * segundos de spinner opaco no dicen si el sistema está trabajando o colgado.
 *
 * ## Las citas se pueden abrir
 *
 * Una cita que no se puede inspeccionar no cumple su función: firma la respuesta
 * sin permitir verificarla. Cada una muestra documento, sección y **vigencia**,
 * porque un fragmento derogado citado sin decirlo es peor que no citar nada.
 *
 * ## Lo que la consola no hace
 *
 * No decide si hay fundamento —eso ya lo hizo el verificador— ni arma citas a
 * partir del texto del modelo. Lo que se muestra viene del `artifact` de las
 * herramientas, que no pasa por el LLM.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { consultar, ErrorDeApi, hiloDe } from "./api";
import { eventos, TIPOS, type EventoSSE } from "./sse";

interface Cita {
  doc_id: string;
  seccion: string;
  titulo?: string | null;
  vigencia?: string;
}

interface PasoDeHerramienta {
  id: string;
  herramienta: string;
  agente: string;
  argumentos: Record<string, unknown>;
  contenido?: string;
  terminado: boolean;
}

interface Turno {
  pregunta: string;
  pasos: PasoDeHerramienta[];
  texto: string;
  citas: Cita[];
  aprobacion: AccionPropuesta[] | null;
  error: string | null;
  enCurso: boolean;
}

interface AccionPropuesta {
  herramienta: string;
  argumentos: Record<string, unknown>;
  descripcion: string;
  decisiones: string[];
}

function turnoNuevo(pregunta: string): Turno {
  return {
    pregunta,
    pasos: [],
    texto: "",
    citas: [],
    aprobacion: null,
    error: null,
    enCurso: true,
  };
}

export function Chat() {
  const [turnos, setTurnos] = useState<Turno[]>([]);
  const [pregunta, setPregunta] = useState("");
  const [hilo, setHilo] = useState<string | undefined>(undefined);
  const [ocupado, setOcupado] = useState(false);
  const fin = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fin.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turnos]);

  /** Modifica el turno en curso, que siempre es el último. */
  const actualizar = useCallback((cambio: (turno: Turno) => Turno) => {
    setTurnos((previos) => {
      if (previos.length === 0) return previos;
      const copia = [...previos];
      const ultimo = copia[copia.length - 1];
      if (ultimo) copia[copia.length - 1] = cambio(ultimo);
      return copia;
    });
  }, []);

  const enviar = useCallback(
    async (texto: string) => {
      setTurnos((previos) => [...previos, turnoNuevo(texto)]);
      setPregunta("");
      setOcupado(true);

      try {
        const respuesta = await consultar(texto, hilo);
        // El hilo se fija con la primera respuesta y se reusa: es lo que hace
        // que la conversación tenga memoria y que el gate se pueda aprobar.
        const devuelto = hiloDe(respuesta);
        if (devuelto) setHilo(devuelto);

        for await (const evento of eventos(respuesta)) {
          actualizar((turno) => aplicar(turno, evento));
        }
      } catch (error: unknown) {
        const motivo =
          error instanceof ErrorDeApi ? error.message : "No se pudo completar la consulta.";
        actualizar((turno) => ({ ...turno, error: motivo }));
      } finally {
        actualizar((turno) => ({ ...turno, enCurso: false }));
        setOcupado(false);
      }
    },
    [actualizar, hilo],
  );

  return (
    <main className="chat">
      <div className="conversacion">
        {turnos.length === 0 ? <Sugerencia /> : null}
        {turnos.map((turno, indice) => (
          <TurnoVisto key={indice} turno={turno} />
        ))}
        <div ref={fin} />
      </div>

      <form
        className="entrada"
        onSubmit={(evento) => {
          evento.preventDefault();
          const texto = pregunta.trim();
          if (texto && !ocupado) void enviar(texto);
        }}
      >
        <input
          type="text"
          value={pregunta}
          onChange={(evento) => setPregunta(evento.target.value)}
          placeholder="¿El P-2101-A sigue apto para servicio?"
          aria-label="Pregunta"
          disabled={ocupado}
        />
        <button type="submit" disabled={ocupado || !pregunta.trim()}>
          {ocupado ? "Trabajando…" : "Preguntar"}
        </button>
      </form>
    </main>
  );
}

/**
 * Aplica un evento del flujo al turno en curso.
 *
 * Está separada del componente para que sea una función pura sobre el estado: la
 * traducción de eventos a lo que se ve no debería necesitar montar React para
 * poder razonarse.
 */
function aplicar(turno: Turno, evento: EventoSSE): Turno {
  const datos = evento.datos;

  switch (evento.tipo) {
    case TIPOS.herramientaInicio:
      return {
        ...turno,
        pasos: [
          ...turno.pasos,
          {
            id: `${String(datos.herramienta)}-${turno.pasos.length}`,
            herramienta: String(datos.herramienta ?? ""),
            agente: String(datos.agente ?? ""),
            argumentos: (datos.argumentos as Record<string, unknown>) ?? {},
            terminado: false,
          },
        ],
      };

    case TIPOS.herramientaFin: {
      // Se cierra el último paso abierto de esa herramienta. Buscar por nombre
      // desde el final importa: el ciclo del verificador puede invocar la misma
      // búsqueda dos veces, y cerrar la primera dejaría la segunda girando.
      const pasos = [...turno.pasos];
      for (let i = pasos.length - 1; i >= 0; i--) {
        const paso = pasos[i];
        if (paso && !paso.terminado && paso.herramienta === datos.herramienta) {
          pasos[i] = { ...paso, terminado: true, contenido: String(datos.contenido ?? "") };
          break;
        }
      }
      return { ...turno, pasos };
    }

    case TIPOS.token:
      return { ...turno, texto: turno.texto + String(datos.texto ?? "") };

    case TIPOS.citas:
      return { ...turno, citas: (datos.citas as Cita[]) ?? [] };

    case TIPOS.aprobacionRequerida:
      return { ...turno, aprobacion: (datos.acciones as AccionPropuesta[]) ?? [] };

    case TIPOS.error:
      return { ...turno, error: String(datos.error ?? "Error del servidor."), enCurso: false };

    case TIPOS.fin:
      return { ...turno, enCurso: false };

    default:
      // Un evento que esta versión de la consola no conoce no puede romperla.
      return turno;
  }
}

function Sugerencia() {
  return (
    <div className="sugerencia">
      <p>Preguntá sobre un activo, su historial o la normativa que le aplica.</p>
      <p className="nota">
        Toda afirmación normativa viene con su fuente. Si no hay fundamento en el corpus, el
        sistema lo dice en lugar de improvisar.
      </p>
    </div>
  );
}

function TurnoVisto({ turno }: { turno: Turno }) {
  return (
    <article className="turno">
      <p className="pregunta">{turno.pregunta}</p>

      {turno.pasos.length > 0 ? (
        <ol className="pasos" aria-label="Lo que hizo el agente">
          {turno.pasos.map((paso) => (
            <li key={paso.id} className={paso.terminado ? "hecho" : "curso"}>
              <code>{paso.herramienta}</code>
              <span className="argumentos">{resumir(paso.argumentos)}</span>
              {paso.agente ? <span className="agente">{paso.agente}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}

      {turno.texto ? <div className="respuesta">{turno.texto}</div> : null}

      {turno.enCurso && !turno.texto ? (
        <p className="nota" aria-live="polite">
          Trabajando…
        </p>
      ) : null}

      {turno.citas.length > 0 ? <Fuentes citas={turno.citas} /> : null}

      {turno.aprobacion?.length ? <Propuesta acciones={turno.aprobacion} /> : null}

      {turno.error ? (
        <p className="error" role="alert">
          {turno.error}
        </p>
      ) : null}
    </article>
  );
}

/**
 * Las fuentes, desplegables.
 *
 * La vigencia se marca porque el corpus tiene documentos derogados a propósito:
 * un procedimiento superado que contradice al vigente es exactamente el caso que
 * el sistema tiene que distinguir, y esconderlo acá desharía ese trabajo.
 */
function Fuentes({ citas }: { citas: Cita[] }) {
  return (
    <details className="fuentes">
      <summary>
        Fuentes ({citas.length})
      </summary>
      <ul>
        {citas.map((cita) => (
          <li key={`${cita.doc_id}-${cita.seccion}`}>
            <code>
              {cita.doc_id} §{cita.seccion}
            </code>
            {cita.titulo ? <span> · {cita.titulo}</span> : null}
            {cita.vigencia && cita.vigencia !== "vigente" ? (
              <span className="derogado"> · {cita.vigencia}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * La acción irreversible que el agente propone y no ejecuta.
 *
 * Se muestran los argumentos **exactos**: aprobar «una parada» no es aprobar
 * nada. Los botones no están acá — la decisión se toma en la bandeja, que es
 * donde vive la validación de autoridad y donde puede entrar alguien que no es
 * quien preguntó.
 */
function Propuesta({ acciones }: { acciones: AccionPropuesta[] }) {
  return (
    <div className="propuesta" role="status">
      <h3>Acción propuesta · requiere aprobación</h3>
      {acciones.map((accion) => (
        <div key={accion.herramienta}>
          <p>
            <code>{accion.herramienta}</code> <span>{resumir(accion.argumentos)}</span>
          </p>
          {accion.descripcion ? <pre className="descripcion">{accion.descripcion}</pre> : null}
        </div>
      ))}
      <p className="nota">
        El agente no la ejecutó. Queda esperando a alguien con autoridad para aprobarla, en la
        bandeja de aprobaciones.
      </p>
    </div>
  );
}

/** Los argumentos en una línea, para que el paso se lea de un vistazo. */
function resumir(argumentos: Record<string, unknown>): string {
  const partes = Object.entries(argumentos)
    .filter(([, valor]) => valor !== null && valor !== undefined && valor !== "")
    .map(([clave, valor]) => `${clave}: ${String(valor)}`);
  return partes.join(" · ");
}
