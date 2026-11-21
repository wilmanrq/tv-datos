#!/usr/bin/env python3
"""Procesa tu respuesta al correo del reporte semanal (comentario del Issue): agregar N / descartar N / quitar N.
Lee el texto de la variable COMMENT. Escribe reply.md (la confirmación que se publica como comentario)."""
import json, os, re, time, urllib.request

try:
    import verificar as V          # listas que ya trae la app de fábrica
    BUILTIN = [u for g in V.LISTS for u in g]
except Exception:
    BUILTIN = []


def norm(u):
    """Misma dirección escrita distinto (mayúsculas en el dominio, barra final, http/https, github.com/blob) = la misma."""
    u = u.strip().split("#")[0].rstrip("/")
    u = re.sub(r"^https?://", "https://", u, flags=re.I)
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", u, re.I)
    if m:
        u = "https://raw.githubusercontent.com/%s/%s/%s" % m.groups()
    m = re.match(r"^(https://[^/]+)(.*)$", u)
    return (m.group(1).lower() + m.group(2)) if m else u.lower()


def first(u):
    return u if isinstance(u, str) else u[0]

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


LISTA = re.compile(r"^\s*(?:mi\s+|agrega(?:r)?\s+|a[ñn]ad(?:e|ir)\s+)?lista(!?)\s+<?(https?://[^\s>`]+)>?\s*$", re.I)


def read_url(u):
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=25).read(3_000_000).decode("utf-8", "ignore")
    except Exception:
        return ""


def parse(text):
    out = []
    for line in text.replace("\r", "").split("\n"):
        if line.lstrip().startswith(">"):
            continue                                  # texto citado del correo original
        ml = LISTA.match(line.strip("`* "))
        if ml:
            out.append(("lista", (ml.group(2), bool(ml.group(1)))))
            continue
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
                out.append("- ✔️ **Lista n.º %d: %s** (ver el comentario de abajo)" % (i, done[i]))
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
        if kind == "lista":
            url, force = ids
            nu = norm(url)
            have = [s for s in cfg.get("sources", []) if any(norm(x) == nu for x in ([s["u"]] if isinstance(s["u"], str) else s["u"]))]
            if have:
                quien = "la agregaste tú" if have[0].get("user") else "la agregó el vigilante"
                reply.append("- ⚠️ **Lista repetida, no la agregué:** ya está en GitHub (n.º %s, %s): %s" % (have[0].get("id", "?"), quien, url))
                continue
            pe = [p for p in pend["items"] if norm(p["url"]) == nu]
            if pe:
                reply.append("- ⚠️ **Lista repetida, no la agregué:** ya está esperando tu decisión como n.º %d. Márcala con «Agregar» (o escribe `agregar %d`): %s" % (pe[0]["id"], pe[0]["id"], url))
                continue
            if any(norm(x) == nu for x in BUILTIN):
                reply.append("- ⚠️ **Lista repetida, no la agregué:** esa lista ya viene incluida de fábrica en la app: %s" % url)
                continue
            t = read_url(url)
            n = len(re.findall(r"(?im)^#EXTINF", t))
            if not force and n < 3:
                reply.append("- ⚠️ **No agregué** %s: no pude leerla o no trae canales (%d entradas). Revisa que la dirección esté bien escrita (un error de tecleo es lo más común). Si estás seguro de que es correcta, escribe `lista! %s` para agregarla igual." % (url, n, url))
                continue
            nid = pend["next"]; pend["next"] += 1
            cfg.setdefault("sources", []).append({"u": url, "mode": "int", "user": True, "id": nid, "desde": now_t})
            reply.append("- ✅ **Agregada tu lista n.º %d** (%d entradas leídas): %s  \n  Llegará a todos tus equipos como si la hubieras puesto a mano (necesitan la app 5.17 o más nueva). Para quitarla: marca «Quitar» en el reporte o escribe `quitar %d`." % (nid, n, url, nid))
            done[nid] = "agregada"
            changed = True
            continue
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
                    reply.append("- ✅ **Agregada la lista n.º %d «%s»**: %s  \n  Llegará a los equipos al abrir la app o en unas horas (hasta 6 h). Para quitarla: en el próximo reporte tendrá una casilla «Quitar» (o escribe `quitar %d` en un comentario)." % (p["id"], name, p["url"], p["id"]))
                else:
                    desc.append(p["url"])
                    done[p["id"]] = "descartada"
                    reply.append("- 🗑️ Descartada la lista n.º %d «%s». No volverá a salir." % (p["id"], name))
                changed = True
        else:
            src = cfg.get("sources", [])
            sel = [s for s in src if (s.get("auto") or s.get("user")) and (ids == "todas" or s.get("id") in ids)]
            if not sel and lines is None:
                reply.append("- `quitar`: no encontré una lista del vigilante con ese número (las que pusiste tú a mano se quitan editando `config.json`).")
            for s in sel:
                src.remove(s)
                done[s.get("id", 0)] = "quitada"
                if s.get("auto"):
                    desc.append(s["u"] if isinstance(s["u"], str) else s["u"][0])
                reply.append("- ✅ **Quitada la lista n.º %s**: %s. Los equipos dejarán de usarla en unas horas." % (s.get("id", "?"), s["u"]))
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
