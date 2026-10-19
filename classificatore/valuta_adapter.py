import json, sys, os, collections, torch
os.environ["HF_HOME"] = "/opt/classificatore_test/hf"; os.environ["HF_HUB_DISABLE_XET"] = "1"
from gliner2 import AutoExtractor
ET = ["bestiario", "incantesimo", "classe", "specie", "background", "oggetto", "regola", "luogo"]
SCH = {"tipologie": {"labels": ET, "multi_label": True, "cls_threshold": 0.0}}
test = [json.loads(l) for l in open("/shared/projects/lora/classificatore/dataset/test.jsonl")]
testi = [r["input"] for r in test]; vere = [set(r["output"]["classifications"][0]["true_label"]) for r in test]
m = AutoExtractor.from_pretrained("fastino/GLiNER2.5-multi-Decide"); m.load_adapter(sys.argv[1]); m = m.to("cuda")
sc = []
for i in range(0, len(testi), 64):
    for r in m.batch_classify_text(testi[i:i+64], SCH, batch_size=8, include_confidence=True, max_len=512):
        sc.append({x["label"]: x["confidence"] for x in r["tipologie"]})
json.dump(sc, open(sys.argv[2], "w"))
for th in (0.5, 0.6, 0.7, 0.8, 0.9):
    tp = fp = fn = ex = 0
    for s, v in zip(sc, vere):
        p = {t for t in ET if s[t] >= th}; ex += p == v
        tp += len(p & v); fp += len(p - v); fn += len(v - p)
    P = tp / max(1, tp + fp); R = tp / max(1, tp + fn)
    print(f"{sys.argv[1].split('/')[-1]} soglia {th}: F1 {2*P*R/max(1e-9,P+R):.3f} P {P:.3f} R {R:.3f} set-esatto {ex/len(sc):.3f}")
