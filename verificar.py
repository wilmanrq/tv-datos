#!/usr/bin/env python3
"""
Verificador en la nube de TV Ecuador (solo biblioteca estándar de Python).
Lee las mismas listas públicas que usa la app, prueba cada transmisión (lista HLS + primer segmento: video, audio, relleno, bucle)
y publica verified.json con las direcciones que funcionan DESDE ESTE SERVIDOR.
La app lo usa solo como pista: confirma en el equipo con una petición ligera y sigue verificando sola lo que la nube no ve
(canales con bloqueo regional, que desde EE. UU. aparecen caídos aunque en Ecuador funcionen).
"""
import sys, re, json, time, hashlib, zlib, urllib.request, urllib.error, urllib.parse, ssl
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
HUB = "https://iptv-org.github.io/iptv/"
JSD = "https://cdn.jsdelivr.net/gh/iptv-org/iptv@master/streams/"
RAW = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/"
CCS = ["mx", "co", "ar", "es", "pe", "cl", "ve", "uy", "py", "bo", "pa", "cr", "gt", "hn", "sv", "ni", "do", "pr", "cu"]
FAST_GH = ["mx_pluto", "mx_samsung"]
FAST_US = ["us_pluto", "us_samsung", "us_tubi", "us_xumo", "us_roku", "us_plex", "us_stirr", "us_vizio"]
LISTS = [[HUB + "countries/ec.m3u", JSD + "ec.m3u"],
         ["https://raw.githubusercontent.com/velfin13/tv-ecuador-m3u/main/playlist.m3u"],
         ["https://www.m3u.cl/lista/EC.m3u"],
         [HUB + "languages/spa.m3u"]]
LISTS += [[RAW + n + ".m3u", JSD + n + ".m3u"] for n in FAST_GH + FAST_US]
LISTS += [["https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"]]
LISTS += [[HUB + "countries/%s.m3u" % cc, JSD + "%s.m3u" % cc] for cc in CCS]
LISTS += [["https://raw.githubusercontent.com/IamJony/IPTVListaLibre/refs/heads/main/es_unificado.m3u"],
          ["https://teleonline.github.io/listas/tv.m3u8"],
          ["https://raw.githubusercontent.com/CharlieII/IPTV_mexico/refs/heads/main/lista.m3u"]]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE        # igual que la app: muchos servidores de TV tienen certificados mal puestos


def http(url, headers=None, maxb=600_000, timeout=10):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            ct = r.headers.get("Content-Type", "") or ""
            if ct.lower().startswith(("video/", "audio/")) and "mpegurl" not in ct.lower():
                return r.status, ct, b"", r.geturl()
            return r.status, ct, r.read(maxb), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, "", b"", url
    except Exception:
        return 0, "", b"", url


def text_of(url):
    c, _, b, _ = http(url, maxb=30_000_000, timeout=30)
    return b.decode("utf-8", "ignore") if c == 200 else ""


def parse(t):
    out = []
    hd = {}
    for ln in t.splitlines():
        ln = ln.strip().lstrip("﻿")
        if not ln:
            continue
        if ln.upper().startswith("#EXTINF"):
            hd = {}
        elif ln.upper().startswith("#EXTVLCOPT:"):
            k, _, v = ln.split(":", 1)[1].partition("=")
            k = k.lower()
            if k in ("http-user-agent",):
                hd["User-Agent"] = v.strip()
            elif k in ("http-referrer", "http-referer"):
                hd["Referer"] = v.strip()
            elif k == "http-origin":
                hd["Origin"] = v.strip()
        elif not ln.startswith("#") and ln.lower().startswith("http"):
            out.append((ln, dict(hd)))
            hd = {}
    return out


DRM = re.compile(r'#EXT-X-(SESSION-)?KEY:[^\n]*(METHOD=SAMPLE-AES|KEYFORMAT="(com\.apple\.streamingkeydelivery|urn:uuid:[^"]*|com\.microsoft\.playready)")', re.I)
VCODEC = re.compile(r"avc1|avc3|hvc1|hev1|av01|vp09|mp4v")
CODECS = re.compile(r'CODECS="([^"]*)"')
BW = re.compile(r"BANDWIDTH=(\d+)")
MAPRX = re.compile(r'#EXT-X-MAP:[^\n]*URI="([^"]*)"')


def is_media(ct):
    ct = ct.lower()
    return (ct.startswith("video/") or ct.startswith("audio/")) and "mpegurl" not in ct


