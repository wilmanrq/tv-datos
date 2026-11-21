#!/usr/bin/env python3
"""Procesa tu respuesta al correo del reporte semanal (comentario del Issue): agregar N / descartar N / quitar N.
Lee el texto de la variable COMMENT. Escribe reply.md (la confirmación que se publica como comentario)."""
import json, os, re, time

CMD = re.compile(r"^\s*(agrega(?:r)?|a[ñn]ad(?:e|ir)|descarta(?:r)?|quita(?:r)?)\s+(todas|todos|[\d#][\d#\s,y]*)\s*\.?\s*$", re.I)


def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def save(p, o):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=1)


def parse(text):
    out = []
    for line in text.replace("\r", "").split("\n"):
        if line.lstrip().startswith(">"):
            continue                                  # texto citado del correo original
        m = CMD.match(line.strip("`* "))
        if m:
            verb = m.group(1).lower()
            kind = "agregar" if verb.startswith(("agrega", "a")) else "descartar" if verb.startswith("descarta") else "quitar"
            arg = m.group(2).lower()
            ids = "todas" if arg.startswith("todas") or arg.startswith("todos") else [int(x) for x in re.findall(r"\d+", arg)]
            out.append((kind, ids))
    return out


BOX = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s.*<!--([adq])(\d+)-->\s*$")


def boxes(body):
    """Casillas del Issue: devuelve (órdenes marcadas, líneas del cuerpo, conflictos)."""
    lines = body.replace("\r", "").split("\n")
    marked = {}
    for ln in lines:
        m = BOX.match(ln)
        if m and m.group(1) in "xX":
            marked.setdefault(int(m.group(3)), set()).add(m.group(2))
    cmds = []
    for i, ks in sorted(marked.items()):
        if "a" in ks and "d" in ks:
            continue                                   # marcaste las dos: no hago nada
        k = "a" if "a" in ks else "d" if "d" in ks else "q"
        cmds.append(({"a": "agregar", "d": "descartar", "q": "quitar"}[k], [i]))
    return cmds, lines, {i for i, ks in marked.items() if "a" in ks and "d" in ks}


def rewrite(lines, done, conflicts):
    """Quita las casillas ya procesadas y deja una línea de estado en su lugar."""
    out, seen = [], set()
    for ln in lines:
        m = BOX.match(ln)
        if m and int(m.group(3)) in done and int(m.group(3)) not in conflicts:
            i = int(m.group(3))
            if i not in seen:
                seen.add(i)
                out.append("- ✔️ **#%d: %s** (ver el comentario de abajo)" % (i, done[i]))
            continue
        if m and int(m.group(3)) in conflicts and m.group(1) in "xX":
            ln = ln.replace("[x]", "[ ]").replace("[X]", "[ ]") + "  ⚠️ _marcaste «agregar» y «descartar»: deja solo una_"
        out.append(ln)
    return "\n".join(out)


def main():
    done, conflicts, lines = {}, set(), None
    if os.environ.get("BODY_FILE"):                    # modo casillas
        cmds, lines, conflicts = boxes(open(os.environ["BODY_FILE"], encoding="utf-8").read())
        if not cmds and not conflicts:
            open("reply.md", "w").write("")
            return
    else:                                              # modo comentario
        cmds = parse(os.environ.get("COMMENT", ""))
    reply = []
    if not cmds and not conflicts:
        open("reply.md", "w", encoding="utf-8").write("No encontré ninguna orden. Escribe, en una línea sola: `agregar 1`, `descartar 2` o `quitar 3` (también `agregar todas`).\n")
        return
    now_t = time.strftime("%Y-%m-%d", time.gmtime())
    pend = load("pendientes.json", {"next": 1, "items": []})
    desc = load("descartadas.json", [])
    cfg = load("config.json", {"auto": False, "sources": [], "block": []})
    changed = False
    for kind, ids in cmds:
        if kind in ("agregar", "descartar"):
            sel = list(pend["items"]) if ids == "todas" else [p for p in pend["items"] if p["id"] in ids]
            if not sel and lines is None:
                reply.append("- `%s`: no hay pendientes con ese número (quizá ya la procesaste)." % kind)
            for p in sel:
                pend["items"].remove(p)
                name = p["repo"].split("/")[-1]
                if kind == "agregar":
                    cfg.setdefault("sources", []).append({"u": p["url"], "mode": "int", "auto": True, "id": p["id"], "desde": now_t})
                    done[p["id"]] = "agregada"
                    reply.append("- ✅ **Agregada la lista #%d «%s»**: %s  \n  Llegará a los equipos al abrir la app o en unas horas (hasta 6 h). Para quitarla: en el próximo reporte tendrá una casilla «Quitar» (o escribe `quitar %d` en un comentario)." % (p["id"], name, p["url"], p["id"]))
                else:
                    desc.append(p["url"])
                    done[p["id"]] = "descartada"
                    reply.append("- 🗑️ Descartada la lista #%d «%s». No volverá a salir." % (p["id"], name))
                changed = True
        else:
            src = cfg.get("sources", [])
            sel = [s for s in src if s.get("auto") and (ids == "todas" or s.get("id") in ids)]
            if not sel and lines is None:
                reply.append("- `quitar`: no encontré una lista del vigilante con ese número (las que pusiste tú a mano se quitan editando `config.json`).")
            for s in sel:
                src.remove(s)
                done[s.get("id", 0)] = "quitada"
                desc.append(s["u"] if isinstance(s["u"], str) else s["u"][0])
                reply.append("- ✅ **Quitada la lista #%s**: %s. Los equipos dejarán de usarla en unas horas." % (s.get("id", "?"), s["u"]))
                changed = True
            cfg["sources"] = src
    if changed:
        save("pendientes.json", pend)
        save("descartadas.json", sorted(set(desc)))
        save("config.json", cfg)
    if lines is not None:
        open("new_body.md", "w", encoding="utf-8").write(rewrite(lines, done, conflicts))
        if conflicts:
            reply.append("- ⚠️ Marcaste «agregar» y «descartar» en la misma lista: no hice nada con ella. Deja solo una casilla.")
    open("reply.md", "w", encoding="utf-8").write("\n".join(reply) + "\n")
    print("\n".join(reply))


if __name__ == "__main__":
    main()
