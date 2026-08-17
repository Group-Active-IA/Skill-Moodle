# Panel

La interfaz local de la skill. Chat con el agente y estado de las comisiones
propias, en el navegador, sin dejar de ser la misma herramienta que en la
terminal.

## Usarlo

Decile a Claude **«abrí el panel»**. La tool `abrir_panel` levanta el servidor y
abre el navegador en `http://127.0.0.1:8787`.

A mano:

```bash
.venv/bin/pip install -r panel/requirements.txt   # una sola vez
.venv/bin/python -m panel.backend.app
```

Para cerrarlo: `pkill -f panel.backend.app`.

## Qué necesita

- **Claude Code instalado y con sesión iniciada.** El chat usa el Agent SDK, que
  lanza el CLI del propio tutor: **no hace falta ninguna API key.**
- Las mismas credenciales del campus que ya usa la skill (`~/.moodle-skill/`).
- **No hace falta Node.** El build compilado viaja en el repo; Node sólo se usa
  para desarrollar la interfaz.

## Abrirle tus carpetas (opcional)

Por omisión el panel ve **sólo la skill**. Quien ya tenga su trabajo docente
organizado afuera —fichas de comisión, apuntes, un vault de notas— puede
abrírselas creando `~/.moodle-skill/panel.json`:

```json
{ "carpetas": ["/home/tutor/Proyectos/Tutor-TUPAD"] }
```

Ese archivo vive en la carpeta personal del tutor y **no en el repo**: son rutas
de una máquina, y este repo lo comparten ~25 personas.

Lo que habilita es simétrico: el agente lee y escribe adentro de esas carpetas
sin pedir permiso por cada línea —las abriste vos a propósito— y **se niega a
escribir afuera**. Verificado: un `Write` a `/tmp` es rechazado con el motivo a
la vista.

Queda un hueco declarado: el gate cubre las tools de archivo (`Write`, `Edit`,
`NotebookEdit`), no `Bash`. Un comando de shell puede escribir donde el usuario
tenga permiso, igual que en la terminal.

## Cómo está armado

```
panel/
  backend/
    app.py       FastAPI. Sirve la interfaz y expone la API. Escucha SÓLO en 127.0.0.1.
    agente.py    El chat: sesión del Agent SDK + el gate de escritura.
    datos.py     Llama las tools del MCP directo, sin protocolo en el medio.
    dia.py       El relevamiento de las comisiones propias, con caché.
  web/           React + Vite + TypeScript. El `dist/` compilado se commitea.
```

### Las tres decisiones que importan

**Los números los produce el código, no el modelo.** Todo lo que se muestra en
pantalla sale de las tools de la skill: `datos.py` importa `mcp/server.py` y
llama las funciones. `@mcp.tool()` deja la función original intacta, así que no
hay protocolo, ni subproceso, ni lógica duplicada. El panel hereda cada freno y
cada uno de los 139 tests. El agente lee esos números y dice qué mirar; no los
fabrica. Un dato que no se puede reproducir dos veces no se puede validar.

**El gate de escritura lo aplica el harness.** `can_use_tool` corre antes de cada
tool: si escribe en el campus, la corrida se frena hasta que el tutor confirma en
pantalla. El modelo no puede saltearlo aunque quiera, que es la diferencia con
pedírselo por prompt. Además de la lista explícita, cualquier tool que reciba un
parámetro `confirmado` queda frenada sola, sin que nadie tenga que acordarse de
agregarla.

**Un blanco nunca tranquiliza.** El relevamiento del día cuenta aparte las
consultas que fallaron y marca la comisión como degradada. `—` (no se sabe) y `0`
(no hay) se ven distinto, siempre.

## Desarrollo

```bash
cd panel/web
npm install
npm run dev        # interfaz en :5173, con proxy al backend en :8787
npm run build      # regenera dist/ — hay que commitearlo
```

**Si tocás un color, corré el verificador antes de commitear:**

```bash
python3 panel/web/contraste.py
```

Calcula el contraste WCAG de la paleta en claro y en oscuro. Sale con código 1 si
algún par no llega al mínimo que declara. El `DESIGN.md` de esta carpeta dice que
los contrastes se verifican y no se estiman: esto es lo que lo hace cierto.