def quick(url, h):
    c, ct, b, _ = http(url, h, 400_000, 8)
    if c != 200:
        return 0
    if is_media(ct):
        return 2 if ct.lower().startswith("audio/") else 3
    t = b.decode("utf-8", "ignore").lstrip()
    if not t.startswith("#EXTM3U") or DRM.search(t):
        return 0
    if "#EXT-X-STREAM-INF" in t:
        infos = [l for l in t.splitlines() if l.startswith("#EXT-X-STREAM-INF")]
        audio_only = all(("RESOLUTION=" not in i and (CODECS.search(i) and not VCODEC.search(CODECS.search(i).group(1)))) for i in infos)
        return 2 if audio_only else 3
    return 3 if "#EXTINF" in t else 0


def fingerprint(d):
    sync = -1
    for i in range(0, max(0, min(188, len(d) - 376))):
        if d[i] == 0x47 and d[i + 188] == 0x47 and d[i + 376] == 0x47:
            sync = i
            break
    if sync >= 0:
        es = bytearray()
        vpid = -1
        p = sync
        while p + 188 <= len(d) and len(es) < 1400:
            if d[p] != 0x47:
                break
            pusi = (d[p + 1] & 0x40) != 0
            pid = ((d[p + 1] & 0x1F) << 8) | d[p + 2]
            afc = (d[p + 3] >> 4) & 3
            off = 4
            if afc & 2:
                off += 1 + d[p + 4]
            if (afc & 1) and off < 188:
                if vpid < 0:
                    if pusi and off + 9 < 188 and d[p + off] == 0 and d[p + off + 1] == 0 and d[p + off + 2] == 1 and (d[p + off + 3] & 0xF0) == 0xE0:
                        vpid = pid
                        start = off + 9 + d[p + off + 8]
                        if start < 188:
                            es += d[p + start:p + 188]
                elif pid == vpid:
                    if pusi:
                        break
                    es += d[p + off:p + 188]
            p += 188
        if vpid < 0 or len(es) < 600:
            return None
        return zlib.crc32(bytes(es[:600]))
    i = d[:4096].find(b"mdat")
    if i >= 0 and len(d) >= i + 4 + 600:
        return zlib.crc32(d[i + 4:i + 604])
    return None


