# CADU_decode

Decodifica i campi fissi dei transfer frame contenuti in un file di CADU (CCSDS TM).

Un CADU (CCSDS 131.0-B) è il sync marker a 32 bit `1ACFFC1D` seguito dal codeblock: il transfer frame TM
(CCSDS 132.0-B) e i simboli di controllo Reed-Solomon (255,223), con il codeblock randomizzato dal
pseudo-randomizer CCSDS. Con interleaving I = 5 il CADU è di 4 + 255·5 = 1279 byte e il transfer frame di
223·5 = 1115 byte.

Per ogni CADU lo script:

- si sincronizza sul sync marker (se tra due CADU c'è altro, salta al marker successivo e lo segnala);
- de-randomizza il codeblock (sequenza h(x) = x⁸ + x⁷ + x⁵ + x³ + 1, che inizia FF 48 0E C0 9A …);
- legge il **primary header**: versione, SCID, VCID, OCF flag, master e virtual channel frame counter,
  secondary header flag, sync flag, packet order flag, segment length ID, First Header Pointer
  (7FF: nessun header di pacchetto, 7FE: solo idle data);
- se il frame ha l'OCF, legge la **CLCW**: type, version, status, COP in effect, VCID, No RF available,
  No bit lock, Lockout, Wait, Retransmit, FARM-B counter, report value.

Il data field e i simboli Reed-Solomon non sono letti (nessuna verifica o correzione RS).

## Riepilogo

Per un file con molti CADU lo script mostra di default un riepilogo:

- numero di CADU, byte fuori dai CADU e quante volte la sincronizzazione si è persa e ritrovata;
- SCID trovati;
- master channel frame counter: primo e ultimo valore, salti e frame mancanti stimati (contatore a 8 bit:
  i salti sono contati modulo 256);
- per ogni **virtual channel**: numero di frame e quota, frame idle (FHP 7FE), frame con inizio di
  pacchetto, frame senza header (FHP 7FF), primo e ultimo VCFC, salti e frame mancanti;
- **CLCW**: valori di VC, COP in effect, status e report value, e in quanti frame sono attivi i flag
  No RF available, No bit lock, Lockout, Wait, Retransmit.

L'elenco frame per frame si aggiunge con `--frames` (o tutti i frame in CSV con `-o`).

## Uso

Richiede solo Python 3.8 o superiore, senza librerie esterne.

```
python cadu_decode.py capture.bin                  # riepilogo
python cadu_decode.py capture.bin --frames         # riepilogo e primi 50 frame
python cadu_decode.py capture.bin --frames 0       # riepilogo e tutti i frame
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
