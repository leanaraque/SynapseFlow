import { describe, expect, it } from "vitest";

import type { GrupoDeCosto } from "./api";
import { anchosRelativos, formatearCantidad, formatearUsd, participacion } from "./cifras";

const grupo = (clave: string, costo_usd: number): GrupoDeCosto => ({
  clave,
  costo_usd,
  llamadas: 1,
  tokens: 1000,
});

describe("formatearUsd", () => {
  it("no redondea a cero el costo de una consulta", () => {
    // Una consulta cuesta del orden de un centavo: con dos decimales todo el
    // panel diría cero.
    expect(formatearUsd(0.0042)).not.toMatch(/0,00\D*$/);
    expect(formatearUsd(0.0042)).toContain("0,0042");
  });

  it("muestra seis decimales para montos menores a un centavo", () => {
    expect(formatearUsd(0.000123)).toContain("0,000123");
  });

  it("usa dos decimales desde un dólar", () => {
    expect(formatearUsd(12.5)).toContain("12,50");
  });

  it("dice cero cuando es cero", () => {
    expect(formatearUsd(0)).toContain("0,00");
  });
});

describe("formatearCantidad", () => {
  it("separa los miles", () => {
    expect(formatearCantidad(1234567)).toBe("1.234.567");
  });
});

describe("participacion", () => {
  it("es la parte del total en porcentaje entero", () => {
    expect(participacion(grupo("synthesis", 0.75), 1)).toBe(75);
  });

  it("no divide por cero cuando no hubo gasto", () => {
    expect(participacion(grupo("router", 0), 0)).toBe(0);
  });
});

describe("anchosRelativos", () => {
  it("normaliza contra el día más caro", () => {
    expect(anchosRelativos([grupo("a", 1), grupo("b", 0.5), grupo("c", 0)])).toEqual([100, 50, 0]);
  });

  it("deja todo en cero si nadie gastó", () => {
    expect(anchosRelativos([grupo("a", 0), grupo("b", 0)])).toEqual([0, 0]);
  });

  it("no falla sin días", () => {
    expect(anchosRelativos([])).toEqual([]);
  });
});
