/**
 * Lo que el panel de costos calcula, separado del componente para poder
 * verificarlo sin navegador.
 *
 * ## Por qué los montos chicos llevan más decimales
 *
 * Una consulta cuesta del orden de un centavo de dólar, y con dos decimales todo
 * el panel diría «US$ 0,00». Un número que no distingue una consulta de diez no
 * sirve para decidir nada, que es para lo único que existe el panel.
 */

import type { GrupoDeCosto } from "./api";

const MONEDA = new Intl.NumberFormat("es-AR", {
  style: "currency",
  currency: "USD",
  currencyDisplay: "code",
});

/** Dólares con los decimales que hacen falta para que el monto no sea cero. */
export function formatearUsd(monto: number): string {
  if (monto === 0) return MONEDA.format(0);

  const decimales = Math.abs(monto) >= 1 ? 2 : Math.abs(monto) >= 0.01 ? 4 : 6;
  return new Intl.NumberFormat("es-AR", {
    style: "currency",
    currency: "USD",
    currencyDisplay: "code",
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  }).format(monto);
}

/** Enteros con separador de miles: los tokens se cuentan de a millones. */
export function formatearCantidad(cantidad: number): string {
  return new Intl.NumberFormat("es-AR").format(cantidad);
}

/**
 * Qué parte del total se lleva cada grupo, en porcentaje entero.
 *
 * Es la pregunta que el panel existe para contestar: no «cuánto gastamos» sino
 * «qué tarea se lleva el presupuesto».
 */
export function participacion(grupo: GrupoDeCosto, total: number): number {
  if (total <= 0) return 0;
  return Math.round((grupo.costo_usd / total) * 100);
}

/**
 * Ancho relativo de cada barra del gráfico por día, entre 0 y 100.
 *
 * Se normaliza contra el día más caro y no contra el total: con treinta días,
 * contra el total todas las barras quedarían de un par de píxeles.
 */
export function anchosRelativos(grupos: GrupoDeCosto[]): number[] {
  const maximo = Math.max(0, ...grupos.map((g) => g.costo_usd));
  if (maximo === 0) return grupos.map(() => 0);
  return grupos.map((g) => Math.round((g.costo_usd / maximo) * 100));
}
