# Dependencia de desarrollo

`pyflakes` es opcional y **sólo para desarrollar**. Habilita el test que verifica que ningún
módulo use un nombre que no existe:

```bash
.venv/bin/pip install pyflakes
```

Sin él, ese test se saltea (no falla). El tutor no lo necesita para usar la skill.

Existe porque un `NameError` en `informe_nexos` llegó a una versión publicada y lo encontró un
tutor con el informe roto adelante: la función necesita red, así que ningún test la tocaba.
