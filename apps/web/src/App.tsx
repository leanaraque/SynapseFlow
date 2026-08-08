/**
 * El armazón de la consola: sesión, identidad y el catálogo del rol.
 *
 * ## El rol se pide a la API, no se deduce del token
 *
 * El id token de Firebase trae el custom claim y sería tentador leerlo acá. No
 * se hace: **la ontología es la que dice qué habilita cada rol**, y esa
 * resolución vive en la API. Un cliente que decide qué mostrar por su cuenta
 * termina ofreciendo acciones que el backend rechaza, y el usuario ve un botón
 * que falla.
 *
 * Que la consola no muestre algo **no** es una medida de seguridad: es
 * comodidad. La barrera está en el catálogo filtrado por rol y en los gates.
 *
 * ## Un usuario sin rol no ve una consola vacía
 *
 * Ve la explicación. Es un problema de aprovisionamiento de identidad, no un
 * error suyo, y «Error 403» lo manda a reintentar el login — que no lo va a
 * resolver.
 */

import { useCallback, useEffect, useState } from "react";

import { ErrorDeApi, identidad, type Identidad } from "./api";
import { Chat } from "./Chat";
import { alCambiarLaSesion, cerrarSesion, iniciarSesion, type Usuario } from "./firebase";

type Pantalla = "consulta" | "catalogo";

export function App() {
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [cargandoSesion, setCargandoSesion] = useState(true);
  const [quien, setQuien] = useState<Identidad | null>(null);
  const [errorDeIdentidad, setErrorDeIdentidad] = useState<string | null>(null);
  const [pantalla, setPantalla] = useState<Pantalla>("consulta");

  useEffect(
    () =>
      alCambiarLaSesion((sesion) => {
        setUsuario(sesion);
        setCargandoSesion(false);
      }),
    [],
  );

  useEffect(() => {
    if (!usuario) {
      setQuien(null);
      return;
    }

    // `vigente` evita escribir estado de una sesión que ya cambió: sin esto,
    // entrar y salir rápido deja la identidad del usuario anterior en pantalla.
    let vigente = true;

    identidad()
      .then((yo) => {
        if (!vigente) return;
        setQuien(yo);
        setErrorDeIdentidad(null);
      })
      .catch((error: unknown) => {
        if (!vigente) return;
        setErrorDeIdentidad(
          error instanceof ErrorDeApi ? error.message : "No se pudo resolver la identidad.",
        );
      });

    return () => {
      vigente = false;
    };
  }, [usuario]);

  const salir = useCallback(() => {
    void cerrarSesion();
  }, []);

  if (cargandoSesion) {
    return (
      <main className="centrado" aria-busy="true">
        <p>Verificando la sesión…</p>
      </main>
    );
  }

  if (!usuario) return <Entrada />;

  return (
    <div className="consola">
      <header className="barra">
        <strong>SynapseFlow</strong>
        <nav>
          <button
            type="button"
            aria-current={pantalla === "consulta" ? "page" : undefined}
            onClick={() => setPantalla("consulta")}
          >
            Consulta
          </button>
          <button
            type="button"
            aria-current={pantalla === "catalogo" ? "page" : undefined}
            onClick={() => setPantalla("catalogo")}
          >
            Mi rol
          </button>
        </nav>
        <span className="identidad">
          {quien ? (
            <>
              {quien.nombre ?? quien.usuario} · <span className="rol">{quien.rol}</span>
            </>
          ) : (
            (usuario.displayName ?? usuario.email ?? usuario.uid)
          )}
        </span>
        <button type="button" onClick={salir}>
          Salir
        </button>
      </header>

      {errorDeIdentidad ? (
        <SinRol motivo={errorDeIdentidad} />
      ) : !quien ? (
        <main className="centrado" aria-busy="true">
          <p>Cargando el dominio…</p>
        </main>
      ) : pantalla === "consulta" ? (
        <Chat />
      ) : (
        <Catalogo identidad={quien} />
      )}
    </div>
  );
}

function Entrada() {
  return (
    <main className="centrado">
      <h1>SynapseFlow</h1>
      <p>Consultas con citas verificables y aprobación de acciones irreversibles.</p>
      <button type="button" onClick={() => void iniciarSesion()}>
        Entrar con Google
      </button>
    </main>
  );
}

function SinRol({ motivo }: { motivo: string }) {
  return (
    <main className="aviso">
      <h2>Tu usuario no tiene un rol asignado en el dominio</h2>
      <p>{motivo}</p>
      <p>
        No se asigna un rol por defecto a propósito: eso convertiría un problema de
        aprovisionamiento de identidad en un acceso silencioso. Pedile a quien administra la
        plataforma que configure tu rol.
      </p>
    </main>
  );
}

/**
 * Lo que este rol puede hacer, derivado de la ontología.
 *
 * Es la primera pantalla porque responde la pregunta que alguien tiene al
 * entrar: qué puedo pedirle a esto. Y marca cuáles necesitan aprobación
 * **antes** de proponerlas, no cuando el gate ya frenó.
 */
function Catalogo({ identidad }: { identidad: Identidad }) {
  return (
    <main className="catalogo">
      <h2>Lo que tu rol habilita</h2>
      <p className="nota">
        Derivado de la ontología del dominio, no cableado acá. Es el mismo catálogo que recibe el
        agente.
      </p>

      <ul>
        {identidad.acciones.map((accion) => (
          <li key={accion.nombre}>
            <code>{accion.nombre}</code>
            {accion.efecto ? <span className="efecto">{accion.efecto}</span> : null}
            {accion.requiere_aprobacion ? (
              <span className="gate" title="Se detiene esperando la aprobación de un humano">
                requiere aprobación
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </main>
  );
}
