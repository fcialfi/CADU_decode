# CADU_decode

Decodifica i campi fissi dei transfer frame contenuti in un file di CADU (CCSDS TM).

Un CADU (CCSDS 131.0-B) è il sync marker a 32 bit `1ACFFC1D` seguito dal codeblock: il transfer frame TM
(CCSDS 132.0-B) e i simboli di controllo Reed-Solomon (255,223), con il codeblock randomizzato dal
pseudo-randomizer CCSDS. Con interleaving I = 5 il CADU è di 4 + 255·5 = 1279 byte e il transfer frame di
223·5 = 1115 byte.

Per ogni CADU lo script:

- si sincronizza sul sync marker (se tra due CADU c'è altro, salta al marker successivo e lo segnala);
- de-randomizza il codeblock (sequenza h(x) = x⁸ + x⁷ + x⁵ + x³ + 1, che inizia FF 48 0E C0 9A …);
- mostra il **primary header**: versione, SCID, VCID, OCF flag, master e virtual channel frame counter,
  secondary header flag, sync flag, packet order flag, segment length ID, First Header Pointer
  (7FF: nessun header di pacchetto, 7FE: solo idle data);
- se il frame ha l'OCF, mostra la **CLCW**: type, version, status, COP in effect, VCID, No RF available,
  No bit lock, Lockout, Wait, Retransmit, FARM-B counter, report value.

Il data field e i simboli Reed-Solomon non sono letti (nessuna verifica o correzione RS). In fondo un
riepilogo: SCID trovati, frame per virtual channel, salti dei contatori di master e virtual channel, CADU
non contigui.

## Uso

Richiede solo Python 3.8 o superiore, senza librerie esterne.

```
python cadu_decode.py capture.bin                  # primi 50 frame a schermo e riepilogo
python cadu_decode.py capture.bin --max-rows 0     # tutti i frame
python cadu_decode.py capture.bin -o frames.csv    # tutti i frame in un file CSV
python cadu_decode.py capture.bin --interleave 4   # altro interleaving (CADU di 4 + 255·I byte)
python cadu_decode.py capture.bin --fecf           # frame con FECF (2 byte) in coda
python cadu_decode.py capture.bin --not-randomized # codeblock non randomizzato
```

Il file può essere binario oppure testo con i byte in esadecimale (spazi e a capo ignorati).

## Test

```
pip install pytest
pytest
```
