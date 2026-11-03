/**
 * El panel de costos: qué tarea, qué modelo y qué persona se llevan el
 * presupuesto de modelos de lenguaje.
 *
 * ## Qué pregunta responde
 *
 * No «cuánto gastamos» sino «qué se lleva el gasto», que es lo que permite
 * decidir si conviene mover un perfil a un modelo más barato. Por eso las tablas
 * van ordenadas de mayor a menor y llevan la participación de cada fila.
 *
 * ## Lo que no muestra
 *
 * Los embeddings de la búsqueda de normativa no se cuentan —no disparan
 * callbacks de chat en LangChain— y la API lo declara en `nota`. El panel lo
 * repite en lugar de esconderlo: un total que se presenta como completo y no lo
 * es lleva a decisiones peores que uno que avisa.
 *
 * ## Quién lo ve
 *
 * La consola solo ofrece la pantalla si `/api/yo` dice `puede_ver_costos`, y la
 * barrera de verdad es el 403 de la API. Ver services/api/costos.py.
 */

import { useCallback, useEffect, useState } from "react";

import { costos, ErrorDeApi, type GrupoDeCosto, type ResumenDeCostos } from "./api";
import { anchosRelativos, formatearCantidad, formatearUsd, participacion } from "./cifras";

const VENTANAS = [7, 30, 90] as const;

export function Costos() {
  const [dias, setDias] = useState<number>(30);
  const [resumen, setResumen] = useState<ResumenDeCostos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);

  const cargar = useCallback(async (ventana: number) => {
    setCargando(true);
    try {
      setResumen(await costos(ventana));
      setError(null);
    } catch (causa: unknown) {
      setError(causa instanceof ErrorDeApi ? causa.message : "No se pudieron leer los costos.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar(dias);
  }, [cargar, dias]);

  return (
    <main className="costos" aria-busy={cargando}>
      <header className="encabezado-bandeja">
        <h2>Costo de modelos</h2>
        <div className="ventanas" role="group" aria-label="Período">
          {VENTANAS.map((ventana) => (
            <button
              key={ventana}
              type="button"
              aria-pressed={dias === ventana}
              onClick={() => setDias(ventana)}
            >
              {ventana} días
            </button>
          ))}
        </div>
      </header>

      {error ? <p className="error">{error}</p> : null}

      {!resumen ? (
        cargando ? <p>Leyendo el consumo…</p> : null
      ) : resumen.totales.llamadas === 0 ? (
        <p className="nota">
          No hubo llamadas a modelos en los últimos {resumen.dias} días. Cada consulta que se haga
          desde la pantalla Consulta va a aparecer acá.
        </p>
      ) : (
        <Detalle resumen={resumen} />
      )}
    </main>
  );
}

function Detalle({ resumen }: { resumen: ResumenDeCostos }) {
  const { totales } = resumen;

  return (
    <>
      {resumen.truncado ? (
        <p className="error">
          Hubo más llamadas que las que el panel suma de una vez: los totales son de una parte del
          período. Elegí una ventana más corta.
        </p>
      ) : null}

      <dl className="tarjetas">
        <Tarjeta titulo="Costo total" valor={formatearUsd(totales.costo_usd)} />
        <Tarjeta
          titulo="Por conversación"
          valor={formatearUsd(totales.costo_por_conversacion_usd)}
          detalle={`${formatearCantidad(totales.conversaciones)} conversaciones`}
        />
        <Tarjeta
          titulo="Llamadas al modelo"
          valor={formatearCantidad(totales.llamadas)}
          detalle={`${formatearCantidad(totales.tokens_entrada + totales.tokens_salida)} tokens`}
        />
        <Tarjeta titulo="Latencia p95" valor={`${formatearCantidad(totales.latencia_p95_ms)} ms`} />
      </dl>

      {totales.llamadas_sin_precio > 0 ? (
        <p className="error">
          {formatearCantidad(totales.llamadas_sin_precio)} llamadas usaron un modelo que no está en
          el catálogo de precios: figuran con costo cero por desconocimiento, no porque hayan sido
          gratis.
        </p>
      ) : null}

      <Tabla titulo="Por tarea" grupos={resumen.por_perfil} total={totales.costo_usd} />
      <Tabla titulo="Por modelo" grupos={resumen.por_modelo} total={totales.costo_usd} />
      <Tabla titulo="Por persona" grupos={resumen.por_usuario} total={totales.costo_usd} />
      <PorDia grupos={resumen.por_dia} />

      <p className="nota">{resumen.nota}</p>
    </>
  );
}

function Tarjeta({ titulo, valor, detalle }: { titulo: string; valor: string; detalle?: string }) {
  return (
    <div className="tarjeta">
      <dt>{titulo}</dt>
      <dd>
        <span className="valor">{valor}</span>
        {detalle ? <span className="detalle">{detalle}</span> : null}
      </dd>
    </div>
  );
}

function Tabla({
  titulo,
  grupos,
  total,
}: {
  titulo: string;
  grupos: GrupoDeCosto[];
  total: number;
}) {
  return (
    <section className="tabla-costos">
      <h3>{titulo}</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">Nombre</th>
            <th scope="col" className="numero">
              Costo
            </th>
            <th scope="col" className="numero">
              Parte
            </th>
            <th scope="col" className="numero">
              Llamadas
            </th>
            <th scope="col" className="numero">
              Tokens
            </th>
          </tr>
        </thead>
        <tbody>
          {grupos.map((grupo) => (
            <tr key={grupo.clave}>
              <th scope="row">
                <code>{grupo.clave}</code>
              </th>
              <td className="numero">{formatearUsd(grupo.costo_usd)}</td>
              <td className="numero">{participacion(grupo, total)} %</td>
              <td className="numero">{formatearCantidad(grupo.llamadas)}</td>
              <td className="numero">{formatearCantidad(grupo.tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function PorDia({ grupos }: { grupos: GrupoDeCosto[] }) {
  const anchos = anchosRelativos(grupos);

  return (
    <section className="tabla-costos">
      <h3>Por día</h3>
      <ul className="dias">
        {grupos.map((grupo, i) => (
          <li key={grupo.clave}>
            <span className="fecha">{grupo.clave}</span>
            <span className="pista" aria-hidden="true">
              <span className="relleno" style={{ width: `${anchos[i] ?? 0}%` }} />
            </span>
            <span className="numero">{formatearUsd(grupo.costo_usd)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