def deep(url, h):
    """0 falla · 1 video · 2 solo audio · 5 relleno o bucle"""
    c, ct, b, final = http(url, h, 600_000, 12)
    if c != 200:
        return 0
    if is_media(ct):
        return 2 if ct.lower().startswith("audio/") else 1
    t = b.decode("utf-8", "ignore")
    if not t.lstrip().startswith("#EXTM3U") or DRM.search(t):
        return 0
    base = final or url
    if "#EXT-X-STREAM-INF" in t:
        ls = t.splitlines()
        best, bw = None, 1 << 62
        for i, l in enumerate(ls):
            if not l.startswith("#EXT-X-STREAM-INF"):
                continue
            cm = CODECS.search(l)
            is_video = "RESOLUTION=" in l or (cm and VCODEC.search(cm.group(1)))
            m = BW.search(l)
            w = int(m.group(1)) if m else (1 << 62) - 1
            u = next((x for x in ls[i + 1:] if x.strip() and not x.startswith("#")), None)
            if u and is_video and w < bw:
                bw, best = w, u
        if best is None:
            return 0
        base = urllib.parse.urljoin(base, best.strip())
        c, ct, b, final = http(base, h, 600_000, 12)
        if c != 200:
            return 0
        base = final or base
        t = b.decode("utf-8", "ignore")
        if not t.lstrip().startswith("#EXTM3U") or DRM.search(t):
            return 0
    segs, durs, last = [], [], 0.0
    for l0 in t.splitlines():
        l = l0.strip()
        if l.startswith("#EXTINF:"):
            try:
                last = float(l.split(":", 1)[1].split(",")[0].strip())
            except Exception:
                last = 0.0
        elif l and not l.startswith("#"):
            segs.append(l)
            durs.append(last)
            last = 0.0
    if not segs:
        return 0
    if len(segs) >= 4 and len(set(segs)) * 2 <= len(segs):
        return 5
    d, dur, chosen = None, 0.0, 0
    for ci in dict.fromkeys([max(0, len(segs) - 2), len(segs) - 1, 0]):
        seg = urllib.parse.urljoin(base, segs[ci])
        if ".aac" in seg.lower() or ".mp3" in seg.lower():
            return 2
        cc, _, dd, _ = http(seg, h, 400_000, 12)
        if cc == 200 and dd:
            d, dur, chosen = dd, durs[ci], ci
            break
    if d is None or len(d) < 188:
        return 0
    low = len(d) < 390_000 and dur >= 2.0 and len(d) * 8.0 / dur < 60_000
    good = 5 if low else 1
    if good == 1 and len(segs) >= 4 and "METHOD=AES-128" not in t:
        f0 = fingerprint(d)
        if f0 is not None:
            seen = {f0}
            picks = [x for x in dict.fromkeys([0, len(segs) // 3, (2 * len(segs)) // 3]) if x != chosen and segs[x] != segs[chosen]]
            for ix in picks:
                hh = dict(h); hh["Range"] = "bytes=0-24575"
                cc, _, o, _ = http(urllib.parse.urljoin(base, segs[ix]), hh, 24_576, 12)
                if cc not in (200, 206) or not o:
                    continue
                f = fingerprint(o)
                if f is None:
                    continue
                if f in seen:
                    return 5
                seen.add(f)
    if "METHOD=AES-128" in t:
        return good
    head = d[:64]
    if any(x in head for x in (b"ftyp", b"moof", b"styp", b"moov")):
        m = MAPRX.search(t)
        if m:
            cc, _, ib, _ = http(urllib.parse.urljoin(base, m.group(1)), h, 65_536, 12)
            if cc in (200, 206) and ib and b"vide" not in ib and b"soun" in ib:
                return 2
        return good
    sync = -1
    for i in range(0, max(0, min(188, len(d) - 376))):
        if d[i] == 0x47 and d[i + 188] == 0x47 and d[i + 376] == 0x47:
            sync = i
            break
    if sync < 0:
        s = d[:16].lstrip()
        return 0 if s[:1] in (b"<", b"{") else good
    audio = False
    j = 0
    n = len(d) - 4
    while j < n:
        if d[j] == 0 and d[j + 1] == 0 and d[j + 2] == 1:
            sid = d[j + 3]
            if (sid & 0xF0) == 0xE0:
                return good
            if (sid & 0xE0) == 0xC0:
                audio = True
            j += 3
        else:
            j += 1
    return 2 if audio else good


def check(url, h):
    try:
        if quick(url, h) != 3:
            return False
        return deep(url, h) == 1
    except Exception:
        return False


def key(u):
    return hashlib.md5(u.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------------------------------------------
# Copias filtradas de listas grandes (la app las lee en vez de la original; si algo falla, lee la original).
# SOLO se quitan entradas que la app descarta igual, con las mismas reglas que usa la app:
#   · listas "sp" (Free-TV, es_unificado): grupo sin ningún país/idioma de habla hispana  (D.SPG)
#   · listas us_*: nombre sin marca clara de español                                        (D.skipUs + T.spanishHint)
# Cualquier duda = la entrada se CONSERVA. La app vuelve a aplicar todos sus filtros de todas formas.
# ---------------------------------------------------------------------------------------------------------------
import os, unicodedata, html

SPG = re.compile(r"cine|mexico|spain|espa|colombia|argentina|chile|peru|venezuela|uruguay|paraguay|bolivia|panama|costa rica|guatemala|honduras|salvador|nicaragua|dominican|puerto rico|cuba|ecuador|latin|spanish|hispan", re.I)
ES_HINT = r"\b(espanol|latino|latina|latinos|telemundo|todo cine|cine (adrenalina|amor|aventura|comedia|premiere|estrella|romantico|en|accion|familiar|clasico|mexicano))\b"
ES_U = re.compile(ES_HINT)
ES_A = re.compile(ES_HINT, re.A)
GROUP_RX = re.compile(r'group-title="([^"]*)"')

FREETV = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"
JONY = "https://raw.githubusercontent.com/IamJony/IPTVListaLibre/refs/heads/main/es_unificado.m3u"
ECLIST = HUB + "countries/ec.m3u"
# la lista de Ecuador va completa pero sin la etiqueta [Geo-blocked]: en un equipo con la región del sistema fuera de Ecuador (muchas TV) la app mandaba esas fuentes al final
FILTERED = [(FREETV, "sp"), (JONY, "sp"), (ECLIST, "ungeo")] + [(RAW + n + ".m3u", "us") for n in FAST_US]
GEO_TAG = re.compile(r"\s*\[\s*geo-?blocked\s*\]", re.I)


def plain(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def spanish_hint(name):
    for n in {name, html.unescape(name)}:
        p = plain(n)
        if ES_U.search(p) or ES_A.search(p):
            return True
    return False


def ext_name(ln):
    cut, q = -1, False
    for i, ch in enumerate(ln):
        if ch == '"':
            q = not q
        elif ch == "," and not q:
            cut = i
            break
    return (ln[cut + 1:] if cut >= 0 else ln.rsplit(",", 1)[-1]).strip()


def split_entries(text):
    """(cabecera, [bloques]) ; cada bloque = líneas desde #EXTINF hasta su dirección (o hasta el siguiente #EXTINF)."""
    head, blocks, cur = [], [], None
    for ln in text.splitlines():
        t = ln.strip().lstrip("\ufeff")
        if t.upper().startswith("#EXTINF"):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        elif cur is None:
            head.append(ln)
        else:
            cur.append(ln)
            if t and not t.startswith("#"):
                blocks.append(cur)
                cur = None
    if cur is not None:
        blocks.append(cur)
    return head, blocks


def drop_entry(block, kind):
    ext = block[0].strip().lstrip("\ufeff")
    has_url = any(l.strip() and not l.strip().startswith("#") for l in block[1:])
    if not has_url:
        return False                                  # entrada rara: se conserva
    if kind == "sp":
        m = GROUP_RX.search(ext)
        g = m.group(1) if m else ""
        if not g:
            for l in block[1:]:
                if l.strip().startswith("#EXTGRP:"):
                    g = l.strip().split(":", 1)[1].strip()
                    break
        return not SPG.search(g)
    name = ext_name(ext)
    if not name:
        return False                                  # sin nombre: se conserva
    return not spanish_hint(name)


def filter_text(text, kind):
    head, blocks = split_entries(text)
    if kind == "ungeo":
        kept = [[GEO_TAG.sub("", b[0])] + list(b[1:]) for b in blocks]      # no se descarta nada: solo se quita la etiqueta
        return head, kept, len(blocks)
    kept = [b for b in blocks if not drop_entry(b, kind)]
    return head, kept, len(blocks)


def build_lists(outdir="l"):
    os.makedirs(outdir, exist_ok=True)
    keys = []
    for origin, kind in FILTERED:
        t = text_of(origin)
        if not t:
            print("lista no leída (se omite):", origin[-50:], flush=True)
            continue
        head, kept, total = filter_text(t, kind)
        if total < 5 or not kept:
            print("lista sospechosa (se omite):", origin[-50:], total, len(kept), flush=True)
            continue
        first = next((h for h in head if h.strip().lstrip("\ufeff").upper().startswith("#EXTM3U")), "#EXTM3U").strip().lstrip("\ufeff")
        lines = [first, "#CLOUD t=%d kept=%d of=%d" % (int(time.time()), len(kept), total)]
        for b in kept:
            lines.extend(x.rstrip("\r\n") for x in b)
        with open(os.path.join(outdir, key(origin) + ".m3u"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        keys.append(key(origin))
        print("%-70s %5d de %5d entradas" % (origin[-70:], len(kept), total), flush=True)
    return keys


def main():
    args = sys.argv[1:]
    lists = [[a] for a in args] if args else list(LISTS)
    if not args:                                        # las listas que pusiste tú en config.json (y las del vigilante) también se verifican
        try:
            with open("config.json", encoding="utf-8") as f:
                for s in json.load(f).get("sources", []):
                    u = s.get("u")
                    u = u if isinstance(u, list) else [u]
                    if u and u[0] and u not in lists:
                        lists.append(u)
        except Exception as e:
            print("config.json no se pudo leer (se sigue sin él):", e, flush=True)
    lkeys = build_lists() if not args else []
    items = {}
    for urls in lists:
        t = ""
        for u in urls:
            t = text_of(u) if u.startswith("http") else open(u, encoding="utf-8").read()
            if t:
                break
        n = 0
        for u, h in parse(t):
            if u not in items:
                items[u] = h; n += 1
        print("%-90s %5d nuevas" % (urls[0][:90], n), flush=True)
    print("Direcciones únicas:", len(items), flush=True)
    good = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=48) as ex:
        fut = {ex.submit(check, u, h): u for u, h in items.items()}
        for i, f in enumerate(as_completed(fut), 1):
            if f.result():
                good.append(key(fut[f]))
            if i % 500 == 0:
                print(i, "revisadas,", len(good), "buenas,", int(time.time() - t0), "s", flush=True)
    good = sorted(set(good))
    with open("verified.json", "w", encoding="utf-8") as f:
        json.dump({"v": 2, "t": int(time.time()), "n": len(items), "g": good, "l": lkeys}, f, separators=(",", ":"))
    print("Listo: %d buenas de %d" % (len(good), len(items)))


if __name__ == "__main__":
    main()
