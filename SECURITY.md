# Política de seguridad

## Reportar una vulnerabilidad

**No abras un issue público.**

Reportá la vulnerabilidad por
[GitHub Security Advisories](https://github.com/leanaraque/SynapseFlow/security/advisories/new),
que permite discutirla en privado hasta que exista una corrección.

Incluí en el reporte:

- Qué componente está afectado.
- Cómo reproducirlo.
- Qué impacto tiene, y bajo qué supuestos.

Vas a recibir una respuesta dentro de los **7 días**.

## Alcance

Este es un proyecto de demostración con datos sintéticos, no un producto en
operación. Aun así, interesan especialmente los reportes sobre las propiedades
que el proyecto afirma sostener:

| Propiedad | Dónde vive |
|---|---|
| Una acción irreversible no se puede ejecutar sin aprobación humana | [`compiler.py`](packages/synapseflow/ontology/compiler.py), [ADR-0005](docs/adr/0005-hitl-con-interrupt-de-langgraph.md) |
| Un rol no puede invocar acciones fuera de su catálogo | `Ontology.actions_for_role` |
| Los campos `pii` o `restricted` no salen en claro hacia un proveedor de LLM | [ADR-0004](docs/adr/0004-gateway-provider-agnostic.md) |
| El cliente no accede a Firestore directamente | [`firestore.rules`](firestore.rules) |
| El log de auditoría es inmutable | [`firestore.rules`](firestore.rules) |

Si encontrás una forma de violar alguna de estas, es exactamente lo que se
quiere saber.

## Fuera de alcance

- Ataques que requieran acceso previo a las credenciales del proyecto.
- Denegación de servicio por consumo de cuota del proveedor de LLM. Los límites
  existen (`ModelCallLimitMiddleware`, `SYNAPSEFLOW_MAX_MODEL_CALLS`) pero no
  pretenden resistir un abuso deliberado.
- Vulnerabilidades en dependencias de terceros: reportalas al proyecto
  correspondiente. Sí interesa saber si este repositorio las expone de una forma
  que el proyecto original no contempla.

## Manejo de credenciales

El repositorio no contiene secretos. `.env` está en `.gitignore` y solo se
versiona [`.env.example`](.env.example), con las claves vacías.

En despliegue, la autenticación contra Firestore sale de la identidad del
servicio de Cloud Run (ADC), sin archivos de clave. Las claves de los
proveedores de LLM se inyectan por Secret Manager, nunca por variable de entorno
en el manifiesto.

Si detectás una credencial commiteada, tratalo como vulnerabilidad y reportalo
por el canal privado.
