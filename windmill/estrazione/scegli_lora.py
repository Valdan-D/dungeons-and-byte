import json, re
from typing import Literal

TUTTE = ["bestiario", "incantesimo", "classe", "specie", "background", "oggetto", "regola", "luogo"]

def edizione(nome):
    """Regola dell'utente: anno scritto nel nome >= 2014 -> new, prima -> old; senza anno, edizione 5 (2014) o 5.5 (2024) -> new."""
    m = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", nome)
    if m: return ("new" if int(m[-1]) >= 2014 else "old"), f"anno {m[-1]} nel nome"
    if re.search(r"(?:^|[ -])5(?:\.5)?(?:[ -]|$)", nome) or "5.2.1" in nome: return "new", "edizione 5 / 5.5 nel nome"
    return None, "nessun anno ne' edizione nel nome"

def main(progetto: str, tipologie: list[str] = TUTTE, bestiario: Literal["auto", "new", "old"] = "auto"):
    """Estrazione, passo 1: decide quali LoRA lanciare. Salta le tipologie senza blocchi etichettati; per il bestiario sceglie new/old dall'anno nel nome."""
    E = json.load(open(f"/shared/projects/{progetto}/etichette/etichette.json"))
    ed, perche = (bestiario, "scelto a mano") if bestiario != "auto" else edizione(progetto)
    lora, saltate = [], []
    for t in tipologie:
        n = len(E["intervalli"].get(t, []))
        if n == 0: saltate.append({"tipologia": t, "motivo": "nessun blocco etichettato"}); continue
        if t == "bestiario":
            if ed is None: raise Exception(f"Non riesco a decidere bestiario new/old: {perche}. Scegli 'new' o 'old' nel form.")
            lora.append({"tipologia": f"bestiario_{ed}", "intervalli": n})
        else: lora.append({"tipologia": t, "intervalli": n})
    return {"lora": lora, "saltate": saltate, "bestiario": f"{ed} ({perche})" if "bestiario" in tipologie else "non richiesto"}
