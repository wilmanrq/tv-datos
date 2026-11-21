#!/usr/bin/env python3
"""
Vigilante semanal de TV Ecuador (solo biblioteca estándar).
1) SALUD: revisa cada lista que usa la app (y las de config.json): ¿responde?, ¿cuántas entradas?, ¿cuántas transmisiones buenas ve la nube?
   Compara con la semana anterior y avisa si una lista se cayó o se vació.
2) NUEVAS LISTAS: busca en GitHub listas M3U en español que aún no usamos, las evalúa (entradas nuevas, transmisiones vivas,
   contenido para adultos) y las PROPONE. Las propone y espera tu respuesta.
Cada semana publica un Issue (GitHub te avisa por correo): con alertas, con listas pendientes o diciendo que no hay novedades.
PENDIENTES: las candidatas se acumulan (máx. 3) en pendientes.json hasta que el dueño responda al correo con "agregar N" / "descartar N" (lo procesa responder.py).
Modo automático OPCIONAL (config.json "auto": true; por defecto false): agrega sola 1 por semana. En ambos casos quita las agregadas por el vigilante que llevan 3 semanas caídas.
Cualquier fallo aquí NO afecta a verificar.py ni a la app.
"""
import json, os, re, sys, time, random, urllib.request, urllib.parse
import verificar as v

TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADULT = re.compile(r"xxx|adult|porn|porno|sex|erotic|erotica|18\+|hentai|playboy|brazzers|camgirl|\bhot\b|nude|desnud", re.I)
QUERIES = ["iptv m3u spanish", "iptv latino m3u", "iptv ecuador m3u", "lista iptv español m3u8", "iptv latinoamerica tv gratis"]
EXCLUDE_OWNERS = {"iptv-org", "free-tv", "iamjony", "velfin13", "charlieii", "teleonline", "dzh0ni", "jmigue85"}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def api(path):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "tv-datos-vigilante"}
    if TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    try:
        with urllib.request.urlopen(urllib.request.Request("https://api.github.com" + path, headers=h), timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("API falló:", path[:80], e, flush=True)
        return None


def good_keys():
    d = load("verified.json", {})
    return set(d.get("g", []))


def known_lists():
    ls = [list(u) for u in v.LISTS]
    for s in load("config.json", {}).get("sources", []):
        u = s.get("u")
        ls.append(u if isinstance(u, list) else [u])
    return ls


def read_list(urls):
    for u in urls:
        t = v.text_of(u)
        if t:
            return t
    return ""


def health(good):
    out, known_urls = {}, set()
    for urls in known_lists():
        t = read_list(urls)
        items = v.parse(t) if t else []
        for u, _ in items:
            known_urls.add(u)
        out[urls[0]] = {"ok": bool(t), "n": len(items), "g": sum(1 for u, _ in items if v.key(u) in good)}
        print("%-75s ok=%s n=%d buenas=%d" % (urls[0][-75:], bool(t), len(items), out[urls[0]]["g"]), flush=True)
    return out, known_urls


def alerts(now, prev):
    msgs = []
    for o, c in now.items():
        p = prev.get(o)
        name = o.replace("https://", "")[:90]
        if not c["ok"]:
            if p and not p["ok"]:
                msgs.append("🔴 SIGUE CAÍDA: " + name)
            else:
                msgs.append("🔴 SE CAYÓ (no responde): " + name)
        elif p and p["ok"]:
            if p["n"] >= 20 and c["n"] < p["n"] * 0.5:
                msgs.append("🟠 SE VACIÓ: %s (de %d a %d entradas)" % (name, p["n"], c["n"]))
            elif p["g"] >= 5 and c["g"] == 0:
                msgs.append("🟠 YA NO TIENE TRANSMISIONES BUENAS (la nube veía %d): %s" % (p["g"], name))
    return msgs


def evaluate(url, text, known_urls):
    items = v.parse(text)
    if len(items) < 20:
        return None
    new = [(u, h) for u, h in items if u not in known_urls]
    if len(new) < 15:
        return None
    # recuento de contenido para adultos (nombre o grupo) y de español (grupo/nombre con país o idioma)
    names = text.splitlines()
    adult = sum(1 for l in names if l.upper().startswith("#EXTINF") and ADULT.search(l))
    es = sum(1 for l in names if l.upper().startswith("#EXTINF") and v.SPG.search(l))
    n_ext = max(1, sum(1 for l in names if l.upper().startswith("#EXTINF")))
    if adult / n_ext > 0.15 or es / n_ext < 0.3:
        return None
    sample = random.sample(new, min(40, len(new)))
    ok = 0
    for u, h in sample:
        try:
            if v.quick(u, h) == 3:
                ok += 1
        except Exception:
            pass
    alive = ok / len(sample)
    if alive < 0.25:
        return None
    return {"url": url, "entradas": len(items), "nuevas": len(new), "vivas_pct": round(alive * 100), "adultos_pct": round(100 * adult / n_ext, 1),
            "estimadas": int(len(new) * alive * (1 - adult / n_ext))}


def discover(known_urls, seen):
    found, tried = [], set()
    for q in QUERIES:
        r = api("/search/repositories?q=" + urllib.parse.quote(q) + "&sort=updated&order=desc&per_page=10")
        for repo in (r or {}).get("items", []):
            full = repo["full_name"]
            if full in tried or repo.get("fork") or repo.get("archived") or repo["owner"]["login"].lower() in EXCLUDE_OWNERS:
                continue
            tried.add(full)
            br = repo.get("default_branch", "main")
            tr = api("/repos/%s/git/trees/%s?recursive=1" % (full, br))
            files = [f for f in (tr or {}).get("tree", []) if f.get("type") == "blob" and re.search(r"\.m3u8?$", f["path"], re.I) and f.get("size", 0) < 6_000_000][:3]
            for f in files:
                raw = "https://raw.githubusercontent.com/%s/%s/%s" % (full, br, urllib.parse.quote(f["path"]))
                if raw in seen:
                    continue
                t = v.text_of(raw)
                ev = evaluate(raw, t, known_urls) if t else None
                if ev:
                    ev["repo"] = full
                    ev["actualizado"] = repo.get("pushed_at", "")[:10]
                    found.append(ev)
                seen.add(raw)
    found.sort(key=lambda e: -e["estimadas"])
    return found[:5]


MAXPEND = 3


def hrefs(c):
    return c["url"]


def prune_dead(cfg, hist, notes):
    """Quita las listas agregadas por el vigilante (marca "auto") que llevan 3 revisiones seguidas sin responder."""
    runs = hist["runs"][-3:]
    keep, changed = [], False
    for s in cfg.get("sources", []):
        if s.get("auto") and len(runs) == 3:
            o = s["u"] if isinstance(s["u"], str) else s["u"][0]
            if all(o in r["lists"] and not r["lists"][o]["ok"] for r in runs):
                notes.append("- 🗑️ **Quitada automáticamente** (3 semanas sin responder): " + o)
                changed = True
                continue
        keep.append(s)
    cfg["sources"] = keep
    return changed


def add_source(cfg, c, now_t):
    cfg.setdefault("sources", []).append({"u": c["url"], "mode": "int", "auto": True, "id": c["id"], "desde": now_t})


def main():
    now_t = time.strftime("%Y-%m-%d", time.gmtime())
    good = good_keys()
    hist = load("salud.json", {"runs": []})
    prev = hist["runs"][-1]["lists"] if hist["runs"] else {}
    cur, known_urls = health(good)
    al = alerts(cur, prev)
    seen = set(load("candidatos_vistos.json", []))
    pend = load("pendientes.json", {"next": 1, "items": []})
    descartadas = set(load("descartadas.json", []))
    cfg = load("config.json", {"auto": False, "sources": [], "block": []})
    cfg_changed, notes = False, []

    # 1) revalidar las pendientes de semanas anteriores (se descartan solas si dejan de servir 2 veces seguidas)
    alive = []
    for p in pend["items"]:
        t = v.text_of(p["url"])
        ev = evaluate(p["url"], t, known_urls) if t else None
        if ev:
            p.update({k: ev[k] for k in ("entradas", "nuevas", "vivas_pct", "adultos_pct", "estimadas")})
            p["fallos"] = 0
            alive.append(p)
        else:
            p["fallos"] = p.get("fallos", 0) + 1
            if p["fallos"] < 2:
                alive.append(p)
            else:
                notes.append("- ⚪ Pendiente #%d retirada (ya no cumple las condiciones): %s" % (p["id"], p["url"]))
    pend["items"] = alive

    # 2) buscar nuevas y acumularlas (máx. 3 pendientes; las que no caben se vuelven a evaluar otra semana)
    cands = []
    try:
        cands = discover(known_urls, seen)
    except Exception as e:
        print("Descubrimiento falló:", e, flush=True)
    have = {p["url"] for p in pend["items"]}
    for c in cands:
        if c["url"] in descartadas or c["url"] in have:
            continue
        if len(pend["items"]) >= MAXPEND:
            seen.discard(c["url"])
            continue
        c["id"] = pend["next"]; pend["next"] += 1; c["desde"] = now_t; c["fallos"] = 0
        pend["items"].append(c)
        have.add(c["url"])

    # 3) lo que se quita solo
    if prune_dead(cfg, hist, notes):
        cfg_changed = True

    # 4) modo automático opcional: agrega la mejor pendiente (máx. 1 por semana)
    if cfg.get("auto") is True and pend["items"]:
        best = max(pend["items"], key=lambda p: p["estimadas"])
        add_source(cfg, best, now_t)
        pend["items"].remove(best)
        notes.append("- ➕ **Se agregó la lista «%s»** (modo automático)  \n  Enlace: %s  \n  ≈%d canales nuevos útiles. Para quitarla: marca «Quitar» en el reporte." % (best["repo"].split("/")[-1], best["url"], best["estimadas"], best["id"]))
        cfg_changed = True

    hist["runs"] = (hist["runs"] + [{"fecha": now_t, "lists": cur}])[-12:]
    save("salud.json", hist)
    save("pendientes.json", pend)
    save("candidatos_vistos.json", sorted(seen))
    if cfg_changed:
        save("config.json", cfg)

    md = ["# Reporte semanal (%s)\n" % now_t]
    if pend["items"]:
        md.append("## 📋 Listas nuevas esperando tu decisión\n")
        md.append("**Cómo decidir:** 1) copia el enlace con el botón de copiar (arriba a la derecha del recuadro) y pruébalo si quieres; "
                  "2) **marca UNA casilla** de la lista: ✅ Agregar o 🗑️ Descartar. En unos 30 segundos aparece la confirmación aquí abajo. "
                  "Si no decides, **se quedan acumuladas** y las reviso cada semana (máximo %d).\n" % MAXPEND)
        for p in pend["items"]:
            md.append("### Lista #%d · %s  _(desde %s)_\n" % (p["id"], p["repo"].split("/")[-1], p["desde"]))
            md.append("```\n%s\n```" % p["url"])
            md.append("Repositorio `%s` (actualizado %s) · %d entradas · **%d nuevas** · ~%d%% vivas · adultos %s%% (la app los descarta) · ≈**%d canales nuevos útiles**\n"
                      % (p["repo"], p["actualizado"], p["entradas"], p["nuevas"], p["vivas_pct"], p["adultos_pct"], p["estimadas"]))
            md.append("- [ ] ✅ **Agregar** la lista #%d <!--a%d-->" % (p["id"], p["id"]))
            md.append("- [ ] 🗑️ **Descartar** la lista #%d <!--d%d-->\n" % (p["id"], p["id"]))
        md.append("Antes de agregar, confirma que sean canales **gratuitos y legales**.\n")
    else:
        md.append("## 📋 Listas nuevas\n\n- Ninguna pendiente esta semana.\n")
    if notes:
        md.append("## Cambios hechos\n")
        md += notes
        md.append("")
    mine = [s for s in cfg.get("sources", []) if s.get("auto")]
    if mine:
        md.append("## Listas que agregó el vigilante (marca la casilla para quitar una)\n")
        for s in mine:
            md.append("- [ ] 🗑️ **Quitar** la lista #%s: %s (desde %s) <!--q%s-->" % (s.get("id", "?"), s["u"] if isinstance(s["u"], str) else s["u"][0], s.get("desde", ""), s.get("id", 0)))
        md.append("")
    md.append("## Alertas\n")
    md += ["- " + a for a in al] or ["- Ninguna: todo igual que la semana pasada."]
    md += ["", "## Salud de las listas\n", "| Lista | Responde | Entradas | Buenas (nube) |", "|---|---|---|---|"]
    for o, c in cur.items():
        md.append("| %s | %s | %d | %d |" % (o.replace("https://", "")[:80], "sí" if c["ok"] else "**NO**", c["n"], c["g"]))
    md.append("\n_Quitar una lista a mano, siempre posible: en `config.json` borra su bloque `{ \"u\": ... }` y guarda._")
    with open("reporte_semanal.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open("issue.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    partes = ["%d alerta(s)" % len(al) if al else "todo bien"]
    partes.append("%d lista(s) esperando tu decisión" % len(pend["items"]) if pend["items"] else "sin listas nuevas")
    if any("Quitada" in n for n in notes):
        partes.append("se quitó una lista caída")
    if any("Se agregó" in n for n in notes):
        partes.append("se agregó una lista")
    with open("issue_title.txt", "w", encoding="utf-8") as f:
        f.write("Reporte semanal %s: %s" % (now_t, " · ".join(partes)))
    print("Listo. Alertas:", len(al), "Pendientes:", len(pend["items"]), flush=True)


if __name__ == "__main__":
    main()
