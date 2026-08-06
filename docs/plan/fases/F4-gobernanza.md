# F4 · Gobernanza como middleware

**Depende de:** F1 (el gateway es el punto donde se aplica la política).
**Cierra el compromiso 5:** los datos sensibles no salen del perímetro.

## Por qué esta fase existe

La gobernanza no se escribe dentro de cada agente. Es una **capa que los
atraviesa**. Agregar un agente nuevo no debe implicar acordarse de agregarle
auditoría y redacción: las hereda por estar en el mismo pipeline.

Es el mismo razonamiento del filtrado de catálogo por rol, aplicado a otra capa:
**eliminar la posibilidad del olvido** en lugar de confiar en la disciplina de
quien escriba el próximo componente.

Se construye sobre `AgentMiddleware` de LangChain 1.x. Los hooks disponibles y
los middlewares ya hechos están en
[`00-convenciones.md` § Hallazgo 4](../00-convenciones.md).

---

## F4.1 · Contexto de ejecución

**Produce:** `packages/synapseflow/governance/rbac.py`

```python
@dataclass(frozen=True)
class ExecutionContext:
    """Quién invoca, con qué rol y en qué hilo.

    Es lo que se inyecta como `ctx` en cada implementación de acción.
    """

    user_id: str
    rol: str
    thread_id: str
    max_classification: int
```

**La regla que sostiene todo:** el agente hereda los permisos **del usuario**,
nunca los de la cuenta de servicio. La cuenta de servicio de la API pasa por
encima de las reglas de Firestore por diseño —el SDK de administración no las
evalúa—, y por eso la API tiene que aplicar los permisos ella misma.

`max_classification` se deriva de la ontología con
`onto.max_classification_for_role(rol)`.

**Verificar:** `pytest tests/governance/test_rbac.py -v`

---

## F4.2 · Tokenización y rehidratación de datos personales

**Produce:** `packages/synapseflow/governance/pii.py`
**Cierra el compromiso 5.**

```python
def detectar_legajos(texto: str) -> list[PIIMatch]:
    """Detector propio para el formato de legajo del dominio.

    PIIMiddleware de LangChain trae email, tarjeta, IP, MAC y URL, pero no
    conoce el formato de legajo de una petrolera. Acepta un `detector`
    propio: acá va.
    """


class Tokenizador:
    """Reemplaza valores sensibles por símbolos estables y los restituye."""

    def tokenizar(self, texto: str) -> tuple[str, MapaDeTokens]: ...
    def rehidratar(self, texto: str, mapa: MapaDeTokens) -> str: ...
```

**Estable es la palabra clave.** El mismo legajo produce siempre el mismo símbolo
dentro de una conversación —`«INSPECTOR_1»`—, para que el modelo pueda razonar
coherentemente sobre «el inspector 1» sin saber quién es. Un token aleatorio por
aparición rompería el razonamiento.

Los campos a proteger **no se listan a mano**: salen de `onto.pii_fields()`, que
ya funciona. Marcar un campo nuevo como `pii` en el YAML lo protege en todas las
rutas sin tocar este módulo.

**Límite conocido y aceptado:** la redacción degrada la calidad cuando el nombre
de la persona era relevante para la respuesta. En este dominio no lo es —al
agente le importa el hallazgo, no quién lo firmó—. Queda documentado en
[ADR-0004](../../adr/0004-gateway-provider-agnostic.md).

**Verificar:** `pytest tests/governance/test_pii.py -v`

---

## F4.3 · Log de auditoría inmutable

**Produce:** `packages/synapseflow/governance/auditoria.py`

```python
async def registrar(evento: EventoAuditoria) -> None: ...
```

Se engancha en `wrap_tool_call`: toda invocación de herramienta queda registrada
sin que la herramienta se entere.

**Qué se guarda, y por qué importa cada campo:**

| Campo | Para qué |
|---|---|
| `user_id`, `rol` | Quién y con qué autoridad |
| `action_id`, argumentos | Qué se ejecutó exactamente |
| `thread_id`, `checkpoint_id` | **Reconstruir el razonamiento**, no solo el hecho |
| `ts` | Cronología |
| `aprobado_por` | Si pasó por un gate |

Los dos del medio son la diferencia entre un log que dice «se pidió una parada» y
uno que permite responder «qué llevó a pedirla». Un auditor pregunta lo segundo.

Las reglas de Firestore ya declaran esta colección como inmutable, incluso para
la propia aplicación: se agrega, no se modifica.

**Verificar:** `pytest tests/governance/test_auditoria.py -v`

---

## F4.4 · Política zero-training

**Produce:** `packages/synapseflow/governance/politica.py`

```python
def exigir_zero_training(proveedor: Provider) -> None:
    """Falla si el proveedor activo no garantiza no-entrenamiento.

    Falla en vez de degradar en silencio: una política de datos que se
    incumple sin aviso es peor que no tenerla.
    """
```

Lee la bandera del proveedor en `models.yaml`. **Leé también el
`zero_training_note` de Gemini**: la API de pago no entrena con los datos, pero
el tier gratuito sí. Si el proyecto no tiene facturación habilitada, la política
se está incumpliendo aunque la bandera diga lo contrario. Es exactamente el tipo
de detalle que en un cliente regulado se verifica en el contrato, no en la
documentación.

**Verificar:** `pytest tests/governance/test_politica.py -v`

---

## F4.5 · Ensamblado del pipeline

**Produce:** `packages/synapseflow/governance/middleware.py`

```python
def construir_middleware(onto: Ontology, ctx: ExecutionContext) -> list[AgentMiddleware]:
    """El pipeline completo, en orden."""
```

Composición sugerida:

| Middleware | Origen | Función |
|---|---|---|
| `PIIMiddleware` | LangChain, con detector propio | Redacción antes de salir |
| `AuditoriaMiddleware` | propio | Registro en `wrap_tool_call` |
| `CostoMiddleware` | propio, usa F1.4 | Contabilidad en `wrap_model_call` |
| `HumanInTheLoopMiddleware` | LangChain | Gates. **Se configura en F5**, no acá |
| `ModelCallLimitMiddleware` | LangChain | Techo por turno |

**El orden importa:** la redacción tiene que correr antes que cualquier cosa que
envíe texto al modelo.

`ModelCallLimitMiddleware` no es un lujo. Un agente que entra en un ciclo de
herramientas sin converger es el modo de falla **más caro** que existe: se corta
ahí y no en la factura. El techo sale de `settings.max_model_calls_per_run`.

**Verificar:** `pytest tests/governance/test_middleware.py -v`

---

## F4.6 · Test de la frontera de punta a punta

**Produce:** `tests/governance/test_frontera_datos.py`

```python
def test_un_campo_restricted_nunca_llega_al_proveedor():
    """La garantía del compromiso 5, comprobada.

    Se arma un payload con un legajo real, se ejecuta el pipeline completo
    contra FakeChatModel, y se inspecciona qué recibió el modelo.
    """
```

Este test más el estructural de F1.5 —que verifica que no hay otro camino de
salida— son los dos que juntos hacen la garantía verificable. Uno solo no alcanza:
el estructural prueba que hay un único camino, este prueba que ese camino redacta.

**Verificar:** `pytest tests/governance -v`

---

## Al cerrar F4

- El compromiso 5 pasa a ✅.
- Actualizar ambos READMEs y el `CHANGELOG.md`.
- Si el ensamblado tomó decisiones no obvias sobre el orden del pipeline, escribir
  un ADR.
