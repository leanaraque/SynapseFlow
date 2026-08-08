/**
 * Firebase en la consola: **solo Auth**.
 *
 * No se importa `firebase/firestore` en ningún lado, y no es una omisión: las
 * reglas de `firestore.rules` cierran el acceso directo del cliente a todas las
 * colecciones a propósito. El dominio se sirve por la API, que es la que aplica
 * el RBAC derivado de la ontología.
 *
 * Si alguien agrega una llamada al SDK de Firestore desde acá «para resolver algo
 * rápido», va a fallar por reglas. **Eso es el diseño funcionando, no un bug.**
 * Hay un test en `tests/web/` que lo detiene antes, con el motivo escrito.
 *
 * Lo único que Firebase aporta es el id token que viaja en `Authorization`. El
 * rol no sale de acá: sale de un custom claim que la API valida contra la
 * ontología, y un token sin rol válido recibe 403 — nunca un rol por defecto.
 */

import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  getAuth,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  GoogleAuthProvider,
  type Auth,
  type User,
} from "firebase/auth";

/**
 * La configuración de Firebase **no es secreta**: identifica al proyecto y viaja
 * en cualquier bundle. Lo que protege los datos son las reglas y el RBAC de la
 * API, no esconder estos valores.
 *
 * Van por variables de entorno igual, para que el mismo build sirva a los
 * proyectos de desarrollo y de producción sin recompilar decisiones.
 */
const configuracion = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

/**
 * El usuario autenticado, con el nombre del dominio.
 *
 * Se reexporta —en lugar de que cada componente importe `User` de
 * `firebase/auth`— para que este módulo sea de verdad la única puerta al SDK.
 * Un tipo importado directo desaparece en el build y no rompe nada, y aun así
 * empieza a desdibujar dónde está la frontera.
 */
export type Usuario = User;

let app: FirebaseApp | undefined;

/** La app de Firebase, inicializada una sola vez. */
export function aplicacion(): FirebaseApp {
  app ??= initializeApp(configuracion);
  return app;
}

export function autenticacion(): Auth {
  return getAuth(aplicacion());
}

export function iniciarSesion(): Promise<unknown> {
  return signInWithPopup(autenticacion(), new GoogleAuthProvider());
}

export function cerrarSesion(): Promise<void> {
  return signOut(autenticacion());
}

export function alCambiarLaSesion(callback: (usuario: User | null) => void): () => void {
  return onAuthStateChanged(autenticacion(), callback);
}

/**
 * El id token vigente, o `null` si no hay sesión.
 *
 * Se pide en **cada** request y no se guarda: el SDK lo renueva solo cuando
 * está por vencer, y un token cacheado a mano empieza a fallar con 401 después
 * de una hora, de manera intermitente y difícil de reproducir.
 */
export async function token(): Promise<string | null> {
  const usuario = autenticacion().currentUser;
  return usuario ? usuario.getIdToken() : null;
}
