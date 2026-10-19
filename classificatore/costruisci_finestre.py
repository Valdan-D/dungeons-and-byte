#!/usr/bin/env python3
"""Costruisce le finestre di estrazione di UN LoRA a partire dagli intervalli etichettati (etichette/etichette.json) di un libro.
Uso: costruisci_finestre.py PROGETTO TIPOLOGIA [--margine N] [--max-tok N] [--out FILE]
TIPOLOGIA = nome del LoRA: bestiario_new | bestiario_old | oggetto | regola | incantesimo | classe | specie | background | luogo
Scrive <progetto>/estrazione/finestre_<tipologia>.json = lista di {id, file, righe, tok, testo}. Non usa la GPU."""
import json, os, re, sys, time
BASE = "/opt/dnb-classificatore"; P = "/shared/projects"
ETICHETTA = {"bestiario_new": "bestiario", "bestiario_old": "bestiario"}      # LoRA -> etichetta del classificatore
MAX_TOK_DEFAULT, MARGINE_DEFAULT, TAIL_MIN = 1800, 1, 120
# riga "tipo + taglia" che apre una scheda mostro nei manuali 5e (stessa famiglia di parole del rilevamento capitoli)
TIPO_TAGLIA = re.compile(r"\b(?:Aberrazione|Bestia|Celestiale|Costrutto|Drago|Elementale|Folletto|Fata|Gigante|Immondo|Mostruosit[aà]|Non\s+Morto|Melma|Pianta|Vegetale|Umanoide)\s+"
                         r"(?:Minuscol[oa]|Piccol[oa]|Medi[oa]|Grande|Enorme|Mastodontic[oa])\b", re.I)

def main(progetto, tip, margine=MARGINE_DEFAULT, max_tok=MAX_TOK_DEFAULT, out=None):
    if "/" in progetto or progetto.startswith("."): sys.exit("nome progetto non valido")
    et = ETICHETTA.get(tip, tip)
    cart = f"{P}/{progetto}"; E = json.load(open(f"{cart}/etichette/etichette.json"))
    from tokenizers import Tokenizer
    TOK = Tokenizer.from_file(f"{BASE}/tokenizer_qwen3.json"); ntok = lambda s: len(TOK.encode(s).ids)
    schede = tip == "bestiario_new"          # mostri 5e: UNA scheda per finestra (con piu' schede il LoRA ne estrae una sola)
    U = E["unita"]; TINY = 40                # unita' "solo titolo" (file-titolo minuscoli): il corpo sta nel file dopo
    sel = set()
    for i, u in enumerate(U):
        if et in u["tipi"]:
            for k in range(max(0, i - margine), min(len(U), i + margine + 1)):
                if U[k]["file"] == u["file"]: sel.add(k)
    # un titolo da solo non viene quasi mai etichettato: se il corpo e' scelto, il titolo (anche di un altro file) lo segue; e viceversa
    for i in sorted(sel):
        if i > 0 and U[i - 1]["tok"] < TINY: sel.add(i - 1)
        if os.environ.get("TINY_AVANTI", "0") == "1" and U[i]["tok"] < TINY and i + 1 < len(U): sel.add(i + 1)
    tratti = []; cur = None
    for k in sorted(sel):
        if cur and k == cur[1] + 1: cur[1] = k
        else:
            if cur: tratti.append(cur)
            cur = [k, k]
    if cur: tratti.append(cur)
    finestre = []; tok_tot = 0; cache_f = {}; visti = set()   # (file, riga_inizio) dei blocchi gia' messi: tabelle lunghe = piu' unita' con le stesse righe
    def righe_di(fn):
        if fn not in cache_f: cache_f[fn] = open(f"{cart}/capitoli_ricapitolati/{fn}", errors="ignore").read().split("\n")
        return cache_f[fn]
    for a_, z_ in tratti:
        blocchi = []   # (file, riga_inizio, testo, riga_fine)
        for k in range(a_, z_ + 1):
            fn = U[k]["file"]; rf = righe_di(fn); ra, rz = U[k]["righe"]
            cur_b = []; ini = None
            for n in range(ra, rz + 1):
                r = rf[n - 1] if n - 1 < len(rf) else ""
                if r.strip():
                    if ini is None: ini = n
                    cur_b.append(r)
                elif cur_b:
                    if (fn, ini) not in visti: visti.add((fn, ini)); blocchi.append((fn, ini, "\n".join(cur_b), n - 1))
                    cur_b = []; ini = None
            if cur_b and (fn, ini) not in visti: visti.add((fn, ini)); blocchi.append((fn, ini, "\n".join(cur_b), rz))
        cand = []
        win = []; wtok = 0
        def chiudi():
            nonlocal win, wtok
            if win: cand.append(win)
            win, wtok = [], 0
        for j, blk in enumerate(blocchi):
            bt = blk[2]; bk = ntok(bt) + 1
            inizia_scheda = schede and bt.lstrip().startswith("#") and any(TIPO_TAGLIA.search(x[2]) for x in blocchi[j + 1:j + 4])
            if win and inizia_scheda: chiudi()
            elif win and wtok + bk > max_tok:
                k2 = max((q for q in range(1, len(win)) if win[q][2].lstrip().startswith("#")), default=None)
                if k2 is not None and k2 > 0:
                    resto = win[k2:]; win = win[:k2]; chiudi(); win = resto; wtok = sum(ntok(x[2]) + 1 for x in win)
                else:
                    if win[-1][2].lstrip().startswith("#") and len(win) > 1:
                        ult = win.pop(); chiudi(); win = [ult]; wtok = ntok(ult[2]) + 1
                    else: chiudi()
            win.append(blk); wtok += bk
        chiudi()
        fin2 = []
        for w_ in cand:
            t_ = sum(ntok(x[2]) + 1 for x in w_)
            if fin2 and not schede and t_ < TAIL_MIN and fin2[-1][1] + t_ <= max_tok * 1.3: fin2[-1][0].extend(w_); fin2[-1][1] += t_
            else: fin2.append([list(w_), t_])
        for w_, t_ in fin2:
            testo = "\n\n".join(x[2] for x in w_)
            finestre.append({"id": len(finestre), "file": w_[0][0], "file_fine": w_[-1][0], "righe": [w_[0][1], w_[-1][3]], "tok": t_, "testo": testo}); tok_tot += t_
    os.makedirs(f"{cart}/estrazione", exist_ok=True)
    dest = out or f"{cart}/estrazione/finestre_{tip}.json"
    json.dump(finestre, open(dest, "w"), ensure_ascii=False)
    r = {"progetto": progetto, "tipologia": tip, "etichetta": et, "finestre": len(finestre), "token_inviati": tok_tot,
         "token_libro": E["token_totali"], "percentuale": round(100 * tok_tot / max(1, E["token_totali"]), 1), "file": dest}
    print(json.dumps(r, ensure_ascii=False))
    return r

if __name__ == "__main__":
    a = sys.argv[1:]; kw = {}
    for flag, nome, tipo in (("--margine", "margine", int), ("--max-tok", "max_tok", int), ("--out", "out", str)):
        if flag in a: i = a.index(flag); kw[nome] = tipo(a[i + 1]); del a[i:i + 2]
    main(a[0], a[1], **kw)
